from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .. import APP_TITLE
from ..i18n import tr
from ..theme import BG, FONT, MUTED
from .common import format_when


def describe_changes(ch: dict) -> str:
    if ch.get("first"):
        return tr("tm.first")
    parts = [tr(f"tm.ch_{k}", n=ch[k]) for k in ("added", "removed", "versions", "toggled") if ch[k]]
    if ch["order"]:
        parts.append(tr("tm.ch_order"))
    return ", ".join(parts) or "—"

def history_text(e: dict) -> str:
    kind = e.get("kind", "")
    key = f"hist.{kind}"
    text = tr(key, name=e.get("name", ""), old=e.get("old", ""), new=e.get("new", ""),
              text=e.get("text", ""))
    return kind if text == key else text

class TimeMachineDialog(tk.Toplevel):
    def __init__(self, tab: "MineTab"):
        super().__init__(tab.app)
        self.tab = tab
        self.title(tr("tm.title"))
        self.configure(bg=BG)
        self.transient(tab.app)
        self.minsize(640, 360)
        outer = tk.Frame(self, bg=BG, padx=16, pady=14)
        outer.pack(fill="both", expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        f = tk.Frame(nb, bg=BG, pady=8)
        hist = tk.Frame(nb, bg=BG, pady=8)
        nb.add(f, text=tr("tm.tab_snapshots"))
        nb.add(hist, text=tr("tm.tab_history"))
        self.notebook = nb
        self.hist_tab = hist
        self._build_history(hist)
        tk.Label(f, text=tr("tm.intro"), bg=BG, fg=MUTED, font=(FONT, 9), justify="left",
                 wraplength=600).pack(anchor="w", pady=(0, 8))
        cols = ("when", "folder", "mods", "changes")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=12, selectmode="browse")
        for c, w in zip(cols, (130, 120, 60, 300)):
            self.tree.heading(c, text=tr(f"tm.col_{c}"))
            self.tree.column(c, width=w, anchor="center" if c == "mods" else "w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.restore())
        rows = tab.lib.time_machine.listing()
        for snap, ch in rows:
            self.tree.insert("", "end", iid=snap["id"], values=(
                format_when(snap["time"]), snap["folder"],
                sum(1 for m in snap["mods"] if m["on"]), describe_changes(ch)))
        if not rows:
            tk.Label(f, text=tr("tm.empty"), bg=BG, fg=MUTED, font=(FONT, 10)).pack(pady=8)
        else:
            self.tree.selection_set([rows[0][0]["id"]])
        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text=tr("tm.close"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=tr("tm.restore"), style="Accent.TButton",
                   command=self.restore).pack(side="right", padx=(0, 8))

    def _build_history(self, parent):
        lib = self.tab.lib
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text=tr("folder.label"), bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.hist_all = tr("tm.all_folders")
        self.hist_folder = tk.StringVar(value=self.hist_all)
        combo = ttk.Combobox(bar, textvariable=self.hist_folder, state="readonly", width=20,
                             values=[self.hist_all] + list(lib.folders))
        combo.pack(side="left", padx=(6, 12))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.render_history())
        tk.Label(bar, text="⌕", bg=BG, fg=MUTED, font=(FONT, 11)).pack(side="left")
        self.hist_search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.hist_search, width=26).pack(side="left", padx=(6, 0))
        self.hist_search.trace_add("write", lambda *_a: self.render_history())
        cols = ("when", "folder", "event")
        self.hist_tree = ttk.Treeview(parent, columns=cols, show="headings", height=12,
                                      selectmode="browse")
        for c, w in zip(cols, (130, 120, 420)):
            self.hist_tree.heading(c, text=tr(f"tm.hcol_{c}"))
            self.hist_tree.column(c, width=w, anchor="w")
        self.hist_tree.pack(fill="both", expand=True)
        self.hist_empty = tk.Label(parent, text=tr("tm.history_empty"), bg=BG, fg=MUTED,
                                   font=(FONT, 10))
        self.render_history()

    def render_history(self):
        folder = self.hist_folder.get()
        rows = self.tab.lib.history.listing(None if folder == self.hist_all else folder,
                                            self.hist_search.get())
        self.hist_tree.delete(*self.hist_tree.get_children())
        for n, e in enumerate(rows[:2000]):
            self.hist_tree.insert("", "end", iid=str(n), values=(
                format_when(datetime.fromtimestamp(e["t"]).isoformat()), e.get("folder", ""),
                history_text(e)))
        if rows:
            self.hist_empty.pack_forget()
        else:
            self.hist_empty.pack(pady=8)

    def restore(self):
        sel = self.tree.selection()
        if not sel:
            return
        snap = self.tab.lib.time_machine.get(sel[0])
        if not messagebox.askyesno(APP_TITLE, tr("tm.confirm", when=format_when(snap["time"]),
                                                 folder=snap["folder"]), parent=self):
            return
        self.destroy()
        self.tab.restore_snapshot(sel[0])
