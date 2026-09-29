#!/usr/bin/env bash
set -e

echo "==> Setting up Nginx for journeys.pe..."

# 1. Install Nginx and Certbot if not installed
echo "==> Installing Nginx and Certbot..."
sudo apt-get update -y
sudo apt-get install -y nginx certbot python3-certbot-nginx

# 2. Copy Nginx configuration
echo "==> Copying Nginx site configuration..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
sudo cp "$SCRIPT_DIR/journeys.pe.conf" /etc/nginx/sites-available/journeys.pe

# 3. Enable site and disable default / old sites
echo "==> Enabling site in Nginx..."
sudo ln -sf /etc/nginx/sites-available/journeys.pe /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo rm -f /etc/nginx/sites-enabled/jpt.dtmetrix.com

# 4. Test Nginx syntax and reload
echo "==> Testing Nginx configuration..."
sudo nginx -t

echo "==> Reloading Nginx..."
sudo systemctl reload nginx

echo "==> Nginx setup complete for journeys.pe!"
echo "==> To issue free SSL certificate (HTTPS), run:"
echo "    sudo certbot --nginx -d journeys.pe -d www.journeys.pe"
