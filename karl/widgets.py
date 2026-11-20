from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import (ACCENT, ACCENT_HOVER, BG, BORDER, BUTTON, BUTTON_HOVER, DANGER, FG, FONT,
                    MUTED, ON_ACCENT, PANEL)


def set_button_style(b: tk.Label, text: str, primary: bool) -> None:
    bg, hover, fg = (ACCENT, ACCENT_HOVER, ON_ACCENT) if primary else (BUTTON, BUTTON_HOVER, FG)
    b._colors = (bg, hover)
    b.configure(text=text, bg=bg, fg=fg)


def flat_button(parent, text, command, primary=False, **kw) -> tk.Label:
    b = tk.Label(parent, cursor="hand2", font=(FONT, 10, "bold"), padx=12, pady=7, **kw)
    set_button_style(b, text, primary)
    b.bind("<Enter>", lambda e: b.configure(bg=b._colors[1]))
    b.bind("<Leave>", lambda e: b.configure(bg=b._colors[0]))
    b.bind("<Button-1>", lambda e: command())
    return b


def check_button(parent, text: str, variable, command=None, **kw) -> tk.Checkbutton:
    style = dict(bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                 disabledforeground=MUTED, anchor="w")
    style.update(kw)
    if command is not None:
        style["command"] = command
    return tk.Checkbutton(parent, text=text, variable=variable, **style)


def text_box(parent, height: int = 14) -> tk.Text:
    box = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    box.pack(fill="both", expand=True)
    t = tk.Text(box, wrap="word", bg=PANEL, fg=FG, relief="flat", bd=0, padx=10, pady=8,
                font=(FONT, 10), height=height, insertbackground=FG, highlightthickness=0)
    sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    t.configure(yscrollcommand=sb.set)
    t.tag_configure("title", font=(FONT, 10, "bold"), foreground=ACCENT)
    t.tag_configure("muted", foreground=MUTED)
    t.tag_configure("warn", foreground=ACCENT)
    t.tag_configure("error", foreground=DANGER)
    t.configure(state="disabled")
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
