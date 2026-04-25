#!/usr/bin/env bash
# Install MEXC DCA Bot as a systemd service.
# Run from the project root: bash install-systemd.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="mexc-dca"
POETRY_BIN="$(command -v poetry || true)"

if [[ -z "$POETRY_BIN" ]]; then
    echo "Error: poetry not found in PATH. Install it first: pip install poetry"
    exit 1
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "Error: $PROJECT_DIR/.env not found. Copy .env.example to .env and fill in your keys first."
    exit 1
fi

echo "Project dir: $PROJECT_DIR"
echo "Poetry:      $POETRY_BIN"
echo "User:        $USER"
echo

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=MEXC Spot DCA Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${POETRY_BIN} run mexc-dca
Restart=always
RestartSec=30
StandardOutput=append:${PROJECT_DIR}/dca.log
StandardError=append:${PROJECT_DIR}/dca.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo
echo "Service installed and started."
echo
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo systemctl stop ${SERVICE_NAME}"
echo "  tail -f ${PROJECT_DIR}/dca.log"
