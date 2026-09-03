#!/usr/bin/env python3
"""Small HuskyLens V1 adapter for Jerry's visual perception.

This module intentionally treats HuskyLens detections as sensor observations,
not authenticated identity. A learned HuskyLens ID is only a sensor-local ID
until a separate, explicit identity-fusion layer evaluates it.

The current Talking Box integration assumes the HuskyLens itself is manually
left in Face Recognition mode. That lets us describe returned blocks as face
detections without pretending a learned HuskyLens ID is a verified person.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

try:
    from pyhuskylens import HuskyLens
except Exception as exc:  # pragma: no cover - exercised on Pi hardware
    HuskyLens = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


DEFAULT_I2C_BUS = 1
DEFAULT_I2C_ADDRESS = 0x32
DEFAULT_MODE = "face_recognition"
DEFAULT_DETECTION_KIND = "face"


@dataclass(frozen=True)
class VisualBlock:
    kind: str
    sensor_id: int
    learned: bool
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HuskyLensVision:
    """Read-only HuskyLens perception adapter."""

    def __init__(
        self,
        bus: int = DEFAULT_I2C_BUS,
        debug: bool = False,
        mode: str = DEFAULT_MODE,
        detection_kind: str = DEFAULT_DETECTION_KIND,
    ):
        if HuskyLens is None:
            raise RuntimeError(
                "pyhuskylens is unavailable; install pyhuskylens[i2c]"
            ) from _IMPORT_ERROR

        self.bus = int(bus)
        self.debug = bool(debug)
        self.mode = str(mode)
        self.detection_kind = str(detection_kind)
        self._eye = HuskyLens(self.bus, debug=self.debug)
        self.available = bool(self._eye.knock())
        self.version = self._eye.version if self.available else None

    def _block_to_observation(self, block: Any) -> VisualBlock:
        sensor_id = int(getattr(block, "ID", 0) or 0)
        return VisualBlock(
            kind=self.detection_kind,
            sensor_id=sensor_id,
            learned=sensor_id > 0,
            x=int(getattr(block, "x", 0) or 0),
            y=int(getattr(block, "y", 0) or 0),
            width=int(getattr(block, "width", 0) or 0),
            height=int(getattr(block, "height", 0) or 0),
        )

    def _base_snapshot(self, available: bool) -> dict[str, Any]:
        return {
            "available": available,
            "provider": "huskylens-v1",
            "version": self.version,
            "bus": self.bus,
            "address": hex(DEFAULT_I2C_ADDRESS),
            "mode": self.mode,
            "detections": [],
        }

    def snapshot(self) -> dict[str, Any]:
        """Return one JSON-safe visual snapshot.

        In the current face-recognition configuration, each HuskyLens block is
        semantically a face detection. ID=0 means an unlearned face; positive
        IDs are learned sensor-local IDs only, not authenticated identities.
        """
        if not self.available:
            return self._base_snapshot(False)

        try:
            snapshot = self._base_snapshot(True)
            blocks = self._eye.get_blocks() or []
            snapshot["detections"] = [
                self._block_to_observation(block).to_dict()
                for block in blocks
            ]
            return snapshot
        except Exception as exc:
            snapshot = self._base_snapshot(False)
            snapshot["error"] = f"{type(exc).__name__}: {exc}"
            return snapshot


if __name__ == "__main__":  # pragma: no cover - manual Pi smoke test
    import json

    vision = HuskyLensVision(debug=True)
    print(json.dumps(vision.snapshot(), indent=2, sort_keys=True))
