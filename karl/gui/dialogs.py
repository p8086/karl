from __future__ import annotations

import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, TYPE_CHECKING

from .. import APP_TITLE, VERSION, diagnostics
from ..details import html_to_text
from ..i18n import human_size, tr
from ..log import log
from ..theme import ACCENT, BG, BORDER, DANGER, FG, FONT, MUTED, PANEL, SUCCESS
from ..ui import px
from ..widgets import check_button, fill_text, flat_button, text_box
from .common import TOKEN_PAGE, safe_grab

if TYPE_CHECKING:
    from .app import App


class LoginDialog(tk.Toplevel):
    def __init__(self, app: App, on_done: Optional[Callable[[], None]] = None):
        super().__init__(app)
        self.app = app
        self.on_done = on_done
        self.title(tr("login.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        safe_grab(self)

        f = tk.Frame(self, bg=BG, padx=22, pady=18)
        f.pack(fill="both", expand=True)
        tk.Label(f, text=tr("login.heading"), bg=BG, fg=FG,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(f, text=tr("login.steps"), bg=BG, fg=MUTED, font=(FONT, 10),
                 justify="left").pack(anchor="w", pady=(8, 12))
        flat_button(f, tr("login.open"), lambda: webbrowser.open(TOKEN_PAGE)).pack(anchor="w")

        tk.Label(f, text=tr("login.token"), bg=BG, fg=FG,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(14, 4))
        self.token = tk.StringVar()
        self.entry = ttk.Entry(f, textvariable=self.token, show="•", width=64)
        self.entry.pack(fill="x")
        self.entry.bind("<FocusIn>", self._paste_if_token)
        self.entry.bind("<Return>", lambda e: self.submit())
        self.msg = tk.Label(f, text="", bg=BG, fg=DANGER, font=(FONT, 9), wraplength=520,
                            justify="left")
        self.msg.pack(anchor="w", pady=(6, 0))

        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self.ok = flat_button(row, tr("login.submit"), self.submit, primary=True)
        self.ok.pack(side="right")
        flat_button(row, tr("common.cancel"), self.destroy).pack(side="right", padx=(0, 8))
        self.entry.focus_set()

    def _paste_if_token(self, _e):
        if self.token.get():
            return
        try:
            clip = self.clipboard_get().strip()
        except tk.TclError:
            return
        if len(clip) > 100 and " " not in clip:
            self.token.set(clip)

    def submit(self):
        token = self.token.get().strip()
        if not token:
            self.msg.configure(text=tr("login.paste_first"))
            return
        self.msg.configure(text=tr("login.checking"), fg=MUTED)

        def ok(me):
            self.app.login_ok(token, me)
            self.destroy()
            if self.on_done:
                self.on_done()

        def fail(err):
            text = tr("login.read_failed", msg=err) if getattr(err, "read_failed", False) else err
            self.msg.configure(text=text, fg=DANGER)

        self.app.submit(lambda: self.app.client(token).check_access(), ok, fail)


class DiagnosticsDialog(tk.Toplevel):
    def __init__(self, app: App, parent=None):
        super().__init__(parent or app)
        self.app = app
        self.title(tr("diag.title"))
        self.configure(bg=BG, padx=16, pady=14)
        self.geometry(f"{px(820)}x{px(560)}")
        self.transient(parent or app)
        tk.Label(self, text=tr("diag.intro"), bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                 justify="left", wraplength=780).pack(fill="x")
        box = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        box.pack(fill="both", expand=True, pady=(8, 10))
        self.text = tk.Text(box, wrap="word", bg=PANEL, fg=FG, relief="flat", bd=0, padx=10,
                            pady=8, font=("Consolas" if sys.platform == "win32" else "Monospace",
                                          9), highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=sb.set)
        for level, color in (("OK", SUCCESS), ("WARN", ACCENT), ("FAIL", DANGER)):
            self.text.tag_configure(level, foreground=color)
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x")
        ttk.Button(btns, text=tr("diag.copy"), command=self.copy).pack(side="left")
        ttk.Button(btns, text=tr("diag.save"), command=self.save).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text=tr("log.open"), command=app.open_log).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text=tr("diag.again"), command=self.run).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text=tr("details.close"), command=self.destroy).pack(side="right")
        self.report = ""
        self.run()

    def run(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", tr("diag.running"))
        self.text.configure(state="disabled")
        self.app.submit(lambda: diagnostics.run(self.app.lib), self._show,
                        lambda msg: self._show([("FAIL", str(msg))]))

    def _show(self, items):
        if not self.winfo_exists():
            return
        self.report = diagnostics.format_report(items)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for line, (level, _t) in zip(self.report.splitlines(), items):
            self.text.insert("end", line[:6], level)
            self.text.insert("end", line[6:] + "\n")
        self.text.configure(state="disabled")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.report)
        self.app.set_status(tr("diag.copied"))

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt",
                                            initialfile="karl-diagnostics.txt",
                                            filetypes=[("Text", "*.txt")])
        if path:
            Path(path).write_text(self.report + "\n", encoding="utf-8")
            self.app.set_status(tr("diag.saved", path=path))


