#!/usr/bin/env bash
set -e

echo "==> Installing JTS PowerTool systemd services..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. Copy service files to /etc/systemd/system/
echo "==> Copying service unit files..."
sudo cp "$SCRIPT_DIR/jts-powertool.service" /etc/systemd/system/jts-powertool.service
sudo cp "$SCRIPT_DIR/jts-worker.service" /etc/systemd/system/jts-worker.service
sudo cp "$SCRIPT_DIR/jts-frontend.service" /etc/systemd/system/jts-frontend.service

# 2. Reload systemd daemon
echo "==> Reloading systemd daemon..."
sudo systemctl daemon-reload

# 3. Enable services for auto-start on boot
echo "==> Enabling auto-start on boot..."
sudo systemctl enable jts-powertool
sudo systemctl enable jts-worker
sudo systemctl enable jts-frontend

# 4. Restart services
echo "==> Starting services..."
sudo systemctl restart jts-powertool
sudo systemctl restart jts-worker
sudo systemctl restart jts-frontend

echo "==> Checking service status..."
sudo systemctl status jts-powertool --no-pager
sudo systemctl status jts-worker --no-pager
sudo systemctl status jts-frontend --no-pager

echo "==> All services successfully installed and running!"
