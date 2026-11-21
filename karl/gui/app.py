from __future__ import annotations

import queue
import re
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from .. import (APP_TITLE, VERSION, browse as browse_mod, crash, game, i18n, log as karl_log,
                selfupdate, single, tray as karl_tray, ui)
from ..browse import BrowseTab
from ..config import Config
from ..details import ModDetails
from ..i18n import tr
from ..library import Library
from ..log import log
from ..modio import ModioClient, extract_mod_urls
from ..theme import (ACCENT, ACCENT_HOVER, BG, BORDER, BUTTON, BUTTON_HOVER, CHIP, FG, FONT,
                     MUTED, ON_ACCENT, PANEL)
from ..ui import px
from ..widgets import flat_button
from .common import ASSETS, ErrorText, GITHUB_PAGE, open_path, remember_me
from .dialogs import DiagnosticsDialog, LoginDialog, WhatsNewDialog
from .mine import MineTab
from .settings import SettingsDialog
from .troubleshoot import CrashDialog


class App(tk.Tk):
    def __init__(self):
        super().__init__(className="Karl")
        self.title(f"{APP_TITLE} {VERSION}")
        self.configure(bg=BG)

        self.config_ = Config.load()
        self._apply_scale(self.config_.ui_scale)
        i18n.set_language(self.config_.language)
        self.lib = Library(self.config_)
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.busy = False
        self.instance = None
        self.karl_release: Optional[dict] = None

        self._set_icon()
        self._style()
        self._build()
        self.after(50, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._bind_shortcuts()
        for cls in ("TEntry", "Entry"):
            try:
                self.bind_class(cls, "<<Paste>>", self._paste_replace)
            except (tk.TclError, AttributeError):
                pass
        self.apply_update_icon()
        self.update_account()
        if self.config_.logged_in:
            self.browse.start()
            if not (self.config_.username and self.config_.avatar_url and self.config_.profile_url):
                self.submit(lambda: self.client().me(), self._refresh_me, lambda err: None)
        if self.lib.needs_install():
            self.after(600, self.mine.install_pending)
        self.after(800, self.check_foreign_loader)
        self.after(1500, self.check_modio_subscriptions)
        self.after(2500, self.check_integration)
        self.after(3000, self.auto_check_updates)
        self.after(4000, self.startup_karl_check)
        self.after(5000, self.check_game_update)
        self.after(6000, self.check_removed_mods)
        self.after(1200, self.mine.resume_vanilla)
        self.after(1500, self.resume_after_restart)
        self.after(4500, self.check_karl_notes)
        self.tray = karl_tray.Tray(self)
        self.apply_tray()
        self.setup_dnd()

    def check_integration(self, force: bool = False):
        if self.busy:
            self.after(300, lambda: self.check_integration(force))
            return
        try:
            n = self.lib.needs_integration()
            first_launch = self.lib.waiting_first_launch()
        except Exception:
            return
        if first_launch and not getattr(self, "_told_first_launch", False):
            self._told_first_launch = True
            messagebox.showinfo(APP_TITLE, tr("integ.proton_first_run"))
        if not n:
            return
        if self.config_.ignore_integration and not force:
            self.set_status(tr("integ.missing", n=n))
            return
        if not messagebox.askyesno(APP_TITLE, tr("integ.ask", n=n)):
            self.config_.ignore_integration = True
            self.config_.save()
            self.set_status(tr("integ.missing", n=n))
            return
        self.install_integration_now()

    def install_integration_now(self):
        if self.busy:
            self.after(300, self.install_integration_now)
            return
        if self.config_.ignore_integration:
            self.config_.ignore_integration = False
            self.config_.save()
        self.run(tr("integ.installing"), self._install_integration)

    def _install_integration(self):
        n = self.lib.install_integration(log=self.log)
        self.post(self.set_status, tr("integ.done", n=max(n, 0)))

    def check_modio_subscriptions(self, force: bool = False):
        if self.config_.ignore_modio_subs and not force:
            return
        logged = self.config_.logged_in

        def gather():
            subs = []
            if logged:
                try:
                    subs = self.lib.account_subscriptions()
                except Exception:
                    subs = []
            return subs, self.lib.local_game_mods()

        def ask(res):
            subs, local = res
            if not subs and not local:
                return
            lines = [f"• {m.get('name', m.get('id'))} (mod.io)" for m in subs]
            lines += [f"• {d.name} (FSD\\Mods)" for d in local]
            names = "\n".join(lines[:20])
            if len(lines) > 20:
                names += "\n" + tr("mine.failed_more", n=len(lines) - 20)
            if not messagebox.askyesno(APP_TITLE, tr("official.found", n=len(lines), mods=names)):
                self.config_.ignore_modio_subs = True
                self.config_.save()
                self.set_status(tr("official.left", n=len(lines)))
                return

            def work():
                n = self.lib.unsubscribe_all(subs) if subs else 0
                adopted = self.lib.adopt_local_mods(log=self.log)
                return n + len(adopted)

            self.submit(work,
                        lambda n: (self.set_status(tr("official.done", n=n)), self.refresh_mine(),
                                   self.mine.install_pending()),
                        self.show_error)

        self.submit(gather, ask, lambda err: None)

    def check_foreign_loader(self):
        if self.config_.ignore_foreign_loader:
            return
        found = self.lib.foreign_loader_files()
        if not found:
            return
        try:
            fsd = self.lib.mods_dir().parent
            names = "\n".join(f"• {p.relative_to(fsd)}" for p in found)
        except Exception:
            names = "\n".join(f"• {p}" for p in found)
        if not messagebox.askyesno(APP_TITLE, tr("loader.found", files=names)):
            self.config_.ignore_foreign_loader = True
            self.config_.save()
            return
        try:
            backup = self.lib.disable_foreign_loader()
        except Exception as err:
            self.show_error(str(err))
            return
        self.set_status(tr("loader.moved", path=backup))

    def post(self, fn: Callable, *args) -> None:
        self.events.put(("call", fn, args))

    def submit(self, fn: Callable, on_done: Optional[Callable] = None,
               on_error: Optional[Callable[[str], None]] = None) -> None:
        def done(fut):
            try:
                res = fut.result()
            except Exception as e:
                self.post(on_error or self.show_error, ErrorText.of(e))
                return
            if on_done:
                self.post(on_done, res)
        self.pool.submit(fn).add_done_callback(done)

    def run(self, label: str, fn: Callable) -> None:
        if self.busy:
            return
        self.busy = True
        self.set_status(label)
        self.progress.configure(value=0)

        def finish(_=None):
            self.busy = False
            self.progress.configure(value=0)
            self.refresh_mine()

        def fail(msg):
            finish()
            self.set_status(tr("status.error"))
            self.show_error(msg)

        self.submit(fn, finish, fail)

    def log(self, msg: str):
        self.post(self.set_status, msg)

    def on_progress(self, done: int, total: int):
        self.post(lambda: self.progress.configure(value=(done / total * 100) if total else 0))

    def _poll(self):
        try:
            for _ in range(200):
                kind, fn, args = self.events.get_nowait()
                if kind == "call":
                    try:
                        fn(*args)
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _apply_scale(self, value: float):
        scale = ui.set_scale(value)
        browse_mod.apply_scale()
        try:
            base = getattr(self, "_base_scaling", None)
            if base is None:
                base = float(self.tk.call("tk", "scaling"))
                self._base_scaling = base
            self.tk.call("tk", "scaling", base * scale)
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass
        w, h = px(1360), px(860)
        try:
            sw, sh = int(self.winfo_screenwidth()), int(self.winfo_screenheight())
            w, h = min(w, max(800, sw - 40)), min(h, max(500, sh - 80))
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass
        self.geometry(f"{w}x{h}")
        self.minsize(min(px(900), w), min(px(560), h))

    def _set_icon(self):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("karl.modmanager")
            except Exception:
                pass
            ico = ASSETS / "icon.ico"
            if ico.exists():
                try:
                    self.iconbitmap(default=str(ico))
                except tk.TclError:
                    pass
        self._icons = [tk.PhotoImage(file=str(ASSETS / f"icon_{s}.png"))
                       for s in (256, 64, 48, 32, 16) if (ASSETS / f"icon_{s}.png").exists()]
        if self._icons:
            self.iconphoto(True, *self._icons)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=(FONT, 10))
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("TButton", background=BUTTON, foreground=FG, padding=px(6), borderwidth=0)
        s.map("TButton", background=[("active", BUTTON_HOVER)])
        s.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT)
        s.map("Accent.TButton", background=[("active", ACCENT_HOVER)])
        s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, padding=6,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        s.configure("TCombobox", fieldbackground=PANEL, foreground=FG, padding=4,
                    background=PANEL, arrowcolor=ACCENT, bordercolor=BORDER)
        s.map("TCombobox",
              fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=px(28), borderwidth=0)
        s.configure("Treeview.Heading", background=BG, foreground=ACCENT, borderwidth=0)
        s.map("Treeview", background=[("selected", CHIP)])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)
        s.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG, arrowcolor=MUTED,
                    bordercolor=BG)
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(16, 0, 0, 0))
        s.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(px(14), px(8)),
                    borderwidth=0, font=(FONT, 11))
        s.map("TNotebook.Tab", background=[("selected", BG)],
              foreground=[("selected", FG)], font=[("selected", (FONT, 11, "bold"))])

    def _build(self):
        self.root_frame = root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True)

        head = tk.Frame(root, bg=BG, padx=16, pady=10)
        head.pack(fill="x")
        logo = ASSETS / "logo_header.png"
        if logo.exists():
            self._head_icon = ui.photo(logo)
        elif self._icons:
            self._head_icon = self._icons[-2] if len(self._icons) > 1 else self._icons[0]
        else:
            self._head_icon = None
        if self._head_icon:
            tk.Label(head, image=self._head_icon, bg=BG).pack(side="left")
        tk.Label(head, text=APP_TITLE, bg=BG, fg=FG, font=(FONT, 18, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(head, text="DRG mod loader · made by pe", bg=BG, fg=MUTED, font=(FONT, 10)).pack(
            side="left", padx=(10, 0), pady=(6, 0))
        self.install_lbl = tk.Label(head, bg=BG, fg=ACCENT, font=(FONT, 10, "bold"),
                                    text=("·  " + self.install_name())
                                    if len(self.config_.installs) > 1 else "")
        self.install_lbl.pack(side="left", padx=(10, 0), pady=(6, 0))
        ttk.Button(head, text=tr("app.settings"), command=self.open_settings).pack(side="right")
        self.account_box = tk.Frame(head, bg=BG)
        self.account_box.pack(side="right", padx=(0, 10))
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.browse = BrowseTab(self.notebook, self)
        self.mine = MineTab(self.notebook, self)
        self.notebook.add(self.browse, text=tr("tab.browse"))
        self.notebook.add(self.mine, text=tr("tab.mine"))
        actions = tk.Frame(root, bg=BG)
        actions.place(in_=self.notebook, relx=1.0, x=-16, y=2, anchor="ne")
        self.vanilla_btn = ttk.Button(actions, text=tr("vanilla.button"),
                                      command=lambda: self.mine.play_vanilla(), padding=(12, 4))
        self.vanilla_btn.pack(side="left", padx=(0, 8))
        self.play_btn = ttk.Button(actions, text=tr("play.button"), style="Accent.TButton",
                                   command=lambda: self.mine.play(), padding=(18, 4))
        self.play_btn.pack(side="left")

        bottom = tk.Frame(root, bg=BG, padx=16, pady=6)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=160)
        self.progress.pack(side="right")
        self.update_lbl = tk.Label(bottom, text="⟳", bg=BG, fg=MUTED, font=(FONT, 11),
                                   cursor="hand2")
        self.update_lbl.bind("<Button-1>", lambda _e: self.karl_update_clicked())
        self.update_lbl.bind("<Enter>", lambda _e: self._update_hover(True))
        self.update_lbl.bind("<Leave>", lambda _e: self._update_hover(False))
        self.github_lbl = tk.Label(bottom, text="GitHub ↗", bg=BG, fg=MUTED, font=(FONT, 9),
                                   cursor="hand2")
        self.github_lbl.pack(side="right", padx=(0, 14))
        self.github_lbl.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_PAGE))
        self.github_lbl.bind("<Enter>", lambda _e: self.github_lbl.configure(fg=FG))
        self.github_lbl.bind("<Leave>", lambda _e: self.github_lbl.configure(fg=MUTED))
        self.status = tk.StringVar(value=tr("status.ready"))
        tk.Label(bottom, textvariable=self.status, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")

    def client(self, token: Optional[str] = None) -> ModioClient:
        return ModioClient(token or self.config_.token)

    def _refresh_me(self, me: dict):
        cfg = self.config_
        cfg.username = me.get("username", cfg.username)
        cfg.avatar_url = (me.get("avatar") or {}).get("thumb_50x50", cfg.avatar_url)
        remember_me(cfg, me)
        cfg.save()
        self.update_account()

    def report_callback_exception(self, exc, val, tb):
        log.error("errore nell'interfaccia", exc_info=(exc, val, tb))
        super().report_callback_exception(exc, val, tb)

    def update_account(self):
        for w in self.account_box.winfo_children():
            w.destroy()
        cfg = self.config_
        if not cfg.logged_in:
            flat_button(self.account_box, tr("account.login"), self.open_login, primary=True).pack()
            return
        avatar = tk.Label(self.account_box, bg=BG)
        avatar.pack(side="left")
        if cfg.avatar_url:
            self.browse.thumbs.get(cfg.avatar_url, (28, 28), lambda im: avatar.configure(image=im))
        name = tk.Label(self.account_box, text=cfg.username or "account", bg=BG, fg=FG,
                        font=(FONT, 10, "bold"), cursor="hand2")
        name.pack(side="left", padx=(6, 10))
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                       activeforeground=ON_ACCENT)
        menu.add_command(label=tr("account.profile"),
                         command=self.open_profile)
        menu.add_command(label=tr("account.logout"), command=self.logout)
        name.bind("<Button-1>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def open_login(self):
        LoginDialog(self)

    def open_profile(self):
        cfg = self.config_
        if cfg.profile_url:
            webbrowser.open(cfg.profile_url)
            return

        def ok(me):
            remember_me(cfg, me)
            cfg.save()
            webbrowser.open(cfg.profile_url or "https://mod.io")

        self.submit(lambda: self.client().me(), ok, self.show_error)

    def login_ok(self, token: str, me: dict):
        cfg = self.config_
        cfg.token = token
        cfg.username = me.get("username", "")
        cfg.avatar_url = (me.get("avatar") or {}).get("thumb_50x50", "")
        remember_me(cfg, me)
        cfg.save()
        self.update_account()
        self.set_status(tr("status.logged_in", name=cfg.username))
        self.browse.start()

    def logout(self, expired: bool = False):
        cfg = self.config_
        cfg.token = cfg.username = cfg.avatar_url = cfg.profile_url = ""
        cfg.save()
        self.update_account()
        self.browse.show_login_needed()
        if expired:
            messagebox.showinfo(APP_TITLE, tr("account.session_expired"))
        self.set_status(tr("status.logged_out"))

    def on_close(self):
        if self.config_.close_to_tray and self.tray.running:
            self.withdraw()
            return
        self.quit_app()

    def quit_app(self):
        if self.mine.wanted and not messagebox.askyesno(APP_TITLE, tr("mine.quit_unsaved")):
            self.bring_to_front()
            return
        if self.config_.vanilla_active and not messagebox.askyesno(APP_TITLE,
                                                                   tr("vanilla.quit")):
            self.bring_to_front()
            return
        self.destroy()

    def destroy(self):
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.stop()
        super().destroy()

    def apply_tray(self):
        if self.config_.tray_icon and karl_tray.available():
            self.tray.start()
        else:
            self.tray.stop()

    def setup_dnd(self):
        self.dnd = False
        try:
            from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD
            TkinterDnD._require(self)
            self.root_frame.drop_target_register(DND_FILES, DND_TEXT)
            self.root_frame.dnd_bind("<<Drop>>", self._on_dnd_drop)
            self.dnd = True
        except Exception as err:
            log.info("trascina e rilascia non disponibile: %s", err)

    def _on_dnd_drop(self, event):
        self.handle_drop(event.data)
        return getattr(event, "action", "copy")

    def handle_drop(self, data: str) -> int:
        try:
            parts = list(self.tk.splitlist(data))
        except (tk.TclError, AttributeError, TypeError):
            parts = [a or b for a, b in re.findall(r"\{([^}]*)\}|(\S+)", str(data))]
        items: list[str] = []
        for part in parts:
            p = Path(part)
            if p.suffix.lower() in (".pak", ".zip") and p.is_file():
                items.append(str(p))
        items += extract_mod_urls(" ".join(parts))[0]
        if not items:
            self.set_status(tr("dnd.nothing"))
            return 0
        self.notebook.select(self.mine)
        mine = self.mine
        if mine.compact:
            mine.set_compact(False)
        text = mine.links_text().rstrip("\n")
        mine.links.delete("1.0", "end")
        mine.links.insert("1.0", "\n".join(([text] if text.strip() else []) + items))
        mine._on_links_changed()
        if self.busy:
            self.set_status(tr("dnd.queued", n=len(items)))
        else:
            mine.add_mod()
        return len(items)

    def check_removed_mods(self, force: bool = False):
        cfg = self.config_
        if not cfg.logged_in or not (cfg.warn_removed or force):
            return

        def done(newly):
            self.refresh_mine()
            if newly:
                names = "\n".join(f"• {e.name}" for e in newly[:15])
                messagebox.showwarning(APP_TITLE, tr("removed.warning", mods=names))

        self.submit(self.lib.check_removed, done, lambda err: None)

    def open_log(self):
        path = karl_log.LOG_FILE
        if not path.exists():
            self.show_error(tr("log.missing", path=path))
            return
        open_path(path)

    def open_diagnostics(self, parent=None):
        DiagnosticsDialog(self, parent)

    CRASH_POLL_MS = 5000
    CRASH_START_WAIT_MS = 180000

    def start_crash_watch(self, seen: bool = False):
        cfg = self.config_
        if not cfg.last_launch or not seen:
            cfg.last_launch = time.time()
            cfg.save()
        self._crash_seen = seen
        self._crash_waited = 0
        job = getattr(self, "_crash_job", None)
        if job:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._crash_job = self.after(self.CRASH_POLL_MS, self._crash_tick)

    def _crash_tick(self):
        self._crash_job = None
        if not self.config_.last_launch:
            return

        def done(running):
            if running:
                self._crash_seen = True
            elif self._crash_seen or self._crash_waited >= self.CRASH_START_WAIT_MS:
                self.game_closed()
                return
            self._crash_waited += self.CRASH_POLL_MS
            self._crash_job = self.after(self.CRASH_POLL_MS, self._crash_tick)

        self.submit(game.is_running, done,
                    lambda err: setattr(self, "_crash_job",
                                        self.after(self.CRASH_POLL_MS, self._crash_tick)))

    def game_closed(self):
        cfg = self.config_
        since = cfg.last_launch
        if not since:
            return
        cfg.last_launch = 0.0
        cfg.save()

        def work():
            try:
                paks = self.lib.paks_dir()
            except Exception:
                return None
            return crash.find_crash(paks, since - 5)

        self.submit(work, self._after_game_closed, lambda err: None)

    def _after_game_closed(self, found):
        win = getattr(self, "bisect_win", None)
        if not found:
            self.lib.log_event("session_ok")
            return
        self.lib.log_event("crash", text=found.get("detail") or "")
        if win is not None and win.winfo_exists():
            self.bring_to_front()
            win.crash_detected(found)
        elif self.config_.crash_watch:
            self.bring_to_front()
            self.tray.notify(tr("crash.title"), found.get("detail") or "")
            CrashDialog(self, found)

    def resume_after_restart(self):
        cfg = self.config_
        if cfg.bisect_restore and not self.busy:
            states = {int(k): bool(v) for k, v in cfg.bisect_restore.items()}
            cfg.bisect_restore = {}
            cfg.save()
            self.set_status(tr("bisect.restored"))

            def work():
                report = self.lib.apply({i: on for i, on in states.items() if i in self.lib.mods},
                                        progress=self.on_progress, log=self.log)
                self.post(self.mine._after_update, report)

            self.run(tr("bisect.restoring"), work)
        if cfg.last_launch and not cfg.vanilla_active:
            def done(running):
                if running:
                    self.start_crash_watch(seen=True)
                else:
                    self.game_closed()
            self.submit(game.is_running, done, lambda err: None)

    def check_karl_notes(self, fetch=None):
        cfg = self.config_
        last = cfg.last_seen_version
        if last == VERSION:
            return
        cfg.last_seen_version = VERSION
        cfg.save()
        if not last or not selfupdate.is_newer(VERSION, last) or not cfg.show_whats_new:
            return
        self.submit(lambda: selfupdate.release_notes(VERSION, fetch),
                    lambda notes: WhatsNewDialog(self, [], karl_notes=notes) if notes else None,
                    lambda err: None)

    def set_status(self, msg: str):
        self.status.set(msg)

    def show_error(self, msg: str):
        messagebox.showerror(APP_TITLE, msg)

    def refresh_mine(self):
        self.mine.refresh()

    def auto_check_updates(self):
        if not self.config_.auto_check_updates or self.busy or not self.lib.mods:
            return

        def work():
            report = self.lib.find_updates(progress=self.on_progress, log=self.log)
            self.post(self.mine._after_check, report)

        self.run(tr("mine.checking_updates"), work)

    def startup_karl_check(self):
        self.apply_update_icon()
        if self.config_.karl_check_startup:
            self.check_karl_update()

    def check_karl_update(self, fetch=None):
        self.apply_update_icon()
        if not self.config_.check_karl_updates:
            return
        self.submit(lambda: selfupdate.latest_release(fetch), self._set_karl_release,
                    lambda err: None)

    def apply_update_icon(self):
        if self.config_.check_karl_updates:
            self.update_lbl.pack(side="right", padx=(0, 14), before=self.github_lbl)
        else:
            self.update_lbl.pack_forget()

    def karl_update_ready(self) -> bool:
        return bool(self.karl_release) and selfupdate.is_newer(self.karl_release["version"])

    def _set_karl_release(self, release: Optional[dict]):
        self.karl_release = release
        ready = self.karl_update_ready()
        self.update_lbl.configure(text="⟳ !" if ready else "⟳", fg=ACCENT if ready else MUTED,
                                  font=(FONT, 11, "bold") if ready else (FONT, 11))

    def _update_hover(self, inside: bool):
        ready = self.karl_update_ready()
        if not ready:
            self.update_lbl.configure(fg=FG if inside else MUTED)
        if inside:
            self._status_before_hover = self.status.get()
            self.set_status(tr("karl_update.ready_hint", version=self.karl_release["version"],
                               current=VERSION) if ready
                            else tr("karl_update.hint", current=VERSION))
        elif getattr(self, "_status_before_hover", None) is not None:
            self.set_status(self._status_before_hover)
            self._status_before_hover = None

    def karl_update_clicked(self, fetch=None):
        if self.busy:
            return
        self._status_before_hover = None
        if self.karl_update_ready():
            self.start_karl_update(self.karl_release)
            return
        self.set_status(tr("karl_update.checking"))

        def done(rel):
            self._set_karl_release(rel)
            if rel is None:
                self.set_status(tr("karl_update.check_failed"))
            elif self.karl_update_ready():
                self.set_status(tr("karl_update.ready_hint", version=rel["version"],
                                   current=VERSION))
            else:
                self.set_status(tr("karl_update.up_to_date", version=VERSION))

        self.submit(lambda: selfupdate.latest_release(fetch), done,
                    lambda err: self.set_status(tr("karl_update.check_failed")))

    def start_karl_update(self, release: dict):
        if not selfupdate.can_self_update():
            webbrowser.open(release.get("page") or selfupdate.RELEASES_PAGE)
            return
        if self.busy:
            return
        if self.mine.wanted and not messagebox.askyesno(APP_TITLE, tr("mine.quit_unsaved")):
            return
        exe = Path(sys.executable)
        work = Path(tempfile.mkdtemp(prefix="karl-update-"))

        def job():
            plan = selfupdate.prepare(release, exe, work, progress=self.on_progress)
            self.post(self._finish_karl_update, release, plan)

        self.busy = True
        self.set_status(tr("karl_update.downloading", version=release["version"]))

        def fail(msg):
            self.busy = False
            self.progress.configure(value=0)
            self.set_status(tr("status.error"))
            if messagebox.askyesno(APP_TITLE, tr("karl_update.failed", msg=msg)):
                webbrowser.open(release.get("page") or selfupdate.RELEASES_PAGE)

        self.submit(job, None, fail)

    def _finish_karl_update(self, release: dict, plan: dict):
        messagebox.showinfo(APP_TITLE, tr("karl_update.restart", version=release["version"]))
        if self.instance is not None:
            self.instance.close()
        try:
            selfupdate.restart(plan)
        except OSError as err:
            self.busy = False
            self.show_error(tr("karl_update.failed", msg=err))
            return
        self.destroy()

    def _bind_shortcuts(self):
        self.bind_all("<Control-f>", lambda e: self._shortcut(self.focus_search))
        self.bind_all("<Control-F>", lambda e: self._shortcut(self.focus_search))
        self.bind_all("<F5>", lambda e: self._shortcut(self.refresh_shortcut))
        self.bind_all("<Control-s>", lambda e: self._shortcut(self.mine.apply_changes))
        self.bind_all("<Control-S>", lambda e: self._shortcut(self.mine.apply_changes))
        self.bind_all("<Control-l>", lambda e: self._shortcut(self.mine.toggle_compact))
        self.bind_all("<Control-L>", lambda e: self._shortcut(self.mine.toggle_compact))

    @staticmethod
    def _paste_replace(event):
        w = event.widget
        try:
            text = w.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            if w.selection_present():
                w.delete("sel.first", "sel.last")
        except (tk.TclError, AttributeError):
            pass
        w.insert("insert", text)
        return "break"

    def _shortcut(self, fn: Callable[[], object]):
        fn()
        return "break"

    def browse_active(self) -> bool:
        try:
            return self.notebook.select() == str(self.browse)
        except tk.TclError:
            return False

    def focus_search(self):
        if self.browse_active():
            entry = getattr(self.browse, "search_entry", None)
        else:
            self.notebook.select(self.mine)
            entry = self.mine.search_entry
        if entry is not None:
            entry.focus_set()
            try:
                entry.select_range(0, "end")
            except (tk.TclError, AttributeError):
                pass

    def refresh_shortcut(self):
        if self.browse_active():
            if self.browse.started:
                self.browse.new_query()
        else:
            self.mine.check_updates()

    def bring_to_front(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except tk.TclError:
            pass

    def open_details(self, game_id: int, mod_id: int, mod: Optional[dict] = None,
                     name: str = ""):
        old = getattr(self, "_details", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        self._details = ModDetails(self, game_id, mod_id, mod, name)

    def check_game_update(self):
        try:
            changed = self.lib.game_update_check()
        except Exception as err:
            log.warning("controllo aggiornamento DRG fallito: %s", err)
            return
        if not changed:
            return
        if not self.config_.logged_in:
            messagebox.showwarning(APP_TITLE, tr("drg_update.generic"))
            return

        def done(res):
            latest, old = res
            if not latest:
                messagebox.showwarning(APP_TITLE, tr("drg_update.generic"))
            elif not old:
                messagebox.showinfo(APP_TITLE, tr("drg_update.all_ok", version=latest))
            else:
                names = "\n".join(f"• {e.name}" for e in old[:15])
                if len(old) > 15:
                    names += "\n" + tr("mine.failed_more", n=len(old) - 15)
                messagebox.showwarning(APP_TITLE, tr("drg_update.outdated", version=latest,
                                                     n=len(old), mods=names))
            self.refresh_mine()

        self.submit(self.lib.outdated_mods, done,
                    lambda err: messagebox.showwarning(APP_TITLE, tr("drg_update.generic")))

    def export_profile(self, parent=None):
        path = filedialog.asksaveasfilename(
            parent=parent or self, title=tr("profile.export"), defaultextension=".karl",
            initialfile="karl-config.karl",
            filetypes=[(tr("profile.file_type"), "*.karl"), (tr("mine.files_all"), "*.*")])
        if not path:
            return
        try:
            skipped = self.lib.export_profile(Path(path))
        except Exception as err:
            self.show_error(str(err))
            return
        msg = tr("profile.exported", path=path)
        if skipped:
            msg += "\n\n" + tr("profile.skipped", n=skipped)
        messagebox.showinfo(APP_TITLE, msg, parent=parent or self)
        self.set_status(tr("profile.exported", path=path))

    def import_profile(self, parent=None):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            parent=parent or self, title=tr("profile.import"),
            filetypes=[(tr("profile.file_type"), "*.karl"), (tr("mine.files_all"), "*.*")])
        if not path:
            return
        try:
            data = self.lib.read_profile(Path(path))
        except Exception as err:
            self.show_error(str(err))
            return
        same = [n for n, _ in data["folders"] if n in self.lib.folders]
        question = tr("profile.confirm", n=len(data["mods"]), folders=len(data["folders"]))
        if same:
            question += "\n\n" + tr("profile.replace", names=", ".join(same))
        if not messagebox.askyesno(APP_TITLE, question, parent=parent or self):
            return
        if parent is not None:
            parent.destroy()
        self.mine.wanted.clear()

        def work():
            failed = self.lib.import_profile(Path(path), progress=self.on_progress, log=self.log)
            report = self.lib.update_all(progress=self.on_progress, log=self.log)
            self.post(self._after_import, report, failed, len(data["folders"]))

        self.run(tr("profile.importing_all"), work)

    def _after_import(self, report: dict, failed: list[str], n_folders: int):
        self.mine._refresh_folders()
        self.mine._after_update(report)
        if failed:
            messagebox.showwarning(APP_TITLE, tr("profile.failed", names=", ".join(failed)))
        if not report["failed"]:
            self.set_status(tr("profile.imported", n=n_folders))
        self.check_integration()

    def open_settings(self):
        SettingsDialog(self)

    def change_language(self, code: str):
        self.config_.language = code
        self.config_.save()
        i18n.set_language(code)
        self.rebuild_ui()

    def restart(self):
        import subprocess
        if self.instance is not None:
            self.instance.close()
        cmd = [sys.executable] if getattr(sys, "frozen", False) else \
            [sys.executable, str(Path(sys.argv[0]).resolve())]
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             **({"start_new_session": True} if sys.platform != "win32" else {}))
        except OSError as err:
            self.show_error(str(err))
            return
        self.destroy()

    def install_name(self) -> str:
        return str(self.config_.current_install().get("name") or "")

    def switch_install(self, install_id: str) -> bool:
        cfg = self.config_
        if install_id == cfg.active_install:
            return True
        if self.busy:
            return False
        if cfg.vanilla_active:
            self.show_error(tr("install.vanilla_busy"))
            return False
        if self.mine.wanted and not messagebox.askyesno(APP_TITLE, tr("folder.discard")):
            return False
        cfg.activate(install_id)
        self.lib = Library(cfg)
        self.rebuild_ui()
        self.set_status(tr("install.active", name=self.install_name()))
        self.after(500, self.check_integration)
        if self.lib.needs_install():
            self.mine.install_pending()
        return True

    def rebuild_ui(self):
        tab = self.notebook.index(self.notebook.select()) if self.notebook.select() else 0
        pending_links = self.mine.links_text().strip()
        self.browse.stop()
        self.root_frame.destroy()
        self._build()
        if pending_links:
            self.mine.links.insert("1.0", pending_links + "\n")
            self.mine._on_links_changed()
        self.update_account()
        self.notebook.select(tab)
        self.apply_update_icon()
        self._set_karl_release(self.karl_release)
        self.setup_dnd()
        self.tray.refresh()
        if self.config_.logged_in:
            self.browse.start()

def main():
    karl_log.setup()
    instance = single.SingleInstance()
    if not instance.acquire() and instance.notify_existing():
        return
    app = App()
    app.instance = instance
    instance.serve(lambda: app.post(app.bring_to_front))
    try:
        app.mainloop()
    finally:
        instance.close()
