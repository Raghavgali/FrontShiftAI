"""Utility helpers for loading the FrontShiftAI RAG corpus.
This module centralises the logic for locating, downloading (if required),
and opening the Chroma vector store that backs the RAG pipeline.  It returns
both the raw Chroma collection – used for dense vector search – and a set of
LangChain :class`~langchain.schema.Document` objects that power lexical
retrievers such as BM25.
"""
from __future__ import annotations
import copy
import json
import logging
import os
import subprocess
import tarfile
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional
import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils import embedding_functions
from langchain_core.documents import Document
from .config_manager import get_vector_store_config

PROJECT_ROOT = Path(
    os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2])
)
_DEFAULT_CHROMA_DIR = PROJECT_ROOT / "data_pipeline" / "data" / "vector_db"
VECTOR_CONFIG = get_vector_store_config()
_DEFAULT_COMPANY_INDEX = _DEFAULT_CHROMA_DIR / "company_index.json"
logger = logging.getLogger(__name__)

def _resolve_path(path_value: Optional[str], default: Path) -> Path:
    if not path_value:
        return default
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate

def _to_optional_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

class _BoundedCache:
    """Tiny thread-safe LRU cache for string-keyed lookups.

    Two properties matter here:

    * **Bounded.** ``resolve_company_filter`` is keyed by a company name that
      arrives from a request payload, so an unbounded dict would let arbitrary
      input grow the process heap forever.
    * **Lock guarded.** The RAG pipeline is served from a thread pool, so the
      same lock-guarded ``OrderedDict`` idiom as ``RAGPipeline._cache`` is used
      instead of a bare dict.
    """

    __slots__ = ("_maxsize", "_data", "_lock")

    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(int(maxsize), 1)
        self._data: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                evicted, _ = self._data.popitem(last=False)
                logger.debug("Evicted cache entry %r (maxsize=%d)", evicted, self._maxsize)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data


# Company names come from user input, so this cache is deliberately small.
COMPANY_FILTER_CACHE_MAXSIZE = 128
_COMPANY_FILTER_CACHE = _BoundedCache(COMPANY_FILTER_CACHE_MAXSIZE)

# Keyed by collection name, which is configuration derived, so a handful of
# slots is plenty. Bounded anyway so nothing here can grow without limit.
_ALL_COMPANIES_CACHE = _BoundedCache(8)


def _cache_company_filter(
        normalized: str,
        resolved_filter: Dict[str, Dict],
) -> Dict[str, Dict]:
    """Store a resolved filter under ``normalized`` and return it.

    A deep copy is cached so a caller that mutates the returned ``where``
    clause cannot corrupt the entry every later request reads.
    """
    _COMPANY_FILTER_CACHE.put(normalized, copy.deepcopy(resolved_filter))
    return resolved_filter


def clear_company_caches() -> None:
    """Drop the company filter and company list caches.

    Call after re-syncing or rebuilding the vector store, and from tests.
    """
    _COMPANY_FILTER_CACHE.clear()
    _ALL_COMPANIES_CACHE.clear()

CHROMA_DIR = _resolve_path(
    os.getenv("CHROMA_DIR") or VECTOR_CONFIG.get("local_path"),
    _DEFAULT_CHROMA_DIR,
)
CHROMA_REMOTE_URI = os.getenv("CHROMA_REMOTE_URI") or VECTOR_CONFIG.get("remote_uri")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION") or VECTOR_CONFIG.get("collection", "frontshift_handbooks")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or VECTOR_CONFIG.get("embedding_model", "all-MiniLM-L6-v2")
DEFAULT_MAX_DOCUMENTS = (
    _to_optional_int(os.getenv("CHROMA_MAX_DOCUMENTS"))
    or _to_optional_int(VECTOR_CONFIG.get("max_documents"))
    or 1000
)
if DEFAULT_MAX_DOCUMENTS is not None and DEFAULT_MAX_DOCUMENTS <= 0:
    DEFAULT_MAX_DOCUMENTS = None

COMPANY_INDEX_PATH = _resolve_path(
    os.getenv("COMPANY_INDEX_PATH"),
    _DEFAULT_COMPANY_INDEX,
)

def _normalize_company(value: Optional[str]) -> str:
    return (value or "").strip().lower()

