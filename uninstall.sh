#!/bin/bash

# Remove console mode from this machine.
#
#   ./uninstall.sh              remove scripts, service, udev rule, Hyprland config
#   ./uninstall.sh --keep-config  leave ~/.config/console-mode alone

set -euo pipefail

BIN_DIR="$HOME/.local/bin"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/console-mode"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/console-mode"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
HYPR_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"
HOOK_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/hooks/post-boot.d"
FLAG_FILE="$HOME/.local/state/omarchy/toggles/hypr/monitor-toggle.lua"
MODE_FILE="$HOME/.local/state/console-mode/mode"
UDEV_RULE=/etc/udev/rules.d/90-steam-controller-wake.rules
NM_PRE_DOWN=/etc/NetworkManager/dispatcher.d/pre-down.d/console-mode-tv-off
TV_SLEEP_MONITOR=/usr/local/lib/console-mode/tv-sleep-monitor.py
TV_SLEEP_SERVICE=/etc/systemd/system/console-mode-tv-sleep.service

keep_config=false
[[ ${1:-} == --keep-config ]] && keep_config=true

step() { printf '\n== %s\n' "$1"; }
note() { printf '   %s\n' "$1"; }

step "Restoring all displays"
# Drop the override first, so removing the scripts cannot leave a display
# disabled with no way to re-enable it.
if [[ -f $FLAG_FILE ]]; then
  rm -f "$FLAG_FILE"
  hyprctl reload >/dev/null 2>&1 || true
  note "removed monitor override"
fi
rm -f "$MODE_FILE"

step "Stopping service"
systemctl --user disable --now controller-wake.service 2>/dev/null || true
rm -f "$UNIT_DIR/controller-wake.service"
systemctl --user daemon-reload
note "controller-wake.service removed"

step "Removing scripts"
for name in toggle-monitor console-mode big-picture controller-wake-monitor tv-power-state tv-power; do
  rm -f "$BIN_DIR/$name"
  note "$name"
done
rm -rf "$LIB_DIR"

step "Removing Hyprland config"
rm -f "$HYPR_DIR/console-mode.lua"
if [[ -f "$HYPR_DIR/hyprland.lua" ]]; then
  sed -i '/^-- Console mode keybindings and window rules\.$/d;/^require("hypr\.console-mode")$/d' \
    "$HYPR_DIR/hyprland.lua"
  note "removed require from hyprland.lua"
fi
rm -f "$HOOK_DIR/toggle-monitor-boot"

if [[ -e $UDEV_RULE ]]; then
  step "Removing udev rule (needs sudo)"
  sudo rm -f "$UDEV_RULE"
  sudo udevadm control --reload-rules
  note "removed $UDEV_RULE"
  note "note: Steam's own 60-steam-input.rules may still enable controller wakeup"
fi

if [[ -e $NM_PRE_DOWN ]]; then
  step "Removing NetworkManager pre-down hook (needs sudo)"
  sudo rm -f "$NM_PRE_DOWN"
  note "removed $NM_PRE_DOWN"
fi

if [[ -e $TV_SLEEP_SERVICE || -e $TV_SLEEP_MONITOR ]]; then
  step "Removing system Suspend watcher (needs sudo)"
  sudo systemctl disable --now console-mode-tv-sleep.service 2>/dev/null || true
  sudo rm -f "$TV_SLEEP_SERVICE" "$TV_SLEEP_MONITOR"
  sudo rmdir /usr/local/lib/console-mode 2>/dev/null || true
  sudo systemctl daemon-reload
  note "removed console-mode-tv-sleep"
fi

if ! $keep_config; then
  step "Removing configuration"
  rm -rf "$CONFIG_DIR"
  note "removed $CONFIG_DIR"
fi

step "Done"
note "run: hyprctl reload"
note "not reverted automatically:"
note "  - omarchy-sleep-lock.service (re-enable: systemctl --user enable --now omarchy-sleep-lock.service)"
note "  - idle.lock in omarchy/shell.json (Omarchy's default is 300)"
