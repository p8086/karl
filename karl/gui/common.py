from __future__ import annotations

import os
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path

from .. import GITHUB_REPO
from ..config import Config


ASSETS = Path(__file__).resolve().parents[1] / "assets"

TOKEN_PAGE = "https://mod.io/me/access"

GITHUB_PAGE = f"https://github.com/{GITHUB_REPO}"

class ErrorText(str):
    status = None

    @classmethod
    def of(cls, exc: Exception) -> "ErrorText":
        t = cls(str(exc))
        t.status = getattr(exc, "status", None)
        t.read_failed = getattr(exc, "read_failed", False)
        return t

def open_path(path) -> None:
    import os
    import subprocess
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except OSError:
        webbrowser.open(Path(path).as_uri())

def safe_grab(win: tk.Misc, tries: int = 20) -> None:
    try:
        if not win.winfo_exists():
            return
        win.grab_set()
    except tk.TclError:
        if tries > 0:
            win.after(50, lambda: safe_grab(win, tries - 1))

def remember_me(cfg: Config, me: dict) -> None:
    url = me.get("profile_url") or ""
    if not url and me.get("name_id"):
        url = f"https://mod.io/u/{me['name_id']}"
    if url:
        cfg.profile_url = url

def format_when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso

