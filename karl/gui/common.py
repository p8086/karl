from __future__ import annotations

import os
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from .. import GITHUB_REPO
from ..library import Config
from ..theme import ACCENT, BORDER, FG, FONT, MUTED, PANEL


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

def text_box(parent, height: int = 14) -> tk.Text:
    box = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    box.pack(fill="both", expand=True)
    t = tk.Text(box, wrap="word", bg=PANEL, fg=FG, relief="flat", bd=0, padx=10, pady=8,
                font=(FONT, 10), height=height, highlightthickness=0)
    sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    t.configure(yscrollcommand=sb.set)
    t.tag_configure("title", font=(FONT, 10, "bold"), foreground=ACCENT)
    t.tag_configure("muted", foreground=MUTED)
    t.tag_configure("warn", foreground="#e05a4f")
    return t

def fill_text(t: tk.Text, chunks: list[tuple[str, str]]) -> None:
    t.configure(state="normal")
    t.delete("1.0", "end")
    for text, tag in chunks:
        if tag:
            t.insert("end", text, tag)
        else:
            t.insert("end", text)
    t.configure(state="disabled")
