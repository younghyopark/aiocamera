"""Intel RealSense driver (color + depth)."""

from __future__ import annotations

import cv2
import numpy as np

try:
    import pyrealsense2 as rs

    HAS_REALSENSE = True
except ImportError:
    HAS_REALSENSE = False

from pycaas.drivers.base import CameraDriver, CameraInfo

STREAM_W = 640
STREAM_H = 480
FPS = 30


def colorize_depth(depth_frame: rs.depth_frame) -> np.ndarray:
    depth_image = np.asanyarray(depth_frame.get_data())
    return cv2.applyColorMap(
        cv2.convertScaleAbs(depth_image, alpha=0.03),
        cv2.COLORMAP_JET,
    )


class RealSenseDriver(CameraDriver):
    def __init__(self, camera_info: CameraInfo) -> None:
        if not HAS_REALSENSE:
            raise ImportError("pyrealsense2 is required: pip install pycaas[realsense]")
        super().__init__(camera_info)
        self._pipeline: rs.pipeline | None = None
        self._align: rs.align | None = None

    def open(self) -> None:
        config = rs.config()
        config.enable_device(self.camera_info.serial)
        config.enable_stream(rs.stream.color, STREAM_W, STREAM_H, rs.format.bgr8, FPS)
        config.enable_stream(rs.stream.depth, STREAM_W, STREAM_H, rs.format.z16, FPS)

        self._pipeline = rs.pipeline()
        self._pipeline.start(config)

        # Let auto-exposure settle
        for _ in range(30):
            self._pipeline.wait_for_frames()

        self._align = rs.align(rs.stream.color)
        self.camera_info.streams = ["color", "depth"]

    def read(self) -> dict[str, np.ndarray]:
        assert self._pipeline is not None and self._align is not None
        frames = self._pipeline.wait_for_frames()
        aligned = self._align.process(frames)

        result: dict[str, np.ndarray] = {}
        color_frame = aligned.get_color_frame()
        if color_frame:
            result["color"] = np.asanyarray(color_frame.get_data())
        depth_frame = aligned.get_depth_frame()
        if depth_frame:
            result["depth"] = colorize_depth(depth_frame)
        return result

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
            self._align = None
