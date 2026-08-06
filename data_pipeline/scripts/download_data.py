import sys
import time
import tqdm
import logging
import requests
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv
import os  # Keep for os.getenv only

load_dotenv()

# ---------------------------------------------------------------------
# Dynamic and portable directory setup (works on any system)
# ---------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]  # one level up from scripts/
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
LOG_DIR = BASE_DIR / "logs" / "download_data_log"
URLS_PATH = DATA_DIR / "url.json"

# Ensure directories exist
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------
logging.basicConfig(
    filename=LOG_DIR / "download.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ---------------------------------------------------------------------
# Retry / integrity settings
# ---------------------------------------------------------------------
DOWNLOAD_MAX_RETRIES = 3                          # retries after the first attempt
DOWNLOAD_BACKOFF_SECONDS = (2.0, 4.0, 8.0)        # sleep before retry 1, 2, 3
DOWNLOAD_TIMEOUT_SECONDS = 20
CHUNK_SIZE = 8192

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.9; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0 Safari/537.36"
    )
}


class DownloadError(RuntimeError):
    """A download attempt failed and is worth retrying."""


class PermanentDownloadError(DownloadError):
    """The server rejected the request in a way retrying cannot fix (4xx)."""


def tmp_path_for(savepath: Path) -> Path:
    """Scratch path a download streams into before the atomic rename."""
    return savepath.with_name(savepath.name + ".tmp")


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - filesystem edge case
        logging.warning(f"Could not remove {path}: {exc}")


def _expected_length(response) -> Optional[int]:
    """Content-Length in bytes, or None when it cannot be trusted.

    With ``stream=True`` requests transparently decodes a compressed body, so
    when the server sets Content-Encoding the header describes the *compressed*
    size and comparing it to the bytes written would report a false mismatch.
    """
    headers = getattr(response, "headers", {}) or {}
    encoding = str(headers.get("Content-Encoding", "") or "").strip().lower()
    if encoding and encoding != "identity":
        return None
    raw = headers.get("Content-Length")
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _stream_to_tmp(
    url: str,
    tmp_path: Path,
    headers: Dict[str, str],
    timeout: int,
) -> int:
    """Stream one attempt into ``tmp_path``. Returns bytes written."""
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise DownloadError(f"request failed: {exc}") from exc

    status = getattr(response, "status_code", 0)
    if not getattr(response, "ok", False):
        # 4xx means the URL is wrong or forbidden. Retrying just wastes time.
        if 400 <= status < 500:
            raise PermanentDownloadError(f"HTTP {status}")
        raise DownloadError(f"HTTP {status}")

    written = 0
    try:
        with open(tmp_path, "wb") as f_out:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f_out.write(chunk)
                    written += len(chunk)
    except requests.exceptions.RequestException as exc:
        # Connection dropped mid-stream: tmp_path holds a partial file.
        raise DownloadError(f"stream interrupted after {written} bytes: {exc}") from exc

    if written == 0:
        raise DownloadError("server returned an empty body")

    expected = _expected_length(response)
    if expected is not None and expected != written:
        raise DownloadError(
            f"size mismatch: Content-Length {expected} != {written} bytes written"
        )

    return written


def download_with_retry(
    url: str,
    savepath: Path,
    *,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = DOWNLOAD_MAX_RETRIES,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> int:
    """Download ``url`` to ``savepath`` durably. Returns bytes written.

    The file is streamed to ``<savepath>.tmp`` and only renamed into place once
    it is non-empty and (when the server sent a trustworthy Content-Length) the
    right size. ``Path.replace`` is atomic within a filesystem, so a reader
    never observes a half-written PDF and the "already exists" skip check can
    only ever see complete files. Partial ``.tmp`` files are deleted on every
    failure so they cannot be mistaken for finished downloads.

    Raises ``DownloadError`` when every attempt fails.
    """
    request_headers = dict(headers or DEFAULT_HEADERS)
    tmp_path = tmp_path_for(savepath)
    savepath.parent.mkdir(parents=True, exist_ok=True)

    last_error: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        # Never build on top of an earlier partial attempt.
        _remove_quietly(tmp_path)
        try:
            written = _stream_to_tmp(url, tmp_path, request_headers, timeout)
        except PermanentDownloadError as exc:
            _remove_quietly(tmp_path)
            logging.warning(f"❌ Not retrying {url}: {exc}")
            raise
        except DownloadError as exc:
            last_error = exc
            _remove_quietly(tmp_path)
            if attempt >= max_retries:
                break
            delay = DOWNLOAD_BACKOFF_SECONDS[
                min(attempt, len(DOWNLOAD_BACKOFF_SECONDS) - 1)
            ]
            logging.warning(
                f"Attempt {attempt + 1}/{max_retries + 1} for {url} failed "
                f"({exc}). Retrying in {delay:.0f}s."
            )
            time.sleep(delay)
            continue

        tmp_path.replace(savepath)
        return written

    _remove_quietly(tmp_path)
    raise DownloadError(
        f"Failed to download {url} after {max_retries + 1} attempts: {last_error}"
    )


# ---------------------------------------------------------------------
# PDF Download Function
# ---------------------------------------------------------------------
def download_pdf(urls_path: Path, save_dir: Path) -> None:
    """
    Downloads PDFs from a list of URLs specified in a JSON file.

    Args:
        urls_path (Path): Path to the JSON file containing list of {"domain": ..., "company": ..., "url": ...}
        save_dir (Path): Directory where the downloaded PDFs should be saved.
    """
    with open(urls_path, 'r') as f:
        url_list = json.load(f)

    logging.info(f"\n{'=' * 50} NEW RUN @ {datetime.now()} {'=' * 50}")
    logging.info(f"Found {len(url_list)} entries in {urls_path}")

    for entry in tqdm.tqdm(url_list, desc="Downloading PDFs..."):
        domain = entry.get("domain")
        company = entry.get("company")
        url = entry.get("url")

        if not url:
            logging.warning(f"Skipping empty URL for domain {domain}, company {company}")
            continue

        try:
            # Clean up names to avoid invalid characters in filenames
            sanitized_domain = re.sub(r'[^\w\-_.]', '_', domain or "unknown_domain")
            sanitized_company = re.sub(r'[^\w\-_.]', '_', company or "unknown_company")

            # Create clean and descriptive filename
            filename = f"{sanitized_domain}_{sanitized_company}.pdf"
            savepath = save_dir / filename

            # A leftover .tmp from a killed run is never a valid artifact.
            _remove_quietly(tmp_path_for(savepath))

            if savepath.exists():
                if savepath.stat().st_size > 0:
                    logging.info(f"File already exists: {filename}, skipping download")
                    continue
                logging.warning(
                    f"Found empty {filename} from an earlier run, re-downloading."
                )
                _remove_quietly(savepath)

            written = download_with_retry(url, savepath)
            logging.info(
                f"✅ Downloaded: {filename} ({written} bytes) | "
                f"Domain: {domain} | Company: {company}"
            )

        except DownloadError as e:
            logging.warning(
                f"❌ Failed to download {url} | Domain: {domain} | "
                f"Company: {company} | Error: {e}"
            )
        except OSError as e:
            logging.exception(
                f"Filesystem error while downloading {url} | Domain: {domain} | "
                f"Company: {company} | Error: {e}"
            )

# ---------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------
if __name__ == "__main__":
    if not URLS_PATH.exists():
        logging.error(f"URL file not found: {URLS_PATH}")
        sys.exit(1)

    download_pdf(urls_path=URLS_PATH, save_dir=RAW_DATA_DIR)
