"""Sequential runner for the data pipeline stages.

Stages are plain scripts executed in order. Every stage that exits 0 writes a
checkpoint marker, so a run that dies at stage 5 can be restarted with
``--resume`` and pick up from stage 5 instead of re-downloading and re-parsing
everything.

Usage::

    python pipeline_runner.py              # fresh run, clears old markers
    python pipeline_runner.py --resume     # skip stages already marked complete
    python pipeline_runner.py --force      # explicit fresh run
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

# --- Directory setup ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /data_pipeline
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
# Markers are run state, not source. data_pipeline/.pipeline_state/ is gitignored.
CHECKPOINT_DIR = os.path.join(BASE_DIR, ".pipeline_state")
os.makedirs(LOGS_DIR, exist_ok=True)

# --- Pipeline script order ---
DEFAULT_SCRIPTS = [
    "download_data.py",
    "pdf_parser.py",
    "preprocess.py",
    "chunker.py",
    "validate_data.py",
    "data_bias.py",
    "store_in_chromadb.py",
]


def _ensure_script_exists(script: str) -> bool:
    """Check whether the script exists in the scripts directory."""
    script_path = os.path.join(SCRIPTS_DIR, script)
    if not os.path.isfile(script_path):
        print(f"⚠️  Skipping missing script: {script}")
        return False
    return True


# ---------------------------------------------------------------------------
# Checkpoint markers
# ---------------------------------------------------------------------------
def marker_path(stage_index: int) -> str:
    """Absolute path of the marker for a 1-indexed stage."""
    return os.path.join(CHECKPOINT_DIR, f".stage_{stage_index}_complete")


def mark_stage_complete(stage_index: int, script: str) -> str:
    """Write the marker for a finished stage and return its path.

    The script name is stored inside the marker so a reordered or edited stage
    list does not silently skip the wrong work on the next ``--resume``.
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    path = marker_path(stage_index)
    payload = {
        "stage": stage_index,
        "script": script,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def stage_completed(stage_index: int, script: str) -> bool:
    """True when a marker exists *and* it was written for this same script."""
    path = marker_path(stage_index)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        # Corrupt or truncated marker: treat the stage as incomplete.
        return False
    return payload.get("script") == script


def clear_markers() -> int:
    """Delete every stage marker. Returns how many were removed."""
    if not os.path.isdir(CHECKPOINT_DIR):
        return 0
    removed = 0
    for name in os.listdir(CHECKPOINT_DIR):
        if name.startswith(".stage_") and name.endswith("_complete"):
            os.remove(os.path.join(CHECKPOINT_DIR, name))
            removed += 1
    return removed


def completed_stages() -> List[int]:
    """Sorted stage indexes that currently have a marker (for diagnostics)."""
    if not os.path.isdir(CHECKPOINT_DIR):
        return []
    found = []
    for name in os.listdir(CHECKPOINT_DIR):
        if name.startswith(".stage_") and name.endswith("_complete"):
            try:
                found.append(int(name.split("_")[1]))
            except (IndexError, ValueError):
                continue
    return sorted(found)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_pipeline(
    script_names: Optional[Iterable[str]] = None,
    *,
    resume: bool = False,
) -> Tuple[str, bool]:
    """Run the pipeline scripts sequentially.

    Args:
        script_names: Override the stage list. Defaults to ``DEFAULT_SCRIPTS``.
        resume: Skip stages that already have a matching checkpoint marker.
            When False (the default, i.e. a fresh run) all markers are cleared
            first so nothing is skipped.

    Returns:
        ``(log_file_path, success)``. ``success`` is False when a stage exited
        non-zero, which lets the caller propagate a real failure instead of
        reporting a broken run as a success.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOGS_DIR, f"pipeline_run_{timestamp}.log")
    scripts: List[str] = list(script_names) if script_names else DEFAULT_SCRIPTS

    if resume:
        print(f"⏩ Resume mode. Existing markers: {completed_stages() or 'none'}")
    else:
        cleared = clear_markers()
        if cleared:
            print(f"🧹 Fresh run: cleared {cleared} checkpoint marker(s).")

    success = True
    with open(log_file, "w", encoding="utf-8") as log:
        for stage_index, script in enumerate(scripts, start=1):
            script_path = os.path.join(SCRIPTS_DIR, script)
            if not _ensure_script_exists(script):
                continue

            if resume and stage_completed(stage_index, script):
                msg = f"⏭️  Stage {stage_index} ({script}) already complete, skipping."
                print(msg)
                log.write(msg + "\n")
                log.flush()
                continue

            print(f"\n🚀 Running stage {stage_index}: {script} ...")
            log.write(f"\n🚀 Running stage {stage_index}: {script} ...\n")
            log.flush()

            try:
                subprocess.run(
                    [sys.executable, script_path],
                    check=True,
                    stdout=log,
                    stderr=log,
                )
            except subprocess.CalledProcessError as e:
                success = False
                print(f"❌ {script} failed. Check {log_file} for details.")
                log.write(f"❌ {script} failed with error code {e.returncode}\n")
                log.write(
                    f"↩️  Re-run with --resume to restart from stage {stage_index}.\n"
                )
                break

            mark_stage_complete(stage_index, script)
            print(f"✅ Stage {stage_index} ({script}) completed successfully.")
            log.write(f"✅ Stage {stage_index} ({script}) completed successfully.\n")

    print(f"\n📘 Pipeline execution finished. Logs saved to: {log_file}")
    return log_file, success


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FrontShiftAI data pipeline.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--resume",
        action="store_true",
        help="Skip stages that already have a checkpoint marker.",
    )
    group.add_argument(
        "--force",
        action="store_true",
        help="Fresh run: clear all checkpoint markers first (default behaviour).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    _, ok = run_pipeline(resume=args.resume)
    sys.exit(0 if ok else 1)
