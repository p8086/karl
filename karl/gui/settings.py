from __future__ import annotations

import os
import shutil
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from .. import APP_TITLE, i18n, tray as karl_tray, ui
from ..browse import Choice
from ..config import Config, find_paks_dir
from ..i18n import human_date, tr
from ..modio import mask_token, token_expiry
from ..paths import install_dirs
from ..theme import ACCENT, BG, BORDER, DANGER, FG, FONT, MUTED, PANEL, SUCCESS
from ..widgets import check_button, flat_button, set_button_style
from .common import TOKEN_PAGE, remember_me
from .dialogs import CacheDialog, LoginDialog

if TYPE_CHECKING:
    from .app import App


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title(tr("settings.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        cfg = app.config_

        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)

        self.token_box = tk.Frame(f, bg=PANEL, padx=14, pady=12, highlightthickness=1,
                                  highlightbackground=BORDER)
        self.token_box.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 14))
        self.show_token = False
        self._build_token_section()

        self._build_language(f, cfg)
        self._build_installs(f, cfg)
        self._build_misc(f, cfg)
        self._build_maintenance(f, cfg)
        self._build_tools(f)

        ttk.Button(f, text=tr("settings.save"), style="Accent.TButton", command=self.save).grid(
            row=19, column=1, sticky="e", pady=(16, 0))

    def _build_language(self, f: ttk.Frame, cfg: Config) -> None:
        ttk.Label(f, text=tr("settings.language")).grid(row=1, column=0, sticky="w")
        self.language = Choice([i18n.AUTO] + i18n.CODES, "", cfg.language or i18n.AUTO,
                               label=lambda c: tr("settings.language_auto") if c == i18n.AUTO
                               else i18n.language_name(c))
        ttk.Combobox(f, textvariable=self.language.var, state="readonly", width=34,
                     values=self.language.labels(), height=18).grid(row=2, column=0, sticky="w")

    def _build_installs(self, f: ttk.Frame, cfg: Config) -> None:
        cfg.ensure_installs()
        cfg.store_active()
        self.installs = [dict(i) for i in cfg.installs]
        self.removed_installs: list[str] = []
        self.sel_install = cfg.active_install
        ttk.Label(f, text=tr("settings.install")).grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.install_var = tk.StringVar()
        self.install_combo = ttk.Combobox(f, textvariable=self.install_var, state="readonly",
                                          width=44)
        self.install_combo.grid(row=4, column=0, sticky="we")
        self.install_combo.bind("<<ComboboxSelected>>", lambda _e: self._pick_install())
        ib = ttk.Frame(f)
        ib.grid(row=4, column=1, sticky="e", padx=(6, 0))
        ttk.Button(ib, text=tr("install.add"), command=self.add_install).pack(side="left")
        ttk.Button(ib, text=tr("install.rename"), command=self.rename_install).pack(
            side="left", padx=(4, 0))
        ttk.Button(ib, text=tr("install.remove"), command=self.remove_install).pack(
            side="left", padx=(4, 0))

        ttk.Label(f, text=tr("settings.paks")).grid(row=5, column=0, sticky="w", pady=(12, 0))
        self.paks = tk.StringVar(value=cfg.paks_dir)
        ttk.Entry(f, textvariable=self.paks, width=46).grid(row=6, column=0, sticky="we")
        bf = ttk.Frame(f)
        bf.grid(row=6, column=1, sticky="e", padx=(6, 0))
        ttk.Button(bf, text=tr("settings.browse"), command=self.browse).pack(side="left")
        ttk.Button(bf, text=tr("settings.detect"), command=self.detect).pack(side="left", padx=(4, 0))
        self._render_installs()

    def _build_misc(self, f: ttk.Frame, cfg: Config) -> None:
        misc = ttk.Frame(f)
        misc.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(misc, text=tr("settings.refresh")).grid(row=0, column=0, sticky="w")
        self.refresh = tk.StringVar(value=str(cfg.refresh_seconds))
        ttk.Combobox(misc, textvariable=self.refresh, state="readonly", width=16,
                     values=["30", "60", "120", "300"]).grid(row=1, column=0, sticky="w")
        ttk.Label(misc, text=tr("settings.ui_scale")).grid(row=0, column=1, sticky="w",
                                                           padx=(24, 0))
        self.scale_var = tk.StringVar(value=ui.label(ui.set_scale(cfg.ui_scale)))
        ttk.Combobox(misc, textvariable=self.scale_var, state="readonly", width=10,
                     values=[ui.label(s) for s in ui.SCALES]).grid(row=1, column=1, sticky="w",
                                                                   padx=(24, 0))

    def _build_maintenance(self, f: ttk.Frame, cfg: Config) -> None:
        tk.Label(f, text=tr("settings.maintenance"), bg=BG, fg=ACCENT,
                 font=(FONT, 11, "bold")).grid(row=9, column=0, sticky="w", pady=(16, 2))
        self.auto_updates = tk.BooleanVar(value=cfg.auto_check_updates)
        self.karl_updates = tk.BooleanVar(value=cfg.check_karl_updates)
        self.karl_startup = tk.BooleanVar(value=cfg.karl_check_startup)
        self.warn_removed = tk.BooleanVar(value=cfg.warn_removed)
        self.tray_icon = tk.BooleanVar(value=cfg.tray_icon)
        self.close_to_tray = tk.BooleanVar(value=cfg.close_to_tray)
        self.whats_new = tk.BooleanVar(value=cfg.show_whats_new)
        checks = {}
        for row, (var, key, pad) in enumerate(
                ((self.auto_updates, "settings.auto_updates", 0),
                 (self.karl_updates, "settings.karl_updates", 0),
                 (self.karl_startup, "settings.karl_startup", 24),
                 (self.warn_removed, "settings.warn_removed", 0),
                 (self.tray_icon, "settings.tray_icon", 0),
                 (self.close_to_tray, "settings.close_to_tray", 24),
                 (self.whats_new, "settings.whats_new", 0)), 10):
            checks[key] = check_button(f, tr(key), var)
            checks[key].grid(row=row, column=0, columnspan=2, sticky="w", padx=(pad, 0))
        self.karl_startup_check = checks["settings.karl_startup"]
        self.close_to_tray_check = checks["settings.close_to_tray"]
        checks["settings.karl_updates"].configure(command=self._sync_karl_startup)
        checks["settings.tray_icon"].configure(command=self._sync_karl_startup)
        if not karl_tray.available():
            checks["settings.tray_icon"].configure(text=tr("settings.tray_icon") + "  "
                                                   + tr("settings.tray_missing"))
        self._sync_karl_startup()

    def _build_tools(self, f: ttk.Frame) -> None:
        tools = ttk.Frame(f)
        tools.grid(row=17, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(tools, text=tr("profile.export"),
                   command=lambda: self.app.export_profile(self)).pack(side="left")
        ttk.Button(tools, text=tr("profile.import"),
                   command=lambda: self.app.import_profile(self)).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text=tr("cache.button"),
                   command=lambda: CacheDialog(self.app)).pack(side="left", padx=(6, 0))
        tools2 = ttk.Frame(f)
        tools2.grid(row=18, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(tools2, text=tr("diag.button"),
                   command=lambda: self.app.open_diagnostics(self)).pack(side="left")
        ttk.Button(tools2, text=tr("log.open"), command=self.app.open_log).pack(
            side="left", padx=(6, 0))

    def _sync_karl_startup(self):
        self.karl_startup_check.configure(
            state="normal" if self.karl_updates.get() else "disabled")
        self.close_to_tray_check.configure(
            state="normal" if self.tray_icon.get() else "disabled")

    def _build_token_section(self):
        box = self.token_box
        for w in box.winfo_children():
            w.destroy()
        cfg = self.app.config_
        tk.Label(box, text=tr("settings.token_section"), bg=PANEL, fg=ACCENT,
                 font=(FONT, 11, "bold")).pack(anchor="w")

        if not cfg.logged_in:
            tk.Label(box, text=tr("settings.token_none"), bg=PANEL, fg=MUTED,
                     font=(FONT, 10)).pack(anchor="w", pady=(6, 10))
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x")
            flat_button(row, tr("settings.token_add"), self._change_token, primary=True).pack(side="left")
            flat_button(row, tr("settings.token_manage"),
                        lambda: webbrowser.open(TOKEN_PAGE)).pack(side="left", padx=(8, 0))
            return

        tk.Label(box, text=tr("settings.token_connected", name=cfg.username or "?"), bg=PANEL,
                 fg=FG, font=(FONT, 10)).pack(anchor="w", pady=(6, 6))

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x")
        self.token_text = tk.Label(row, bg=BG, fg=FG, font=("Consolas", 9), anchor="w",
                                   padx=8, pady=5, width=44)
        self.token_text.pack(side="left", fill="x", expand=True)
        self.show_btn = flat_button(row, "", self._toggle_show)
        self.show_btn.pack(side="left", padx=(6, 0))
        flat_button(row, tr("settings.token_copy"), self._copy_token).pack(side="left", padx=(6, 0))
        self._render_token()

        exp = token_expiry(cfg.token)
        if exp is None:
            text, color = tr("settings.token_expiry_unknown"), MUTED
        else:
            date = human_date(exp)
            left = exp - time.time()
            if left <= 0:
                text, color = tr("settings.token_expired", date=date), DANGER
            elif left < 7 * 86400:
                text, color = tr("settings.token_expires_soon", date=date), ACCENT
            else:
                text, color = tr("settings.token_expires", date=date), MUTED
        self.expiry_lbl = tk.Label(box, text=text, bg=PANEL, fg=color, font=(FONT, 9))
        self.expiry_lbl.pack(anchor="w", pady=(6, 8))

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x")
        flat_button(row, tr("settings.token_verify"), self._verify_token).pack(side="left")
        flat_button(row, tr("settings.token_change"), self._change_token).pack(side="left", padx=(6, 0))
        flat_button(row, tr("settings.token_remove"), self._remove_token).pack(side="left", padx=(6, 0))
        flat_button(row, tr("settings.token_manage"),
                    lambda: webbrowser.open(TOKEN_PAGE)).pack(side="left", padx=(6, 0))
        self.token_msg = tk.Label(box, text="", bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=520,
                                  justify="left")
        self.token_msg.pack(anchor="w", pady=(6, 0))

    def _render_token(self):
        token = self.app.config_.token
        self.token_text.configure(text=token if self.show_token else mask_token(token))
        set_button_style(self.show_btn, tr("settings.token_hide" if self.show_token
                                           else "settings.token_show"), False)

    def _toggle_show(self):
        self.show_token = not self.show_token
        self._render_token()

    def _copy_token(self):
        self.clipboard_clear()
        self.clipboard_append(self.app.config_.token)
        self.token_msg.configure(text=tr("settings.token_copied"), fg=MUTED)

    def _verify_token(self):
        self.token_msg.configure(text=tr("login.checking"), fg=MUTED)
        token = self.app.config_.token

        def ok(me):
            cfg = self.app.config_
            cfg.username = me.get("username", cfg.username)
            cfg.avatar_url = (me.get("avatar") or {}).get("thumb_50x50", cfg.avatar_url)
            remember_me(cfg, me)
            cfg.save()
            self.app.update_account()
            self._build_token_section()
            self.token_msg.configure(text=tr("settings.token_valid"), fg=SUCCESS)

        def fail(err):
            text = tr("login.read_failed", msg=err) if getattr(err, "read_failed", False) else err
            self.token_msg.configure(text=text, fg=DANGER)

        self.app.submit(lambda: self.app.client(token).check_access(), ok, fail)

    def _change_token(self):
        LoginDialog(self.app, on_done=self._build_token_section)

    def _remove_token(self):
        if not messagebox.askyesno(tr("settings.token_section"), tr("settings.token_remove_confirm")):
            return
        self.show_token = False
        self.app.logout()
        self._build_token_section()

    def _install(self, install_id: str) -> dict:
        return next(i for i in self.installs if i["id"] == install_id)

    def _install_label(self, inst: dict) -> str:
        mark = "  ✓" if inst["id"] == self.app.config_.active_install else ""
        return f"{inst['name']}{mark}"

    def _render_installs(self):
        labels = [self._install_label(i) for i in self.installs]
        self.install_combo.configure(values=labels)
        self.install_var.set(self._install_label(self._install(self.sel_install)))

    def _store_install(self):
        self._install(self.sel_install)["paks_dir"] = self.paks.get().strip()

    def _pick_install(self):
        labels = [self._install_label(i) for i in self.installs]
        value = self.install_var.get()
        if value not in labels:
            return
        self._store_install()
        self.sel_install = self.installs[labels.index(value)]["id"]
        self.paks.set(self._install(self.sel_install).get("paks_dir", ""))

    def add_install(self):
        name = simpledialog.askstring(tr("settings.title"), tr("install.ask_name"), parent=self)
        if not name or not name.strip():
            return
        self._store_install()
        import uuid
        new = {"id": uuid.uuid4().hex[:10], "name": name.strip()[:40], "paks_dir": "",
               "game_build": ""}
        self.installs.append(new)
        self.sel_install = new["id"]
        self.paks.set("")
        self._render_installs()

    def rename_install(self):
        inst = self._install(self.sel_install)
        name = simpledialog.askstring(tr("settings.title"), tr("install.ask_name"),
                                      initialvalue=inst["name"], parent=self)
        if name and name.strip():
            inst["name"] = name.strip()[:40]
            self._render_installs()

    def remove_install(self):
        cfg = self.app.config_
        inst = self._install(self.sel_install)
        if inst["id"] in (cfg.active_install, "main"):
            messagebox.showwarning(tr("settings.title"), tr("install.cant_remove"), parent=self)
            return
        if not messagebox.askyesno(tr("settings.title"),
                                   tr("install.confirm_remove", name=inst["name"]), parent=self):
            return
        self.installs.remove(inst)
        self.removed_installs.append(inst["id"])
        self.sel_install = cfg.active_install
        self.paks.set(self._install(self.sel_install).get("paks_dir", ""))
        self._render_installs()

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.paks.get() or None)
        if d:
            self.paks.set(d)

    def detect(self):
        p = find_paks_dir()
        if p:
            self.paks.set(str(p))
        else:
            messagebox.showwarning(tr("settings.title"), tr("settings.not_found"))

    def save(self):
        app, cfg = self.app, self.app.config_
        self._store_install()
        dirs = [os.path.normcase(os.path.abspath(i["paks_dir"])) for i in self.installs
                if i.get("paks_dir")]
        if len(dirs) != len(set(dirs)):
            messagebox.showerror(tr("settings.title"), tr("install.duplicate"), parent=self)
            return
        bad = [i["paks_dir"] for i in self.installs
               if i.get("paks_dir") and not Path(i["paks_dir"]).expanduser().is_dir()]
        if bad:
            messagebox.showerror(tr("settings.title"), tr("err.paks_missing", path=bad[0]),
                                 parent=self)
            return
        cfg.installs = [dict(i) for i in self.installs]
        cfg.paks_dir = self._install(cfg.active_install).get("paks_dir", "")
        target = self.sel_install
        old_scale = ui.set_scale(cfg.ui_scale)
        labels = [ui.label(s) for s in ui.SCALES]
        if self.scale_var.get() in labels:
            cfg.ui_scale = ui.SCALES[labels.index(self.scale_var.get())]
        ui.set_scale(old_scale)
        cfg.refresh_seconds = int(self.refresh.get() or 60)
        cfg.auto_check_updates = bool(self.auto_updates.get())
        karl_was = cfg.check_karl_updates
        cfg.check_karl_updates = bool(self.karl_updates.get())
        cfg.karl_check_startup = bool(self.karl_startup.get())
        removed_was = cfg.warn_removed
        cfg.warn_removed = bool(self.warn_removed.get())
        cfg.tray_icon = bool(self.tray_icon.get())
        cfg.close_to_tray = bool(self.close_to_tray.get()) and cfg.tray_icon
        cfg.show_whats_new = bool(self.whats_new.get())
        new_lang = self.language.key
        lang_changed = new_lang != (cfg.language or i18n.AUTO)
        cfg.save()
        self.destroy()
        if lang_changed:
            app.change_language(new_lang)
        app.apply_update_icon()
        if cfg.check_karl_updates and not karl_was and cfg.karl_check_startup:
            app.check_karl_update()
        app.apply_tray()
        if cfg.warn_removed and not removed_was:
            app.check_removed_mods()
        for install_id in self.removed_installs:
            for d in install_dirs(install_id):
                shutil.rmtree(d, ignore_errors=True)
        app.set_status(tr("status.settings_saved"))
        if target != cfg.active_install:
            app.switch_install(target)
        elif len(cfg.installs) > 1 or self.removed_installs:
            app.install_lbl.configure(text=("·  " + app.install_name())
                                      if len(cfg.installs) > 1 else "")
        if cfg.ui_scale != old_scale and messagebox.askyesno(APP_TITLE, tr("scale.restart")):
            app.restart()
