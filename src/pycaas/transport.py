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
            stream_ids = list(self.frame_store.keys())
            self._socket.send_json(
                {
                    "running": True,
                    "uptime": round(time.time() - self._start_time, 1),
                    "cameras": self.camera_meta,
                    "active_streams": stream_ids,
                }
            )

        elif cmd == "stop":
            self._socket.send_json({"ok": True})
            return "stop"

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
