"""Abstract base class for camera drivers."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CameraIntrinsics:
    """Pinhole camera intrinsics."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    dist_coeffs: list[float] = field(default_factory=list)

    def camera_matrix(self) -> np.ndarray:
        return np.array(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )

    def dist_array(self) -> np.ndarray:
        return np.array(self.dist_coeffs, dtype=np.float64)

    def to_dict(self) -> dict:
        return {
            "fx": self.fx,
            "fy": self.fy,
            "cx": self.cx,
            "cy": self.cy,
            "width": self.width,
            "height": self.height,
            "dist_coeffs": self.dist_coeffs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CameraIntrinsics:
        return cls(**d)


@dataclass
class CameraInfo:
    """Metadata about a discovered camera."""

    id: str  # unique identifier used as prefix for stream names
    type: str  # "webcam", "realsense", "oak"
    name: str  # human-readable name
    serial: str = ""
    device_path: str = ""
    streams: list[str] = field(default_factory=list)  # populated after open()
    resolution: tuple[int, int] | None = None  # (width, height) override
    fps: int | None = None
    exposure: int | None = None  # None = auto exposure


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

    def get_intrinsics(self) -> CameraIntrinsics | None:
        """Return camera intrinsics if available. Default: None."""
        return None

    def get_exposure(self) -> dict | None:
        """Return {"auto": bool, "value": int} if available. Default: None."""
        return None

    def set_exposure(self, value: int | None) -> None:
        """Set exposure at runtime. None = auto. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{type(self).__name__} does not support set_exposure")

    def get_supported_modes(self) -> list[dict] | None:
        """Return supported (width, height, fps) combos, or None if unknown."""
        return None
