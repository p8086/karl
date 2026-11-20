from __future__ import annotations

import hashlib
import io
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from tkinter import ttk
from typing import Callable, Optional, TYPE_CHECKING

from . import i18n
from .i18n import human_count, human_date, human_size, tr
from .log import log
from .modio import fetch_bytes, mod_name
from .paths import CACHE_DIR
from .theme import ACCENT, BG, BORDER, CARD, CHIP, DANGER, FG, FONT, IMAGE_BG, MUTED, PANEL, SUCCESS
from .ui import px
from .widgets import flat_button, set_button_style

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except ImportError as _pil_error:
    HAVE_PIL = False
    log.warning("Pillow non disponibile, solo copertine PNG: %s", _pil_error)

if TYPE_CHECKING:
    from .gui import App

GAME_SLUG = "drg"
PAGE_SIZE = 24
CARD_W = 290
IMG_W, IMG_H = CARD_W, 163
GAP = 8


def apply_scale() -> None:
    global CARD_W, IMG_W, IMG_H, GAP
    from .ui import px
    CARD_W = px(290)
    IMG_W, IMG_H = CARD_W, px(163)
    GAP = px(8)


SORTS = {
    "trending": "-downloads_today",
    "recent": "-date_live",
    "updated": "-date_updated",
    "popular": "-downloads_total",
    "subscribers": "-subscribers_total",
    "rated": "-ratings_weighted_aggregate",
    "alpha": "name",
}

DATES = {
    "any": None,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "year": 365 * 86400,
}

LIB_ALL, LIB_SUB, LIB_NOT_SUB = "all", "sub", "not_sub"
LIBRARY = [LIB_ALL, LIB_SUB, LIB_NOT_SUB]


def build_query(search: str, selected: dict[str, set[str]], group_types: dict[str, str],
                sort: str, date: str, library: str, lib_ids: set[int],
                now: Optional[float] = None) -> tuple[Optional[dict], Callable[[dict], bool]]:
    params: dict = {"_sort": SORTS.get(sort, SORTS["trending"])}
    if search.strip():
        params["_q"] = search.strip()

    seconds = DATES.get(date)
    if seconds:
        params["date_live-min"] = int((now or time.time()) - seconds)

    if library == LIB_SUB:
        if not lib_ids:
            return None, lambda m: True
        params["id-in"] = ",".join(str(i) for i in sorted(lib_ids))
    elif library == LIB_NOT_SUB and lib_ids:
        params["id-not-in"] = ",".join(str(i) for i in sorted(lib_ids))

    all_tags: list[str] = []
    multi: list[set[str]] = []
    for group, tags in selected.items():
        if not tags:
            continue
        if len(tags) == 1 or group_types.get(group) == "dropdown":
            all_tags.extend(sorted(tags))
        else:
            multi.append(set(tags))
    if all_tags:
        params["tags"] = ",".join(all_tags)
    if multi:
        params["tags-in"] = ",".join(sorted(multi[0]))
    local_groups = multi[1:]

    def local_filter(mod: dict) -> bool:
        names = {t.get("name") for t in mod.get("tags", [])}
        return all(names & group for group in local_groups)

    return params, local_filter


def shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _localized(translations: dict, fallback: str) -> str:
    if not translations:
        return fallback
    code = i18n.current()
    for key in (code, code.split("-")[0], code.lower(), code.split("-")[0].lower()):
        if translations.get(key):
            return translations[key]
    return fallback


def group_label(group: dict) -> str:
    return _localized(group.get("name_localization") or {}, group.get("name", ""))


def tag_label(group: dict, tag: str) -> str:
    for item in group.get("tags_localization") or []:
        if item.get("tag") == tag:
            return _localized(item.get("translations") or {}, tag)
    return tag


def mod_link(mod: dict) -> str:
    return mod.get("profile_url") or f"https://mod.io/g/{GAME_SLUG}/m/{mod.get('name_id', '')}"


def mods_signature(mods: list[dict], lib_ids: set[int]) -> tuple:
    return tuple((m["id"], m.get("date_updated"),
                  (m.get("stats") or {}).get("downloads_total"),
                  (m.get("stats") or {}).get("subscribers_total"),
                  int(m["id"]) in lib_ids) for m in mods)


