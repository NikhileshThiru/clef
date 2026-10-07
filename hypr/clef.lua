-- Clef dashboard kiosk (~/projects/clef). Loaded from ~/.config/hypr/hyprland.lua via require("hypr.clef").
-- The `clef` command opens http://clef.localhost:8077 as a Chromium app, which Chromium names
-- "chrome-clef.localhost__-Default" (it ignores --class on Wayland). This pins it to workspace 10 (Super+0),
-- fullscreen, no border, fully opaque, and keeps the screen awake while it's open.
o.window("^chrome-clef\\.localhost__.*$", {
  workspace = "10 silent", -- `clef` focuses it explicitly; a crash-restart never yanks you over
  fullscreen = true,
  border_size = 0,
  opacity = "1 override 1 override 1 override",
  idle_inhibit = "always",
})
