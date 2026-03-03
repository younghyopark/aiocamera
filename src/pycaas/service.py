"""Core service: discover cameras, run capture threads, serve frames via ZMQ."""

from __future__ import annotations

import logging
import signal
import threading
import time

from pycaas.discovery import discover_cameras
from pycaas.drivers import create_driver
from pycaas.drivers.base import CameraDriver
from pycaas.transport import FrameData, ZmqServer

log = logging.getLogger(__name__)


class CameraService:
    """Discovers cameras, captures frames in threads, handles ZMQ requests."""

    def __init__(self) -> None:
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        self._zmq = ZmqServer()
        self._drivers: list[CameraDriver] = []
        self._threads: list[threading.Thread] = []

    # ── capture loop (one per driver) ───────────────────────────────────────

    def _capture_loop(self, driver: CameraDriver) -> None:
        cam = driver.camera_info
        log.info("Capture started: %s (%s)", cam.id, cam.name)
        try:
            driver.open()
        except Exception:
            log.exception("Failed to open %s", cam.id)
            return

        try:
            while not self._shutdown.is_set():
                frames = driver.read()
                ts = time.time()
                with self._lock:
                    for stream_name, frame in frames.items():
                        key = f"{cam.id}_{stream_name}"
                        self._zmq.frame_store[key] = FrameData(
                            frame=frame,
                            shape=frame.shape,
                            dtype=str(frame.dtype),
                            timestamp=ts,
                        )
        except Exception:
            log.exception("Capture error on %s", cam.id)
        finally:
            driver.close()
            log.info("Capture stopped: %s", cam.id)

    # ── public API ──────────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking entry point — discover, capture, serve until stopped."""
        # Handle SIGTERM for clean daemon shutdown
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown.set())

        cameras = discover_cameras()
        if not cameras:
            log.warning("No cameras found — service will idle")

        # Populate metadata for ZMQ status responses
        self._zmq.camera_meta = [
            {"id": c.id, "type": c.type, "name": c.name, "serial": c.serial}
            for c in cameras
        ]
        log.info("Discovered %d camera(s)", len(cameras))

        # Create & start one thread per camera
        for cam_info in cameras:
            try:
                drv = create_driver(cam_info)
            except (ImportError, ValueError):
                log.exception("Skipping %s — driver unavailable", cam_info.id)
                continue
            self._drivers.append(drv)
            t = threading.Thread(
                target=self._capture_loop, args=(drv,), daemon=True, name=cam_info.id
            )
            t.start()
            self._threads.append(t)

        # Main loop: service ZMQ requests
        log.info("Service ready — waiting for requests on ZMQ")
        while not self._shutdown.is_set():
            cmd = self._zmq.poll_once(timeout_ms=100)
            if cmd == "stop":
                log.info("Stop command received")
                self._shutdown.set()

        self._zmq.close()
        # Daemon threads will be reaped when the process exits
        log.info("Service shut down")
