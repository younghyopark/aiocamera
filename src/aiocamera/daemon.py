"""Daemon lifecycle: start / stop / status via PID file + subprocess."""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator

log = logging.getLogger(__name__)

PID_FILE = "/tmp/aiocamera.pid"
LOG_FILE = "/tmp/aiocamera.log"

# sudo asks for the password on the terminal, unless it has cached it (for 5 minutes, by default).
SUDO = ["sudo", "-p", "aiocamera needs root for the RealSense on macOS. Password for %u: "]


def needs_root() -> bool:
    """Whether the cameras need root: librealsense on macOS can't open a RealSense without it."""
    if sys.platform != "darwin" or os.geteuid() == 0 or importlib.util.find_spec("pyrealsense2") is None:
        return False
    # The USB registry, as librealsense takes seconds to list devices.
    usb = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True).stdout
    return "RealSense" in usb


def _read_pid() -> int | None:
    try:
        return int(open(PID_FILE).read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0 = existence check
        return True
    except PermissionError:
        return True  # another user's: root's, for a daemon started through sudo
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


def start_daemon(camera_configs: dict[str, dict] | None = None) -> int:
    """Fork a background process running the camera service.  Returns the PID.

    The process runs as root, through sudo, when needs_root().
    """
    if is_service_running():
        pid = _read_pid()
        raise RuntimeError(f"Service already running (PID {pid})")

    cmd = [sys.executable, "-m", "aiocamera.daemon"]
    if camera_configs is not None:
        import json
        # e.g. {"rs0": {"resolution": [1280, 720], "fps": 15}, ...}
        cmd += ["--camera-configs", json.dumps(camera_configs)]

    if needs_root():
        # As root, --spawn spawns the daemon and exits.
        if subprocess.run([*SUDO, *cmd, "--spawn"]).returncode != 0:
            raise RuntimeError("Could not start the service as root through sudo")
        return _read_pid()
    return _spawn(cmd)


def _spawn(cmd: list[str]) -> int:
    with _as_sudo_user(LOG_FILE):
        log_fd = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,  # nothing of the terminal, or of the pty sudo runs --spawn in
        stdout=log_fd,
        stderr=log_fd,
        start_new_session=True,
    )
    # Write PID
    with _as_sudo_user(PID_FILE), open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    return proc.pid


@contextlib.contextmanager
def _as_sudo_user(*paths: str) -> Iterator[None]:
    """Under sudo, make *paths* in /tmp as the user who ran it, so they are theirs, as without sudo.

    Their clients reach the socket, and root follows no other user's links there: whatever else is
    at *paths* (another user's symlink, or a file left by a root daemon) is removed first.
    """
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or uid is None or gid is None:
        yield
        return
    for path in paths:
        if os.path.lexists(path) and os.lstat(path).st_uid != int(uid):
            os.unlink(path)
    os.setegid(int(gid))
    os.seteuid(int(uid))
    try:
        yield
    finally:
        os.seteuid(0)
        os.setegid(0)


def stop_daemon() -> None:
    """Send SIGTERM to the running daemon."""
    pid = _read_pid()
    if pid is None or not _is_running(pid):
        raise RuntimeError("Service is not running")

    _kill(pid, signal.SIGTERM)

    # Wait up to 5 seconds for clean exit
    for _ in range(50):
        if not _is_running(pid):
            break
        time.sleep(0.1)
    else:
        # Force kill
        _kill(pid, signal.SIGKILL)

    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def _kill(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except PermissionError:
        # Root's daemon, started through sudo.
        if subprocess.run([*SUDO, "kill", f"-{int(sig)}", str(pid)]).returncode != 0:
            raise RuntimeError(f"Could not signal the service (PID {pid}) as root through sudo")


# ── Entry point for the subprocess (`python -m aiocamera.daemon`) ──────────────

def _run() -> None:
    import argparse
    import json as json_mod

    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-configs", default=None,
                        help='JSON dict e.g. \'{"rs0": {"resolution": [1280, 720], "fps": 15}}\'')
    parser.add_argument("--spawn", action="store_true",
                        help="spawn the daemon in the background and exit (start_daemon, through sudo)")
    args = parser.parse_args()

    if args.spawn:
        _spawn([sys.executable, "-m", "aiocamera.daemon", *(a for a in sys.argv[1:] if a != "--spawn")])
        return

    camera_configs = None
    if args.camera_configs:
        camera_configs = json_mod.loads(args.camera_configs)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("aiocamera daemon starting (PID %d)", os.getpid())
    from aiocamera.service import CameraService
    from aiocamera.transport import IPC_PATH

    with _as_sudo_user(IPC_PATH):
        svc = CameraService(camera_configs=camera_configs)  # binds the socket
    svc.run()
    # Clean up PID file on graceful exit
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    _run()
