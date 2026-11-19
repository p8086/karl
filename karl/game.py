from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

STEAM_APP_ID = 548430
GAME_EXE = "FSD-Win64-Shipping.exe"
STEAM_URL = f"steam://rungameid/{STEAM_APP_ID}"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
PROC = Path("/proc")


def _linux_pids() -> list[int]:
    me, out = os.getpid(), []
    needle = GAME_EXE.lower().encode()
    try:
        entries = list(PROC.iterdir())
    except OSError:
        return out
    for d in entries:
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            cmd = (d / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in cmd.lower():
            first = cmd.split(b"\0", 1)[0].lower()
            if first.endswith(needle):
                out.append(int(d.name))
    return out


def is_running() -> bool:
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return GAME_EXE.lower() in out.lower()
    if sys.platform.startswith("linux"):
        return bool(_linux_pids())
    return False


def _spawn(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def launch() -> None:
    if sys.platform == "win32":
        os.startfile(STEAM_URL)
        return
    if sys.platform == "darwin":
        if _spawn(["open", STEAM_URL]):
            return
    else:
        for cmd in (["steam", STEAM_URL],
                    ["flatpak", "run", "com.valvesoftware.Steam", STEAM_URL],
                    ["xdg-open", STEAM_URL]):
            if shutil.which(cmd[0]) and (cmd[0] != "flatpak" or _flatpak_steam()) \
                    and _spawn(cmd):
                return
    webbrowser.open(STEAM_URL)


def _flatpak_steam() -> bool:
    return (Path.home() / ".var/app/com.valvesoftware.Steam").is_dir()


def close() -> bool:
    if sys.platform == "win32":
        if not is_running():
            return False
        try:
            subprocess.run(["taskkill", "/IM", GAME_EXE, "/F"], capture_output=True, timeout=15,
                           creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return False
        return True
    if not sys.platform.startswith("linux"):
        return False
    pids = _linux_pids()
    if not pids:
        return False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        for _ in range(50):
            if not _linux_pids():
                return True
            time.sleep(0.2)
    return not _linux_pids()
