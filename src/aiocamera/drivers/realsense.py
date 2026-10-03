"""Intel RealSense driver (color + depth)."""

from __future__ import annotations

import cv2
import numpy as np

try:
    import pyrealsense2 as rs

    HAS_REALSENSE = True
except ImportError:
    HAS_REALSENSE = False

from aiocamera.drivers.base import CameraDriver, CameraInfo, CameraIntrinsics

DEFAULT_W = 640
DEFAULT_H = 480
DEFAULT_FPS = 30


def colorize_depth(depth_frame: rs.depth_frame) -> np.ndarray:
    depth_image = np.asanyarray(depth_frame.get_data())
    return cv2.applyColorMap(
        cv2.convertScaleAbs(depth_image, alpha=0.03),
        cv2.COLORMAP_JET,
    )


class RealSenseDriver(CameraDriver):
    def __init__(self, camera_info: CameraInfo) -> None:
        if not HAS_REALSENSE:
            raise ImportError("pyrealsense2 is required: pip install aiocamera[realsense]")
        super().__init__(camera_info)
        self._pipeline: rs.pipeline | None = None
        self._align: rs.align | None = None
        self._color_sensor: rs.sensor | None = None

    def open(self) -> None:
        w, h = self.camera_info.resolution or (DEFAULT_W, DEFAULT_H)
        fps = self.camera_info.fps or DEFAULT_FPS
        config = rs.config()
        config.enable_device(self.camera_info.serial)
        config.enable_stream(rs.stream.color, w, h, rs.format.rgb8, fps)
        config.enable_stream(rs.stream.depth, w, h, rs.format.z16, fps)

        self._pipeline = rs.pipeline()
        self._pipeline.start(config)

        # Set exposure
        profile = self._pipeline.get_active_profile()
        color_sensor = None
        for sensor in profile.get_device().query_sensors():
            if sensor.get_info(rs.camera_info.name) == "RGB Camera":
                color_sensor = sensor
                break
            # D405 has color on stereo module
            if sensor.supports(rs.option.exposure):
                color_sensor = sensor

        self._color_sensor = color_sensor
        if self.camera_info.exposure is not None and color_sensor is not None:
            color_sensor.set_option(rs.option.enable_auto_exposure, 0)
            color_sensor.set_option(rs.option.exposure, self.camera_info.exposure)
        else:
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
            result["depth"] = np.asanyarray(depth_frame.get_data())  # uint16, millimeters
        return result

    def get_intrinsics(self) -> CameraIntrinsics:
        assert self._pipeline is not None
        profile = self._pipeline.get_active_profile()
        color_profile = profile.get_stream(rs.stream.color).as_video_stream_profile()
        intr = color_profile.get_intrinsics()
        return CameraIntrinsics(
            fx=intr.fx,
            fy=intr.fy,
            cx=intr.ppx,
            cy=intr.ppy,
            width=intr.width,
            height=intr.height,
            dist_coeffs=list(intr.coeffs),
        )

    def get_exposure(self) -> dict | None:
        if self._color_sensor is None:
            return None
        auto = bool(self._color_sensor.get_option(rs.option.enable_auto_exposure))
        value = int(self._color_sensor.get_option(rs.option.exposure))
        return {"auto": auto, "value": value}

    def set_exposure(self, value: int | None) -> None:
        if self._color_sensor is None:
            raise RuntimeError("Camera not opened or no color sensor found")
        if value is None:
            self._color_sensor.set_option(rs.option.enable_auto_exposure, 1)
        else:
            self._color_sensor.set_option(rs.option.enable_auto_exposure, 0)
            self._color_sensor.set_option(rs.option.exposure, value)

    def get_supported_modes(self) -> list[dict]:
        from aiocamera.discovery import query_realsense_resolutions

        modes = query_realsense_resolutions(self.camera_info.serial)
        return [{"width": w, "height": h, "fps": fps} for w, h, fps in modes]

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
            self._align = None