class WhatsNewDialog(tk.Toplevel):
    def __init__(self, app: App, changes: list[dict], karl_notes: Optional[str] = None):
        super().__init__(app)
        self.app = app
        self.changes = list(changes)
        self.karl_notes = karl_notes
        self.logs: dict[int, str] = {}
        self.title(tr("news.title"))
        self.configure(bg=BG, padx=16, pady=14)
        self.geometry(f"{px(720)}x{px(560)}")
        self.transient(app)
        head = tr("news.karl", version=VERSION) if karl_notes else tr("news.mods",
                                                                       n=len(self.changes))
        tk.Label(self, text=head, bg=BG, fg=FG, font=(FONT, 12, "bold"), anchor="w",
                 justify="left").pack(fill="x", pady=(0, 8))
        self.text = text_box(self)
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        self.hide_var = tk.BooleanVar(value=False)
        check_button(bottom, tr("news.dont_show"), self.hide_var).pack(side="left")
        ttk.Button(bottom, text=tr("details.close"), command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.render()
        self.load()

    def load(self):
        todo = [c for c in self.changes if c.get("file_id") and c.get("game_id")]
        if not todo or not self.app.config_.logged_in:
            return

        def work():
            client = self.app.client()
            out = {}
            for c in todo:
                try:
                    mf = client.get_modfile(c["game_id"], c["mod_id"], c["file_id"])
                    out[c["mod_id"]] = html_to_text(mf.get("changelog") or "")
                except Exception:
                    out[c["mod_id"]] = ""
            return out

        self.app.submit(work, self._loaded, lambda err: None)

    def _loaded(self, logs: dict):
        if self.winfo_exists():
            self.logs = logs
            self.render()

    def render(self):
        chunks: list[tuple[str, str]] = []
        if self.karl_notes:
            chunks.append((html_to_text(self.karl_notes) or self.karl_notes, ""))
        for c in self.changes:
            chunks.append((f"{c.get('name', '')}   ", "title"))
            chunks.append((f"{c.get('old') or '-'} → {c.get('new') or '-'}\n", "muted"))
            log = self.logs.get(c["mod_id"])
            if log is None and self.app.config_.logged_in and c.get("file_id"):
                chunks.append((tr("details.loading") + "\n\n", "muted"))
            else:
                chunks.append(((log or tr("details.no_changelog")) + "\n\n", "" if log else "muted"))
        fill_text(self.text, chunks)

    def close(self):
        if self.hide_var.get():
            self.app.config_.show_whats_new = False
            self.app.config_.save()
        self.destroy()


class CacheDialog(tk.Toplevel):
    ROWS = (("mods", False), ("snapshots", False), ("unused", True), ("covers", True),
            ("previous", True))

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title(tr("cache.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        f = tk.Frame(self, bg=BG, padx=20, pady=16)
        f.pack(fill="both", expand=True)
        tk.Label(f, text=tr("cache.title"), bg=BG, fg=FG, font=(FONT, 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.report = app.lib.cache_report()
        self.vars: dict[str, tk.BooleanVar] = {}
        self.size_lbls: dict[str, tk.Label] = {}
        for row, (key, cleanable) in enumerate(self.ROWS, 1):
            if cleanable:
                var = tk.BooleanVar(value=key != "previous")
                self.vars[key] = var
                check_button(f, tr(f"cache.{key}"), var).grid(row=row, column=0, sticky="w")
            else:
                tk.Label(f, text=tr(f"cache.{key}"), bg=BG, fg=MUTED, anchor="w",
                         padx=24).grid(row=row, column=0, sticky="w")
            lbl = tk.Label(f, bg=BG, fg=FG, font=(FONT, 10, "bold"), anchor="e")
            lbl.grid(row=row, column=1, sticky="e", padx=(24, 0))
            self.size_lbls[key] = lbl
        self.total = tk.Label(f, bg=BG, fg=MUTED, anchor="w")
        self.total.grid(row=len(self.ROWS) + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        btns = tk.Frame(f, bg=BG)
        btns.grid(row=len(self.ROWS) + 2, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text=tr("cache.close"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=tr("cache.clean"), style="Accent.TButton",
                   command=self.clean).pack(side="right", padx=(0, 8))
        self._render()

    def _render(self):
        for key, lbl in self.size_lbls.items():
            lbl.configure(text=human_size(self.report.get(key, 0)))
        self.total.configure(text=tr("cache.total", size=human_size(sum(self.report.values()))))

    def clean(self):
        kinds = {k for k, v in self.vars.items() if v.get()}
        if not kinds or self.app.busy:
            return
        if "previous" in kinds and not messagebox.askyesno(APP_TITLE, tr("cache.previous_confirm"),
                                                            parent=self):
            return
        freed = self.app.lib.clean_cache(kinds)
        self.report = self.app.lib.cache_report()
        self._render()
        self.app.set_status(tr("cache.done", size=human_size(freed)))


class TagsDialog(tk.Toplevel):
    COLUMNS = 3

    def __init__(self, app: App, ids: list[int], on_done: Callable[[], None]):
        super().__init__(app)
        self.app = app
        self.ids = ids
        self.on_done = on_done
        self.title(tr("tags.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        safe_grab(self)
        mods = [app.lib.mods[i] for i in ids]
        f = tk.Frame(self, bg=BG, padx=20, pady=16)
        f.pack(fill="both", expand=True)
        head = (tr("tags.choose", name=mods[0].name) if len(mods) == 1
                else tr("tags.choose_many", n=len(mods)))
        tk.Label(f, text=head, bg=BG, fg=FG, font=(FONT, 11, "bold"), anchor="w",
                 wraplength=px(460), justify="left").pack(fill="x")
        self.box = tk.Frame(f, bg=BG)
        self.box.pack(fill="x", pady=(10, 0))
        self.vars: dict[str, tk.BooleanVar] = {}
        self.initial: dict[str, bool] = {}
        for t in app.lib.all_tags():
            on = all(t.lower() in (x.lower() for x in m.tags) for m in mods)
            self.vars[t] = tk.BooleanVar(value=on)
            self.initial[t] = on
        self._render()
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        tk.Label(row, text=tr("tags.new"), bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.new_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.new_var, width=24)
        entry.pack(side="left", padx=(8, 6))
        entry.bind("<Return>", lambda _e: self.add_new())
        ttk.Button(row, text=tr("tags.add"), command=self.add_new).pack(side="left")
        btns = tk.Frame(f, bg=BG)
        btns.pack(fill="x", pady=(16, 0))
        ttk.Button(btns, text=tr("settings.save"), style="Accent.TButton",
                   command=self.save).pack(side="right")
        ttk.Button(btns, text=tr("common.cancel"), command=self.destroy).pack(
            side="right", padx=(0, 8))
        entry.focus_set()

    def _render(self) -> None:
        for w in self.box.winfo_children():
            w.destroy()
        if not self.vars:
            tk.Label(self.box, text=tr("tags.none_yet"), bg=BG, fg=MUTED,
                     font=(FONT, 9)).grid(row=0, column=0, sticky="w")
            return
        for n, (tag, var) in enumerate(self.vars.items()):
            cell = tk.Frame(self.box, bg=BG)
            cell.grid(row=n // self.COLUMNS, column=n % self.COLUMNS, sticky="w", padx=(0, 16))
            check_button(cell, tag, var).pack(side="left")
            x = tk.Label(cell, text="✕", bg=BG, fg=MUTED, cursor="hand2", font=(FONT, 9))
            x.pack(side="left", padx=(2, 0))
            x.bind("<Button-1>", lambda _e, t=tag: self.delete_tag(t))
            x.bind("<Enter>", lambda _e, w=x: w.configure(fg=DANGER))
            x.bind("<Leave>", lambda _e, w=x: w.configure(fg=MUTED))

    def add_new(self) -> None:
        name = " ".join(self.new_var.get().replace("#", " ").replace(",", " ").split())[:30]
        self.new_var.set("")
        if not name:
            return
        known = {t.lower(): t for t in self.vars}
        if name.lower() in known:
            self.vars[known[name.lower()]].set(True)
            return
        self.vars[name] = tk.BooleanVar(value=True)
        self.initial[name] = False
        self._render()

    def delete_tag(self, tag: str) -> None:
        lib = self.app.lib
        users = [i for i, e in lib.mods.items() if tag.lower() in (t.lower() for t in e.tags)]
        if users and not messagebox.askyesno(tr("tags.title"),
                                             tr("tags.delete_ask", tag=tag, n=len(users)),
                                             parent=self):
            return
        if users:
            lib.update_tags(users, [], [tag])
        del self.vars[tag]
        del self.initial[tag]
        self._render()
        self.app.set_status(tr("tags.deleted", tag=tag))
        self.on_done()

    def save(self) -> None:
        add = [t for t, v in self.vars.items() if v.get() and not self.initial[t]]
        remove = [t for t, v in self.vars.items() if not v.get() and self.initial[t]]
        if add or remove:
            self.app.lib.update_tags(self.ids, add, remove)
        self.destroy()
        self.on_done()


class VersionDialog(tk.Toplevel):
    def __init__(self, app: App, mod_id: int, versions: list[dict],
                 on_choose: Callable[[int, int], None]):
        super().__init__(app)
        self.app = app
        self.mod_id = mod_id
        self.on_choose = on_choose
        self.title(tr("versions.title", name=app.lib.mods[mod_id].name))
        self.configure(bg=BG)
        self.transient(app)
        safe_grab(self)
        f = tk.Frame(self, bg=BG, padx=20, pady=16)
        f.pack(fill="both", expand=True)
        tk.Label(f, text=tr("versions.hint"), bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                 wraplength=px(460), justify="left").pack(fill="x")
        cols = ("version", "date", "size", "state")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=min(12, len(versions)),
                                 selectmode="browse")
        for c, w in zip(cols, (180, 140, 90, 80)):
            self.tree.heading(c, text=tr(f"versions.col_{c}"))
            self.tree.column(c, width=px(w), anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        for v in versions:
            when = time.strftime("%d/%m/%Y", time.localtime(v["date"])) if v["date"] else "-"
            self.tree.insert("", "end", iid=str(v["file_id"]),
                             values=(v["version"], when, human_size(v["size"]),
                                     tr("versions.current") if v["current"] else ""))
        current = next((v for v in versions if v["current"]), versions[0])
        self.tree.selection_set([str(current["file_id"])])
        self.tree.bind("<Double-1>", lambda _e: self.choose())
        btns = tk.Frame(f, bg=BG)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text=tr("versions.install"), style="Accent.TButton",
                   command=self.choose).pack(side="right")
        ttk.Button(btns, text=tr("common.cancel"), command=self.destroy).pack(
            side="right", padx=(0, 8))

    def choose(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        file_id = int(sel[0])
        self.destroy()
        self.on_choose(self.mod_id, file_id)
