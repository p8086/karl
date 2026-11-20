from __future__ import annotations

import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import TYPE_CHECKING

from ..bisect import Bisect
from ..i18n import tr
from ..theme import ACCENT, BG, FG, FONT, MUTED
from ..ui import px
from .common import fill_text, format_when, text_box
from .timemachine import history_text

if TYPE_CHECKING:
    from .app import App


class CrashDialog(tk.Toplevel):
    def __init__(self, app: App, found: dict):
        super().__init__(app)
        self.app = app
        self.title(tr("crash.title"))
        self.configure(bg=BG, padx=16, pady=14)
        self.geometry(f"{px(640)}x{px(480)}")
        self.transient(app)
        tk.Label(self, text=tr("crash.title"), bg=BG, fg="#e05a4f", font=(FONT, 13, "bold"),
                 anchor="w").pack(fill="x")
        when = datetime.fromtimestamp(found.get("time") or time.time()).strftime("%H:%M")
        kind = tr("crash.kind_ue4ss") if found.get("ue4ss") else tr("crash.kind_game")
        tk.Label(self, text=tr("crash.intro", when=when, kind=kind), bg=BG, fg=MUTED,
                 font=(FONT, 9), anchor="w", justify="left", wraplength=px(600)).pack(
            fill="x", pady=(4, 8))
        self.text = text_box(self, height=10)
        lib = app.lib
        changes = lib.history.changes_since_good(lib.active_folder)
        chunks: list[tuple[str, str]] = []
        if found.get("detail"):
            chunks.append((tr("crash.error") + "\n", "title"))
            chunks.append((found["detail"] + "\n\n", "warn"))
        chunks.append((tr("crash.changes") + "\n", "title"))
        if changes:
            for e in changes[-20:]:
                chunks.append((f"{format_when(datetime.fromtimestamp(e['t']).isoformat())}  ",
                               "muted"))
                chunks.append((history_text(e) + "\n", ""))
        else:
            chunks.append((tr("crash.no_changes") + "\n", "muted"))
        fill_text(self.text, chunks)
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text=tr("crash.find"), style="Accent.TButton",
                   command=self.find).pack(side="left")
        ttk.Button(btns, text=tr("crash.time_machine"), command=self.time_machine).pack(
            side="left", padx=(6, 0))
        ttk.Button(btns, text=tr("crash.ignore"), command=self.destroy).pack(side="right")

    def find(self):
        self.destroy()
        self.app.notebook.select(self.app.mine)
        self.app.mine.open_bisect()

    def time_machine(self):
        self.destroy()
        self.app.notebook.select(self.app.mine)
        self.app.mine.open_time_machine()

class BisectDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        lib = app.lib
        self.lib = lib
        self.folder = lib.active_folder
        mods = lib.folder_mods()
        self.original = {m.mod_id: m.enabled for m in mods}
        candidates = [m.mod_id for m in mods if m.enabled]
        deps = {m.mod_id: [d["id"] for d in (m.deps or [])] for m in mods}
        self.bs = Bisect(candidates, deps)
        self.finished = False
        cfg = app.config_
        cfg.bisect_restore = {str(k): v for k, v in self.original.items()}
        cfg.save()
        lib.log_event("bisect", text=str(len(candidates)))
        lib.quiet_toggles = True
        self.title(tr("bisect.title"))
        self.configure(bg=BG, padx=16, pady=14)
        self.geometry(f"{px(660)}x{px(560)}")
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        tk.Label(self, text=tr("bisect.title"), bg=BG, fg=FG, font=(FONT, 13, "bold"),
                 anchor="w").pack(fill="x")
        self.info = tk.Label(self, text=tr("bisect.intro", n=len(candidates),
                                           steps=self.bs.estimate),
                             bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                             wraplength=px(620))
        self.info.pack(fill="x", pady=(4, 6))
        self.progress = tk.Label(self, text="", bg=BG, fg=ACCENT, font=(FONT, 10, "bold"),
                                 anchor="w")
        self.progress.pack(fill="x")
        self.alert = tk.Label(self, text="", bg=BG, fg="#e05a4f", font=(FONT, 9, "bold"),
                              anchor="w", justify="left", wraplength=px(620))
        self.alert.pack(fill="x")
        self.text = text_box(self, height=12)
        self.btns = tk.Frame(self, bg=BG)
        self.btns.pack(fill="x", pady=(10, 0))
        self._buttons([("bisect.start", self.next_test, True)])
        fill_text(self.text, [(tr("bisect.how") + "\n", "")])

    def _buttons(self, spec):
        for w in self.btns.winfo_children():
            w.destroy()
        for key, cmd, accent in spec:
            ttk.Button(self.btns, text=tr(key), command=cmd,
                       style="Accent.TButton" if accent else "TButton").pack(
                side="left", padx=(0, 6))
        ttk.Button(self.btns, text=tr("bisect.cancel"), command=self.cancel).pack(side="right")

    def _names(self, ids) -> list[str]:
        return [self.lib.mods[i].name for i in ids if i in self.lib.mods]

    def _apply(self, states: dict[int, bool], then):
        def work():
            report = self.lib.apply({i: on for i, on in states.items() if i in self.lib.mods},
                                    progress=self.app.on_progress, log=self.app.log)
            self.app.post(self._applied, report, then)

        self.app.run(tr("bisect.preparing"), work)

    def _applied(self, report: dict, then):
        if report.get("failed"):
            self.app.mine._after_update(report)
            return
        if self.winfo_exists():
            then()

    def next_test(self):
        self.alert.configure(text="")
        self._apply(self.bs.states(), self._show_test)

    def _show_test(self):
        bs = self.bs
        on = [m for m in bs.all if m in bs.on]
        off = [m for m in bs.all if m not in bs.on]
        self.progress.configure(text=tr("bisect.step", i=bs.step + 1, n=bs.estimate,
                                        k=len(bs.suspects)))
        chunks = [(tr("bisect.test") + "\n\n", ""),
                  (tr("bisect.on", n=len(on)) + "\n", "title"),
                  (", ".join(self._names(on)) + "\n\n", ""),
                  (tr("bisect.off", n=len(off)) + "\n", "title"),
                  (", ".join(self._names(off)) + "\n", "muted")]
        fill_text(self.text, chunks)
        self._buttons([("bisect.launch", self.launch, True),
                       ("bisect.broken", lambda: self.answer(True), False),
                       ("bisect.works", lambda: self.answer(False), False)])

    def launch(self):
        self.app.mine._launch()

    def crash_detected(self, found: dict):
        if self.winfo_exists():
            self.alert.configure(text=tr("bisect.crash_seen"))

    def answer(self, broken: bool):
        if self.app.busy:
            return
        self.bs.answer(broken)
        if self.bs.done:
            self.show_result()
        else:
            self.next_test()

    def show_result(self):
        bs = self.bs
        names = self._names(bs.result())
        req = self._names(bs.required_by_result())
        self.progress.configure(text=tr("bisect.found_title"))
        chunks = [(tr("bisect.found", name=", ".join(names)) + "\n\n", "title")]
        if req:
            chunks.append((tr("bisect.found_req", mods=", ".join(req)) + "\n\n", "muted"))
        chunks.append((tr("bisect.found_hint") + "\n", ""))
        fill_text(self.text, chunks)
        self.lib.log_event("culprit", name=", ".join(names))
        self._buttons([("bisect.disable", self.disable_culprit, True),
                       ("bisect.restore", self.restore_all, False)])

    def disable_culprit(self):
        states = dict(self.original)
        for m in self.bs.result():
            states[m] = False
        self._apply(states, self.finish)

    def restore_all(self):
        self._apply(dict(self.original), self.finish)

    def cancel(self):
        if self.app.busy:
            return
        if self.finished:
            self.destroy()
            return
        self.restore_all()

    def finish(self):
        self.finished = True
        self.lib.quiet_toggles = False
        cfg = self.app.config_
        cfg.bisect_restore = {}
        cfg.save()
        self.app.bisect_win = None
        self.app.refresh_mine()
        self.app.set_status(tr("bisect.done"))
        self.destroy()
