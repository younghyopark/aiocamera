"""Auto-detect connected cameras (RealSense, OAK, generic webcam)."""

from __future__ import annotations

import glob
import logging
import os

import cv2

from aiocamera.drivers.base import CameraInfo

log = logging.getLogger(__name__)


def _discover_realsense() -> list[CameraInfo]:
    try:
        import pyrealsense2 as rs
    except ImportError:
        return []

    cameras: list[CameraInfo] = []
    ctx = rs.context()
    for i, dev in enumerate(ctx.query_devices()):
        serial = dev.get_info(rs.camera_info.serial_number)
        name = dev.get_info(rs.camera_info.name)
        cameras.append(
            CameraInfo(
                id=f"rs_{serial}",
                type="realsense",
                name=name,
                serial=serial,
            )
        )
        log.info("RealSense: %s (serial %s)", name, serial)
    return cameras


def _discover_oak() -> list[CameraInfo]:
    try:
        import depthai as dai
    except ImportError:
        return []

    cameras: list[CameraInfo] = []
    for i, dev_info in enumerate(dai.Device.getAllAvailableDevices()):
        mx_id = dev_info.getMxId()
        cameras.append(
            CameraInfo(
                id=f"oak_{mx_id}",
                type="oak",
                name=f"OAK ({dev_info.name})" if dev_info.name else f"OAK-{i}",
                serial=mx_id,
            )
        )
        log.info("OAK: %s (MX ID %s)", cameras[-1].name, mx_id)
    return cameras


_SKIP_NAMES = {"realsense", "oak", "myriad", "movidius", "luxonis"}


def _discover_webcams(exclude_names: set[str] | None = None) -> list[CameraInfo]:
    """Enumerate /dev/video* and filter out devices belonging to known SDKs."""
    if exclude_names is None:
        exclude_names = set()

    cameras: list[CameraInfo] = []
    video_devices = sorted(glob.glob("/dev/video*"))

    for dev_path in video_devices:
        index_str = dev_path.replace("/dev/video", "")
        if not index_str.isdigit():
            continue

        # Read V4L2 device name from sysfs to skip known SDK cameras
        sysfs_name_path = f"/sys/class/video4linux/video{index_str}/name"
        dev_name = ""
        if os.path.exists(sysfs_name_path):
            try:
                dev_name = open(sysfs_name_path).read().strip()
            except OSError:
                pass

        if any(kw in dev_name.lower() for kw in _SKIP_NAMES):
            log.debug("Skipping %s (%s) — belongs to a known SDK", dev_path, dev_name)
            continue

        # Quick probe: can OpenCV actually open it?
        cap = cv2.VideoCapture(int(index_str), cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue
        ok, _ = cap.read()
        cap.release()
        if not ok:
            continue

        cam_id = f"webcam{len(cameras)}"
        cameras.append(
            CameraInfo(
                id=cam_id,
                type="webcam",
                name=dev_name or f"Video {index_str}",
                device_path=dev_path,
            )
        )
        log.info("Webcam: %s (%s)", dev_name or dev_path, dev_path)

    return cameras


def query_realsense_exposure_range(serial: str, sensor_name: str = "RGB Camera") -> dict | None:
    """Return {"min", "max", "step", "default"} for exposure on a RealSense sensor."""
    try:
        import pyrealsense2 as rs
    except ImportError:
        return None

    ctx = rs.context()
    for dev in ctx.query_devices():
        if dev.get_info(rs.camera_info.serial_number) != serial:
            continue
        for sensor in dev.query_sensors():
            sname = sensor.get_info(rs.camera_info.name)
            if sensor_name.lower() not in sname.lower():
                continue
            if sensor.supports(rs.option.exposure):
                r = sensor.get_option_range(rs.option.exposure)
                return {"min": int(r.min), "max": int(r.max), "step": int(r.step), "default": int(r.default)}
    return None


def query_realsense_resolutions(serial: str) -> list[tuple[int, int, int]]:
    """Return available (width, height, fps) combos supported by BOTH color and depth.

    Only combos where both streams support the same (w, h, fps) are returned,
    so ``pipeline.start()`` won't fail due to a depth/color mismatch.
    """
    try:
        import pyrealsense2 as rs
    except ImportError:
        return []

    ctx = rs.context()
    for dev in ctx.query_devices():
        if dev.get_info(rs.camera_info.serial_number) != serial:
            continue

        color_modes: set[tuple[int, int, int]] = set()
        depth_modes: set[tuple[int, int, int]] = set()

        for sensor in dev.query_sensors():
            for profile in sensor.get_stream_profiles():
                vp = profile.as_video_stream_profile()
                entry = (vp.width(), vp.height(), vp.fps())
                if vp.stream_type() == rs.stream.color:
                    color_modes.add(entry)
                elif vp.stream_type() == rs.stream.depth:
                    depth_modes.add(entry)

        valid = sorted(color_modes & depth_modes, key=lambda r: (r[2], r[0] * r[1]), reverse=True)
        return valid
    return []


def discover_cameras() -> list[CameraInfo]:
    """Return all detected cameras (RealSense first, then OAK, then webcams)."""
    cameras: list[CameraInfo] = []
    cameras.extend(_discover_realsense())
    cameras.extend(_discover_oak())
    cameras.extend(_discover_webcams())
    return cameras
