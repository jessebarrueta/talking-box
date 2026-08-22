#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${TALKING_BOX_VENV:-$HOME/piper-venv}"
MODEL_DIR="$ROOT/models/speaker"
MODEL="$MODEL_DIR/wespeaker_en_voxceleb_resnet34.onnx"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Python venv not found at: $VENV" >&2
  exit 1
fi

echo "Installing speaker-recognition runtime into $VENV ..."
"$VENV/bin/python" -m pip install sherpa-onnx sherpa-onnx-bin numpy

mkdir -p "$MODEL_DIR"

if [[ ! -s "$MODEL" ]]; then
  echo "Downloading WeSpeaker English VoxCeleb ResNet34 model ..."
  curl -fL --retry 3 "$MODEL_URL" -o "$MODEL"
else
  echo "Speaker model already present: $MODEL"
fi

echo
echo "Checking runtime ..."
"$VENV/bin/python" - <<'PY'
import sherpa_onnx
import numpy
print("sherpa_onnx:", getattr(sherpa_onnx, "__version__", "installed"))
print("numpy:", numpy.__version__)
PY

echo
echo "Speaker recognition runtime is ready."
echo "Next: ./pi/enroll-speaker.sh jesse \"Jesse\""
