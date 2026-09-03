#!/usr/bin/env python3
"""Run Jerry's existing Talking Box runtime with HuskyLens perception enabled.

This wrapper keeps the proven V7.3 interaction runtime unchanged while adding a
read-only visual snapshot to device context. HuskyLens learned IDs remain
sensor-local evidence only and are not treated as authenticated identity.
"""

from __future__ import annotations

import json

import talking_box as core
from huskylens_vision import HuskyLensVision


_original_device_context = core.device_context
_vision = None


def initialize_vision():
    global _vision

    try:
        _vision = HuskyLensVision()
        snapshot = _vision.snapshot()
        print("Vision sensor: " + json.dumps(snapshot, sort_keys=True))
    except Exception as exc:
        _vision = None
        print(
            "Vision sensor unavailable: "
            f"{type(exc).__name__}: {exc}"
        )


def device_context_with_vision():
    context = _original_device_context()

    if _vision is None:
        context["vision"] = {
            "available": False,
            "provider": "huskylens-v1",
            "detections": [],
        }
        return context

    snapshot = _vision.snapshot()
    context["vision"] = snapshot

    if snapshot.get("available"):
        capabilities = list(context.get("body_capabilities") or [])
        if "camera" not in capabilities:
            capabilities.append("camera")
        context["body_capabilities"] = capabilities

    return context


def main():
    initialize_vision()
    core.device_context = device_context_with_vision
    core.main()


if __name__ == "__main__":
    main()
