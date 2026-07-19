"""Subprocess supervision helpers for the on-demand voice worker."""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence


def _stop_process(
    process: subprocess.Popen[str],
    *,
    shutdown_timeout: float,
) -> None:
    """Terminate a child process, escalating to kill when it does not exit."""

    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=shutdown_timeout)
    except subprocess.TimeoutExpired:
        print("Worker ignored SIGTERM; sending SIGKILL", flush=True)
        process.kill()
        process.wait()


def _run_once(
    command: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    heartbeat_timeout: float,
    shutdown_timeout: float,
) -> tuple[int, bool]:
    """Run one child and return ``(return_code, heartbeat_expired)``."""

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    last_output = time.monotonic()
    heartbeat_expired = False
    poll_interval = min(1.0, heartbeat_timeout)

    try:
        while True:
            try:
                line = output_queue.get(timeout=poll_interval)
            except queue.Empty:
                line = ""

            if line is None:
                break

            if line:
                print(line, end="", flush=True)
                last_output = time.monotonic()

            if (
                process.poll() is None
                and time.monotonic() - last_output >= heartbeat_timeout
            ):
                heartbeat_expired = True
                print(
                    f"No worker output for {heartbeat_timeout:.0f}s; "
                    "restarting worker",
                    flush=True,
                )
                _stop_process(process, shutdown_timeout=shutdown_timeout)
                break

        reader.join(timeout=shutdown_timeout)
        return process.wait(), heartbeat_expired
    except BaseException:
        _stop_process(process, shutdown_timeout=shutdown_timeout)
        reader.join(timeout=shutdown_timeout)
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def run_supervised_process(
    command: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    max_restarts: int = 2,
    heartbeat_timeout: float = 60.0,
    shutdown_timeout: float = 10.0,
    restart_backoff: float = 1.0,
) -> None:
    """Run a process, restarting crashes or heartbeat stalls up to a limit."""

    if max_restarts < 0:
        raise ValueError("max_restarts must be non-negative")
    if heartbeat_timeout <= 0:
        raise ValueError("heartbeat_timeout must be positive")
    if shutdown_timeout <= 0:
        raise ValueError("shutdown_timeout must be positive")
    if restart_backoff < 0:
        raise ValueError("restart_backoff must be non-negative")

    for attempt in range(max_restarts + 1):
        print(
            "Starting voice worker "
            f"(attempt {attempt + 1}/{max_restarts + 1})",
            flush=True,
        )

        return_code, heartbeat_expired = _run_once(
            command,
            cwd=cwd,
            env=env,
            heartbeat_timeout=heartbeat_timeout,
            shutdown_timeout=shutdown_timeout,
        )

        if return_code == 0 and not heartbeat_expired:
            print("Voice worker exited normally", flush=True)
            return

        reason = (
            "heartbeat timeout"
            if heartbeat_expired
            else f"exit code {return_code}"
        )
        if attempt == max_restarts:
            raise RuntimeError(
                f"Voice worker failed after {max_restarts} restarts: {reason}"
            )

        backoff = restart_backoff * (attempt + 1)
        print(
            f"Worker failed due to {reason}; restarting in {backoff:g}s",
            flush=True,
        )
        time.sleep(backoff)