@lru_cache(maxsize=1)
def _load_company_index() -> Dict[str, str]:
    """Return a cached lookup table of company names (if available)."""
    try:
        if not COMPANY_INDEX_PATH or not COMPANY_INDEX_PATH.exists():
            return {}
        with COMPANY_INDEX_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to load company index from %s: %s",
            COMPANY_INDEX_PATH,
            exc,
        )
        return {}

    names: List[str] = []
    if isinstance(payload, dict):
        if "companies" in payload and isinstance(payload["companies"], list):
            names = [str(item) for item in payload["companies"]]
        else:
            names = [str(value) for value in payload.values()]
    elif isinstance(payload, list):
        names = [str(item) for item in payload]

    index: Dict[str, str] = {}
    for name in names:
        normalized = _normalize_company(name)
        if normalized:
            index[normalized] = name
    return index

class CompanyCorpus(NamedTuple):
    """Container returned by :func`load_data_company`.
    Attributes
    ----------
    collection:
        The live Chroma collection handle for dense/vector retrieval.
    documents:
        LangChain ``Document`` objects materialised from the same store,
        suitable for BM25 or other lexical strategies.
    filter_kwargs:
        Optional ``where`` clause applied when fetching data; useful for
        reusing the same filter during collection queries.
    """
    collection: Collection
    documents: List[Document]
    filter_kwargs: Dict[str, Dict]

# Mirrors the ``gcs_sync`` policy in docs/resilience_policy.md (300s timeout,
# 3 retries, exponential 5s base). It is restated here rather than imported
# from ``backend.utils.resilience`` because chat_pipeline must stay importable
# without the backend package on sys.path.
GCS_SYNC_MAX_RETRIES = 3                          # retries after the first attempt
GCS_SYNC_BACKOFF_SECONDS = (5.0, 10.0, 20.0)      # sleep before retry 1, 2, 3
GCS_SYNC_TIMEOUT_SECONDS = 300.0


class ChromaSyncError(RuntimeError):
    """Raised when the remote Chroma archive cannot be fetched or trusted."""


