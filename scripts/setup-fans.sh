#!/bin/bash
# Install the root fan reader. Safe to re-run.   sudo ./scripts/setup-fans.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")/.."
# Root-owned copy: a root service must never run code from a user-writable path.
install -Dm755 -o root -g root system/clef-fans.py /usr/local/lib/clef/clef-fans.py
install -Dm644 -o root -g root system/clef-fans.service /etc/systemd/system/clef-fans.service
systemctl daemon-reload
systemctl enable --now clef-fans.service
systemctl restart clef-fans.service
sleep 3
cat /run/clef/fans.json; echo
