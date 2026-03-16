"""ZMQ transport — server (inside daemon) and client (for CLI / user code)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
import zmq

log = logging.getLogger(__name__)

IPC_ENDPOINT = "ipc:///tmp/pycaas.sock"


# ── Frame data container ────────────────────────────────────────────────────


@dataclass
class FrameData:
    """Stored per stream in the frame store."""

    frame: np.ndarray
    shape: tuple[int, ...]
    dtype: str
    timestamp: float = 0.0


# ── Server ───────────────────────────────────────────────────────────────────


class ZmqServer:
    """REP socket that answers frame / status / control requests."""

    def __init__(self) -> None:
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.REP)
        self._socket.bind(IPC_ENDPOINT)
        # Poller for non-blocking receive with timeout
        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

        self.frame_store: dict[str, FrameData] = {}
        self.camera_meta: list[dict] = []  # set by service after discovery
        self._start_time = time.time()
        self._handlers: dict[str, object] = {}  # cmd -> callable(request) -> dict

    def register_handler(self, cmd: str, handler: object) -> None:
        """Register a handler function for a custom command."""
        self._handlers[cmd] = handler

    def poll_once(self, timeout_ms: int = 50) -> str | None:
        """Process one pending request (if any).  Returns the command name, or None."""
        socks = dict(self._poller.poll(timeout_ms))
        if self._socket not in socks:
            return None

        parts = self._socket.recv_multipart()
        request = json.loads(parts[0])
        cmd = request.get("cmd", "")

        if cmd == "list_cameras":
            self._socket.send_json({"cameras": self.camera_meta})

        elif cmd == "get_frame":
            cam_id = request.get("camera_id", "")
            fd = self.frame_store.get(cam_id)
            if fd is None:
                self._socket.send_json({"error": f"Unknown stream: {cam_id}"})
            else:
                frame = fd.frame
                resolution = request.get("resolution")  # [w, h] or None
                if resolution is not None:
                    w, h = int(resolution[0]), int(resolution[1])
                    if (w, h) != (frame.shape[1], frame.shape[0]):
                        frame = cv2.resize(frame, (w, h))
                meta = {
                    "camera_id": cam_id,
                    "shape": list(frame.shape),
                    "dtype": fd.dtype,
                    "timestamp": fd.timestamp,
                }
                self._socket.send_multipart(
                    [json.dumps(meta).encode(), frame.tobytes()]
                )

        elif cmd == "status":
            stream_info = {
                k: {"resolution": [fd.shape[1], fd.shape[0]]}
                for k, fd in self.frame_store.items()
            }
            self._socket.send_json(
                {
                    "running": True,
                    "uptime": round(time.time() - self._start_time, 1),
                    "cameras": self.camera_meta,
                    "active_streams": list(stream_info.keys()),
                    "streams": stream_info,
                }
            )

        elif cmd == "stop":
            self._socket.send_json({"ok": True})
            return "stop"

        elif cmd in self._handlers:
            try:
                result = self._handlers[cmd](request)
                self._socket.send_json(result)
            except Exception as e:
                log.exception("Handler error for %s", cmd)
                self._socket.send_json({"error": str(e)})

        else:
            self._socket.send_json({"error": f"Unknown command: {cmd}"})

        return cmd

    def close(self) -> None:
        self._socket.close(linger=0)


# ── Client ───────────────────────────────────────────────────────────────────


class PycaasClient:
    """Lightweight ZMQ REQ client for talking to the pycaas service."""

    def __init__(self, endpoint: str = IPC_ENDPOINT, timeout_ms: int = 3000) -> None:
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.REQ)
        self._socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self._socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
        self._socket.connect(endpoint)
        self._resolution: tuple[int, int] | None = None  # default: full res

    # -- helpers --

    def _send_cmd(self, cmd: str, **kwargs: object) -> dict:
        payload = {"cmd": cmd, **kwargs}
        self._socket.send_json(payload)
        parts = self._socket.recv_multipart()
        return json.loads(parts[0])  # first part is always JSON

    # -- public API --

    def list_cameras(self) -> list[dict]:
        resp = self._send_cmd("list_cameras")
        return resp.get("cameras", [])

    def set_resolution(self, width: int, height: int) -> None:
        """Set a default resolution for all subsequent get_frame() calls.

        Pass (0, 0) or call with no args to clear and return to full resolution.
        """
        if width <= 0 or height <= 0:
            self._resolution = None
        else:
            self._resolution = (width, height)

    def get_frame(
        self,
        camera_id: str,
        resolution: tuple[int, int] | None = None,
    ) -> np.ndarray:
        """Fetch the latest frame as a numpy array.

        Args:
            camera_id: Stream identifier (e.g. "rs0_color").
            resolution: Optional (width, height) override. If None, uses the
                        default set via set_resolution(). If that is also None,
                        returns the full native resolution.
        """
        res = resolution or self._resolution
        payload: dict = {"cmd": "get_frame", "camera_id": camera_id}
        if res is not None:
            payload["resolution"] = [res[0], res[1]]
        self._socket.send_json(payload)
        parts = self._socket.recv_multipart()
        meta = json.loads(parts[0])
        if "error" in meta:
            raise KeyError(meta["error"])
        shape = tuple(meta["shape"])
        dtype = np.dtype(meta["dtype"])
        return np.frombuffer(parts[1], dtype=dtype).reshape(shape)

    def get_exposure(self, camera_id: str) -> dict | None:
        """Fetch current exposure setting: {"auto": bool, "value": int}."""
        resp = self._send_cmd("get_exposure", camera_id=camera_id)
        if "error" in resp:
            return None
        return resp.get("exposure")

    def get_intrinsics(self, camera_id: str) -> dict:
        """Fetch camera intrinsics as a dict."""
        resp = self._send_cmd("get_intrinsics", camera_id=camera_id)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp.get("intrinsics", {})

    def get_intrinsics_matrix(self, camera_id: str) -> np.ndarray:
        """Fetch camera intrinsics as a 3x3 numpy camera matrix."""
        intr = self.get_intrinsics(camera_id)
        return np.array(
            [[intr["fx"], 0.0, intr["cx"]],
             [0.0, intr["fy"], intr["cy"]],
             [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )

    def set_exposure(self, camera_id: str, value: int | None = None) -> dict:
        """Set exposure on a camera. None = auto exposure."""
        return self._send_cmd("set_exposure", camera_id=camera_id, value=value)

    def set_config(
        self,
        camera_id: str,
        resolution: tuple[int, int] | None = None,
        fps: int | None = None,
        timeout_ms: int = 15000,
    ) -> dict:
        """Change resolution/fps at runtime (blocking — waits for pipeline restart).

        Args:
            camera_id: Camera identifier (e.g. "rs0").
            resolution: Optional (width, height) to change to.
            fps: Optional fps to change to.
            timeout_ms: How long to wait for the reconfigure to complete.
        """
        kwargs: dict = {"camera_id": camera_id}
        if resolution is not None:
            kwargs["resolution"] = list(resolution)
        if fps is not None:
            kwargs["fps"] = fps

        # Temporarily increase socket timeout for blocking reconfigure
        old_timeout = self._socket.getsockopt(zmq.RCVTIMEO)
        self._socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        try:
            return self._send_cmd("set_config", **kwargs)
        finally:
            self._socket.setsockopt(zmq.RCVTIMEO, old_timeout)

    def get_supported_modes(self, camera_id: str) -> list[dict] | None:
        """Get supported (width, height, fps) combos for a camera.

        Returns None if the camera doesn't report supported modes.
        """
        resp = self._send_cmd("get_supported_modes", camera_id=camera_id)
        return resp.get("modes")

    def status(self) -> dict:
        return self._send_cmd("status")

    def stop(self) -> dict:
        return self._send_cmd("stop")

    def close(self) -> None:
        self._socket.close(linger=0)

    def __enter__(self) -> PycaasClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
