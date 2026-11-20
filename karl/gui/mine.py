from __future__ import annotations

import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional, TYPE_CHECKING

from .. import APP_TITLE, game, ui
from ..browse import human_size
from ..entry import SOURCE_FILE, extract_local_paths
from ..i18n import tr
from ..modio import extract_mod_urls
from ..theme import ACCENT, BG, BORDER, FG, FONT, MUTED, ON_ACCENT, PANEL
from ..ui import px
from .common import ASSETS, format_when
from .dialogs import WhatsNewDialog
from .timemachine import TimeMachineDialog
from .troubleshoot import BisectDialog

if TYPE_CHECKING:
    from .app import App


class MineTab(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.lib = app.lib

        top = tk.Frame(self, bg=BG, padx=16, pady=12)
        top.pack(fill="x")
        self.links_frame = top
        tk.Label(top, text=tr("mine.links_label"), bg=BG, fg=FG,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        box = tk.Frame(row, bg=BORDER, padx=1, pady=1)
        box.pack(side="left", fill="x", expand=True)
        self.links = tk.Text(box, height=5, wrap="none", bg=PANEL, fg=FG, insertbackground=FG,
                             relief="flat", bd=0, padx=8, pady=6, font=(FONT, 10),
                             selectbackground=ACCENT, selectforeground=ON_ACCENT,
                             highlightthickness=1, highlightbackground=PANEL,
                             highlightcolor=ACCENT, undo=True)
        self.links.pack(fill="both", expand=True)
        self.links.bind("<Control-Return>", self._ctrl_enter)
        self.links.bind("<FocusIn>", self._paste_if_link)
        self.links.bind("<<Modified>>", self._on_links_changed)

        right = tk.Frame(row, bg=BG)
        right.pack(side="left", fill="y", padx=(8, 0))
        self.add_btn = ttk.Button(right, text=tr("mine.add"), style="Accent.TButton",
                                  command=self.add_mod)
        self.add_btn.pack(fill="x")
        ttk.Button(right, text=tr("mine.add_files"), command=self.add_files).pack(fill="x", pady=(6, 0))
        ttk.Button(right, text=tr("mine.clear"), command=self.clear_links).pack(fill="x", pady=(6, 0))
        self.links_info = tk.Label(top, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                                   justify="left", text=self._hint())
        self.links_info.pack(fill="x", pady=(4, 0))

        bar = tk.Frame(self, bg=BG, padx=16)
        bar.pack(fill="x", pady=(8, 0))
        self.folder_bar = bar
        folder_box = tk.Frame(bar, bg=BG)
        folder_box.pack(side="left", padx=(0, 14))
        tk.Label(folder_box, text=tr("folder.label"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(0, 6))
        self.folder_var = tk.StringVar(value=self.lib.active_folder)
        self.folder_combo = ttk.Combobox(folder_box, textvariable=self.folder_var, width=18,
                                         state="readonly", values=list(self.lib.folders))
        self.folder_combo.pack(side="left")
        self.folder_combo.bind("<<ComboboxSelected>>", lambda _e: self.choose_folder(self.folder_var.get()))
        for key, cmd in (("folder.new", self.new_folder), ("folder.duplicate", self.duplicate_folder),
                         ("folder.rename", self.rename_folder), ("folder.delete", self.delete_folder),
                         ("share.export", self.export_folder), ("share.import", self.import_folder)):
            ttk.Button(folder_box, text=tr(key), command=cmd).pack(side="left", padx=(4, 0))
        self.check_btn = ttk.Button(bar, text=tr("mine.check"), command=self.check_updates)
        self.check_btn.pack(side="left")
        self.apply_btn = ttk.Button(bar, text=tr("mine.apply"), style="Accent.TButton",
                                    command=self.apply_changes)
        self.apply_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=tr("tm.button"), command=self.open_time_machine).pack(
            side="left", padx=(8, 0))
        self.unsaved_lbl = tk.Label(bar, bg=BG, fg=ACCENT, font=(FONT, 9), text="")
        self.unsaved_lbl.pack(side="left", padx=(12, 0))
        filt = tk.Frame(self, bg=BG, padx=16)
        filt.pack(fill="x", pady=(10, 0))
        tk.Label(filt, text="⌕", bg=BG, fg=MUTED, font=(FONT, 12)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(filt, textvariable=self.search_var, width=32)
        self.search_entry.pack(side="left", padx=(6, 0))
        self.search_var.trace_add("write", lambda *_a: self.refresh())
        self.search_entry.bind("<Escape>", lambda _e: self.search_var.set(""))
        tk.Label(filt, text=tr("mine.search_hint"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(8, 0))
        self.show_choices = [tr("mine.show_all"), tr("mine.show_fav")]
        self.show_var = tk.StringVar(value=self.show_choices[0])
        self.show_combo = ttk.Combobox(filt, textvariable=self.show_var,
                                       values=self.show_choices, state="readonly", width=18)
        self.show_combo.pack(side="right")
        self.show_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        tk.Label(filt, text=tr("mine.show_label"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="right", padx=(12, 6))
        self.sort_keys = ["order", "name", "size", "added", "updated"]
        self.sort_labels = [tr(f"mine.sort_{k}") for k in self.sort_keys]
        self.sort_var = tk.StringVar(value=self.sort_labels[0])
        sort_combo = ttk.Combobox(filt, textvariable=self.sort_var, values=self.sort_labels,
                                  state="readonly", width=16)
        sort_combo.pack(side="right")
        sort_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        tk.Label(filt, text=tr("mine.sort_label"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="right", padx=(12, 6))
        self.compact_btn = ttk.Button(filt, command=self.toggle_compact)
        self.compact_btn.pack(side="right")
        footer = tk.Frame(self, bg=BG, padx=16)
        footer.pack(side="bottom", fill="x", pady=(0, 6))
        counts = tk.Frame(footer, bg=BG)
        counts.pack(side="right")
        self.count_lbls = {}
        for key in ("installed", "pending", "updates"):
            box = tk.Frame(counts, bg=PANEL, padx=10, pady=3)
            box.pack(side="left", padx=(6, 0))
            num = tk.Label(box, bg=PANEL, fg=FG, font=(FONT, 11, "bold"), text="0")
            num.pack(side="left")
            tk.Label(box, bg=PANEL, fg=MUTED, font=(FONT, 9),
                     text=tr(f"mine.count_{key}")).pack(side="left", padx=(5, 0))
            self.count_lbls[key] = num

        mid = tk.Frame(self, bg=BG, padx=16, pady=8)
        mid.pack(fill="both", expand=True)
        self.sw_on = ui.photo(ASSETS / "switch_on.png")
        self.sw_off = ui.photo(ASSETS / "switch_off.png")
        self.wanted: dict[int, bool] = {}
        cols = ("nome", "versione", "dimensione", "modalita", "preferito")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", image=self.sw_off, command=self.toggle_all)
        self.tree.column("#0", width=px(64), minwidth=px(64), anchor="center", stretch=False)
        for c, key, w in zip(cols[:4], ("mine.col_name", "mine.col_version", "mine.col_size",
                                        "mine.col_mode"), (420, 150, 90, 110)):
            self.tree.heading(c, text=tr(key))
            self.tree.column(c, width=px(w), anchor="w")
        self.tree.heading("preferito", text="★")
        self.tree.column("preferito", width=px(48), minwidth=px(48), anchor="center",
                         stretch=False)
        self.tree.tag_configure("off", foreground=MUTED)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<B1-Motion>", self._on_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_drop, add="+")
        self._drag: Optional[str] = None
        self._drag_moved = False
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<<TreeviewSelect>>", self.show_note, add="+")
        self.tree.bind("<space>", lambda e: (self.toggle(), "break")[1])
        self.tree.bind("<Delete>", lambda e: (self.remove(), "break")[1])
        self.tree.bind("<Alt-Up>", lambda e: (self._move_selected(-1), "break")[1])
        self.tree.bind("<Alt-Down>", lambda e: (self._move_selected(1), "break")[1])
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        side = tk.Frame(mid, bg=BG, padx=10)
        side.pack(side="left", fill="y")
        for key, cmd in (("mine.remove", self.remove),
                         ("mine.open", self.open_page),
                         ("rollback.button", self.rollback),
                         ("mine.note", self.edit_note),
                         ("tags.button", self.edit_tags)):
            ttk.Button(side, text=tr(key), command=cmd, width=20).pack(fill="x", pady=3)
        tk.Label(side, text=tr("dev.title"), bg=BG, fg=ACCENT, font=(FONT, 9, "bold"),
                 anchor="w").pack(fill="x", pady=(16, 2))
        cfg = self.app.config_
        self.dev_var = tk.BooleanVar(value=cfg.dev_mode)
        self.dev_restart_var = tk.BooleanVar(value=cfg.dev_restart)
        for var, key in ((self.dev_var, "dev.mode"), (self.dev_restart_var, "dev.restart")):
            tk.Checkbutton(side, text=tr(key), variable=var, command=self._dev_settings_changed,
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG, anchor="w", justify="left",
                           wraplength=170).pack(fill="x")
        self.crash_var = tk.BooleanVar(value=cfg.crash_watch)
        tk.Checkbutton(side, text=tr("crash.watch"), variable=self.crash_var,
                       command=self._crash_setting_changed, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, anchor="w", justify="left",
                       wraplength=170).pack(fill="x")
        ttk.Button(side, text=tr("bisect.button"), command=self.open_bisect, width=20).pack(
            fill="x", pady=(8, 0))
        self._dev_job = None
        self._dev_waiting = False
        self._schedule_dev()
        self.compact = False
        self.set_compact(bool(cfg.compact_links), save=False)
        self.update_show_choices()
        self.refresh()

    def set_compact(self, on: bool, save: bool = True):
        if on and not self.compact:
            self.links_frame.pack_forget()
        elif not on and self.compact:
            self.links_frame.pack(fill="x", before=self.folder_bar)
        self.compact = on
        self.compact_btn.configure(text=tr("mine.show_links") if on else tr("mine.hide_links"))
        if save and self.app.config_.compact_links != on:
            self.app.config_.compact_links = on
            self.app.config_.save()

    def toggle_compact(self):
        self.set_compact(not self.compact)

    @staticmethod
    def _hint() -> str:
        return tr("mine.hint") + "\n" + tr("mine.hint_files")

    def links_text(self) -> str:
        return self.links.get("1.0", "end")

    def parse_input(self) -> tuple[list[str], list[str], list[str]]:
        paths, rest = extract_local_paths(self.links_text())
        urls, ignored = extract_mod_urls("\n".join(rest))
        return urls, paths, ignored

    def clear_links(self):
        self.links.delete("1.0", "end")
        self._on_links_changed()

    def _paste_if_link(self, _e=None):
        if self.links_text().strip():
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            return
        if extract_mod_urls(clip)[0]:
            self.links.insert("1.0", clip.strip() + "\n")
            self._on_links_changed()

    def _update_tab_title(self, count: int):
        try:
            self.app.notebook.tab(self, text=tr("tab.mine") + (f" ({count})" if count else ""))
        except (tk.TclError, AttributeError):
            pass

    def append_link(self, url: str) -> bool:
        urls, _paths, _ignored = self.parse_input()
        new = extract_mod_urls(url)[0]
        if not new or new[0] in urls:
            return False
        if self.compact:
            self.set_compact(False)
        text = self.links_text().rstrip("\n")
        if text.strip():
            self.links.insert("end", "\n" + url)
        else:
            self.links.delete("1.0", "end")
            self.links.insert("1.0", url)
        self._on_links_changed()
        return True

    def _on_links_changed(self, _e=None):
        self.links.edit_modified(False)
        urls, paths, ignored = self.parse_input()
        total = len(urls) + len(paths)
        self._update_tab_title(total)
        if not total and not ignored:
            self.links_info.configure(text=self._hint(), fg=MUTED)
            self.add_btn.configure(text=tr("mine.add"))
            return
        msg = tr("mine.ready", n=total)
        if ignored:
            msg += " · " + tr("mine.ignored", n=len(ignored))
        self.links_info.configure(text=msg, fg=FG if total else "#e05a4f")
        self.add_btn.configure(text=tr("mine.add_n", n=total) if total > 1 else tr("mine.add"))

    def _ctrl_enter(self, _e):
        self.add_mod()
        return "break"

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        mods = self.visible_mods()
        missing = self.lib.missing_dependencies(self.wanted)
        clashes = self.lib.conflicts(self.wanted)
        for m in mods:
            on = self.is_on(m)
            ver = m.version or "-"
            if m.hold:
                ver = f"{ver} 🔒"
            if m.has_update:
                ver = f"{ver} → {m.latest_version}"
            label = (m.name + (" ✎" if m.note else "") + (" ⚠" if m.mod_id in missing else "")
                     + (" ⇄" if m.mod_id in clashes else "") + (" ⊘" if m.removed else "")
                     + (f"   [{', '.join(m.tags)}]" if m.tags else ""))
            self.tree.insert("", "end", iid=str(m.mod_id), text="",
                             image=self.sw_on if on else self.sw_off,
                             tags=() if on else ("off",),
                             values=(label, ver, human_size(self.lib.mod_size(m)),
                                     tr("mode.file") if m.source == SOURCE_FILE
                                     else (m.approval or "-"),
                                     "★" if m.favorite else "☆"))
        keep = [i for i in selected if self.tree.exists(i)] if selected else []
        if keep:
            self.tree.selection_set(keep)
        all_on = bool(mods) and all(self.is_on(m) for m in mods)
        self.tree.heading("#0", image=self.sw_on if all_on else self.sw_off)
        self._update_unsaved()

    def only_favorites(self) -> bool:
        return self.show_var.get() == self.show_choices[1]

    def search_text(self) -> str:
        return " ".join(self.search_var.get().lower().split())

    def selected_tag(self) -> Optional[str]:
        value = self.show_var.get()
        for t in self.lib.all_tags():
            if value == tr("mine.show_tag", tag=t):
                return t
        return None

    def sort_key(self) -> str:
        value = self.sort_var.get()
        return self.sort_keys[self.sort_labels.index(value)] if value in self.sort_labels \
            else "order"

    def update_show_choices(self):
        choices = [tr("mine.show_all"), tr("mine.show_fav")] + [
            tr("mine.show_tag", tag=t) for t in self.lib.all_tags()]
        self.show_choices = choices
        self.show_combo.configure(values=choices)
        if self.show_var.get() not in choices:
            self.show_var.set(choices[0])

    def filtered(self) -> bool:
        return (self.only_favorites() or bool(self.search_text())
                or self.selected_tag() is not None or self.sort_key() != "order")

    def visible_mods(self) -> list:
        mods = self.lib.folder_mods()
        if self.only_favorites():
            mods = [m for m in mods if m.favorite]
        tag = self.selected_tag()
        if tag is not None:
            mods = [m for m in mods if tag.lower() in (t.lower() for t in m.tags)]
        words = self.search_text().split()
        if words:
            mods = [m for m in mods if all(
                w in f"{m.name} {m.slug} {m.note} {' '.join(m.tags)}".lower() for w in words)]
        key = self.sort_key()
        if key == "name":
            mods = sorted(mods, key=lambda m: m.name.lower())
        elif key == "size":
            mods = sorted(mods, key=lambda m: -self.lib.mod_size(m))
        elif key == "added":
            mods = sorted(mods, key=lambda m: -m.added_at)
        elif key == "updated":
            mods = sorted(mods, key=lambda m: -m.updated_at)
        return mods

    def is_on(self, m) -> bool:
        return self.wanted.get(m.mod_id, m.enabled)

    def counts(self) -> dict[str, int]:
        mods = self.lib.folder_mods()
        installed = sum(1 for m in mods if m.enabled and m.installed_paks)
        missing = sum(1 for m in mods if m.mod_id not in self.wanted and m.enabled
                      and (not m.installed_paks or self.lib.needs_reorder(m)))
        updates = sum(1 for m in mods if m.has_update)
        return {"installed": installed, "pending": len(self.wanted) + missing, "updates": updates}

    def _update_unsaved(self):
        n = len(self.wanted)
        self.unsaved_lbl.configure(text=tr("mine.unsaved", n=n) if n else "")
        self.apply_btn.configure(text=tr("mine.apply_n", n=n) if n else tr("mine.apply"))
        for key, value in self.counts().items():
            hot = value > 0 and key != "installed"
            self.count_lbls[key].configure(text=str(value), fg=ACCENT if hot else FG)

    def _on_click(self, e):
        region = self.tree.identify_region(e.x, e.y)
        if region in ("tree", "cell") and self.tree.identify_column(e.x) == "#5":
            row = self.tree.identify_row(e.y)
            if row and int(row) in self.lib.mods:
                self.lib.set_favorite([int(row)])
                self.refresh()
            return "break"
        if region in ("tree", "cell") and self.tree.identify_column(e.x) != "#0":
            self._drag = self.tree.identify_row(e.y) or None
            self._drag_moved = False
        if region not in ("tree", "cell") or self.tree.identify_column(e.x) != "#0":
            return None
        row = self.tree.identify_row(e.y)
        if row:
            mid = int(row)
            if mid in self.lib.mods:
                self.set_states({mid: not self.is_on(self.lib.mods[mid])})
        return "break"

    def _on_drag(self, e):
        if not self._drag or self.app.busy:
            return
        if self.filtered():
            self.app.set_status(tr("mine.order_all_hint"))
            return
        target = self.tree.identify_row(e.y)
        if target and target != self._drag:
            self.tree.move(self._drag, "", self.tree.index(target))
            self._drag_moved = True

    def _on_drop(self, _e):
        drag, moved = self._drag, self._drag_moved
        self._drag, self._drag_moved = None, False
        if drag and moved:
            self.apply_order([int(i) for i in self.tree.get_children()])

    def _move_selected(self, delta: int):
        sel = self.selected_ids()
        if self.filtered():
            self.app.set_status(tr("mine.order_all_hint"))
            return
        if len(sel) == 1:
            self.move_mod(sel[0], delta)

    def apply_order(self, ids: list[int]):
        self.lib.set_order(ids)
        self.refresh()
        self.app.set_status(tr("order.changed"))

    def move_mod(self, mod_id: int, delta: int):
        ids = [m.mod_id for m in self.lib.folder_mods()]
        if mod_id not in ids:
            return
        i = ids.index(mod_id)
        j = max(0, min(len(ids) - 1, i + delta))
        if i != j:
            ids.insert(j, ids.pop(i))
            self.apply_order(ids)
            self.tree.selection_set([str(mod_id)])

    def _on_double(self, e):
        if self.tree.identify_column(e.x) in ("#0", "#5"):
            return "break"
        row = self.tree.identify_row(e.y)
        if row:
            self.tree.selection_set([row])
            self.open_details()
        return "break"

    def open_details(self):
        ids = self.selected_ids()
        if len(ids) != 1 or ids[0] not in self.lib.mods:
            return
        e = self.lib.mods[ids[0]]
        if e.source == SOURCE_FILE:
            self.app.set_status(tr("details.local_file", name=e.name))
            return
        self.app.open_details(e.game_id, e.mod_id, name=e.name)

    def toggle_all(self):
        mods = self.visible_mods()
        if not mods:
            return
        target = not all(self.is_on(m) for m in mods)
        self.set_states({m.mod_id: target for m in mods})

    def set_states(self, wanted: dict[int, bool]):
        if self.app.busy:
            return
        for i, on in wanted.items():
            if i not in self.lib.mods:
                continue
            if on == self.lib.mods[i].enabled:
                self.wanted.pop(i, None)
            else:
                self.wanted[i] = on
        self.refresh()

    def play(self):
        if self.app.busy:
            return
        if game.is_running():
            self.app.set_status(tr("play.running"))
            return
        if self.counts()["pending"]:
            app, wanted = self.app, dict(self.wanted)

            def work():
                report = self.lib.apply(wanted, progress=app.on_progress, log=app.log)
                app.post(self._after_play_apply, report)

            app.run(tr("play.preparing"), work)
        else:
            self._launch()

    def _after_play_apply(self, report: dict):
        self.wanted.clear()
        self._after_update(report)
        if report["failed"]:
            return
        self._launch()

    def _launch(self):
        try:
            game.launch()
        except Exception as err:
            self.app.show_error(str(err))
            return
        self.app.set_status(tr("play.started"))
        self.app.start_crash_watch()

    def _crash_setting_changed(self):
        cfg = self.app.config_
        cfg.crash_watch = bool(self.crash_var.get())
        cfg.save()

    def open_bisect(self):
        if self.app.busy:
            return
        win = getattr(self.app, "bisect_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        if self.wanted:
            messagebox.showwarning(APP_TITLE, tr("bisect.unsaved"))
            return
        on = [m.mod_id for m in self.lib.folder_mods() if m.enabled]
        if len(on) < 2:
            messagebox.showinfo(APP_TITLE, tr("bisect.too_few"))
            return
        self.app.bisect_win = BisectDialog(self.app)

    VANILLA_POLL_MS = 5000
    VANILLA_START_WAIT_MS = 180000

    def play_vanilla(self):
        if self.app.busy:
            return
        if game.is_running():
            self.app.set_status(tr("play.running"))
            return
        if self.wanted and not messagebox.askyesno(APP_TITLE, tr("folder.discard")):
            return
        self.wanted.clear()
        app = self.app

        def work():
            self.lib.start_vanilla()
            app.post(self._vanilla_launch)

        app.run(tr("vanilla.preparing"), work)

    def _vanilla_launch(self):
        try:
            game.launch()
        except Exception as err:
            self.app.show_error(str(err))
        self.app.set_status(tr("vanilla.started"))
        self._vanilla_seen = False
        self._vanilla_waited = 0
        self.after(self.VANILLA_POLL_MS, self._vanilla_tick)

    def resume_vanilla(self):
        if not self.app.config_.vanilla_active:
            return
        self._vanilla_seen = True
        self._vanilla_waited = 0
        self._vanilla_tick()

    def _vanilla_tick(self):
        if not self.app.config_.vanilla_active:
            return

        def done(running):
            if running:
                self._vanilla_seen = True
                self.app.set_status(tr("vanilla.started"))
            elif self._vanilla_seen or self._vanilla_waited >= self.VANILLA_START_WAIT_MS:
                self.end_vanilla()
                return
            self._vanilla_waited += self.VANILLA_POLL_MS
            self.after(self.VANILLA_POLL_MS, self._vanilla_tick)

        self.app.submit(game.is_running, done,
                        lambda err: self.after(self.VANILLA_POLL_MS, self._vanilla_tick))

    def end_vanilla(self):
        if self.app.busy:
            self.after(self.VANILLA_POLL_MS, self.end_vanilla)
            return
        app = self.app

        def work():
            report = self.lib.end_vanilla(progress=app.on_progress, log=app.log)
            app.post(self._after_vanilla, report)

        app.run(tr("vanilla.restoring"), work)

    def _after_vanilla(self, report: dict):
        self._after_switch(report)
        self.app.set_status(tr("vanilla.restored"))

    def choose_folder_from_tray(self, name: str):
        self.app.bring_to_front()
        self.choose_folder(name)

    def edit_note(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        e = self.lib.mods[ids[0]]
        text = simpledialog.askstring(APP_TITLE, tr("mine.note_ask", name=e.name),
                                      initialvalue=e.note, parent=self)
        if text is None:
            return
        self.lib.set_note(e.mod_id, text)
        self.refresh()
        self.show_note()

    def edit_tags(self):
        ids = [i for i in self.selected_ids() if i in self.lib.mods]
        if not ids:
            return
        first = self.lib.mods[ids[0]]
        prompt = tr("tags.ask", name=first.name) if len(ids) == 1 else tr("tags.ask_many",
                                                                            n=len(ids))
        known = self.lib.all_tags()
        if known:
            prompt += "\n\n" + tr("tags.known", tags=", ".join(known))
        text = simpledialog.askstring(APP_TITLE, prompt, initialvalue=", ".join(first.tags),
                                      parent=self)
        if text is None:
            return
        self.lib.set_tags(ids, text)
        self.update_show_choices()
        self.refresh()

    def show_note(self, _event=None):
        ids = self.selected_ids()
        if len(ids) != 1 or ids[0] not in self.lib.mods:
            return
        missing = self.lib.missing_dependencies(self.wanted).get(ids[0])
        if missing:
            self.app.set_status(tr("deps.status", name=self.lib.mods[ids[0]].name,
                                   deps=", ".join(d["name"] for d in missing)))
            return
        if self.lib.mods[ids[0]].removed:
            self.app.set_status(tr("removed.status", name=self.lib.mods[ids[0]].name))
            return
        clashes = self.lib.conflicts(self.wanted).get(ids[0])
        if clashes:
            mods = self.lib.mods
            items = ", ".join(tr("conflict.item", name=mods[c["id"]].name, n=c["files"],
                                 winner=mods[c["winner"]].name) for c in clashes[:4])
            if len(clashes) > 4:
                items += " …"
            self.app.set_status(tr("conflict.status", name=mods[ids[0]].name, mods=items))
        elif self.lib.mods[ids[0]].note:
            self.app.set_status(tr("mine.note_status", note=self.lib.mods[ids[0]].note))

    def rollback(self):
        ids = self.selected_ids()
        if not ids or self.app.busy:
            return
        no_prev = [self.lib.mods[i].name for i in ids if not self.lib.has_previous(i)]
        if no_prev:
            self.app.show_error(tr("rollback.none_for", names=", ".join(no_prev)))
            return

        def work():
            for i in ids:
                self.lib.rollback(i, log=self.app.log)

        self.app.run(tr("rollback.working"), work)

    def _dev_settings_changed(self):
        cfg = self.app.config_
        cfg.dev_mode = bool(self.dev_var.get())
        cfg.dev_restart = bool(self.dev_restart_var.get())
        cfg.save()
        self._dev_waiting = False
        self.app.set_status(tr("dev.on" if cfg.dev_mode else "dev.off"))
        self._schedule_dev()

    def _schedule_dev(self):
        if self._dev_job:
            try:
                self.after_cancel(self._dev_job)
            except Exception:
                pass
            self._dev_job = None
        if self.app.config_.dev_mode:
            self._dev_job = self.after(2000, self.dev_tick)

    def dev_tick(self):
        self._dev_job = None
        try:
            if self.app.config_.dev_mode and not self.app.busy and \
                    not self.app.config_.vanilla_active and self.lib.changed_files():
                running = game.is_running()
                if running and not self.app.config_.dev_restart:
                    if not self._dev_waiting:
                        self._dev_waiting = True
                        self.app.set_status(tr("dev.close_game"))
                else:
                    self._dev_waiting = False
                    restart = running and self.app.config_.dev_restart
                    app = self.app

                    def work():
                        if restart:
                            game.close()
                            time.sleep(3)
                        self.lib.reimport_changed(log=app.log)
                        if restart:
                            game.launch()

                    app.run(tr("dev.updating"), work)
        finally:
            self._schedule_dev()

    def export_folder(self):
        code, skipped = self.lib.export_folder()
        self.clipboard_clear()
        self.clipboard_append(code)
        msg = tr("share.exported", name=self.lib.active_folder)
        if skipped:
            msg += "\n\n" + tr("share.skipped", n=skipped)
        messagebox.showinfo(APP_TITLE, msg)
        self.app.set_status(tr("share.copied"))

    def import_folder(self):
        if self.app.busy:
            return
        code = simpledialog.askstring(APP_TITLE, tr("share.ask_code"), parent=self)
        if not code or not code.strip():
            return
        try:
            self.lib.parse_share_code(code)
        except Exception as err:
            self.app.show_error(str(err))
            return
        if self.wanted and not messagebox.askyesno(APP_TITLE, tr("folder.discard")):
            return
        self.wanted.clear()
        app = self.app
        result: dict = {}

        def change():
            result["name"], result["failed"] = self.lib.import_folder(
                code, progress=app.on_progress, log=app.log)
            if result["failed"]:
                app.post(app.show_error, tr("share.failed", names=", ".join(result["failed"])))

        self._switch_and_apply(change)

    def _refresh_folders(self):
        self.folder_combo.configure(values=list(self.lib.folders))
        self.folder_var.set(self.lib.active_folder)
        tray = getattr(self.app, "tray", None)
        if tray is not None:
            tray.refresh()

    def _ask_name(self, title_key: str, initial: str = "") -> Optional[str]:
        name = simpledialog.askstring(APP_TITLE, tr(title_key), initialvalue=initial, parent=self)
        return name.strip() if name and name.strip() else None

    def choose_folder(self, name: str):
        if name == self.lib.active_folder or self.app.busy:
            self._refresh_folders()
            return
        if self.wanted and not messagebox.askyesno(APP_TITLE, tr("folder.discard")):
            self._refresh_folders()
            return
        self.wanted.clear()
        self._switch_and_apply(lambda: self.lib.switch_folder(name))

    def _switch_and_apply(self, change: Callable[[], object]):
        app = self.app

        def work():
            change()
            app.post(self._refresh_folders)
            app.post(self.refresh)
            report = self.lib.update_all(progress=app.on_progress, log=app.log)
            app.post(self._after_switch, report)

        app.run(tr("folder.switching"), work)

    def open_time_machine(self):
        if self.app.busy:
            return
        TimeMachineDialog(self)

    def restore_snapshot(self, snap_id: str):
        if self.app.busy:
            return
        tm = self.lib.time_machine
        snap = tm.get(snap_id)
        when = format_when(snap["time"])
        if self.wanted and not messagebox.askyesno(APP_TITLE, tr("folder.discard")):
            return
        self.wanted.clear()
        app = self.app

        def work():
            missing = tm.restore(snap_id)
            app.post(self._refresh_folders)
            app.post(self.refresh)
            report = self.lib.update_all(progress=app.on_progress, log=app.log)
            app.post(self._after_restore, report, when, missing)

        app.run(tr("tm.restoring"), work)

    def _after_restore(self, report: dict, when: str, missing: list):
        self._after_update(report)
        if not report["failed"]:
            self.app.set_status(tr("tm.restored", when=when))
        if missing:
            messagebox.showwarning(APP_TITLE, tr("tm.missing", names=", ".join(missing)))
        self.app.check_integration()

    def _after_switch(self, report: dict):
        self._after_update(report)
        if not report["failed"]:
            self.app.set_status(tr("folder.active", name=self.lib.active_folder))
        self.app.check_integration()

    def new_folder(self, copy_current: bool = False):
        if self.app.busy:
            return
        name = self._ask_name("folder.ask_name")
        if not name:
            return
        try:
            name = self.lib.create_folder(name, copy_current=copy_current)
        except Exception as err:
            self.app.show_error(str(err))
            return
        self._refresh_folders()
        self.choose_folder(name)

    def duplicate_folder(self):
        self.new_folder(copy_current=True)

    def rename_folder(self):
        if self.app.busy:
            return
        old = self.lib.active_folder
        name = self._ask_name("folder.ask_name", initial=old)
        if not name or name == old:
            return
        try:
            self.lib.rename_folder(old, name)
        except Exception as err:
            self.app.show_error(str(err))
            return
        self._refresh_folders()

    def delete_folder(self):
        if self.app.busy:
            return
        name = self.lib.active_folder
        if len(self.lib.folders) == 1:
            self.app.show_error(tr("folder.last"))
            return
        if not messagebox.askyesno(APP_TITLE, tr("folder.confirm_delete", name=name)):
            return
        self.wanted.clear()
        self._switch_and_apply(lambda: self.lib.delete_folder(name))

    def check_updates(self):
        app = self.app

        def work():
            ids = [m.mod_id for m in self.lib.folder_mods()]
            report = self.lib.find_updates(progress=app.on_progress, log=app.log, ids=ids)
            app.post(self._after_check, report)

        app.run(tr("mine.checking_updates"), work)

    def _after_check(self, report: dict):
        self.refresh()
        n = len(report["updates"])
        self.app.set_status(tr("mine.updates_found", n=n) if n else tr("mine.no_updates"))
        if report["failed"]:
            lines = "\n".join(f"• {name}: {err}" for name, err in report["failed"][:15])
            self.app.show_error(f"{tr('mine.update_failed_title')}\n\n{lines}")

    def apply_changes(self):
        app = self.app
        wanted = dict(self.wanted)
        newly_on = [i for i, on in wanted.items() if on]

        def work():
            report = self.lib.apply(wanted, progress=app.on_progress, log=app.log)
            app.post(self._after_apply, report, newly_on)

        app.run(tr("mine.saving"), work)

    def _after_apply(self, report: dict, newly_on: list[int]):
        self.wanted.clear()
        self._after_update(report)
        if not report["failed"]:
            self.app.set_status(tr("mine.saved"))
        self.app.check_modio_subscriptions(force=True)
        self.app.check_integration()

    def selected_ids(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def add_mod(self):
        urls, paths, _ignored = self.parse_input()
        if not urls and not paths:
            if self.links_text().strip():
                self.app.show_error(tr("mine.no_valid_links"))
            return
        if self.app.busy:
            return
        self.clear_links()
        items = urls + paths
        self.app.run(tr("mine.adding", n=len(items)), lambda: self._add_many(items))

    def add_files(self):
        if self.app.busy:
            return
        files = filedialog.askopenfilenames(
            parent=self, title=tr("mine.files_title"),
            filetypes=[(tr("mine.files_filter"), "*.pak *.zip"), (tr("mine.files_all"), "*.*")])
        files = [f for f in (files or ()) if f]
        if files:
            self.app.run(tr("mine.adding", n=len(files)), lambda: self._add_many(files))

    def _add_many(self, items: list[str]):
        app = self.app
        added, failed = [], []
        for n, item in enumerate(items, 1):
            is_file = not item.lower().startswith(("http://", "https://"))
            label = Path(item).name if is_file else item.rsplit("/", 1)[-1]
            app.log(tr("mine.adding_one", i=n, n=len(items), name=label))
            try:
                if is_file:
                    e = self.lib.add_local_file(item)
                else:
                    e = self.lib.add(item, progress=app.on_progress, log=lambda s: None)
            except Exception as err:
                failed.append((item, str(err)))
                continue
            added.append(e.name)
            app.post(self.refresh)
        added_ids = [self.lib.mods[i].mod_id for i in list(self.lib.mods)
                     if self.lib.mods[i].name in added]
        missing = self.lib.missing_dependencies()
        need = {self.lib.mods[i].name: missing[i] for i in added_ids if i in missing}
        if need:
            app.post(self._warn_missing_deps, need)
        summary = tr("mine.added_summary", ok=len(added), n=len(items))
        if failed:
            summary += " " + tr("mine.failed_count", n=len(failed))
        app.log(summary)
        if failed:
            app.post(self._report_failed, failed)

    def _warn_missing_deps(self, need: dict[str, list[dict]]):
        lines = []
        for name, deps in list(need.items())[:10]:
            lines.append(f"• {name}: " + ", ".join(d["name"] for d in deps))
        links = sorted({d["url"] for deps in need.values() for d in deps if d.get("url")})
        text = tr("deps.warning", mods="\n".join(lines))
        if links:
            text += "\n\n" + tr("deps.links") + "\n" + "\n".join(links[:10])
        messagebox.showwarning(APP_TITLE, text)

    def _report_failed(self, failed: list[tuple[str, str]]):
        self.links.insert("1.0", "\n".join(u for u, _ in failed) + "\n")
        self._on_links_changed()
        lines = "\n".join(f"• {Path(u).name if not u.startswith('http') else u.rsplit('/', 1)[-1]}: {e}"
                          for u, e in failed[:15])
        more = "\n" + tr("mine.failed_more", n=len(failed) - 15) if len(failed) > 15 else ""
        self.app.show_error(f"{tr('mine.failed_title')}\n\n{lines}{more}\n\n"
                            f"{tr('mine.failed_retry')}")

    def toggle(self):
        self.set_states({i: not self.is_on(self.lib.mods[i])
                         for i in self.selected_ids() if i in self.lib.mods})

    def remove(self):
        ids = self.selected_ids()
        if not ids or not messagebox.askyesno(APP_TITLE, tr("mine.confirm_remove", n=len(ids))):
            return

        def work():
            for i in ids:
                self.lib.remove(i, log=self.app.log)
                self.wanted.pop(i, None)
        self.app.run(tr("mine.removing"), work)

    def update_mods(self):
        app = self.app

        def work():
            report = self.lib.update_all(progress=app.on_progress, log=app.log)
            app.post(self._after_update, report)

        app.run(tr("mine.checking_updates"), work)

    def install_pending(self):
        if self.lib.needs_install() and not self.app.busy:
            self.update_mods()

    def _after_update(self, report: dict):
        self.refresh()
        if report["failed"]:
            lines = "\n".join(f"• {name}: {err}" for name, err in report["failed"][:15])
            more = ("\n" + tr("mine.failed_more", n=len(report["failed"]) - 15)
                    if len(report["failed"]) > 15 else "")
            self.app.show_error(f"{tr('mine.update_failed_title')}\n\n{lines}{more}")
        if report.get("changes") and self.app.config_.show_whats_new:
            WhatsNewDialog(self.app, report["changes"])

    def open_page(self):
        for i in self.selected_ids():
            if self.lib.mods[i].url:
                webbrowser.open(self.lib.mods[i].url)
