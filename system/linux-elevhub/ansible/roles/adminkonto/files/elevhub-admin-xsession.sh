#!/bin/sh
# Reuse the kiosk's lightweight window manager without running the GUI as root.
matchbox-window-manager -use_titlebar no &
window_manager_pid=$!
trap 'kill "$window_manager_pid" 2>/dev/null || true' EXIT
exec /usr/local/bin/elevhub-local-update-gui