def _verify_chroma_archive(tar_path: Path) -> None:
    """Raise ``ChromaSyncError`` unless ``tar_path`` is a readable, non-empty tar.

    ``gsutil cp`` can leave behind a truncated object when the transfer dies
    mid-stream, and a truncated gzip stream only fails later during extraction.
    Checking size and readability up front keeps a bad artifact from being
    treated as a successful sync.
    """
    if not tar_path.exists():
        raise ChromaSyncError(f"Download reported success but {tar_path} is missing.")
    size = tar_path.stat().st_size
    if size <= 0:
        raise ChromaSyncError(f"Downloaded archive {tar_path} is empty (0 bytes).")
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            if tar.next() is None:
                raise ChromaSyncError(
                    f"Downloaded archive {tar_path} contains no members."
                )
    # A truncated gzip stream surfaces as EOFError, and a corrupt one as
    # gzip.BadGzipFile (an OSError), neither of which is a TarError.
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ChromaSyncError(
            f"Downloaded archive {tar_path} ({size} bytes) is not a readable tar.gz: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    logger.info("Verified Chroma archive %s (%d bytes).", tar_path, size)


def _fetch_chroma_archive(remote_uri: str, tar_path: Path) -> None:
    """Single ``gsutil cp`` attempt followed by an integrity check.

    Raises ``FileNotFoundError`` when gsutil itself is missing (not retryable)
    and ``ChromaSyncError`` for anything transient.
    """
    if tar_path.exists():
        # A partial file from a previous attempt must never be reused.
        tar_path.unlink()
    try:
        subprocess.run(
            ["gsutil", "cp", remote_uri, str(tar_path)],
            check=True,
            capture_output=True,
            timeout=GCS_SYNC_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        error_msg = exc.stderr.decode().strip() if exc.stderr else str(exc)
        raise ChromaSyncError(
            f"Failed to download Chroma store from {remote_uri}: {error_msg}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ChromaSyncError(
            f"Download of {remote_uri} exceeded {GCS_SYNC_TIMEOUT_SECONDS}s."
        ) from exc
    _verify_chroma_archive(tar_path)


def _download_chroma_archive(remote_uri: str, tar_path: Path) -> None:
    """Download the archive with bounded retries and exponential backoff.

    Partial downloads are removed between attempts so a truncated file can
    never be mistaken for a complete one.
    """
    last_error: Optional[BaseException] = None
    for attempt in range(GCS_SYNC_MAX_RETRIES + 1):
        try:
            logger.info(
                "Downloading ChromaDB archive to %s (attempt %d/%d)",
                tar_path,
                attempt + 1,
                GCS_SYNC_MAX_RETRIES + 1,
            )
            _fetch_chroma_archive(remote_uri, tar_path)
            return
        except FileNotFoundError as exc:
            # gsutil is not installed. Retrying cannot help.
            raise RuntimeError(
                "gsutil is required to download the remote Chroma store."
            ) from exc
        except ChromaSyncError as exc:
            last_error = exc
            if tar_path.exists():
                tar_path.unlink()
            if attempt >= GCS_SYNC_MAX_RETRIES:
                break
            delay = GCS_SYNC_BACKOFF_SECONDS[
                min(attempt, len(GCS_SYNC_BACKOFF_SECONDS) - 1)
            ]
            logger.warning(
                "Chroma sync attempt %d failed (%s). Retrying in %.0fs.",
                attempt + 1,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Chroma sync from {remote_uri} failed after "
        f"{GCS_SYNC_MAX_RETRIES + 1} attempts: {last_error}"
    ) from last_error


def ensure_chroma_store(chroma_dir: Path = CHROMA_DIR, remote_uri: Optional[str] = CHROMA_REMOTE_URI) -> Path:
    """Ensure the Chroma vector store is available locally.
    Parameters
    ----------
    chroma_dir:
        Path to the expected local store. Defaults to ``CHROMA_DIR``.
    remote_uri:
        Optional GCS/HTTP URI to sync from when the local store is missing.
    Returns
    -------
    Path
        The directory that contains the Chroma DB files.
    Raises
    ------
    FileNotFoundError
        If the store cannot be found and no remote URI is provided.
    RuntimeError
        If syncing from the remote URI fails.
    """
    chroma_dir = Path(chroma_dir)
    
    # If already exists, return immediately
    if chroma_dir.exists() and any(chroma_dir.iterdir()):
        logger.info(f"ChromaDB store found at: {chroma_dir}")
        return chroma_dir

    # If no remote URI provided, raise error
    if not remote_uri:
        raise FileNotFoundError(
            f"Chroma store not found at {chroma_dir}. Set CHROMA_DIR, use DVC to pull it, "
            "or provide CHROMA_REMOTE_URI."
        )

    logger.info(f"ChromaDB not found locally. Downloading from: {remote_uri}")
    
    # Create parent directories
    chroma_dir.parent.mkdir(parents=True, exist_ok=True)
    
    # Download tar.gz file from GCS
    tar_path = chroma_dir.parent / "chroma_db.tar.gz"
    
    try:
        # Retries + integrity verification live in _download_chroma_archive.
        _download_chroma_archive(remote_uri, tar_path)

        logger.info("Download complete. Extracting archive...")
        with tarfile.open(tar_path, "r:gz") as tar:
            # filter="data" rejects absolute paths and links that escape the
            # destination, so a tampered archive cannot write outside it.
            tar.extractall(path=chroma_dir.parent, filter="data")

        logger.info(f"ChromaDB store extracted to: {chroma_dir}")
    except tarfile.TarError as exc:
        raise RuntimeError(f"Failed to extract Chroma store archive: {exc}") from exc
    finally:
        # Clean up tar file even if extraction failed
        if tar_path.exists():
            tar_path.unlink()

    # Verify extraction succeeded
    if not chroma_dir.exists() or not any(chroma_dir.iterdir()):
        raise RuntimeError(f"Chroma store download completed but {chroma_dir} is still missing or empty.")
    
    logger.info(f"ChromaDB store ready at: {chroma_dir}")
    return chroma_dir

@lru_cache(maxsize=1)
def _embedding_function():
    """Create (and cache) the sentence transformer embedding function."""
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )

@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.PersistentClient:
    """Return a cached Chroma ``PersistentClient`` instance."""
    chroma_path = ensure_chroma_store()
    return chromadb.PersistentClient(path=str(chroma_path))

@lru_cache(maxsize=1)
def get_collection() -> Collection:
    """Open the configured Chroma collection with the shared embedding fn."""
    client = get_chroma_client()
    try:
        return client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=_embedding_function(),
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(
            "Failed to open Chroma collection. The vector store may be corrupt or "
            "incompatible. Try deleting the directory at "
            f"{CHROMA_DIR} and re-syncing it."
        ) from exc

def _get_all_companies(collection: Collection) -> List[str]:
    """Fetch all unique company names from the vector store.

    Cached per collection name. ``functools.lru_cache`` cannot be used here:
    ``chromadb.api.models.Collection.Collection`` defines ``__eq__`` without
    ``__hash__``, so ``Collection.__hash__`` is ``None`` and every call would
    raise ``TypeError: unhashable type: 'Collection'``. An lru_cache would also
    pin the Collection (and its client) alive for the process lifetime.

    Failures are not cached, so a transient Chroma error cannot poison the
    cache with an empty list forever.
    """
    cache_key = str(getattr(collection, "name", "") or "<unnamed>")
    cached = _ALL_COMPANIES_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    try:
        # Fetch a large sample of metadata to find unique companies
        # Note: Chroma doesn't have a "SELECT DISTINCT" so we peek/scan
        peek = collection.get(include=["metadatas"], limit=10000)
        metadatas = peek.get("metadatas", [])
        companies = set()
        for m in metadatas:
            if m and isinstance(m.get("company"), str) and m["company"]:
                companies.add(m["company"])
    except Exception as e:
        logger.error(f"Failed to fetch companies: {e}")
        return []

    resolved = sorted(companies)
    _ALL_COMPANIES_CACHE.put(cache_key, resolved)
    return list(resolved)

def resolve_company_filter(collection: Collection, company_name: Optional[str]) -> Dict[str, Dict]:
    """Build a case-insensitive ``where`` clause for the requested company."""
    if not company_name:
        return {}
    normalized = _normalize_company(company_name)
    if not normalized:
        return {}

    cached_filter = _COMPANY_FILTER_CACHE.get(normalized)
    if cached_filter is not None:
        return copy.deepcopy(cached_filter)

    index = _load_company_index()
    if index:
        if normalized in index:
            return _cache_company_filter(
                normalized,
                {"where": {"company": index[normalized]}},
            )
        for key, canonical in index.items():
            if normalized in key:
                return _cache_company_filter(
                    normalized,
                    {"where": {"company": canonical}},
                )
    try:
        peek = collection.peek(limit=200)
        matches = [
            meta.get("company")
            for meta in peek.get("metadatas", [])
            if normalized in _normalize_company(meta.get("company"))
        ]
        if matches:
            return _cache_company_filter(
                normalized,
                {"where": {"company": matches[0]}},
            )
    except Exception as exc:  # pragma: no cover - best effort fallback
        logger.debug("Unable to identify company filter via peek(): %s", exc)

    # Fallback: Dynamic Lookup (Replaces hardcoded list)
    known_companies = _get_all_companies(collection)
    for known in known_companies:
        if normalized in known.lower():
            logger.info("Mapped '%s' to known company '%s'", company_name, known)
            return _cache_company_filter(
                normalized,
                {"where": {"company": known}},
            )

    # If no match found in dynamic list, try raw contains
    logger.warning(
        "Company '%s' not found in dynamic list. Using raw contains.",
        company_name,
    )
    return _cache_company_filter(
        normalized,
        {"where": {"company": {"$contains": normalized}}},
    )

def _collection_documents(
    collection: Collection,
    where: Optional[Dict] = None,
    limit: Optional[int] = DEFAULT_MAX_DOCUMENTS,
) -> List[Document]:
    """Materialise LangChain ``Document`` objects from a collection snapshot."""
    kwargs: Dict = {"include": ["documents", "metadatas"]}
    if where:
        kwargs["where"] = where
    if limit:
        kwargs["limit"] = limit

    try:
        snapshot = collection.get(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(
            "Unable to materialise documents from Chroma. If the store was upgraded "
            "with a different Chroma version, re-ingest or resync the store."
        ) from exc

    documents = snapshot.get("documents", [])
    metadatas = snapshot.get("metadatas", [])
    return [
        Document(page_content=doc, metadata=meta or {})
        for doc, meta in zip(documents, metadatas)
    ]

def load_data_company(
    company_name: Optional[str] = None,
    max_documents: Optional[int] = DEFAULT_MAX_DOCUMENTS,
) -> CompanyCorpus:
    """Return the core corpus for the requested company.
    Parameters
    ----------
    company_name:
        Optional company filter applied to both vector and lexical views.
    max_documents:
        Optional guard to avoid loading the entire collection when BM25 only
        needs a subset. Defaults to ``CHROMA_MAX_DOCUMENTS`` when set.
    Returns
    -------
    CompanyCorpus
        The NamedTuple containing the ``collection`` handle, ``documents``
        list, and ``filter_kwargs`` (for reuse in vector queries).
    """
    collection = get_collection()
    filter_kwargs = resolve_company_filter(collection, company_name)
    documents = _collection_documents(
        collection,
        where=filter_kwargs.get("where"),
        limit=max_documents,
    )
    return CompanyCorpus(collection=collection, documents=documents, filter_kwargs=filter_kwargs)

__all__ = [
    "ChromaSyncError",
    "CompanyCorpus",
    "clear_company_caches",
    "load_data_company",
    "get_collection",
    "ensure_chroma_store",
    "resolve_company_filter",
]
