"""OpenCV VideoCapture driver for generic USB webcams."""

from __future__ import annotations

import cv2
import numpy as np

from pycaas.drivers.base import CameraDriver, CameraInfo

class WebcamDriver(CameraDriver):
    def __init__(self, camera_info: CameraInfo) -> None:
        super().__init__(camera_info)
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        path = self.camera_info.device_path
        # Accept both "/dev/videoN" strings and bare integers
        index = int(path.replace("/dev/video", "")) if path.startswith("/dev/video") else int(path)
        self._cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open webcam {path}")
        self.camera_info.streams = ["color"]

    def read(self) -> dict[str, np.ndarray]:
        assert self._cap is not None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return {}
        return {"color": frame}

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
