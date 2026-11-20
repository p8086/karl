from __future__ import annotations

if __name__ == "__main__" and not __package__:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from karl.gui import main as _main
    _main()
    raise SystemExit

import queue
import sys
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

from . import APP_TITLE, VERSION, game
from .browse import BrowseTab, Choice, flat_button, human_date, human_size, set_button_style
from . import selfupdate, single
from .details import ModDetails
from .library import (MODE_LOCAL, MODE_SUBSCRIBE, SOURCE_FILE, Config, Library,
                      extract_local_paths, find_paks_dir)
from . import i18n
from .i18n import tr
from .log import log
from . import log as karl_log
from .modio import ModioClient, extract_mod_urls, mask_token, token_expiry
from .theme import (ACCENT, ACCENT_HOVER, BG, BORDER, BUTTON, BUTTON_HOVER, CHIP, FG, FONT,
                    MUTED, ON_ACCENT, PANEL)

ASSETS = Path(__file__).resolve().parent / "assets"
TOKEN_PAGE = "https://mod.io/me/access"


class ErrorText(str):
    status = None

    @classmethod
    def of(cls, exc: Exception) -> "ErrorText":
        t = cls(str(exc))
        t.status = getattr(exc, "status", None)
        t.read_failed = getattr(exc, "read_failed", False)
        return t


