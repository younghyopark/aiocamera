"""Luxonis OAK (DepthAI) camera driver."""

from __future__ import annotations

import numpy as np

try:
    import depthai as dai

    HAS_DEPTHAI = True
except ImportError:
    HAS_DEPTHAI = False

from pycaas.drivers.base import CameraDriver, CameraInfo

class OakDriver(CameraDriver):
    def __init__(self, camera_info: CameraInfo) -> None:
        if not HAS_DEPTHAI:
            raise ImportError("depthai is required: pip install pycaas[oak]")
        super().__init__(camera_info)
        self._device: dai.Device | None = None
        self._pipeline: dai.Pipeline | None = None
        self._queues: dict[str, dai.DataOutputQueue] = {}

    def open(self) -> None:
        device_info = dai.DeviceInfo(self.camera_info.serial)  # MX ID
        self._device = dai.Device(device_info)

        pipeline = dai.Pipeline(self._device)
        sockets = self._device.getConnectedCameras()

        for socket in sockets:
            cam = pipeline.create(dai.node.Camera).build(socket)
            out = cam.requestOutput((640, 480), type=dai.ImgFrame.Type.BGR888p)
            self._queues[str(socket)] = out.createOutputQueue(maxSize=2, blocking=False)

        self.camera_info.streams = [str(s) for s in sockets]
        self._pipeline = pipeline
        self._pipeline.start()

    def read(self) -> dict[str, np.ndarray]:
        result: dict[str, np.ndarray] = {}
        for name, queue in self._queues.items():
            video_in = queue.tryGet()
            if video_in is None:
                continue
            result[name] = video_in.getCvFrame()
        return result

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        if self._device is not None:
            self._device.close()
            self._device = None
        self._queues.clear()
