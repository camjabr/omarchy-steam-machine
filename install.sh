#!/bin/bash

# Install console mode onto this machine.
#
#   ./install.sh                 scripts, service, udev rule, Hyprland config
#   ./install.sh --passwordless  also remove the password prompt on resume
#   ./install.sh --skip-udev     leave /etc alone (no sudo needed)

set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

BIN_DIR="$HOME/.local/bin"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/console-mode"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/console-mode"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
HYPR_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"
HOOK_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/hooks/post-boot.d"
SHELL_JSON="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/shell.json"
UDEV_RULE=/etc/udev/rules.d/90-steam-controller-wake.rules

passwordless=false
skip_udev=false
for arg in "$@"; do
  case $arg in
    --passwordless) passwordless=true ;;
    --skip-udev) skip_udev=true ;;
    -h | --help)
      sed -n '3,8p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

step() { printf '\n== %s\n' "$1"; }
note() { printf '   %s\n' "$1"; }

# --- prerequisites -----------------------------------------------------------

step "Checking prerequisites"
missing=()
for cmd in hyprctl jq pactl flock; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if ((${#missing[@]})); then
  echo "   missing required commands: ${missing[*]}" >&2
  exit 1
fi
note "required commands present"

for cmd in lg-buddy steam omarchy; do
  if command -v "$cmd" >/dev/null 2>&1; then
    note "$cmd found"
  else
    note "$cmd NOT found -- related features will be skipped at run time"
  fi
done

# --- scripts -----------------------------------------------------------------

step "Installing scripts to $BIN_DIR"
install -d "$BIN_DIR" "$LIB_DIR"
for script in "$REPO"/bin/*; do
  install -m 755 "$script" "$BIN_DIR/"
  note "$(basename "$script")"
done
install -m 644 "$REPO/lib/tv_power_state.py" "$LIB_DIR/"
install -m 644 "$REPO/lib/tv_ssap.py" "$LIB_DIR/"
note "tv_power_state.py, tv_ssap.py -> $LIB_DIR"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) note "WARNING: $BIN_DIR is not on PATH" ;;
esac

# --- configuration -----------------------------------------------------------

step "Configuration"
install -d "$CONFIG_DIR"
if [[ -e "$CONFIG_DIR/config.env" ]]; then
  note "keeping existing $CONFIG_DIR/config.env"
else
  install -m 644 "$REPO/config.env.example" "$CONFIG_DIR/config.env"
  note "created $CONFIG_DIR/config.env -- EDIT THIS before first use"
  note "outputs: $(hyprctl monitors all -j 2>/dev/null | jq -r '[.[].name] | join(", ")')"
fi

# --- Hyprland ----------------------------------------------------------------

step "Installing Hyprland config"
install -d "$HYPR_DIR"
install -m 644 "$REPO/hypr/console-mode.lua" "$HYPR_DIR/"
note "console-mode.lua -> $HYPR_DIR"

if [[ -f "$HYPR_DIR/hyprland.lua" ]]; then
  if grep -q 'require("hypr.console-mode")' "$HYPR_DIR/hyprland.lua"; then
    note "hyprland.lua already requires it"
  else
    printf '\n-- Console mode keybindings and window rules.\nrequire("hypr.console-mode")\n' \
      >>"$HYPR_DIR/hyprland.lua"
    note "appended require to hyprland.lua"
  fi
else
  note "no hyprland.lua found; add require(\"hypr.console-mode\") yourself"
fi

# --- Omarchy post-boot hook --------------------------------------------------

if command -v omarchy >/dev/null 2>&1; then
  step "Installing Omarchy post-boot hook"
  install -d "$HOOK_DIR"
  install -m 755 "$REPO/omarchy/hooks/post-boot.d/toggle-monitor-boot" "$HOOK_DIR/"
  note "toggle-monitor-boot -> $HOOK_DIR"
fi

# --- udev --------------------------------------------------------------------

if $skip_udev; then
  step "Skipping udev rule (--skip-udev)"
  note "the controller will not wake the machine without it"
else
  step "Installing udev rule (needs sudo)"
  sudo install -m 644 -o root -g root "$REPO/udev/90-steam-controller-wake.rules" "$UDEV_RULE"
  sudo udevadm control --reload-rules
  # The rule matches ACTION=="add", so a plain trigger (which sends "change")
  # would not apply it to already-connected devices.
  sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=28de
  note "installed and applied $UDEV_RULE"
  for dir in /sys/bus/usb/devices/*/; do
    [[ $(cat "$dir/idVendor" 2>/dev/null) == 28de ]] || continue
    note "$(basename "$dir") ($(cat "$dir/product" 2>/dev/null)): wakeup=$(cat "$dir/power/wakeup" 2>/dev/null)"
  done
fi

# --- service -----------------------------------------------------------------

step "Installing user service"
install -d "$UNIT_DIR"
install -m 644 "$REPO/systemd/user/controller-wake.service" "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable controller-wake.service
systemctl --user restart controller-wake.service
note "controller-wake.service: $(systemctl --user is-active controller-wake.service)"

# --- optional: passwordless resume -------------------------------------------

if $passwordless; then
  step "Removing the password prompt on resume"

  if systemctl --user list-unit-files omarchy-sleep-lock.service >/dev/null 2>&1; then
    systemctl --user disable --now omarchy-sleep-lock.service 2>/dev/null || true
    note "omarchy-sleep-lock.service disabled (it locked the screen before every suspend)"
  fi

  if [[ -f $SHELL_JSON ]]; then
    # 0 means "lock immediately" rather than "never", so a long timeout is the
    # only way to disable the idle lock while keeping the screensaver, which
    # matters on OLED panels.
    tmp=$(mktemp)
    jq '.idle.lock = 86400' "$SHELL_JSON" >"$tmp" && mv "$tmp" "$SHELL_JSON"
    note "idle.lock set to 86400 in shell.json (screensaver left enabled)"
  fi
fi

# --- done --------------------------------------------------------------------

step "Done"
note "1. edit $CONFIG_DIR/config.env for your outputs and audio sinks"
note "2. run: hyprctl reload"
note "3. check: toggle-monitor status"
note ""
note "SUPER+M toggle displays | SUPER+ALT+G console mode | SUPER+ALT+B Big Picture"
