import subprocess
import sys

BG = "#121110"
PANEL = "#1e1c1a"
CARD = "#262321"
BORDER = "#3a3532"
CHIP = "#3a3532"
BUTTON = "#33302d"
BUTTON_HOVER = "#403c38"
FG = "#f1ede8"
MUTED = "#a39c94"
ACCENT = "#e69a1e"
ACCENT_HOVER = "#f2ad3a"
ON_ACCENT = "#1a1714"

if sys.platform == "win32":
    FONT = "Segoe UI"
elif sys.platform == "darwin":
    FONT = "Helvetica Neue"
else:
    def _linux_font() -> str:
        try:
            have = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True,
                                  timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return "Ubuntu"
        families = {f.strip().lower() for line in have.splitlines() for f in line.split(",")}
        for name in ("Ubuntu", "Noto Sans", "Cantarell", "Inter", "DejaVu Sans", "Liberation Sans"):
            if name.lower() in families:
                return name
        return "TkDefaultFont"

    FONT = _linux_font()
