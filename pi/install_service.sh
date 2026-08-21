#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

sudo apt install -y mpg123
sudo install -m 0644 "$REPO_DIR/systemd/talking-box.service" /etc/systemd/system/talking-box.service
sudo install -m 0440 "$REPO_DIR/sudoers/talking-box" /etc/sudoers.d/talking-box
sudo visudo -cf /etc/sudoers.d/talking-box
sudo systemctl daemon-reload
sudo systemctl enable talking-box.service
sudo systemctl restart talking-box.service
sudo systemctl --no-pager --full status talking-box.service || true

echo
echo "Logs: journalctl -u talking-box.service -f"
