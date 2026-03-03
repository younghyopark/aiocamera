"""Camera driver registry."""

from __future__ import annotations

from pycaas.drivers.base import CameraDriver, CameraInfo


def create_driver(camera_info: CameraInfo) -> CameraDriver:
    """Instantiate the right driver for *camera_info.type*."""
    if camera_info.type == "realsense":
        from pycaas.drivers.realsense import RealSenseDriver

        return RealSenseDriver(camera_info)
    if camera_info.type == "oak":
        from pycaas.drivers.oak import OakDriver

        return OakDriver(camera_info)
    if camera_info.type == "webcam":
        from pycaas.drivers.webcam import WebcamDriver

        return WebcamDriver(camera_info)
    raise ValueError(f"Unknown camera type: {camera_info.type!r}")
