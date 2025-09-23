#!/usr/bin/env bash
set -euo pipefail

cd /var/www/medbot
python3 -m venv .venv || true
. .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart medbot.service
