#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <speaker-id> <display-name>" >&2
  echo "Example: $0 jesse \"Jesse\"" >&2
  exit 2
fi

SPEAKER_ID="$1"
DISPLAY_NAME="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${TALKING_BOX_VENV:-$HOME/piper-venv}/bin/python"
CAPTURE_DEVICE="${TALKING_BOX_CAPTURE_DEVICE:-plughw:CARD=sndrpigooglevoi}"
SAMPLES="${TALKING_BOX_ENROLL_SAMPLES:-5}"
SAMPLE_SECONDS="${TALKING_BOX_ENROLL_SECONDS:-5}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found: $PYTHON" >&2
  exit 1
fi

TMP="$(mktemp -d)"
WAS_RUNNING=0

cleanup() {
  rm -rf "$TMP"
  if [[ "$WAS_RUNNING" == "1" ]]; then
    echo "Restarting talking-box.service ..."
    sudo systemctl start talking-box.service || true
  fi
}
trap cleanup EXIT

if systemctl is-active --quiet talking-box.service; then
  WAS_RUNNING=1
  echo "Stopping talking-box.service while we enroll the microphone ..."
  sudo systemctl stop talking-box.service
fi

echo
echo "Enrolling voice: $DISPLAY_NAME ($SPEAKER_ID)"
echo "Voice embeddings will be stored locally on this Pi."
echo "Temporary WAV recordings are deleted when this script exits."
echo

files=()
for i in $(seq 1 "$SAMPLES"); do
  file="$TMP/sample-$i.wav"
  files+=("$file")

  while true; do
    echo "Sample $i/$SAMPLES"
    echo "Press Enter, then speak naturally for about $SAMPLE_SECONDS seconds."
    read -r

    arecord \
      -q \
      -D "$CAPTURE_DEVICE" \
      -f S16_LE \
      -r 16000 \
      -c 1 \
      -t wav \
      -d "$SAMPLE_SECONDS" \
      "$file"

    if "$PYTHON" "$ROOT/pi/speaker_identity.py" quality "$file"; then
      echo "Captured."
      echo
      break
    fi

    echo
    echo "That sample was too quiet or too short. Let's redo just this one."
    echo
  done
done

"$PYTHON" "$ROOT/pi/speaker_identity.py" \
  enroll \
  --id "$SPEAKER_ID" \
  --name "$DISPLAY_NAME" \
  --consent \
  "${files[@]}"

echo
echo "Current enrolled speakers:"
"$PYTHON" "$ROOT/pi/speaker_identity.py" list
