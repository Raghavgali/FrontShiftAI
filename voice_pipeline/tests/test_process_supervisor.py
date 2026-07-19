import os
import sys

import pytest

from voice_pipeline.utils.process_supervisor import run_supervised_process


def test_supervisor_returns_after_clean_exit(tmp_path, capsys) -> None:
    run_supervised_process(
        [sys.executable, "-u", "-c", "print('worker ready', flush=True)"],
        cwd=str(tmp_path),
        env=os.environ,
        max_restarts=2,
        heartbeat_timeout=1.0,
        restart_backoff=0,
    )

    output = capsys.readouterr().out
    assert "worker ready" in output
    assert "attempt 1/3" in output
    assert "attempt 2/3" not in output
    assert "exited normally" in output


def test_supervisor_restarts_a_crashed_worker(tmp_path, capsys) -> None:
    marker = tmp_path / "first-attempt-failed"
    script = (
        "from pathlib import Path; import sys; "
        "marker = Path(sys.argv[1]); "
        "already_failed = marker.exists(); "
        "marker.write_text('1'); "
        "print('recovered' if already_failed else 'crashed', flush=True); "
        "sys.exit(0 if already_failed else 7)"
    )

    run_supervised_process(
        [sys.executable, "-u", "-c", script, str(marker)],
        cwd=str(tmp_path),
        env=os.environ,
        max_restarts=1,
        heartbeat_timeout=1.0,
        restart_backoff=0,
    )

    output = capsys.readouterr().out
    assert "crashed" in output
    assert "exit code 7" in output
    assert "attempt 2/2" in output
    assert "recovered" in output


def test_supervisor_kills_worker_after_heartbeat_timeout(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="heartbeat timeout"):
        run_supervised_process(
            [sys.executable, "-u", "-c", "import time; time.sleep(5)"],
            cwd=str(tmp_path),
            env=os.environ,
            max_restarts=0,
            heartbeat_timeout=0.05,
            shutdown_timeout=0.2,
            restart_backoff=0,
        )


def test_periodic_output_keeps_worker_alive(tmp_path) -> None:
    script = (
        "import time; "
        "[(print('WORKER_HEARTBEAT', flush=True), time.sleep(0.02)) "
        "for _ in range(5)]"
    )

    run_supervised_process(
        [sys.executable, "-u", "-c", script],
        cwd=str(tmp_path),
        env=os.environ,
        max_restarts=0,
        heartbeat_timeout=0.05,
        restart_backoff=0,
    )
