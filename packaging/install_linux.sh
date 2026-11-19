#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

bin="$HOME/.local/bin"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$bin" "$apps" "$icons"
install -m 755 karl "$bin/karl"
install -m 644 karl.png "$icons/karl.png"
cat > "$apps/karl.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Karl
Comment=DRG mod loader
Exec=$bin/karl
Icon=karl
Terminal=false
Categories=Game;Utility;
StartupWMClass=Karl
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
echo "Karl installato: lo trovi nel menu delle applicazioni (oppure lancia $bin/karl)."
