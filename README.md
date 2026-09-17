# omarchy-steam-machine

Turn an Omarchy desktop into something that behaves like a console: press the
Steam button on a controller and the machine wakes from sleep, powers on the
TV, switches it to the right input, moves picture and sound there, and comes up
in Steam Big Picture — without a password prompt. Press one keybinding to get
your desk monitor back.

Built for a specific setup, but the hardware specifics live in a config file.

## What you get

| Trigger | Behaviour |
| --- | --- |
| Steam button while suspended | Wake, TV on, display and audio to TV, Big Picture |
| Keyboard / mouse / power button while on the TV | Wake to the desk monitor, TV off |
| Sleep while on the TV | TV powered off before suspend |
| `SUPER + M` | Toggle desk monitor / TV, audio follows |
| `SUPER + ALT + G` | Console mode on demand |
| `SUPER + ALT + B` | Big Picture on the current display, nothing else changes |

## Requirements

Required: Hyprland (the Omarchy Lua config layout), `jq`, `pactl`, `flock`.

Optional but expected:

- **[LG Buddy](https://lgbuddy.com)**, paired with the TV, for power and input
  control. Without it the display toggle still works but the TV is never
  powered on or switched.
- **Steam**, for Big Picture.
- **Omarchy**, for notifications, the idle-policy toggle, and the post-boot
  hook. Each is skipped gracefully if absent.
- A Python with the `websockets` module, used to read the TV's real power
  state. LG Buddy's bundled virtualenv already has it, and is found
  automatically; otherwise set `CONSOLE_MODE_PYTHON`.

Assumes an LG webOS TV and a Valve wireless receiver (USB vendor `28de`).

## Install

```sh
git clone https://github.com/camjabr/omarchy-steam-machine && cd omarchy-steam-machine
./install.sh --passwordless
$EDITOR ~/.config/console-mode/config.env
hyprctl reload
toggle-monitor status
```

`--passwordless` is opt-in because it weakens security: it disables
`omarchy-sleep-lock.service` and pushes the idle lock timeout out to a day, so
resuming never asks for a password. Leave it off to keep the lock screen.

`--skip-udev` avoids touching `/etc` (and so needs no sudo), at the cost of the
controller not being able to wake the machine.

Uninstall with `./uninstall.sh`. It drops the display override first, so it
cannot leave you with a disabled monitor and no script to re-enable it.

## Configuration

`~/.config/console-mode/config.env`, created from `config.env.example`:

```sh
DESK_OUTPUT=DP-2      # required
TV_OUTPUT=DP-1        # required
DESK_SINK=...         # optional; empty leaves audio alone
TV_SINK=...
```

Find the values with `hyprctl monitors all -j | jq -r '.[].name'` and
`pactl list sinks short | cut -f2`.

## How it works, and why it works that way

Most of the design here exists because the obvious approach didn't survive
contact with the hardware.

**The display override is a generated Lua file.** `toggle-monitor` writes
`~/.local/state/omarchy/toggles/hypr/monitor-toggle.lua`, which Omarchy's
`hyprland.lua` sources after the monitor config, so the choice survives
`hyprctl reload` and reboots. The generated file re-checks DRM connectivity
itself, so a disabled output is never asserted when its cable is absent. Every
switch is rolled back if the display it was handing over to never appears.

**Reachability is not power.** An LG OLED answers its webOS port on 3001 for
several seconds after being told to turn off, and answers indefinitely once
Quick Start+ is enabled. A TCP probe therefore reports a dark panel as ready,
and the session gets handed to a display that is off. `tv-power-state` asks
`com.webos.service.tvpower/power/getPowerState` instead and only `Active`
counts; transitional states arrive as `Active|Request Power Off Logo` and are
deliberately excluded by matching exactly. It reuses the client key LG Buddy
already paired, so it needs no approval prompt of its own.

**The DisplayPort link stays up in standby.** So the TV looks connected to
Hyprland even when it is off, and a session can start with a workspace on a
black screen. The Omarchy post-boot hook reasserts desk mode at login unless
the TV genuinely reports itself awake.

**A waking TV needs time to negotiate a mode.** Checking half a second after
`hyprctl reload` reported failure on switches that were actually fine, and
rolled them straight back. The wait is now up to `MONITOR_WAIT_SECONDS`.

**The controller cannot be detected by enumeration.** The wireless puck
presents four static slots as keyboard/mouse pairs (Steam's "lizard mode")
whether or not a controller is connected, created at boot. Powering a
controller on adds no device and fires no udev event, so there is nothing to
match on. What the kernel does expose is the puck's wakeup source counter at
`/sys/bus/usb/devices/*/power/wakeup_active_count`, which advances when the
puck signals a remote wakeup. Measured across suspends: the puck's counter
moves when the controller wakes the machine and no other device's does, while a
keyboard wake moves only the keyboard's. The counter belongs to the puck, so
**any controller paired to it triggers this equally.**

**Polling beats the logind signal for resume.** Watching `PrepareForSleep` over
D-Bus looked right, but `dbus-monitor` is refused new-style monitoring under a
user service and its eavesdrop fallback delivered the pre-sleep signal and never
the matching resume — so console mode silently never fired. The watcher polls
the counter instead, which cannot miss the transition because the loop freezes
with the machine. Pre-sleep TV power-off still uses the PrepareForSleep *true*
signal (which eavesdrop does deliver), held open with a logind delay inhibitor
so the call finishes before the network drops.

**LG Buddy's sleep_wake_policy is left disabled.** When enabled it restores the
TV after every wake — including keyboard — which fights desk-mode resume. Its
`power off` path also skips when it has no screen-ownership marker, which is how
sleeping from Steam Big Picture left the panel on. Console-mode therefore sends
`ssap://system/turnOff` itself before suspend, and only powers the TV back on
when entering console mode.

**A running Steam ignores `-gamepadui`.** Worse, closing the Big Picture window
leaves Steam alive with no window at all, so relaunching does nothing visible.
`steam steam://open/bigpicture` is what reopens it. Big Picture also maps as an
ordinary 1600x1000 tile, hence the fullscreen window rule.

**Idle policy follows the display.** Gamepad input does not reliably register
as desktop activity, so TV mode sets Omarchy's stay-awake and desk mode
restores normal idling. Note that `idle.lock = 0` means *lock immediately*, not
*never*, which is why disabling the lock uses a long timeout instead.

## Limits

**The controller cannot boot the machine from a full shutdown.** USB wake is
offered up to S4 on the hardware this was built on; booting from S5 needs a
"Power On By USB" style BIOS option that Linux cannot set. Suspend instead of
shutting down.

**Connecting a controller while the machine is awake does nothing
automatically.** The puck's runtime power management is off (`power/control`
reads `on`), so it never autosuspends and never needs to signal a wakeup while
the system is running. Use `SUPER + ALT + B`. Setting `power/control` to `auto`
would surface the event at the cost of putting a suspend/resume cycle between
the controller and your inputs.

**Cold boot still asks for a password.** This only removes the prompt on
resume. Autologin is a separate decision and is not configured here.

**Quick Start+ has to be enabled on the TV itself**, under Settings →
General. It cannot be set over the network on webOS 6: SSAP's
`settings/getSystemSettings` whitelist refuses to read or write
`quickStartMode`, and while the `system.notifications/createAlert` luna bridge
does execute settings writes (verified against `option.audioGuidance`), writes
return no result, so a rejected key fails silently and unverifiably. Setting
`quickStartMode` under both the `option` and `general` categories changed
nothing measurable.

## Troubleshooting

```sh
toggle-monitor status                                   # displays, TV, audio
tv-power-state                                          # what the TV reports
journalctl --user -u controller-wake.service -f         # trigger activity
cat /sys/bus/usb/devices/*/power/wakeup                 # is wakeup armed?
toggle-monitor reset                                    # re-enable everything
```

If a switch leaves you on the wrong display, `toggle-monitor reset` enables
every output unconditionally.
