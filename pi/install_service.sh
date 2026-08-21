#!/usr/bin/env bash
set -euo pipefail
sudo apt install -y mpg123
sudo install -m 0644 "$(dirname "$0")/../systemd/talking-box.service" /etc/systemd/system/talking-box.service
sudo install -m 0440 "$(dirname "$0")/../sudoers/talking-box" /etc/sudoers.d/talking-box
sudo visudo -cf /etc/sudoers.d/talking-box
sudo systemctl daemon-reload
sudo systemctl enable talking-box.service
sudo systemctl restart talking-box.service
sudo systemctl --no-pager status talking-box.service || true
echo "Logs: journalctl -u talking-box.service -f"
