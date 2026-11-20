from __future__ import annotations

import html
import re
import tkinter as tk
import webbrowser
from html.parser import HTMLParser
from tkinter import ttk
from typing import TYPE_CHECKING, Optional

from .i18n import tr
from .modio import dependency_info, game_versions_of, mod_name
from .ui import px
from .theme import ACCENT, BG, BORDER, FG, FONT, MUTED, PANEL

if TYPE_CHECKING:
    from .gui import App

IMG_W, IMG_H = 320, 180
THUMB_W, THUMB_H = 160, 90
MAX_GALLERY = 6


class _TextParser(HTMLParser):
    BREAKS = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAKS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    p = _TextParser()
    try:
        p.feed(raw)
        p.close()
        text = "".join(p.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html.unescape(raw))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _readonly_text(parent, height: int = 12) -> tk.Text:
    box = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    box.pack(fill="both", expand=True)
    t = tk.Text(box, height=height, wrap="word", bg=PANEL, fg=FG, relief="flat", bd=0,
                padx=10, pady=8, font=(FONT, 10), insertbackground=FG,
                highlightthickness=0)
    sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    t.configure(yscrollcommand=sb.set)
    t.tag_configure("title", font=(FONT, 10, "bold"), foreground=ACCENT)
    t.tag_configure("muted", foreground=MUTED)
    t.tag_configure("warn", foreground=ACCENT)
    t.configure(state="disabled")
    return t


def _fill(t: tk.Text, chunks: list[tuple[str, str]]) -> None:
    t.configure(state="normal")
    t.delete("1.0", "end")
    for text, tag in chunks:
        if tag:
            t.insert("end", text, tag)
        else:
            t.insert("end", text)
    t.configure(state="disabled")