class ThumbCache:
    def __init__(self, post: Callable[..., None]):
        self.post = post
        self.dir = CACHE_DIR / "thumbs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.images: dict[tuple, tk.PhotoImage] = {}
        self.pending: dict[tuple, list[Callable]] = {}
        self.pool = ThreadPoolExecutor(max_workers=6)

    def _path(self, url: str):
        return self.dir / hashlib.sha1(url.encode()).hexdigest()

    def _load(self, url: str) -> bytes:
        p = self._path(url)
        if p.exists():
            return p.read_bytes()
        data = fetch_bytes(url)
        p.write_bytes(data)
        return data

    def get(self, url: str, size: tuple[int, int], callback: Callable[[tk.PhotoImage], None]):
        if not url:
            return
        key = (url, size)
        if key in self.images:
            callback(self.images[key])
            return
        if key in self.pending:
            self.pending[key].append(callback)
            return
        self.pending[key] = [callback]
        fut = self.pool.submit(self._load, url)
        fut.add_done_callback(lambda f: self.post(self._finish, key, f))

    def _finish(self, key, fut):
        callbacks = self.pending.pop(key, [])
        try:
            data = fut.result()
            img = self._to_photo(data, key[1])
        except Exception as e:
            log.warning("immagine non caricata %s: %s", key[0], e)
            return
        self.images[key] = img
        for cb in callbacks:
            try:
                cb(img)
            except tk.TclError:
                pass

    @staticmethod
    def _to_photo(data: bytes, size: tuple[int, int]) -> tk.PhotoImage:
        if HAVE_PIL:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            tw, th = size
            scale = max(tw / im.width, th / im.height)
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                           Image.LANCZOS)
            left, top = (im.width - tw) // 2, (im.height - th) // 2
            return ImageTk.PhotoImage(im.crop((left, top, left + tw, top + th)))
        import base64
        return tk.PhotoImage(data=base64.b64encode(data))


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg: str, on_scroll: Optional[Callable[[float], None]] = None):
        super().__init__(parent, bg=bg)
        self.on_scroll = on_scroll
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=self._set_scroll)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _yview(self, *args):
        self.canvas.yview(*args)

    def _set_scroll(self, first, last):
        self.vsb.set(first, last)
        if self.on_scroll:
            self.on_scroll(float(last))

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self._scroll(-3))
        self.canvas.bind_all("<Button-5>", lambda e: self._scroll(3))

    def _unbind_wheel(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _wheel(self, e):
        self._scroll(-1 * (e.delta // 120 or (1 if e.delta > 0 else -1)) * 3)

    def _scroll(self, units: int):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(units, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


class Choice:
    def __init__(self, keys: list[str], prefix: str, default: str,
                 label: Optional[Callable[[str], str]] = None):
        self.keys = keys
        self.label = label or (lambda k: tr(prefix + k))
        self.var = tk.StringVar(value=self.label(default if default in keys else keys[0]))

    def labels(self) -> list[str]:
        return [self.label(k) for k in self.keys]

    @property
    def key(self) -> str:
        labels = self.labels()
        v = self.var.get()
        return self.keys[labels.index(v)] if v in labels else self.keys[0]

    def set_key(self, key: str) -> None:
        self.var.set(self.label(key))


class ModCard(tk.Frame):
    def __init__(self, parent, tab: "BrowseTab", mod: dict):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
                         width=CARD_W)
        self.tab = tab
        self.mod = mod
        open_page = lambda e: tab.open_details(mod)

        holder = tk.Frame(self, width=IMG_W, height=IMG_H, bg=IMAGE_BG)
        holder.pack_propagate(False)
        holder.pack(fill="x")
        self.img = tk.Label(holder, bg=IMAGE_BG, fg=MUTED, text="Deep Rock Galactic",
                            font=(FONT, 9), cursor="hand2")
        self.img.pack(fill="both", expand=True)
        self.img.bind("<Button-1>", open_page)
        logo = mod.get("logo") or {}
        tab.thumbs.get(logo.get("thumb_320x180") or logo.get("original", ""), (IMG_W, IMG_H),
                       lambda im: self.img.configure(image=im, text=""))

        body = tk.Frame(self, bg=CARD, padx=12, pady=10)
        body.pack(fill="both", expand=True)

        title = tk.Label(body, text=shorten(mod_name(mod), 34), bg=CARD, fg=FG,
                         font=(FONT, 12, "bold"), anchor="w", cursor="hand2")
        title.pack(fill="x")
        title.bind("<Button-1>", open_page)

        meta = tk.Frame(body, bg=CARD)
        meta.pack(fill="x", pady=(2, 6))
        author = (mod.get("submitted_by") or {}).get("username", "")
        tk.Label(meta, text=author, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        tk.Label(meta, text="⟳ " + human_date(mod.get("date_updated")), bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="right")

        tk.Label(body, text=shorten(mod.get("summary", ""), 120), bg=CARD, fg=FG,
                 font=(FONT, 9), wraplength=CARD_W - px(26), justify="left", anchor="nw",
                 height=3).pack(fill="x")

        tags = [t.get("name") for t in mod.get("tags", []) if t.get("name")]
        trow = tk.Frame(body, bg=CARD)
        trow.pack(fill="x", pady=(8, 6))
        if tags:
            first = tab.preferred_tag(tags)
            tk.Label(trow, text=tab.tag_text(first), bg=CHIP, fg=FG, font=(FONT, 8, "bold"),
                     padx=8, pady=2).pack(side="left")
            if len(tags) > 1:
                tk.Label(trow, text=tr("browse.more_tags", n=len(tags) - 1), bg=CARD, fg=FG,
                         font=(FONT, 9, "bold")).pack(side="right")
        else:
            tk.Label(trow, text=" ", bg=CARD, font=(FONT, 8, "bold"), pady=2).pack(side="left")

        stats = mod.get("stats") or {}
        size = (mod.get("modfile") or {}).get("filesize", 0)
        tk.Label(body, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w",
                 text=f"↓ {human_count(stats.get('downloads_total'))}     "
                      f"▣ {human_size(size)}     "
                      f"♥ {human_count(stats.get('subscribers_total'))}").pack(fill="x")

        self.copy_btn = flat_button(body, "🔗 " + tr("browse.copy_link"), self.copy_link)
        self.copy_btn.pack(fill="x", pady=(10, 0))
        self._copy_reset = None

    @property
    def mod_id(self) -> int:
        return int(self.mod["id"])

    @property
    def link(self) -> str:
        return mod_link(self.mod)

    def copy_link(self):
        self.tab.copy_link(self.mod)
        set_button_style(self.copy_btn, tr("browse.copied"), True)
        if self._copy_reset:
            self.after_cancel(self._copy_reset)
        self._copy_reset = self.after(1500, self._reset_copy)

    def _reset_copy(self):
        self._copy_reset = None
        set_button_style(self.copy_btn, "🔗 " + tr("browse.copy_link"), False)


class TagSidebar(ScrollFrame):
    def __init__(self, parent, on_change: Callable[[], None]):
        super().__init__(parent, bg=PANEL)
        self.on_change = on_change
        self.groups: list[dict] = []
        self.vars: dict[str, dict[str, tk.BooleanVar]] = {}
        self.radio: dict[str, tk.StringVar] = {}
        self.types: dict[str, str] = {}
        self.collapsed: set[str] = set()

    def set_groups(self, groups: list[dict]):
        keep = self.selected()
        self.groups = groups
        self.types = {g["name"]: g.get("type", "checkboxes") for g in groups}
        for w in self.inner.winfo_children():
            w.destroy()
        self.vars.clear()
        self.radio.clear()
        for g in groups:
            self._build_group(g, keep.get(g["name"], set()))

    def _build_group(self, g: dict, keep: set[str]):
        name = g["name"]
        counts = g.get("tag_count_map") or {}
        box = tk.Frame(self.inner, bg=PANEL, padx=14, pady=8)
        box.pack(fill="x")
        head = tk.Frame(box, bg=PANEL, cursor="hand2")
        head.pack(fill="x")
        arrow = tk.Label(head, text="▴" if name not in self.collapsed else "▾", bg=PANEL,
                         fg=FG, font=(FONT, 11))
        arrow.pack(side="right")
        tk.Label(head, text=group_label(g), bg=PANEL, fg=FG, font=(FONT, 11, "bold")).pack(side="left")
        rows = tk.Frame(box, bg=PANEL)
        if name not in self.collapsed:
            rows.pack(fill="x", pady=(8, 0))

        def toggle(_e=None):
            if name in self.collapsed:
                self.collapsed.discard(name)
                rows.pack(fill="x", pady=(8, 0))
                arrow.configure(text="▴")
            else:
                self.collapsed.add(name)
                rows.pack_forget()
                arrow.configure(text="▾")
        for w in (head, arrow):
            w.bind("<Button-1>", toggle)

        style = dict(bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
                     selectcolor=CHIP, font=(FONT, 10), anchor="w", bd=0,
                     highlightthickness=0, cursor="hand2")
        if g.get("type") == "dropdown":
            var = tk.StringVar(value=next(iter(keep)) if keep else "")
            self.radio[name] = var
            items = [("", tr("browse.tag_all"), None)] + [(t, tag_label(g, t), counts.get(t))
                                                         for t in g.get("tags", [])]
            for value, label, n in items:
                r = tk.Frame(rows, bg=PANEL)
                r.pack(fill="x")
                tk.Radiobutton(r, text=label, value=value, variable=var,
                               command=self.on_change, **style).pack(side="left")
                if n is not None:
                    tk.Label(r, text=str(n), bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="right")
        else:
            self.vars[name] = {}
            for t in g.get("tags", []):
                var = tk.BooleanVar(value=t in keep)
                self.vars[name][t] = var
                r = tk.Frame(rows, bg=PANEL)
                r.pack(fill="x")
                tk.Checkbutton(r, text=tag_label(g, t), variable=var, command=self.on_change,
                               **style).pack(side="left")
                tk.Label(r, text=str(counts.get(t, 0)), bg=PANEL, fg=MUTED,
                         font=(FONT, 9)).pack(side="right")
        tk.Frame(self.inner, bg=BORDER, height=1).pack(fill="x", padx=14)

    def selected(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for g in self.groups:
            name = g["name"]
            if name in self.vars:
                out[name] = {t for t, v in self.vars[name].items() if v.get()}
            elif name in self.radio:
                v = self.radio[name].get()
                out[name] = {v} if v else set()
        return out

    def clear(self):
        for tags in self.vars.values():
            for v in tags.values():
                v.set(False)
        for v in self.radio.values():
            v.set("")


class BrowseTab(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=BG)
        self.app = app
        self.thumbs = ThumbCache(app.post)
        self.game_id: Optional[int] = None
        self.groups: list[dict] = []
        self.type_tags: set[str] = set()
        self.mods: list[dict] = []
        self.cards: list[ModCard] = []
        self.total = 0
        self.gen = 0
        self.loading = False
        self.local_filter: Callable[[dict], bool] = lambda m: True
        self.params: Optional[dict] = None
        self.server_offset = 0
        self.columns = 0
        self.signature: tuple = ()
        self.refresh_count = 0
        self._search_job = None
        self._refresh_job = None
        self.started = False
        self.query_lib_ids: set[int] = set()

        self._build()
        self.show_login_needed()

    def _build(self):
        top = tk.Frame(self, bg=BG, padx=16, pady=12)
        top.pack(fill="x")
        self.count_lbl = tk.Label(top, text="", bg=BG, fg=FG, font=(FONT, 15, "bold"))
        self.count_lbl.pack(side="left")
        self.live_lbl = tk.Label(top, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.live_lbl.pack(side="left", padx=(12, 0))

        self.sort = Choice(list(SORTS), "browse.sort_", "trending")
        self.date = Choice(list(DATES), "browse.date_", "any")
        self.library = Choice(LIBRARY, "browse.lib_", LIB_ALL)
        for label, choice, w in ((tr("browse.sort"), self.sort, 22),
                                 (tr("browse.date"), self.date, 18),
                                 (tr("browse.library"), self.library, 14)):
            box = tk.Frame(top, bg=BG)
            box.pack(side="right", padx=(8, 0))
            tk.Label(box, text=label, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
            cb = ttk.Combobox(box, textvariable=choice.var, values=choice.labels(),
                              state="readonly", width=w)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.new_query())

        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, width=28)
        self.search_entry = search
        search.pack(side="right", padx=(8, 0))
        tk.Label(top, text="⌕", bg=BG, fg=MUTED, font=(FONT, 13)).pack(side="right")
        self.search_var.trace_add("write", lambda *a: self._debounce_search())

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        left = tk.Frame(self.body, bg=PANEL, width=px(270))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        lhead = tk.Frame(left, bg=PANEL, padx=14, pady=8)
        lhead.pack(fill="x")
        tk.Label(lhead, text=tr("browse.filters"), bg=PANEL, fg=ACCENT, font=(FONT, 11, "bold")).pack(side="left")
        reset = tk.Label(lhead, text=tr("browse.reset"), bg=PANEL, fg=MUTED, font=(FONT, 9, "underline"),
                         cursor="hand2")
        reset.pack(side="right")
        reset.bind("<Button-1>", lambda e: (self.sidebar.clear(), self.new_query()))
        self.sidebar = TagSidebar(left, self.new_query)
        self.sidebar.pack(fill="both", expand=True)

        self.grid_area = ScrollFrame(self.body, bg=BG, on_scroll=self._maybe_load_more)
        self.grid_area.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.grid_area.canvas.bind("<Configure>", self._on_resize, add="+")

        self.overlay = tk.Frame(self, bg=BG)

    def _on_resize(self, e):
        cols = max(1, (e.width + GAP) // (CARD_W + 2 + GAP))
        if cols != self.columns:
            self.columns = cols
            self._regrid()

    def _regrid(self):
        cols = max(1, self.columns)
        for i, card in enumerate(self.cards):
            card.grid(row=i // cols, column=i % cols, padx=(0, GAP), pady=(0, GAP), sticky="n")
        if hasattr(self, "footer"):
            self.footer.grid(row=len(self.cards) // cols + 1, column=0, columnspan=cols,
                             pady=12, sticky="we")

    def _show_overlay(self, title: str, text: str, button: Optional[tuple] = None):
        for w in self.overlay.winfo_children():
            w.destroy()
        box = tk.Frame(self.overlay, bg=BG)
        box.place(relx=0.5, rely=0.4, anchor="center")
        tk.Label(box, text=title, bg=BG, fg=FG, font=(FONT, 16, "bold")).pack()
        tk.Label(box, text=text, bg=BG, fg=MUTED, font=(FONT, 10), wraplength=px(480),
                 justify="center").pack(pady=(6, 14))
        if button:
            flat_button(box, button[0], button[1], primary=True).pack()
        self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self.overlay.lift()

    def _hide_overlay(self):
        self.overlay.place_forget()

    def show_login_needed(self):
        self.stop()
        self._show_overlay(tr("browse.login_title"), tr("browse.login_text"),
                           (tr("account.login"), self.app.open_login))

    def start(self):
        self._hide_overlay()
        self.started = True
        self.count_lbl.configure(text=tr("browse.loading"))

        def init():
            client = self.app.client()
            gid = client.game_id(GAME_SLUG)
            return gid, client.game_tags(gid)

        self.app.submit(init, self._on_init, self._on_error)

    def stop(self):
        self.started = False
        self.gen += 1
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None

    def _on_init(self, res):
        self.game_id, groups = res
        self._apply_tags(groups)
        self.new_query()
        self._schedule_refresh()

    def _apply_tags(self, groups: list[dict]):
        if groups == self.groups:
            return
        self.groups = groups
        self.type_tags = {t for g in groups if g.get("name", "").lower() == "type"
                          for t in g.get("tags", [])}
        self.sidebar.set_groups(groups)

    def _on_error(self, msg: str):
        self.loading = False
        if getattr(msg, "status", None) == 401 or "(401)" in msg:
            self.app.logout(expired=True)
            return
        self.live_lbl.configure(text=tr("browse.error", msg=msg), fg=DANGER)

    def open_details(self, mod: dict):
        self.app.open_details(int(mod.get("game_id") or self.game_id or 0), int(mod["id"]), mod)

    def tag_text(self, tag: str) -> str:
        for g in self.groups:
            if tag in g.get("tags", []):
                return tag_label(g, tag)
        return tag

    def preferred_tag(self, tags: list[str]) -> str:
        for t in tags:
            if t in self.type_tags:
                return t
        return tags[0]

    def _debounce_search(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(450, self.new_query)

    def _query(self):
        self.query_lib_ids = self.library_ids()
        return build_query(self.search_var.get(), self.sidebar.selected(), self.sidebar.types,
                           self.sort.key, self.date.key, self.library.key, self.query_lib_ids)

    def new_query(self):
        if not self.started or self.game_id is None:
            return
        self.gen += 1
        self.params, self.local_filter = self._query()
        self.mods, self.total, self.server_offset = [], 0, 0
        self._render()
        self.grid_area.to_top()
        if self.params is None:
            self.count_lbl.configure(text=tr("browse.count", n=0))
            return
        self._fetch_page()

    def _fetch_page(self):
        if self.loading or self.params is None:
            return
        self.loading = True
        gen, params = self.gen, dict(self.params)
        params.update({"_limit": PAGE_SIZE, "_offset": self.server_offset})
        gid = self.game_id
        self._set_footer(tr("browse.loading"))
        self.app.submit(lambda: self.app.client().list_mods(gid, params),
                        lambda res: self._on_page(gen, res), self._on_error)

    def _on_page(self, gen: int, res):
        self.loading = False
        if gen != self.gen:
            return
        page, total = res
        self.server_offset += len(page)
        self.total = total
        self.mods.extend(m for m in page if self.local_filter(m))
        self._render(append=True)
        self._update_header()

    def _maybe_load_more(self, last: float):
        if last > 0.92 and self.has_more():
            self._fetch_page()

    def has_more(self) -> bool:
        return self.params is not None and self.server_offset < self.total

    def _render(self, append: bool = False):
        inner = self.grid_area.inner
        if not append:
            for c in self.cards:
                c.destroy()
            self.cards = []
        for mod in self.mods[len(self.cards):]:
            self.cards.append(ModCard(inner, self, mod))
        if not hasattr(self, "footer"):
            self.footer = tk.Frame(inner, bg=BG)
            self.footer_lbl = tk.Label(self.footer, bg=BG, fg=MUTED, font=(FONT, 10))
            self.footer_lbl.pack()
            self.more_btn = flat_button(self.footer, tr("browse.load_more"), self._fetch_page)
        self.signature = mods_signature(self.mods, self.library_ids())
        if self.has_more():
            self._set_footer("")
            self.more_btn.pack(pady=(4, 0))
        else:
            self.more_btn.pack_forget()
            self._set_footer(tr("browse.none") if not self.mods and not self.loading
                             else (tr("browse.end") if self.mods else ""))
        self._regrid()

    def _set_footer(self, text: str):
        if hasattr(self, "footer_lbl"):
            self.footer_lbl.configure(text=text)

    def _update_header(self):
        self.count_lbl.configure(text=tr("browse.count", n=self.total))
        self.live_lbl.configure(text=tr("browse.live", time=f"{datetime.now():%H:%M}"),
                                fg=SUCCESS)

    def copy_link(self, mod: dict):
        url = mod_link(mod)
        mine = self.app.mine
        added = mine.append_link(url)
        urls, _paths, _ignored = mine.parse_input()
        self.clipboard_clear()
        self.clipboard_append("\n".join(urls) if urls else url)
        name = mod.get("name", "")
        self.app.set_status(tr("browse.link_added" if added else "browse.link_already", name=name))

    def library_ids(self) -> set[int]:
        return {i for i, e in self.app.lib.mods.items() if i > 0 and e.enabled}

    def _schedule_refresh(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        secs = max(15, int(self.app.config_.refresh_seconds or 60))
        self._refresh_job = self.after(secs * 1000, self._auto_refresh)

    def _auto_refresh(self):
        self._refresh_job = None
        if not self.started:
            return
        if self.loading or self.params is None or self.game_id is None:
            self._schedule_refresh()
            return
        self.refresh_count += 1
        with_tags = self.refresh_count % 5 == 0
        gen, gid = self.gen, self.game_id
        params = dict(self.params)
        params.update({"_limit": min(100, max(PAGE_SIZE, self.server_offset)), "_offset": 0})

        def work():
            client = self.app.client()
            page = client.list_mods(gid, params)
            tags = client.game_tags(gid) if with_tags else None
            return page, tags

        self.app.submit(work, lambda res: self._on_refresh(gen, res),
                        lambda msg: (self._on_error(msg), self._schedule_refresh()))

    def _on_refresh(self, gen: int, res):
        self._schedule_refresh()
        if gen != self.gen:
            return
        (page, total), tags = res
        if tags is not None:
            self._apply_tags(tags)
        if self.library.key != LIB_ALL and self.query_lib_ids != self.library_ids():
            self.new_query()
            return
        mods = [m for m in page if self.local_filter(m)]
        self.total = total
        self.server_offset = len(page)
        if mods_signature(mods, self.library_ids()) != self.signature:
            pos = self.grid_area.canvas.yview()[0]
            self.mods = mods
            self._render()
            self.grid_area.canvas.update_idletasks()
            self.grid_area.canvas.yview_moveto(pos)
        self._update_header()
