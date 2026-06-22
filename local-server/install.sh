#!/bin/bash
# Fraimic Local Controller — Raspberry Pi Zero setup
# Run once: bash install.sh

set -e

echo "==> Installing system dependencies"
sudo apt-get update -q
sudo apt-get install -y python3-pip python3-venv libjpeg-dev zlib1g-dev libwebp-dev

echo "==> Creating virtual environment"
python3 -m venv venv
source venv/bin/activate

echo "==> Installing Python packages"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Creating library directory"
mkdir -p library

echo "==> Writing systemd service"
SERVICE_DIR="$(pwd)"
cat > /tmp/fraimic.service <<EOF
[Unit]
Description=Fraimic Local Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SERVICE_DIR}
ExecStart=${SERVICE_DIR}/venv/bin/python3 app.py
Restart=always
RestartSec=10
User=$(whoami)

[Install]
WantedBy=multi-user.target
EOF

sudo mv /tmp/fraimic.service /etc/systemd/system/fraimic.service
sudo systemctl daemon-reload
sudo systemctl enable fraimic
sudo systemctl start fraimic

echo ""
echo "==> Done! Fraimic controller is running."
echo "    Open http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "    Other useful commands:"
echo "    sudo systemctl status fraimic    — check status"
echo "    sudo systemctl restart fraimic   — restart"
echo "    sudo journalctl -fu fraimic      — live logs"
