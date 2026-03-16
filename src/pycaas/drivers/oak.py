"""Luxonis OAK (DepthAI) camera driver."""

from __future__ import annotations

import cv2
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
        self._control_queue: dai.DataInputQueue | None = None

    def open(self) -> None:
        device_info = dai.DeviceInfo(self.camera_info.serial)  # MX ID
        self._device = dai.Device(device_info)

        pipeline = dai.Pipeline(self._device)
        sockets = self._device.getConnectedCameras()
        resolution = self.camera_info.resolution or (640, 480)

        first_cam = None
        for socket in sockets:
            cam = pipeline.create(dai.node.Camera).build(socket)
            if first_cam is None:
                first_cam = cam
            out = cam.requestOutput(resolution, type=dai.ImgFrame.Type.BGR888p)
            self._queues[str(socket)] = out.createOutputQueue(maxSize=2, blocking=False)

        # Set up control queue for runtime exposure changes
        if first_cam is not None:
            ctrl_in = pipeline.create(dai.node.XLinkIn)
            ctrl_in.setStreamName("cam_control")
            ctrl_in.out.link(first_cam.inputControl)
            self._control_queue = ctrl_in.getInputQueue() if hasattr(ctrl_in, 'getInputQueue') else None

        self.camera_info.streams = [str(s) for s in sockets]
        self._pipeline = pipeline
        self._pipeline.start()

        # Create control input queue after pipeline starts
        if self._control_queue is None and self._device is not None:
            try:
                self._control_queue = self._device.getInputQueue("cam_control")
            except Exception:
                self._control_queue = None

        # Apply initial exposure if configured
        if self.camera_info.exposure is not None:
            try:
                self.set_exposure(self.camera_info.exposure)
            except Exception:
                pass

    def read(self) -> dict[str, np.ndarray]:
        result: dict[str, np.ndarray] = {}
        for name, queue in self._queues.items():
            video_in = queue.tryGet()
            if video_in is None:
                continue
            result[name] = cv2.cvtColor(video_in.getCvFrame(), cv2.COLOR_BGR2RGB)
        return result

    def set_exposure(self, value: int | None) -> None:
        if self._control_queue is None:
            raise RuntimeError("No control queue available for exposure changes")
        ctrl = dai.CameraControl()
        if value is None:
            ctrl.setAutoExposureEnable()
        else:
            ctrl.setManualExposure(value, 100)  # exposure_us, iso
        self._control_queue.send(ctrl)

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        if self._device is not None:
            self._device.close()
            self._device = None
        self._queues.clear()
        self._control_queue = None
