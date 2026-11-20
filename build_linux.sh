#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "Manca Tkinter. Installalo con il gestore pacchetti della tua distribuzione:"
  echo "  Ubuntu/Debian/Mint:  sudo apt install python3-tk python3-venv"
  echo "  Fedora:              sudo dnf install python3-tkinter"
  echo "  Arch/SteamOS:        sudo pacman -S tk"
  exit 1
fi

python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip >/dev/null
.venv-build/bin/python -m pip install -r requirements.txt pyinstaller

.venv-build/bin/python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name karl \
  --hidden-import PIL.ImageTk --hidden-import PIL._imagingtk --hidden-import PIL._tkinter_finder \
  --collect-all tkinterdnd2 --collect-submodules pystray --collect-submodules Xlib \
  --add-data "karl/assets:karl/assets" \
  --add-data "karl/locales:karl/locales" \
  --add-data "karl/loader:karl/loader" \
  main.py

apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$apps" "$icons"
cp karl/assets/icon_256.png "$icons/karl.png"
cat > "$apps/karl.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Karl
Comment=DRG mod loader
Exec=$PWD/dist/karl
Icon=karl
Terminal=false
Categories=Game;Utility;
StartupWMClass=Karl
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true

echo
echo "Fatto: dist/karl (trovi Karl anche nel menu delle applicazioni)"