class App(tk.Tk):
    def __init__(self):
        super().__init__(className="Karl")
        self.title(f"{APP_TITLE} {VERSION}")
        self.geometry("1360x860")
        self.minsize(900, 560)
        self.configure(bg=BG)

        self.config_ = Config.load()
        i18n.set_language(self.config_.language)
        self.lib = Library(self.config_)
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.busy = False

        self._set_icon()
        self._style()
        self._build()
        self.after(50, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_account()
        if self.config_.logged_in:
            self.browse.start()
            if not (self.config_.username and self.config_.avatar_url and self.config_.profile_url):
                self.submit(lambda: self.client().me(), self._refresh_me, lambda err: None)
        self.submit(self.lib.migrate_install_location,
                    lambda moved: (self.refresh_mine(), self.mine.install_pending())
                    if moved else None,
                    lambda err: None)
        self.after(800, self.check_foreign_loader)
        self.after(1500, self.check_modio_subscriptions)
        self.after(2500, self.check_integration)
        self.after(3000, self.auto_check_updates)
        self.after(4000, self.check_karl_update)
        self.after(5000, self.check_game_update)

    def check_integration(self):
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
        if self.config_.ignore_integration:
            self.set_status(tr("integ.missing", n=n))
            return
        if not messagebox.askyesno(APP_TITLE, tr("integ.ask", n=n)):
            self.config_.ignore_integration = True
            self.config_.save()
            self.set_status(tr("integ.missing", n=n))
            return
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
        s.configure("TButton", background=BUTTON, foreground=FG, padding=6, borderwidth=0)
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
                    rowheight=28, borderwidth=0)
        s.configure("Treeview.Heading", background=BG, foreground=ACCENT, borderwidth=0)
        s.map("Treeview", background=[("selected", CHIP)])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)
        s.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG, arrowcolor=MUTED,
                    bordercolor=BG)
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(16, 0, 0, 0))
        s.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(14, 8),
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
            self._head_icon = tk.PhotoImage(file=str(logo))
        elif self._icons:
            self._head_icon = self._icons[-2] if len(self._icons) > 1 else self._icons[0]
        else:
            self._head_icon = None
        if self._head_icon:
            tk.Label(head, image=self._head_icon, bg=BG).pack(side="left")
        tk.Label(head, text=APP_TITLE, bg=BG, fg=FG, font=(FONT, 18, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(head, text="DRG mod loader · made by pe", bg=BG, fg=MUTED, font=(FONT, 10)).pack(
            side="left", padx=(10, 0), pady=(6, 0))
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
        self.play_btn = ttk.Button(root, text=tr("play.button"), style="Accent.TButton",
                                   command=lambda: self.mine.play(), padding=(18, 4))
        self.play_btn.place(in_=self.notebook, relx=1.0, x=-16, y=2, anchor="ne")

        bottom = tk.Frame(root, bg=BG, padx=16, pady=6)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=160)
        self.progress.pack(side="right")
        self.status = tk.StringVar(value=tr("status.ready"))
        tk.Label(bottom, textvariable=self.status, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")

    def client(self) -> ModioClient:
        return ModioClient(self.config_.token)

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
        if self.mine.wanted and not messagebox.askyesno(APP_TITLE, tr("mine.quit_unsaved")):
            return
        self.destroy()

    def set_status(self, msg: str):
        self.status.set(msg)

    def show_error(self, msg: str):
        messagebox.showerror(APP_TITLE, msg)

    def refresh_mine(self):
        self.mine.refresh()
        if self.browse.started:
            self.browse.refresh_cards_state()

    def auto_check_updates(self):
        if not self.config_.auto_check_updates or self.busy or not self.lib.mods:
            return

        def work():
            report = self.lib.find_updates(progress=self.on_progress, log=self.log)
            self.post(self.mine._after_check, report)

        self.run(tr("mine.checking_updates"), work)

    def check_karl_update(self, fetch=None):
        cfg = self.config_
        if not cfg.check_karl_updates:
            return
        kw = {"fetch": fetch} if fetch else {}

        def done(version):
            if not version or not selfupdate.is_newer(version) or version == cfg.skip_karl_version:
                return
            if messagebox.askyesno(APP_TITLE, tr("karl_update.available", version=version,
                                                 current=VERSION)):
                webbrowser.open(selfupdate.RELEASES_PAGE)
            else:
                cfg.skip_karl_version = version
                cfg.save()

        self.submit(lambda: selfupdate.latest_version(**kw), done, lambda err: None)

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
        if self.config_.logged_in:
            self.browse.start()


MODE_KEYS = {MODE_SUBSCRIBE: "mode.subscribe", MODE_LOCAL: "mode.local"}
CHECK_ON, CHECK_OFF, CHECK_BUSY = "☑", "☐", "…"


def mode_choice(default: str) -> Choice:
    return Choice([MODE_SUBSCRIBE, MODE_LOCAL], "", default, label=lambda k: tr(MODE_KEYS[k]))


class MineTab(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.lib = app.lib

        top = tk.Frame(self, bg=BG, padx=16, pady=12)
        top.pack(fill="x")
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
        show_combo = ttk.Combobox(filt, textvariable=self.show_var, values=self.show_choices,
                                  state="readonly", width=18)
        show_combo.pack(side="right")
        show_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        tk.Label(filt, text=tr("mine.show_label"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="right", padx=(12, 6))
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
        self.sw_on = tk.PhotoImage(file=str(ASSETS / "switch_on.png"))
        self.sw_off = tk.PhotoImage(file=str(ASSETS / "switch_off.png"))
        self.wanted: dict[int, bool] = {}
        cols = ("nome", "versione", "modalita", "preferito")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", image=self.sw_off, command=self.toggle_all)
        self.tree.column("#0", width=64, minwidth=64, anchor="center", stretch=False)
        for c, key, w in zip(cols[:3], ("mine.col_name", "mine.col_version", "mine.col_mode"),
                             (440, 160, 120)):
            self.tree.heading(c, text=tr(key))
            self.tree.column(c, width=w, anchor="w")
        self.tree.heading("preferito", text="★")
        self.tree.column("preferito", width=48, minwidth=48, anchor="center", stretch=False)
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
        self.tree.bind("<Alt-Up>", lambda e: (self._move_selected(-1), "break")[1])
        self.tree.bind("<Alt-Down>", lambda e: (self._move_selected(1), "break")[1])
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        side = tk.Frame(mid, bg=BG, padx=10)
        side.pack(side="left", fill="y")
        for key, cmd in (("mine.remove", self.remove),
                         ("details.button", self.open_details),
                         ("mine.open", self.open_page),
                         ("rollback.button", self.rollback),
                         ("mine.note", self.edit_note)):
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
        self._dev_job = None
        self._dev_waiting = False
        self._schedule_dev()
        self.refresh()

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
        for m in mods:
            on = self.is_on(m)
            ver = m.version or "-"
            if m.hold:
                ver = f"{ver} 🔒"
            if m.has_update:
                ver = f"{ver} → {m.latest_version}"
            label = m.name + (" ✎" if m.note else "") + (" ⚠" if m.mod_id in missing else "")
            self.tree.insert("", "end", iid=str(m.mod_id), text="",
                             image=self.sw_on if on else self.sw_off,
                             tags=() if on else ("off",),
                             values=(label, ver,
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

    def filtered(self) -> bool:
        return self.only_favorites() or bool(self.search_text())

    def visible_mods(self) -> list:
        mods = self.lib.folder_mods()
        if self.only_favorites():
            mods = [m for m in mods if m.favorite]
        words = self.search_text().split()
        if words:
            mods = [m for m in mods
                    if all(w in f"{m.name} {m.slug} {m.note}".lower() for w in words)]
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
        if region in ("tree", "cell") and self.tree.identify_column(e.x) == "#4":
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
        if self.tree.identify_column(e.x) in ("#0", "#4"):
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

    def show_note(self, _event=None):
        ids = self.selected_ids()
        if len(ids) != 1 or ids[0] not in self.lib.mods:
            return
        missing = self.lib.missing_dependencies(self.wanted).get(ids[0])
        if missing:
            self.app.set_status(tr("deps.status", name=self.lib.mods[ids[0]].name,
                                   deps=", ".join(d["name"] for d in missing)))
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
            if self.app.config_.dev_mode and not self.app.busy and self.lib.changed_files():
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
        risky = self.lib.not_verified(newly_on)
        if risky:
            self._warn_not_verified(risky)
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
        mode = MODE_LOCAL
        self.clear_links()
        items = urls + paths
        self.app.run(tr("mine.adding", n=len(items)), lambda: self._add_many(items, mode))

    def add_files(self):
        if self.app.busy:
            return
        files = filedialog.askopenfilenames(
            parent=self, title=tr("mine.files_title"),
            filetypes=[(tr("mine.files_filter"), "*.pak *.zip"), (tr("mine.files_all"), "*.*")])
        files = [f for f in (files or ()) if f]
        if files:
            self.app.run(tr("mine.adding", n=len(files)), lambda: self._add_many(files, MODE_LOCAL))

    def _add_many(self, items: list[str], mode: str):
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
                    e = self.lib.add(item, mode, progress=app.on_progress, log=lambda s: None)
            except Exception as err:
                failed.append((item, str(err)))
                continue
            added.append(e.name)
            if e.mode == MODE_SUBSCRIBE:
                app.post(app.browse.sub_ids.add, e.mod_id)
            app.post(self.refresh)
        added_ids = [self.lib.mods[i].mod_id for i in list(self.lib.mods)
                     if self.lib.mods[i].name in added]
        risky = self.lib.not_verified(added_ids)
        if risky:
            app.post(self._warn_not_verified, risky)
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

    def _warn_not_verified(self, names: list[str]):
        messagebox.showwarning(APP_TITLE, tr("warn.not_verified",
                                             mods="\n".join(f"• {n}" for n in names[:15])))

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
                self.app.post(self.app.browse.sub_ids.discard, i)
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

    def convert_to_karl(self):
        if self.app.busy:
            return
        ids = self.selected_ids() or None
        converted = self.lib.convert_to_local(ids)
        if not converted:
            return
        self.refresh()
        self.app.set_status(tr("mine.converting", n=len(converted)))
        self.update_mods()

    def _after_update(self, report: dict):
        browse = self.app.browse
        if report.get("sub_ids") is not None:
            browse.sub_ids = set(report["sub_ids"])
            if browse.started:
                browse.refresh_cards_state()
        self.refresh()
        if report["failed"]:
            lines = "\n".join(f"• {name}: {err}" for name, err in report["failed"][:15])
            more = ("\n" + tr("mine.failed_more", n=len(report["failed"]) - 15)
                    if len(report["failed"]) > 15 else "")
            self.app.show_error(f"{tr('mine.update_failed_title')}\n\n{lines}{more}")

    def open_page(self):
        for i in self.selected_ids():
            if self.lib.mods[i].url:
                webbrowser.open(self.lib.mods[i].url)


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
        self.msg = tk.Label(f, text="", bg=BG, fg="#e05a4f", font=(FONT, 9), wraplength=520,
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
            self.msg.configure(text=text, fg="#e05a4f")

        self.app.submit(lambda: ModioClient(token).check_access(), ok, fail)


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


def describe_changes(ch: dict) -> str:
    if ch.get("first"):
        return tr("tm.first")
    parts = [tr(f"tm.ch_{k}", n=ch[k]) for k in ("added", "removed", "versions", "toggled") if ch[k]]
    if ch["order"]:
        parts.append(tr("tm.ch_order"))
    return ", ".join(parts) or "—"


class TimeMachineDialog(tk.Toplevel):
    def __init__(self, tab: "MineTab"):
        super().__init__(tab.app)
        self.tab = tab
        self.title(tr("tm.title"))
        self.configure(bg=BG)
        self.transient(tab.app)
        self.minsize(640, 360)
        f = tk.Frame(self, bg=BG, padx=16, pady=14)
        f.pack(fill="both", expand=True)
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
        btns = tk.Frame(f, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text=tr("tm.close"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=tr("tm.restore"), style="Accent.TButton",
                   command=self.restore).pack(side="right", padx=(0, 8))

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

        ttk.Label(f, text=tr("settings.language")).grid(row=1, column=0, sticky="w")
        self.language = Choice([i18n.AUTO] + i18n.CODES, "", cfg.language or i18n.AUTO,
                               label=lambda c: tr("settings.language_auto") if c == i18n.AUTO
                               else i18n.language_name(c))
        ttk.Combobox(f, textvariable=self.language.var, state="readonly", width=34,
                     values=self.language.labels(), height=18).grid(row=2, column=0, sticky="w")

        ttk.Label(f, text=tr("settings.paks")).grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.paks = tk.StringVar(value=cfg.paks_dir)
        ttk.Entry(f, textvariable=self.paks, width=46).grid(row=4, column=0, sticky="we")
        bf = ttk.Frame(f)
        bf.grid(row=4, column=1, sticky="e", padx=(6, 0))
        ttk.Button(bf, text=tr("settings.browse"), command=self.browse).pack(side="left")
        ttk.Button(bf, text=tr("settings.detect"), command=self.detect).pack(side="left", padx=(4, 0))


        ttk.Label(f, text=tr("settings.refresh")).grid(row=7, column=0, sticky="w", pady=(12, 0))
        self.refresh = tk.StringVar(value=str(cfg.refresh_seconds))
        ttk.Combobox(f, textvariable=self.refresh, state="readonly", width=16,
                     values=["30", "60", "120", "300"]).grid(row=8, column=0, sticky="w")

        tk.Label(f, text=tr("settings.maintenance"), bg=BG, fg=ACCENT,
                 font=(FONT, 11, "bold")).grid(row=9, column=0, sticky="w", pady=(16, 2))
        self.auto_updates = tk.BooleanVar(value=cfg.auto_check_updates)
        self.karl_updates = tk.BooleanVar(value=cfg.check_karl_updates)
        for row, (var, key) in enumerate(((self.auto_updates, "settings.auto_updates"),
                                          (self.karl_updates, "settings.karl_updates")), 10):
            tk.Checkbutton(f, text=tr(key), variable=var, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG, anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="w")
        tools = ttk.Frame(f)
        tools.grid(row=12, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(tools, text=tr("profile.export"),
                   command=lambda: app.export_profile(self)).pack(side="left")
        ttk.Button(tools, text=tr("profile.import"),
                   command=lambda: app.import_profile(self)).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text=tr("cache.button"),
                   command=lambda: CacheDialog(app)).pack(side="left", padx=(6, 0))

        ttk.Button(f, text=tr("settings.save"), style="Accent.TButton", command=self.save).grid(
            row=13, column=1, sticky="e", pady=(16, 0))

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
                text, color = tr("settings.token_expired", date=date), "#e05a4f"
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
            self.token_msg.configure(text=tr("settings.token_valid"), fg="#5fbf6a")

        def fail(err):
            text = tr("login.read_failed", msg=err) if getattr(err, "read_failed", False) else err
            self.token_msg.configure(text=text, fg="#e05a4f")

        self.app.submit(lambda: ModioClient(token).check_access(), ok, fail)

    def _change_token(self):
        LoginDialog(self.app, on_done=self._build_token_section)

    def _remove_token(self):
        if not messagebox.askyesno(tr("settings.token_section"), tr("settings.token_remove_confirm")):
            return
        self.show_token = False
        self.app.logout()
        self._build_token_section()

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
        cfg.paks_dir = self.paks.get().strip()
        cfg.default_mode = MODE_LOCAL
        cfg.refresh_seconds = int(self.refresh.get() or 60)
        cfg.auto_check_updates = bool(self.auto_updates.get())
        cfg.check_karl_updates = bool(self.karl_updates.get())
        new_lang = self.language.key
        lang_changed = new_lang != (cfg.language or i18n.AUTO)
        cfg.save()
        self.destroy()
        if lang_changed:
            app.change_language(new_lang)
        app.set_status(tr("status.settings_saved"))


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
                tk.Checkbutton(f, text=tr(f"cache.{key}"), variable=var, bg=BG, fg=FG,
                               selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                               anchor="w").grid(row=row, column=0, sticky="w")
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


def main():
    karl_log.setup()
    instance = single.SingleInstance()
    if not instance.acquire() and instance.notify_existing():
        return
    app = App()
    instance.serve(lambda: app.post(app.bring_to_front))
    try:
        app.mainloop()
    finally:
        instance.close()
