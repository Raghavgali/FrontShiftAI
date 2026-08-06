"""
Embedding and ChromaDB storage pipeline.
Reads validated chunks from data/validated/valid_chunks.jsonl
and stores embeddings into data/vector_db/.
"""

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import chromadb
import pandas as pd
from chromadb.utils import embedding_functions

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
VALIDATED_CHUNKS_PATH = DATA_DIR / "validated" / "valid_chunks.jsonl"
VECTOR_DB_PATH = DATA_DIR / "vector_db"
LOG_DIR = BASE_DIR / "logs" / "vector_store"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = logging.FileHandler(LOG_DIR / "store_in_chromadb.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# Chroma embeds every document in the call, so one giant add() is a single
# all-or-nothing unit of work whose failure leaves the store in an unknown
# state. 500 keeps each unit small enough to report on.
BATCH_SIZE = 500


class BatchWriteError(RuntimeError):
    """A batch insert failed. Carries the batches that did land."""

    def __init__(self, message: str, succeeded_batches: Sequence[int], written: int):
        super().__init__(message)
        self.succeeded_batches = list(succeeded_batches)
        self.written = written


def chunk_dedupe_key(chunk: Dict[str, Any]) -> str:
    """Stable identity for a chunk.

    Prefers the ``hash_64`` produced upstream by the chunker. Falls back to a
    digest of the text so chunks missing a hash are still deduplicated instead
    of all colliding on the empty string.
    """
    existing = chunk.get("hash")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    text = chunk.get("text") or ""
    return "sha256:" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def dedupe_chunks(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Drop repeated chunks, keeping first occurrence order.

    Returns ``(unique_chunks, dropped_count)``.
    """
    seen = set()
    unique: List[Dict[str, Any]] = []
    dropped = 0
    for chunk in chunks:
        key = chunk_dedupe_key(chunk)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        unique.append(chunk)
    return unique, dropped


def add_in_batches(
    collection,
    documents: Sequence[str],
    metadatas: Sequence[Dict[str, Any]],
    ids: Sequence[str],
    batch_size: int = BATCH_SIZE,
) -> List[int]:
    """Insert in bounded batches, logging each one that lands.

    Returns the 1-indexed batch numbers that succeeded. Raises
    ``BatchWriteError`` naming the last good batch, so a failed run says
    exactly how far it got instead of leaving an opaque partial collection.
    """
    total = len(documents)
    if not (total == len(metadatas) == len(ids)):
        raise ValueError(
            f"documents/metadatas/ids length mismatch: "
            f"{total}/{len(metadatas)}/{len(ids)}"
        )
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")

    batch_count = (total + batch_size - 1) // batch_size
    succeeded: List[int] = []
    written = 0

    for batch_number, start in enumerate(range(0, total, batch_size), start=1):
        end = min(start + batch_size, total)
        try:
            collection.add(
                documents=list(documents[start:end]),
                metadatas=list(metadatas[start:end]),
                ids=list(ids[start:end]),
            )
        except Exception as exc:
            logger.error(
                "❌ Batch %d/%d (rows %d-%d) failed: %s. Batches that succeeded: %s",
                batch_number,
                batch_count,
                start,
                end - 1,
                exc,
                succeeded or "none",
            )
            raise BatchWriteError(
                f"Batch {batch_number}/{batch_count} (rows {start}-{end - 1}) failed: "
                f"{exc}. Succeeded batches: {succeeded or 'none'} "
                f"({written} of {total} chunks stored).",
                succeeded,
                written,
            ) from exc

        succeeded.append(batch_number)
        written += end - start
        logger.info(
            "✅ Batch %d/%d stored (rows %d-%d, %d/%d chunks total).",
            batch_number,
            batch_count,
            start,
            end - 1,
            written,
            total,
        )

    return succeeded


def main():
    logger.info("🚀 Starting embedding and ChromaDB storage pipeline...")

    try:
        if not VALIDATED_CHUNKS_PATH.exists():
            msg = f"Validated chunks file not found: {VALIDATED_CHUNKS_PATH}. Run validate_data.py first."
            logger.error(msg)
            raise FileNotFoundError(msg)

        VECTOR_DB_PATH.mkdir(parents=True, exist_ok=True)

        # --- Load validated chunks ---
        logger.info(f"📥 Loading validated chunks from: {VALIDATED_CHUNKS_PATH}")
        all_chunks = []
        with open(VALIDATED_CHUNKS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                    meta = record.get("metadata", {})
                    all_chunks.append({
                        "text": record.get("text", ""),
                        "company": meta.get("company", "Unknown"),
                        "chunk_id": meta.get("chunk_id", ""),
                        "filename": meta.get("doc_id", ""),
                        "section": meta.get("section_title", ""),
                        "hash": meta.get("hash_64", "")
                    })
                except json.JSONDecodeError:
                    logger.warning("⚠️ Skipping invalid JSON line in valid_chunks.jsonl")

        logger.info(f"✅ Loaded {len(all_chunks)} valid chunks for embedding.")

        # --- Deduplicate before embedding ---
        # Embedding is the expensive step, so duplicates are dropped first.
        all_chunks, dropped = dedupe_chunks(all_chunks)
        if dropped:
            logger.warning(
                f"🧽 Dropped {dropped} duplicate chunk(s); "
                f"{len(all_chunks)} unique chunks remain."
            )

        df = pd.DataFrame(all_chunks)

        if df.empty:
            raise ValueError("No valid chunks found in file. Check validation output.")

        # --- Initialize ChromaDB ---
        client = chromadb.PersistentClient(path=str(VECTOR_DB_PATH))
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        collection_name = "frontshift_handbooks"
        existing_collections = [c.name for c in client.list_collections()]

        # Clean rebuild
        if collection_name in existing_collections:
            logger.warning(f"🧹 Removing existing collection '{collection_name}' for rebuild.")
            client.delete_collection(name=collection_name)

        collection = client.create_collection(
            name=collection_name,
            embedding_function=embedding_fn
        )

        # --- Prepare data ---
        documents = df["text"].tolist()
        metadatas = df[["filename", "company", "chunk_id", "section"]].to_dict(orient="records")
        ids = [f"chunk_{i}" for i in range(len(df))]

        # --- Sanity checks ---
        if len(documents) == 0:
            logger.warning("No documents to embed. Check valid_chunks.jsonl contents.")
        elif len(documents) < 10:
            logger.warning(f"Only {len(documents)} chunks detected. Possible small dataset.")

        # --- Add to ChromaDB in bounded batches ---
        logger.info(
            f"🧠 Adding {len(documents)} chunks to ChromaDB collection "
            f"'{collection_name}' in batches of {BATCH_SIZE}..."
        )
        succeeded = add_in_batches(collection, documents, metadatas, ids)

        logger.info(
            f"💾 Stored {len(documents)} embeddings in collection "
            f"'{collection_name}' across {len(succeeded)} batch(es)."
        )
        logger.info(f"📂 Vector DB saved at: {VECTOR_DB_PATH}")

    except Exception as e:
        logger.error(f"❌ Error during embedding/storage stage: {e}", exc_info=True)
        raise

    logger.info("✅ Embedding pipeline completed successfully.")


if __name__ == "__main__":
    main()
