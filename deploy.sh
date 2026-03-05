#!/bin/bash
set -e
# Deploy script for Raspberry Pi OS Lite
# Usage: sudo bash deploy.sh

PROJECT_DIR="/home/pi/PhotoFrame"
VENV_DIR="$PROJECT_DIR/venv"
MEDIA_DIR="/home/pi/cadre"
SERVICE_FILE="$PROJECT_DIR/photoframe.service"
SERVICE_NAME="photoframe.service"
USER_NAME="pi"

if [ "$EUID" -ne 0 ]; then
  echo "This script must be run with sudo: sudo bash deploy.sh"
  exit 1
fi

echo "== Updating system packages and installing runtime deps =="
apt update
apt install -y mpv fbi python3-venv python3-pip || true

echo "== Creating media directory and setting ownership =="
mkdir -p "$MEDIA_DIR"
chown -R "$USER_NAME:$USER_NAME" "$PROJECT_DIR" "$MEDIA_DIR" || true

echo "== Creating/Updating virtualenv and installing Python deps =="
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
  pip install -r "$PROJECT_DIR/requirements.txt"
else
  pip install flask
fi
deactivate

echo "== Deploying systemd service =="
if [ -f "$SERVICE_FILE" ]; then
  cp "$SERVICE_FILE" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME" || true
  echo "Service $SERVICE_NAME deployed"
else
  echo "Warning: $SERVICE_FILE not found — please copy your .service file to /etc/systemd/system/ manually" >&2
fi

echo ""
echo "✅ Deploy complete. Follow runtime logs with:"
echo "  sudo journalctl -u photoframe -f"
echo "Or check the application log (default): /home/pi/photoframe.log"

exit 0
