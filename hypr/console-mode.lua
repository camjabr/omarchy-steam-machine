-- Console mode keybindings and window rules.
--
-- Loaded from ~/.config/hypr/hyprland.lua with:
--     require("hypr.console-mode")

-- Switch between the desk monitor and the TV, moving audio along with it.
o.bind("SUPER + M", "Toggle monitor", "toggle-monitor")

-- Console mode: TV, Big Picture. Same thing the controller does on wake.
o.bind("SUPER + ALT + G", "Console mode", "console-mode")

-- Big Picture on whatever display is already active, for using a controller
-- at the desk without handing the session to the TV.
o.bind("SUPER + ALT + B", "Big Picture here", "big-picture")

-- Big Picture is a couch UI: it opens as an ordinary 1600x1000 tile otherwise,
-- which leaves it sharing the screen with whatever else is on the workspace.
o.window({ class = "^steam$", title = "^Steam Big Picture Mode$" }, { fullscreen = true })

-- gamescope nested session (CONSOLE_GAMESCOPE=1): keep the nest fullscreen on
-- whichever output console-mode switched to.
o.window({ class = "^gamescope$" }, { fullscreen = true })
o.window({ class = "^gamescope-wl$" }, { fullscreen = true })
