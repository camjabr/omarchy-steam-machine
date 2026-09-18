#!/bin/bash

# Power the TV off the moment something asks logind to suspend — before
# PrepareForSleep, and before NetworkManager deauthenticates Wi-Fi.
#
# User-session PrepareForSleep handlers and NM pre-down.d both lose that race
# on this machine: by the time they run, IPv4 routes are already gone and SSAP
# returns ENETUNREACH. Eavesdrop cannot see method_calls either. BecomeMonitor
# (dbus-monitor --monitor), which needs to run as root, can — and the Suspend
# call arrives while the network is still fully up.

set -uo pipefail

MODE_GLOB="${MODE_GLOB:-/home/*/.local/state/console-mode/mode}"

log() {
  logger -t console-mode-tv-sleep "$*"
  printf '%s\n' "$*"
}

power_off_for_user() {
  local mode_file=$1 user home

  user=$(printf '%s\n' "$mode_file" | cut -d/ -f3)
  home="/home/$user"

  [[ -r $home/.config/lg-buddy/tvs/primary/access-token.json ]] || return 1
  [[ -r $home/.local/share/console-mode/tv_ssap.py ]] || return 1

  log "Suspend requested while $user is on the TV; powering off"
  if env HOME="$home" USER="$user" \
    LG_BUDDY_CONFIG="$home/.config/lg-buddy/config.env" \
    LG_BUDDY_TOKEN="$home/.config/lg-buddy/tvs/primary/access-token.json" \
    /usr/bin/LG_Buddy_PIP/bin/python "$home/.local/share/console-mode/tv_ssap.py" off; then
    log "TV powered off for $user"
    return 0
  fi
  log "TV power-off failed for $user"
  return 1
}

handle_suspend_request() {
  local mode_file any=0
  for mode_file in $MODE_GLOB; do
    [[ -f $mode_file ]] || continue
    [[ $(cat "$mode_file") == tv ]] || continue
    any=1
    power_off_for_user "$mode_file" || true
  done
  if ((any == 0)); then
    log "Suspend requested; no console-mode session on the TV"
  fi
}

log "watching logind Suspend method calls"

# --monitor uses BecomeMonitor so we see point-to-point method_calls (eavesdrop
# cannot). Match every sleep-entry method logind exposes.
dbus-monitor --system --monitor \
  "type='method_call',path='/org/freedesktop/login1',interface='org.freedesktop.login1.Manager',member='Suspend'" \
  "type='method_call',path='/org/freedesktop/login1',interface='org.freedesktop.login1.Manager',member='Hibernate'" \
  "type='method_call',path='/org/freedesktop/login1',interface='org.freedesktop.login1.Manager',member='HybridSleep'" \
  "type='method_call',path='/org/freedesktop/login1',interface='org.freedesktop.login1.Manager',member='SuspendThenHibernate'" \
  2>/dev/null | while IFS= read -r line; do
  case $line in
    method\ call*)
      case $line in
        *member=Suspend* | *member=Hibernate* | *member=HybridSleep* | *member=SuspendThenHibernate*)
          handle_suspend_request
          ;;
      esac
      ;;
  esac
done
