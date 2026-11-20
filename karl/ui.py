from __future__ import annotations

from pathlib import Path

SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
SCALE = 1.0


def set_scale(value: float) -> float:
    global SCALE
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 1.0
    SCALE = min(SCALES, key=lambda s: abs(s - value))
    return SCALE


def px(n: float) -> int:
    return int(round(n * SCALE))


def label(value: float) -> str:
    return f"{int(round(value * 100))}%"


def photo(path: Path, master=None):
    import tkinter as tk
    if SCALE == 1.0:
        return tk.PhotoImage(master=master, file=str(path))
    try:
        from PIL import Image, ImageTk
        im = Image.open(path)
        im = im.resize((max(1, px(im.width)), max(1, px(im.height))), Image.LANCZOS)
        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        img = tk.PhotoImage(master=master, file=str(path))
        factor = int(round(SCALE))
        try:
            return img.zoom(factor, factor) if factor > 1 else img
        except (AttributeError, tk.TclError):
            return img
