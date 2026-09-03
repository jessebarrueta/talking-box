#!/usr/bin/env python3
"""Small HuskyLens V1 adapter for Jerry's visual perception.

This module intentionally treats HuskyLens detections as sensor observations,
not authenticated identity. A learned HuskyLens ID is only a sensor-local ID
until a separate, explicit identity-fusion layer evaluates it.
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

    def __init__(self, bus: int = DEFAULT_I2C_BUS, debug: bool = False):
        if HuskyLens is None:
            raise RuntimeError(
                "pyhuskylens is unavailable; install pyhuskylens[i2c]"
            ) from _IMPORT_ERROR

        self.bus = int(bus)
        self.debug = bool(debug)
        self._eye = HuskyLens(self.bus, debug=self.debug)
        self.available = bool(self._eye.knock())
        self.version = self._eye.version if self.available else None

    @staticmethod
    def _block_to_observation(block: Any) -> VisualBlock:
        sensor_id = int(getattr(block, "ID", 0) or 0)
        return VisualBlock(
            kind="block",
            sensor_id=sensor_id,
            learned=sensor_id > 0,
            x=int(getattr(block, "x", 0) or 0),
            y=int(getattr(block, "y", 0) or 0),
            width=int(getattr(block, "width", 0) or 0),
            height=int(getattr(block, "height", 0) or 0),
        )

    def snapshot(self) -> dict[str, Any]:
        """Return one JSON-safe visual snapshot.

        HuskyLens algorithm semantics are deliberately not promoted to person
        identity here. For example, ID=0 means an unlearned detection, while
        positive IDs are only learned sensor-local IDs.
        """
        if not self.available:
            return {
                "available": False,
                "provider": "huskylens-v1",
                "bus": self.bus,
                "address": hex(DEFAULT_I2C_ADDRESS),
                "detections": [],
            }

        try:
            blocks = self._eye.get_blocks() or []
            return {
                "available": True,
                "provider": "huskylens-v1",
                "version": self.version,
                "bus": self.bus,
                "address": hex(DEFAULT_I2C_ADDRESS),
                "detections": [
                    self._block_to_observation(block).to_dict()
                    for block in blocks
                ],
            }
        except Exception as exc:
            return {
                "available": False,
                "provider": "huskylens-v1",
                "version": self.version,
                "bus": self.bus,
                "address": hex(DEFAULT_I2C_ADDRESS),
                "detections": [],
                "error": f"{type(exc).__name__}: {exc}",
            }


if __name__ == "__main__":  # pragma: no cover - manual Pi smoke test
    import json

    vision = HuskyLensVision(debug=True)
    print(json.dumps(vision.snapshot(), indent=2, sort_keys=True))
