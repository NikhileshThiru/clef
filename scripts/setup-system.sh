#!/bin/bash
# One-time root setup for the Clef box. Safe to re-run.
#   sudo ./scripts/setup-system.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
user=${SUDO_USER:?run via sudo, not as root directly}

echo "==> build deps: CUDA toolkit, cmake, ninja, uv"
pacman -S --needed --noconfirm cuda cmake ninja uv

echo "==> lid close does nothing, never suspend"
install -Dm644 /dev/stdin /etc/systemd/logind.conf.d/30-clef-lid.conf <<'CONF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
CONF
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

echo "==> battery: charge to 80%, resume below 70% (persists via tmpfiles)"
install -Dm644 /dev/stdin /etc/tmpfiles.d/clef-battery.conf <<'CONF'
w /sys/class/power_supply/BAT0/charge_control_start_threshold - - - - 70
w /sys/class/power_supply/BAT0/charge_control_end_threshold - - - - 80
CONF
systemd-tmpfiles --create /etc/tmpfiles.d/clef-battery.conf

echo "==> user services start at boot without a login"
loginctl enable-linger "$user"

# Applies the logind drop-in without killing the session (a restart would log you out).
systemctl kill -s HUP systemd-logind

echo
echo "done. battery: start=$(cat /sys/class/power_supply/BAT0/charge_control_start_threshold) end=$(cat /sys/class/power_supply/BAT0/charge_control_end_threshold)"
