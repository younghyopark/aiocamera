"""Daemon lifecycle: start / stop / status via PID file + subprocess."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

log = logging.getLogger(__name__)

PID_FILE = "/tmp/pycaas.pid"
LOG_FILE = "/tmp/pycaas.log"


def _read_pid() -> int | None:
    try:
        return int(open(PID_FILE).read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0 = existence check
        return True
    except OSError:
        return False


def is_service_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    if _is_running(pid):
        return True
    # Stale PID file — clean up
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass
    return False


def start_daemon() -> int:
    """Fork a background process running the camera service.  Returns the PID."""
    if is_service_running():
        pid = _read_pid()
        raise RuntimeError(f"Service already running (PID {pid})")

    log_fd = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "pycaas.daemon"],
        stdout=log_fd,
        stderr=log_fd,
        start_new_session=True,
    )
    # Write PID
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    return proc.pid


def stop_daemon() -> None:
    """Send SIGTERM to the running daemon."""
    pid = _read_pid()
    if pid is None or not _is_running(pid):
        raise RuntimeError("Service is not running")

    os.kill(pid, signal.SIGTERM)

    # Wait up to 5 seconds for clean exit
    for _ in range(50):
        if not _is_running(pid):
            break
        time.sleep(0.1)
    else:
        # Force kill
        os.kill(pid, signal.SIGKILL)

    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


# ── Entry point for the subprocess (`python -m pycaas.daemon`) ──────────────

def _run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("pycaas daemon starting (PID %d)", os.getpid())
    from pycaas.service import CameraService

    svc = CameraService()
    svc.run()
    # Clean up PID file on graceful exit
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    _run()
