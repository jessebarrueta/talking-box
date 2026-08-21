#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/jesse/talking-box"
SERVICE_NAME="talking-box.service"

cd "$REPO_DIR"

echo "Pulling latest Talking Box code..."
git pull --ff-only

echo "Refreshing systemd service..."
sudo install -m 0644 "$REPO_DIR/systemd/talking-box.service" /etc/systemd/system/talking-box.service

echo "Refreshing shutdown sudoers rule..."
sudo install -m 0440 "$REPO_DIR/sudoers/talking-box" /etc/sudoers.d/talking-box
sudo visudo -cf /etc/sudoers.d/talking-box >/dev/null

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Restarting Talking Box..."
sudo systemctl restart "$SERVICE_NAME"

echo
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "Live logs: journalctl -u $SERVICE_NAME -f"