class ModDetails(tk.Toplevel):
    def __init__(self, app: "App", game_id: int, mod_id: int, mod: Optional[dict] = None,
                 name: str = ""):
        super().__init__(app)
        self.app = app
        self.game_id, self.mod_id = int(game_id), int(mod_id)
        self.mod: dict = dict(mod or {})
        self.files: list[dict] = []
        self.deps: Optional[list[dict]] = None
        self.photos: list = []
        self.title(tr("details.title", name=mod_name(self.mod) or name or mod_id))
        self.configure(bg=BG)
        self.geometry(f"{px(820)}x{px(760)}")
        self.minsize(px(640), px(520))
        self.transient(app)

        top = tk.Frame(self, bg=BG, padx=16, pady=14)
        top.pack(fill="x")
        holder = tk.Frame(top, width=px(IMG_W), height=px(IMG_H), bg="#0d0c0b")
        holder.pack_propagate(False)
        holder.pack(side="left")
        self.img = tk.Label(holder, bg="#0d0c0b", fg=MUTED, text="Deep Rock Galactic",
                            font=(FONT, 9))
        self.img.pack(fill="both", expand=True)
        info = tk.Frame(top, bg=BG, padx=16)
        info.pack(side="left", fill="both", expand=True)
        self.name_lbl = tk.Label(info, bg=BG, fg=FG, font=(FONT, 15, "bold"), anchor="w",
                                 justify="left", wraplength=px(420),
                                 text=mod_name(self.mod) or name)
        self.name_lbl.pack(fill="x")
        self.meta_lbl = tk.Label(info, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                                 justify="left", wraplength=px(420))
        self.meta_lbl.pack(fill="x", pady=(4, 0))
        self.stats_lbl = tk.Label(info, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                                  justify="left", wraplength=px(420))
        self.stats_lbl.pack(fill="x", pady=(4, 0))
        self.versions_lbl = tk.Label(info, bg=BG, fg=FG, font=(FONT, 9), anchor="w",
                                     justify="left", wraplength=px(420))
        self.versions_lbl.pack(fill="x", pady=(8, 0))
        self.state_lbl = tk.Label(info, bg=BG, fg=ACCENT, font=(FONT, 9, "bold"), anchor="w",
                                  justify="left", wraplength=px(420))
        self.state_lbl.pack(fill="x", pady=(4, 0))
        votes = tk.Frame(info, bg=BG)
        votes.pack(fill="x", pady=(8, 0))
        self.my_rating = 0
        self.up_btn = ttk.Button(votes, command=lambda: self.vote(1))
        self.up_btn.pack(side="left")
        self.down_btn = ttk.Button(votes, command=lambda: self.vote(-1))
        self.down_btn.pack(side="left", padx=(6, 0))
        self.vote_lbl = tk.Label(votes, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.vote_lbl.pack(side="left", padx=(10, 0))
        btns = tk.Frame(info, bg=BG)
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text=tr("details.open"), command=self.open_page).pack(side="left")
        ttk.Button(btns, text=tr("details.copy"), command=self.copy_link).pack(
            side="left", padx=(6, 0))
        ttk.Button(btns, text=tr("details.close"), command=self.destroy).pack(side="right")

        self.gallery = tk.Frame(self, bg=BG, padx=16)
        self.gallery.pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        pages = {}
        for key in ("description", "versions", "deps"):
            page = tk.Frame(nb, bg=BG, pady=6)
            nb.add(page, text=tr(f"details.tab_{key}"))
            pages[key] = _readonly_text(page)
        self.desc_text = pages["description"]
        self.files_text = pages["versions"]
        self.deps_text = pages["deps"]
        self.bind("<Escape>", lambda _e: self.destroy())

        self.render()
        self.load()

    def link(self) -> str:
        url = self.mod.get("profile_url") or ""
        if not url:
            e = self.app.lib.mods.get(self.mod_id)
            url = e.url if e else ""
        return url

    def open_page(self):
        if self.link():
            webbrowser.open(self.link())

    def copy_link(self):
        if self.link():
            self.clipboard_clear()
            self.clipboard_append(self.link())
            self.app.set_status(tr("browse.copied"))

    def load(self):
        if not self.app.config_.logged_in:
            _fill(self.desc_text, [(tr("details.login"), "muted")])
            return
        _fill(self.desc_text, [(tr("details.loading"), "muted")])
        game_id, mod_id, have = self.game_id, self.mod_id, dict(self.mod)

        def work():
            client = self.app.client()
            mod = have if have.get("description") is not None and have.get("media") is not None \
                else client.get_mod_by_id(game_id, mod_id)
            files = client.list_modfiles(game_id, mod_id)
            deps = ([dependency_info(d) for d in client.dependencies(game_id, mod_id)]
                    if mod.get("dependencies", True) else [])
            try:
                mine = client.my_rating(game_id, mod_id)
            except Exception:
                mine = 0
            return mod, files, deps, mine

        self.app.submit(work, self._loaded, self._failed)

    def _failed(self, msg: str):
        if self.winfo_exists():
            _fill(self.desc_text, [(tr("details.error", msg=msg), "warn")])

    def _loaded(self, res):
        if not self.winfo_exists():
            return
        self.mod, self.files, self.deps, self.my_rating = res
        e = self.app.lib.mods.get(self.mod_id)
        if e is not None:
            e.deps = list(self.deps)
            e.game_versions = game_versions_of(self.mod)
            self.app.lib._save_list()
            self.app.refresh_mine()
        self.render()

    def render(self):
        from .browse import human_count, human_date, human_size
        m = self.mod
        name = mod_name(m) or self.name_lbl.cget("text") or str(self.mod_id)
        self.title(tr("details.title", name=name))
        self.name_lbl.configure(text=name)
        author = (m.get("submitted_by") or {}).get("username", "")
        parts = [p for p in (author and tr("details.by", author=author),
                             m.get("date_updated") and tr("details.updated",
                                                          date=human_date(m["date_updated"])))
                 if p]
        self.meta_lbl.configure(text="   ·   ".join(parts))
        if m.get("stats") or m.get("modfile"):
            stats = m.get("stats") or {}
            mf = m.get("modfile") or {}
            self.stats_lbl.configure(
                text=f"↓ {human_count(stats.get('downloads_total'))}     "
                     f"▣ {human_size(mf.get('filesize', 0))}     "
                     f"♥ {human_count(stats.get('subscribers_total'))}"
                     + (f"     v{mf['version']}" if mf.get("version") else ""))
        versions = game_versions_of(m) if m.get("tags") is not None else []
        self.versions_lbl.configure(
            text=tr("details.game_versions", versions=", ".join(versions)) if versions else "")
        self.state_lbl.configure(text=self._library_state())
        self._render_votes()
        logo = m.get("logo") or {}
        url = logo.get("thumb_640x360") or logo.get("thumb_320x180") or logo.get("original", "")
        thumbs = getattr(getattr(self.app, "browse", None), "thumbs", None)
        if url and thumbs is not None:
            thumbs.get(url, (px(IMG_W), px(IMG_H)), lambda im: self._set_img(self.img, im))
        self._render_gallery(thumbs)
        desc = html_to_text(m.get("description") or "") or m.get("summary") or ""
        if m.get("id") or desc:
            _fill(self.desc_text, [(desc or tr("details.no_description"),
                                    "" if desc else "muted")])
        self._render_files()
        self._render_deps()

    def _render_votes(self):
        stats = self.mod.get("stats") or {}
        up = int(stats.get("ratings_positive") or 0)
        down = int(stats.get("ratings_negative") or 0)
        self.up_btn.configure(text=f"▲ {up}", style="Accent.TButton" if self.my_rating == 1
                              else "TButton")
        self.down_btn.configure(text=f"▼ {down}", style="Accent.TButton" if self.my_rating == -1
                                else "TButton")
        if self.my_rating == 1:
            text = tr("vote.liked")
        elif self.my_rating == -1:
            text = tr("vote.disliked")
        else:
            text = tr("vote.hint") if self.app.config_.logged_in else tr("details.login")
        self.vote_lbl.configure(text=text)

    def vote(self, value: int):
        if not self.app.config_.logged_in:
            self.app.open_login()
            return
        new = 0 if self.my_rating == value else value
        old = self.my_rating
        game_id, mod_id = self.game_id, self.mod_id

        def done(_):
            if not self.winfo_exists():
                return
            stats = dict(self.mod.get("stats") or {})
            for v, key in ((1, "ratings_positive"), (-1, "ratings_negative")):
                n = int(stats.get(key) or 0) - (old == v) + (new == v)
                stats[key] = max(0, n)
            self.mod["stats"] = stats
            self.my_rating = new
            self._render_votes()
            self.app.set_status(tr("vote.saved") if new else tr("vote.removed"))

        def fail(msg):
            if self.winfo_exists():
                self.app.show_error(tr("vote.failed", msg=msg))

        self.app.submit(lambda: self.app.client().rate(game_id, mod_id, new), done, fail)

    def _library_state(self) -> str:
        lib = self.app.lib
        if self.mod_id not in lib.mods:
            return tr("details.not_in_library")
        folder = lib.folders.get(lib.active_folder, {})
        if self.mod_id not in folder:
            return tr("details.other_folder")
        e = lib.mods[self.mod_id]
        return tr("details.in_folder_on") if e.enabled else tr("details.in_folder_off")

    def _set_img(self, label, im):
        if self.winfo_exists():
            self.photos.append(im)
            label.configure(image=im, text="")

    def _render_gallery(self, thumbs):
        for w in self.gallery.winfo_children():
            w.destroy()
        images = ((self.mod.get("media") or {}).get("images") or [])[:MAX_GALLERY]
        if not images or thumbs is None:
            return
        tk.Label(self.gallery, text=tr("details.images"), bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(self.gallery, bg=BG)
        row.pack(fill="x")
        for img in images:
            original = img.get("original") or ""
            lbl = tk.Label(row, bg="#0d0c0b", width=THUMB_W // 8, height=THUMB_H // 18,
                           cursor="hand2")
            lbl.pack(side="left", padx=(0, 6))
            if original:
                lbl.bind("<Button-1>", lambda _e, u=original: webbrowser.open(u))
            src = img.get("thumb_320x180") or original
            if src:
                thumbs.get(src, (px(THUMB_W), px(THUMB_H)),
                           lambda im, w=lbl: self._set_img_sized(w, im))

    def _set_img_sized(self, label, im):
        if self.winfo_exists():
            self.photos.append(im)
            label.configure(image=im, text="", width=px(THUMB_W), height=px(THUMB_H))

    def _render_files(self):
        from .browse import human_date, human_size
        if not self.files:
            if self.mod.get("id"):
                _fill(self.files_text, [(tr("details.no_versions"), "muted")])
            return
        e = self.app.lib.mods.get(self.mod_id)
        chunks: list[tuple[str, str]] = []
        for f in self.files:
            ver = f.get("version") or "-"
            mine = e is not None and f.get("id") == e.file_id
            chunks.append((ver + (f"  {tr('details.installed')}" if mine else ""), "title"))
            chunks.append(("   " + human_date(f.get("date_added")) + "   ·   "
                           + human_size(f.get("filesize", 0)) + "\n", "muted"))
            log = html_to_text(f.get("changelog") or "")
            chunks.append(((log or tr("details.no_changelog")) + "\n\n", "" if log else "muted"))
        _fill(self.files_text, chunks)

    def _render_deps(self):
        if self.deps is None:
            return
        if not self.deps:
            _fill(self.deps_text, [(tr("details.no_deps"), "muted")])
            return
        lib = self.app.lib
        folder = lib.folders.get(lib.active_folder, {})
        chunks: list[tuple[str, str]] = [(tr("details.deps_intro") + "\n\n", "muted")]
        for d in self.deps:
            ok = d["id"] in folder and d["id"] in lib.mods and lib.mods[d["id"]].enabled
            chunks.append((("✓ " if ok else "⚠ ") + d["name"], "" if ok else "warn"))
            chunks.append(("   " + (tr("details.dep_ok") if ok else tr("details.dep_missing"))
                           + "\n", "muted"))
            if d.get("url"):
                chunks.append(("   " + d["url"] + "\n", "muted"))
        _fill(self.deps_text, chunks)
