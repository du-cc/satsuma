#!/bin/bash
# Remove a per-user Satsuma installation (settings in ~/.config/satsuma are kept).
set -euo pipefail
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
if [ -x "$HOME/.local/bin/satsuma" ]; then
  "$HOME/.local/bin/satsuma" uninstall-integration || true
fi
rm -rf "$DATA/satsuma" "$HOME/.local/bin/satsuma" \
  "$DATA/applications/io.github.satsuma.Satsuma.desktop" \
  "$DATA/icons/hicolor/scalable/apps/io.github.satsuma.Satsuma.svg" \
  "$DATA/icons/hicolor/symbolic/apps/io.github.satsuma.Satsuma-symbolic.svg"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
nautilus -q >/dev/null 2>&1 || true
echo "Satsuma removed."
