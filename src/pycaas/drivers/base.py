"""Abstract base class for camera drivers."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CameraInfo:
    """Metadata about a discovered camera."""

    id: str  # unique identifier used as prefix for stream names
    type: str  # "webcam", "realsense", "oak"
    name: str  # human-readable name
    serial: str = ""
    device_path: str = ""
    streams: list[str] = field(default_factory=list)  # populated after open()


class CameraDriver(abc.ABC):
    """Interface that every camera backend must implement."""

    def __init__(self, camera_info: CameraInfo) -> None:
        self.camera_info = camera_info

    @abc.abstractmethod
    def open(self) -> None:
        """Initialize hardware and start internal capture resources."""

    @abc.abstractmethod
    def read(self) -> dict[str, np.ndarray]:
        """Return the latest frame(s) keyed by stream name (e.g. "color", "depth")."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release all hardware resources."""
