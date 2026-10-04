"""Core service: discover cameras, run capture threads, serve frames via ZMQ."""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field

from aiocamera.discovery import discover_cameras
from aiocamera.drivers import create_driver
from aiocamera.drivers.base import CameraDriver
from aiocamera.transport import FrameData, ZmqServer

log = logging.getLogger(__name__)


@dataclass
class _ReconfigState:
    """Per-driver state for coordinating blocking reconfigure between threads."""

    request_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    new_config: dict = field(default_factory=dict)
    error: str | None = None


class CameraService:
    """Discovers cameras, captures frames in threads, handles ZMQ requests."""

    def __init__(self, camera_configs: dict[str, dict] | None = None) -> None:
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        self._zmq = ZmqServer()
        self._drivers: list[CameraDriver] = []
        self._threads: list[threading.Thread] = []
        self._camera_configs = camera_configs or {}
        self._reconfig: dict[str, _ReconfigState] = {}

    # ── capture loop (one per driver) ───────────────────────────────────────

    def _capture_loop(self, driver: CameraDriver, reconfig: _ReconfigState) -> None:
        cam = driver.camera_info
        log.info("Capture started: %s (%s)", cam.id, cam.name)
        try:
            driver.open()
        except Exception:
            log.exception("Failed to open %s", cam.id)
            return

        try:
            while not self._shutdown.is_set():
                # Check for reconfigure request
                if reconfig.request_event.is_set():
                    reconfig.request_event.clear()
                    self._do_reconfigure(driver, reconfig)
                    continue

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

    def _do_reconfigure(self, driver: CameraDriver, reconfig: _ReconfigState) -> None:
        """Close, apply new config, reopen. Rollback on failure."""
        cam = driver.camera_info
        cfg = reconfig.new_config

        # Snapshot old config for rollback
        old_resolution = cam.resolution
        old_fps = cam.fps

        try:
            driver.close()

            # Apply new config (merge: only override what's provided)
            if "resolution" in cfg:
                cam.resolution = tuple(cfg["resolution"])
            if "fps" in cfg:
                cam.fps = cfg["fps"]

            driver.open()
            log.info("Reconfigured %s: resolution=%s fps=%s", cam.id, cam.resolution, cam.fps)
            reconfig.error = None
        except Exception as e:
            log.exception("Reconfigure failed on %s, rolling back", cam.id)
            # Rollback
            cam.resolution = old_resolution
            cam.fps = old_fps
            try:
                driver.open()
                reconfig.error = f"Reconfigure failed: {e}"
            except Exception as e2:
                reconfig.error = f"Reconfigure failed: {e}; rollback also failed: {e2}"
                log.critical("Rollback failed on %s — camera is dead", cam.id)
                reconfig.done_event.set()
                raise  # exit capture loop
        finally:
            reconfig.done_event.set()

    # ── driver helpers ────────────────────────────────────────────────────────

    def _get_driver(self, camera_id: str) -> CameraDriver | None:
        for drv in self._drivers:
            if drv.camera_info.id == camera_id:
                return drv
        return None

    # ── ZMQ command handlers ─────────────────────────────────────────────────

    def _handle_get_exposure(self, request: dict) -> dict:
        cam_id = request.get("camera_id", "")
        drv = self._get_driver(cam_id)
        if drv is None:
            return {"error": f"Unknown camera: {cam_id}"}
        exposure = drv.get_exposure()
        if exposure is None:
            return {"error": f"No exposure info for {cam_id}"}
        return {"exposure": exposure}

    def _handle_set_exposure(self, request: dict) -> dict:
        cam_id = request.get("camera_id", "")
        drv = self._get_driver(cam_id)
        if drv is None:
            return {"error": f"Unknown camera: {cam_id}"}
        value = request.get("value")  # int or None (auto)
        try:
            drv.set_exposure(value)
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def _handle_set_config(self, request: dict) -> dict:
        cam_id = request.get("camera_id", "")
        drv = self._get_driver(cam_id)
        if drv is None:
            return {"error": f"Unknown camera: {cam_id}"}

        reconfig = self._reconfig.get(cam_id)
        if reconfig is None:
            return {"error": f"No reconfigure state for {cam_id}"}

        # Validate against supported modes if available
        modes = drv.get_supported_modes()
        if modes is not None:
            res = request.get("resolution")
            fps = request.get("fps")
            if res is not None and fps is not None:
                target = {"width": res[0], "height": res[1], "fps": fps}
                if target not in modes:
                    return {"error": f"Unsupported mode: {res[0]}x{res[1]}@{fps}fps"}
            elif res is not None:
                if not any(m["width"] == res[0] and m["height"] == res[1] for m in modes):
                    return {"error": f"Unsupported resolution: {res[0]}x{res[1]}"}

        # Signal capture thread and wait
        reconfig.new_config = request
        reconfig.error = None
        reconfig.done_event.clear()
        reconfig.request_event.set()

        if not reconfig.done_event.wait(timeout=10.0):
            return {"error": "Reconfigure timed out"}

        if reconfig.error:
            return {"error": reconfig.error}
        return {"ok": True}

    def _handle_get_supported_modes(self, request: dict) -> dict:
        cam_id = request.get("camera_id", "")
        drv = self._get_driver(cam_id)
        if drv is None:
            return {"error": f"Unknown camera: {cam_id}"}
        modes = drv.get_supported_modes()
        return {"modes": modes}

    def _handle_get_intrinsics(self, request: dict) -> dict:
        cam_id = request.get("camera_id", "")
        drv = self._get_driver(cam_id)
        if drv is None:
            return {"error": f"Unknown camera: {cam_id}"}
        intrinsics = drv.get_intrinsics()
        if intrinsics is None:
            return {"error": f"No intrinsics for {cam_id}"}
        return {"intrinsics": intrinsics.to_dict()}

    # ── public API ──────────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking entry point — discover, capture, serve until stopped."""
        # Handle SIGTERM for clean daemon shutdown
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown.set())

        cameras = discover_cameras()
        if not cameras:
            log.warning("No cameras found — service will idle")

        opened = []
        for cam in cameras:
            # A camera's own config, else its type's (e.g. "webcam" for every webcam).
            cfg = self._camera_configs.get(cam.id) or self._camera_configs.get(cam.type, {})
            if not cfg.get("enabled", True):
                log.info("Not opening %s (%s): disabled by its config", cam.id, cam.name)
                continue
            if "resolution" in cfg:
                cam.resolution = tuple(cfg["resolution"])
            if "fps" in cfg:
                cam.fps = cfg["fps"]
            if "exposure" in cfg:
                cam.exposure = cfg["exposure"]
            if "streams" in cfg:
                cam.streams = list(cfg["streams"])
            opened.append(cam)
        cameras = opened

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
            reconfig = _ReconfigState()
            self._reconfig[cam_info.id] = reconfig
            t = threading.Thread(
                target=self._capture_loop, args=(drv, reconfig), daemon=True, name=cam_info.id
            )
            t.start()
            self._threads.append(t)

        # Register ZMQ command handlers
        self._zmq.register_handler("get_exposure", self._handle_get_exposure)
        self._zmq.register_handler("set_exposure", self._handle_set_exposure)
        self._zmq.register_handler("set_config", self._handle_set_config)
        self._zmq.register_handler("get_supported_modes", self._handle_get_supported_modes)
        self._zmq.register_handler("get_intrinsics", self._handle_get_intrinsics)

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
