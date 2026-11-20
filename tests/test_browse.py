import io
import os
import sys
import tempfile
import types
import time
import unittest
import urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_CACHE_HOME"] = str(TMP / "cache")
os.environ["LANG"] = "it_IT.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_tk

fake_tk.install()

from PIL import Image

from karl import browse, config, gui, modio, paths
from karl.browse import LIB_ALL, LIB_NOT_SUB, LIB_SUB, build_query
from karl.config import Config
from karl.library import Library

TAGS = [
    {"name": "Game Version", "type": "checkboxes", "tags": ["1.38", "1.39", "1.40"],
     "tag_count_map": {"1.38": 1699, "1.39": 2348, "1.40": 970}},
    {"name": "Approval Category", "type": "dropdown", "tags": ["Verified", "Approved", "Sandbox"],
     "tag_count_map": {"Verified": 1767, "Approved": 1072, "Sandbox": 221}},
    {"name": "Type", "type": "checkboxes", "tags": ["Audio", "Framework", "QoL", "Visual"],
     "tag_count_map": {"Audio": 2328, "Framework": 46, "QoL": 835, "Visual": 1012}},
]


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), (200, 100, 0)).save(buf, "PNG")
    return buf.getvalue()


def make_mod(i, tags=("1.40", "Verified", "QoL")):
    return {"id": i, "game_id": 2475, "name": f"Mod {i}", "name_id": f"mod-{i}",
            "summary": "Una mod di prova " * 10, "profile_url": f"https://mod.io/g/drg/m/mod-{i}",
            "submitted_by": {"username": f"autore{i}"}, "date_updated": 1718236800,
            "date_live": 1718236800, "logo": {"thumb_320x180": f"https://img/{i}.png"},
            "tags": [{"name": t} for t in tags],
            "stats": {"downloads_total": 3_600_000 + i, "subscribers_total": 1000},
            "modfile": {"id": 100 + i, "version": "1.0", "filesize": 1_760_000,
                        "download": {"binary_url": "https://x/file.zip"}}}


class FakeModio:
    calls: list = []
    mods = [make_mod(i) for i in range(1, 61)]
    subs: set = {5}
    valid_token = "TOKEN-OK"

    def __init__(self, token):
        if not token:
            raise modio.ModioError("Manca il token")
        self.token = token

    def _log(self, *a):
        FakeModio.calls.append(a)

    def me(self):
        if self.token != FakeModio.valid_token:
            raise modio.ModioError("Token mod.io non valido o scaduto (401).")
        return {"username": "pe8086", "name_id": "giuseppe8086",
                "profile_url": "https://mod.io/u/giuseppe8086",
                "avatar": {"thumb_50x50": "https://img/avatar.png"}}

    no_read_tokens: set = set()

    def check_access(self):
        me = self.me()
        if self.token in FakeModio.no_read_tokens:
            e = modio.ModioError("Token mod.io non valido o scaduto. (401 · 11104)", status=401)
            e.read_failed = True
            raise e
        return me

    def game_id(self, slug):
        return 2475

    def get_mod(self, game, slug):
        for m in FakeModio.mods:
            if m["name_id"] == slug:
                return m
        raise modio.ModioError(f"Mod '{slug}' non trovata.")

    def game_tags(self, gid):
        self._log("tags")
        return TAGS

    def list_mods(self, gid, params):
        self._log("list", dict(params))
        off, lim = params["_offset"], params["_limit"]
        return FakeModio.mods[off:off + lim], len(FakeModio.mods)

    def subscribed_mods(self, gid):
        return [m for m in FakeModio.mods if m["id"] in FakeModio.subs]

    def unsubscribe(self, gid, mid):
        FakeModio.subs.discard(mid)

    deps: dict = {}

    def dependencies(self, gid, mid):
        return FakeModio.deps.get(mid, [])

    def list_modfiles(self, gid, mid, limit=10):
        self._log("files", mid)
        return [{"id": 900 + mid, "version": "2.0", "date_added": 1700000000, "filesize": 1024,
                 "changelog": "<p>Fixed <b>things</b></p>"}]

    gone: set = set()
    votes: dict = {}

    def mods_by_ids(self, gid, ids):
        return [m for m in FakeModio.mods if m["id"] in ids and m["id"] not in FakeModio.gone]

    def get_modfile(self, gid, mid, fid):
        return {"id": fid, "changelog": f"<p>Novità {mid}</p>"}

    def rate(self, gid, mid, rating):
        FakeModio.votes[mid] = rating

    def my_rating(self, gid, mid):
        return FakeModio.votes.get(mid, 0)

    def download(self, url, dest, progress=None, md5=""):
        import zipfile
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Mod.pak", b"PAK")
        return dest

    def get_mod_by_id(self, gid, mid):
        return next(m for m in FakeModio.mods if m["id"] == mid)


def pump(app, rounds=40):
    for _ in range(rounds):
        time.sleep(0.01)
        app._poll()
    fake_tk.after_queue.clear()


class QueryTests(unittest.TestCase):
    types = {g["name"]: g["type"] for g in TAGS}

    def q(self, selected=None, lib=LIB_ALL, subs=frozenset(), search="", date="any",
          sort="trending"):
        return build_query(search, selected or {}, self.types, sort, date, lib, set(subs),
                           now=1_000_000)

    def test_default(self):
        params, f = self.q()
        self.assertEqual(params, {"_sort": "-downloads_today"})

    def test_sorts_and_search_and_date(self):
        params, _ = self.q(search="  heat crosshair ", date="week", sort="alpha")
        self.assertEqual(params["_q"], "heat crosshair")
        self.assertEqual(params["date_live-min"], 1_000_000 - 7 * 86400)
        self.assertEqual(params["_sort"], "name")
        for key in browse.SORTS:
            self.assertIn("_sort", self.q(sort=key)[0])

    def test_tags_logic(self):
        sel = {"Game Version": {"1.39", "1.40"}, "Approval Category": {"Verified"},
               "Type": {"QoL"}}
        params, f = self.q(sel)
        self.assertEqual(params["tags"], "Verified,QoL")
        self.assertEqual(params["tags-in"], "1.39,1.40")
        sel["Type"] = {"QoL", "Audio"}
        params, f = self.q(sel)
        self.assertEqual(params["tags-in"], "1.39,1.40")
        self.assertTrue(f(make_mod(1, ("1.40", "Verified", "Audio"))))
        self.assertFalse(f(make_mod(1, ("1.40", "Verified", "Visual"))))

    def test_library_filter(self):
        self.assertEqual(self.q(lib=LIB_SUB, subs=set())[0], None)
        self.assertEqual(self.q(lib=LIB_SUB, subs={9, 3})[0]["id-in"], "3,9")
        self.assertEqual(self.q(lib=LIB_NOT_SUB, subs={9, 3})[0]["id-not-in"], "3,9")
        self.assertNotIn("id-not-in", self.q(lib=LIB_NOT_SUB, subs=set())[0])

    def test_formatting(self):
        self.assertEqual(browse.human_count(3_600_000), "3.6M")
        self.assertEqual(browse.human_count(2_000_000), "2M")
        self.assertEqual(browse.human_count(1_500), "1K")
        self.assertEqual(browse.human_count(914), "914")
        self.assertEqual(browse.human_size(1_761_607), "1.68 MB")
        self.assertEqual(browse.human_size(462_029), "451.2 KB")
        self.assertIn("giu 2024", browse.human_date(1718236800))
        self.assertTrue(browse.shorten("a " * 200, 20).endswith("…"))


def offline(*a, **k):
    raise OSError("offline")


urllib.request.urlopen = offline


class GuiBase(unittest.TestCase):
    def setUp(self):
        for f in (paths.CONFIG_FILE, paths.LIBRARY_FILE, paths.FOLDERS_FILE,
                  paths.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        FakeModio.subs = {5}
        FakeModio.calls = []
        gui.app.ModioClient = FakeModio
        Library.__init__.__defaults__ = (FakeModio,)
        browse.fetch_bytes = lambda url, timeout=20: png_bytes()
        browse.Image = Image
        browse.ImageTk = types.SimpleNamespace(PhotoImage=lambda im: ("foto", im.size))
        browse.HAVE_PIL = True
        saved_get = gui.app.selfupdate._http_get
        self.addCleanup(setattr, gui.app.selfupdate, "_http_get", saved_get)
        gui.app.selfupdate._http_get = lambda url, timeout=10: b"{}"
        fake_tk.sys.modules["tkinter.messagebox"].messages.clear()
        fake_tk.Widget.clipboard = None
        self.paks = Path(tempfile.mkdtemp()) / "Deep Rock Galactic" / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        self.mods = self.paks.parent.parent / "Mods"
        config.find_paks_dir = lambda: self.paks

    def installed(self, slug):
        found = sorted(self.paks.glob(f"FSD-WindowsNoEditor_karl_{slug}_*_P.pak"))
        return found[0] if found else self.paks / f"FSD-WindowsNoEditor_karl_{slug}_0_P.pak"


class GuiTests(GuiBase):
    def test_full_flow(self):
        app = gui.App()
        pump(app)
        b = app.browse
        self.assertFalse(b.started)

        dlg = gui.dialogs.LoginDialog(app)
        dlg.token.set("SBAGLIATO")
        dlg.submit()
        pump(app)
        self.assertIn("401", dlg.msg.kw["text"])
        dlg.token.set(FakeModio.valid_token)
        dlg.submit()
        pump(app, 80)
        self.assertEqual(app.config_.username, "pe8086")
        self.assertEqual(app.config_.profile_url, "https://mod.io/u/giuseppe8086")
        opened = []
        saved_open = gui.app.webbrowser.open
        self.addCleanup(setattr, gui.app.webbrowser, "open", saved_open)
        gui.app.webbrowser.open = opened.append
        app.open_profile()
        app.config_.profile_url = ""
        app.open_profile()
        pump(app)
        self.assertEqual(opened, ["https://mod.io/u/giuseppe8086"] * 2)
        self.assertTrue(b.started)
        self.assertEqual(Config.load().token, FakeModio.valid_token)

        self.assertEqual(len(b.mods), browse.PAGE_SIZE)
        self.assertEqual(len(b.cards), browse.PAGE_SIZE)
        self.assertEqual(b.count_lbl.kw["text"], "60 mod")
        self.assertEqual(b.cards[0].img.kw["image"], ("foto", (browse.IMG_W, browse.IMG_H)))
        self.assertNotIn(5, app.lib.mods)
        self.assertNotIn(1, b.library_ids())
        self.assertEqual(len(b.sidebar.vars["Type"]), 4)

        b._maybe_load_more(0.99)
        pump(app)
        self.assertEqual(len(b.cards), 2 * browse.PAGE_SIZE)
        b._maybe_load_more(0.99)
        pump(app)
        self.assertEqual(len(b.cards), 60)
        self.assertFalse(b.has_more())

        app.lib.add_mod_object(b.cards[0].mod)
        app.refresh_mine()
        self.assertNotIn(1, FakeModio.subs)
        self.assertIn(1, b.library_ids())
        self.assertTrue(self.installed("mod-1").exists())
        self.assertIn("1", app.mine.tree.get_children())

        b.sidebar.vars["Type"]["QoL"].set(True)
        b.sidebar.radio["Approval Category"].set("Verified")
        b.sort.set_key("subscribers")
        b.new_query()
        pump(app)
        last = [c for c in FakeModio.calls if c[0] == "list"][-1][1]
        self.assertEqual(last["tags"], "Verified,QoL")
        self.assertEqual(last["_sort"], "-subscribers_total")
        self.assertEqual(last["_offset"], 0)
        b.library.set_key(LIB_SUB)
        b.new_query()
        pump(app)
        self.assertEqual([c for c in FakeModio.calls if c[0] == "list"][-1][1]["id-in"], "1")

        b.library.set_key(LIB_ALL)
        b.sidebar.clear()
        b.new_query()
        pump(app)
        FakeModio.subs.add(7)
        b._auto_refresh()
        pump(app)
        self.assertNotIn(7, app.lib.mods)
        self.assertNotIn(7, b.library_ids())
        self.assertIn("aggiornato alle", b.live_lbl.kw["text"])

        subs_before = set(FakeModio.subs)
        app.mine.tree._sel = ("1",)
        app.mine.toggle()
        pump(app)
        self.assertTrue(self.installed("mod-1").exists())
        app.mine.apply_changes()
        pump(app)
        self.assertFalse(self.installed("mod-1").exists())
        self.assertTrue(subs_before)
        self.assertEqual(FakeModio.subs, set())

        b._on_error("Token mod.io non valido o scaduto (401).")
        self.assertFalse(app.config_.logged_in)
        self.assertFalse(b.started)
        app.pool.shutdown(wait=True)

    def test_start_logged_in(self):
        cfg = Config(token=FakeModio.valid_token, username="giuseppe8086")
        cfg.save()
        app = gui.App()
        pump(app, 80)
        self.assertTrue(app.browse.started)
        self.assertEqual(len(app.browse.cards), browse.PAGE_SIZE)
        gui.settings.SettingsDialog(app).save()
        app.pool.shutdown(wait=True)

    def test_paste_many_links(self):
        Config(token=FakeModio.valid_token, username="giuseppe8086",
               ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine = app.mine
        mine.links.insert("1.0", (
            "https://mod.io/g/drg/m/mod-11\n"
            "  https://mod.io/g/drg/m/mod-12#description   https://mod.io/g/drg/m/mod-13\n"
            "https://mod.io/g/drg/m/MOD-11/\n"
            "https://modcat.top/#/modDetail?ModId=123\n"
            "https://mod.io/g/drg/m/non-esiste\n"))
        mine._on_links_changed()
        self.assertIn("4 mod pronte", mine.links_info.kw["text"])
        self.assertIn("1 righe senza link", mine.links_info.kw["text"])
        self.assertEqual(mine.add_btn.kw["text"], "Aggiungi (4)")
        mine.add_mod()
        pump(app, 80)
        for i in (11, 12, 13):
            self.assertIn(i, app.lib.mods)
            self.assertNotIn(i, FakeModio.subs)
            self.assertTrue(self.installed(f"mod-{i}").exists())
            self.assertIn(str(i), mine.tree.get_children())
        self.assertEqual(mine.links.buf.strip(), "https://mod.io/g/drg/m/non-esiste")
        msgs = fake_tk.sys.modules["tkinter.messagebox"].messages
        self.assertTrue(any("non-esiste" in m[2] for m in msgs if m[0] == "error"))
        self.assertIn("Aggiunte 3 mod su 4", app.status.get())
        app.pool.shutdown(wait=True)


class CheckboxTests(GuiBase):
    def test_checkboxes(self):
        FakeModio.subs = {5, 6, 7}
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        app.check_modio_subscriptions()
        pump(app, 160)
        mine, tree = app.mine, app.mine.tree
        self.assertEqual(FakeModio.subs, set())
        for i in (5, 6, 7):
            self.assertTrue(self.installed(f"mod-{i}").exists())

        def box(i):
            img = tree._images[str(i)]
            return "on" if img is mine.sw_on else "off"

        def head():
            return "on" if tree._headings["#0"]["image"] is mine.sw_on else "off"

        self.assertEqual([box(i) for i in (5, 6, 7)], ["on", "on", "on"])
        self.assertEqual(head(), "on")

        self.assertEqual(tree.click("6", column="#0", region="tree"), "break")
        self.assertEqual(box(6), "off")
        self.assertEqual(tree._tags["6"], ("off",))
        self.assertEqual(head(), "off")
        self.assertTrue(app.lib.mods[6].enabled)
        self.assertTrue(self.installed("mod-6").exists())
        self.assertEqual(mine.wanted, {6: False})
        self.assertIn("1", mine.unsaved_lbl.kw["text"])
        self.assertIn("(1)", mine.apply_btn.kw["text"])
        self.assertEqual(mine.counts(), {"installed": 3, "pending": 1, "updates": 0})
        self.assertEqual(mine.count_lbls["pending"].kw["text"], "1")

        tree.click("6", column="#0", region="tree")
        self.assertEqual(mine.wanted, {})
        self.assertEqual(mine.unsaved_lbl.kw["text"], "")
        tree.click("6", column="#0", region="tree")

        self.assertIsNone(tree.click("5", column="#2"))
        self.assertEqual(box(5), "on")

        tree.__dict__["_hit"] = ("tree", "#0", "6")
        self.assertEqual(mine._on_double(fake_tk.types.SimpleNamespace(x=1, y=1)), "break")
        self.assertEqual(box(6), "off")

        mine.apply_changes()
        pump(app, 80)
        self.assertFalse(app.lib.mods[6].enabled)
        self.assertFalse(self.installed("mod-6").exists())
        self.assertEqual(mine.wanted, {})
        self.assertEqual(FakeModio.subs, set())

        mine.toggle_all()
        self.assertEqual([box(i) for i in (5, 6, 7)], ["on", "on", "on"])
        mine.toggle_all()
        self.assertEqual([box(i) for i in (5, 6, 7)], ["off", "off", "off"])
        mine.apply_changes()
        pump(app, 80)
        self.assertFalse(any(m.enabled for m in app.lib.mods.values()))
        self.assertFalse(any(self.installed(f"mod-{i}").exists() for i in (5, 6, 7)))

        mine.toggle_all()
        msgbox = fake_tk.sys.modules["tkinter.messagebox"]
        old = gui.app.messagebox.askyesno
        gui.app.messagebox.askyesno = lambda *a, **k: False
        app.on_close()
        self.assertFalse(getattr(app, "destroyed", False))
        gui.app.messagebox.askyesno = old
        app.pool.shutdown(wait=True)


class FolderTests(GuiBase):
    def test_folders_gui(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="giuseppe8086", ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        lib.add("https://mod.io/g/drg/m/mod-31")
        mine.refresh()
        first = lib.active_folder
        self.assertEqual(mine.tree.get_children(), ["30", "31"])
        mine.move_mod(31, -1)
        self.assertEqual(mine.tree.get_children(), ["31", "30"])
        self.assertEqual(mine.counts()["pending"], 2)
        self.assertIn("priorit", app.status.get())
        mine.apply_changes()
        pump(app, 80)
        prio = lambda slug: int(self.installed(slug).name.rsplit("_", 2)[1])
        self.assertGreater(prio("mod-31"), prio("mod-30"))
        self.assertEqual(mine.counts()["pending"], 0)
        mine._drag, mine._drag_moved = "30", True
        mine._on_drop(None)
        self.assertEqual(lib.priority(31) > lib.priority(30), True)
        sd = fake_tk.sys.modules["tkinter.simpledialog"]
        sd.answers[:] = ["Amici"]
        mine.duplicate_folder()
        pump(app, 80)
        self.assertEqual(lib.active_folder, "Amici")
        self.assertEqual(mine.folder_var.get(), "Amici")
        self.assertEqual(set(mine.tree.get_children()), {"30", "31"})
        mine.tree.click("31", column="#0", region="tree")
        mine.apply_changes()
        pump(app, 80)
        self.assertFalse(self.installed("mod-31").exists())
        sd.answers[:] = ["Vuota"]
        mine.new_folder()
        pump(app, 80)
        self.assertEqual(mine.tree.get_children(), [])
        self.assertFalse(self.installed("mod-30").exists())
        self.assertIn("Vuota", app.status.get())
        mine.choose_folder(first)
        pump(app, 80)
        self.assertTrue(self.installed("mod-30").exists() and self.installed("mod-31").exists())
        sd.answers[:] = ["Base"]
        mine.rename_folder()
        self.assertEqual(lib.active_folder, "Base")
        mine.delete_folder()
        pump(app, 80)
        self.assertNotIn("Base", lib.folders)
        app.pool.shutdown(wait=True)


class NewFeaturesTests(GuiBase):
    def test_play_share_rollback_dev(self):
        import os
        from karl import game
        launched, running = [], [False]
        saved = (game.launch, game.is_running, game.close, gui.mine.time)
        def restore():
            game.launch, game.is_running, game.close, gui.mine.time = saved
        self.addCleanup(restore)
        game.launch = lambda: launched.append(1)
        game.is_running = lambda: running[0]
        game.close = lambda: running.__setitem__(0, False) or True
        gui.mine.time = fake_tk.types.SimpleNamespace(sleep=lambda s: None, time=time.time)
        Config(token=FakeModio.valid_token, username="giuseppe8086", ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        mine.refresh()

        mine.tree.click("30", column="#0", region="tree")
        mine.play()
        pump(app, 80)
        self.assertEqual(launched, [1])
        self.assertFalse(self.installed("mod-30").exists())
        self.assertEqual(mine.wanted, {})
        mine.play()
        self.assertEqual(launched, [1, 1])
        running[0] = True
        mine.play()
        self.assertEqual(launched, [1, 1])
        self.assertIn("aperto", app.status.get())
        running[0] = False

        mine.export_folder()
        code = fake_tk.Widget.clipboard
        self.assertTrue(code.startswith("KARL1:"))
        sd = fake_tk.sys.modules["tkinter.simpledialog"]
        sd.answers[:] = [code]
        mine.import_folder()
        pump(app, 80)
        self.assertEqual(len(lib.folders), 2)
        self.assertEqual(mine.folder_var.get(), lib.active_folder)
        self.assertIn("30", mine.tree.get_children())

        mine.tree._sel = ("30",)
        mine.rollback()
        msgs = fake_tk.sys.modules["tkinter.messagebox"].messages
        self.assertTrue(any(m[0] == "error" for m in msgs))
        mod = next(m for m in FakeModio.mods if m["id"] == 30)
        orig = mod["modfile"]
        self.addCleanup(mod.__setitem__, "modfile", orig)
        lib.set_enabled(30, True)
        old_ver = lib.mods[30].version
        mod["modfile"] = dict(orig, id=orig["id"] + 500, version="9.9")
        lib.update_all()
        self.assertEqual(lib.mods[30].version, old_ver)
        lib.find_updates()
        lib.update_all(install_updates=True)
        self.assertEqual(lib.mods[30].version, "9.9")
        mine.rollback()
        pump(app, 80)
        self.assertEqual(lib.mods[30].version, old_ver)
        self.assertIn("🔒", mine.tree._rows["30"][1])

        src = Path(tempfile.mkdtemp()) / "MiaMod.pak"
        src.write_bytes(b"v1")
        lib.add_local_file(str(src))
        mine.dev_var.set(True)
        mine._dev_settings_changed()
        self.assertTrue(Config.load().dev_mode)
        src.write_bytes(b"v2")
        os.utime(src, (time.time() + 5, time.time() + 5))
        running[0] = True
        mine.dev_tick()
        self.assertIn("DRG", app.status.get())
        self.assertEqual(self.installed("file-miamod").read_bytes(), b"v1")
        mine.dev_restart_var.set(True)
        mine._dev_settings_changed()
        mine.dev_tick()
        pump(app, 80)
        self.assertEqual(self.installed("file-miamod").read_bytes(), b"v2")
        self.assertEqual(len(launched), 3)
        app.pool.shutdown(wait=True)


class UpdateCheckTests(GuiBase):
    def test_check_and_apply(self):
        Config(token=FakeModio.valid_token, username="giuseppe8086",
               ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        e = lib.add("https://mod.io/g/drg/m/mod-30")
        old_ver, old_file = e.version, e.file_id
        mod = next(m for m in FakeModio.mods if m["id"] == 30)
        orig = mod["modfile"]
        self.addCleanup(mod.__setitem__, "modfile", orig)
        mod["modfile"] = dict(orig, id=(old_file or 0) + 1000, version="9.9")
        mine.check_updates()
        pump(app, 80)
        self.assertEqual(lib.mods[30].latest_version, "9.9")
        self.assertEqual(lib.mods[30].file_id, old_file)
        self.assertIn("→ 9.9", mine.tree._rows["30"][1])
        self.assertEqual(mine.counts()["updates"], 1)
        self.assertEqual(mine.count_lbls["updates"].kw["text"], "1")
        self.assertIn("1", app.status.get())
        mine.apply_changes()
        pump(app, 80)
        self.assertEqual(lib.mods[30].file_id, old_file + 1000)
        self.assertEqual(lib.mods[30].latest_version, "")
        self.assertNotIn("→", mine.tree._rows["30"][1])
        app.pool.shutdown(wait=True)


class LocalFileTests(GuiBase):
    def test_add_pak_files(self):
        paks = self.paks
        Config(token=FakeModio.valid_token, username="giuseppe8086", paks_dir=str(paks)).save()
        app = gui.App()
        pump(app, 80)
        mine = app.mine
        src = Path(tempfile.mkdtemp())
        (src / "MiaMod_P.pak").write_bytes(b"x")
        (src / "Altra.pak").write_bytes(b"y")
        mine.links.insert("1.0", f'"{src / "MiaMod_P.pak"}"\nhttps://mod.io/g/drg/m/mod-20\n')
        mine._on_links_changed()
        self.assertIn("2", mine.links_info.kw["text"])
        mine.add_mod()
        pump(app, 80)
        names = {m.name for m in app.lib.mods.values()}
        self.assertIn("MiaMod", names)
        self.assertIn("Mod 20", names)
        self.assertTrue(self.installed("file-miamod").exists())
        file_row = next(r for i, r in mine.tree._rows.items() if r[0] == "MiaMod")
        self.assertEqual(file_row[3], "file .pak")
        fake_tk.sys.modules["tkinter.filedialog"].askopenfilenames = \
            lambda **k: (str(src / "Altra.pak"),)
        gui.mine.filedialog.askopenfilenames = fake_tk.sys.modules["tkinter.filedialog"].askopenfilenames
        mine.add_files()
        pump(app, 80)
        self.assertIn("Altra", {m.name for m in app.lib.mods.values()})
        self.assertTrue(self.installed("file-altra").exists())
        app.pool.shutdown(wait=True)


class UpdateButtonTests(GuiBase):
    def test_update_button(self):
        paks = self.paks
        FakeModio.subs = {5}
        Config(token=FakeModio.valid_token, username="giuseppe8086", paks_dir=str(paks)).save()
        app = gui.App()
        pump(app, 80)
        mine = app.mine
        app.lib.add("https://mod.io/g/drg/m/mod-30")
        pak = self.installed("mod-30")
        self.assertTrue(pak.exists())
        pak.unlink()
        FakeModio.subs.add(8)
        mine.update_mods()
        pump(app, 80)
        self.assertTrue(pak.exists())
        self.assertNotIn(8, app.lib.mods)
        self.assertIn("Installate:", app.status.get())
        msgs = fake_tk.sys.modules["tkinter.messagebox"].messages
        self.assertFalse([m for m in msgs if m[0] == "error"])
        app.pool.shutdown(wait=True)


class CopyLinkTests(GuiBase):
    def test_copy_link(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        b, mine = app.browse, app.mine
        before_mods, before_subs = dict(app.lib.mods), set(FakeModio.subs)

        self.assertFalse(hasattr(b.cards[0], "button"))
        b.cards[0].copy_link()
        url0 = "https://mod.io/g/drg/m/mod-1"
        self.assertEqual(fake_tk.Widget.clipboard, url0)
        self.assertEqual(mine.links.buf.strip(), url0)
        self.assertIn("link aggiunto", app.status.get())
        self.assertEqual(b.cards[0].copy_btn.kw["text"], "Copiato ✓")
        b.cards[0]._reset_copy()
        self.assertEqual(b.cards[0].copy_btn.kw["text"], "🔗 Copia link")

        b.cards[0].copy_link()
        self.assertEqual(mine.links.buf.strip(), url0)
        self.assertIn("già nella lista", app.status.get())

        b.cards[1].copy_link()
        self.assertEqual(mine.links.buf.strip().splitlines(),
                         [url0, "https://mod.io/g/drg/m/mod-2"])
        self.assertIn("2 mod pronte", mine.links_info.kw["text"])
        self.assertEqual(mine.add_btn.kw["text"], "Aggiungi (2)")
        for c in b.cards[2:5]:
            c.copy_link()
        expected = [f"https://mod.io/g/drg/m/mod-{i}" for i in range(1, 6)]
        self.assertEqual(mine.links.buf.strip().splitlines(), expected)
        self.assertEqual(fake_tk.Widget.clipboard.splitlines(), expected)
        self.assertEqual(mine.add_btn.kw["text"], "Aggiungi (5)")

        pump(app)
        self.assertEqual(app.lib.mods, before_mods)
        self.assertEqual(FakeModio.subs, before_subs)

        mine.add_mod()
        pump(app, 80)
        for i in range(1, 6):
            self.assertIn(i, app.lib.mods)
        app.pool.shutdown(wait=True)


def make_jwt(exp):
    import base64
    import json as _json
    enc = lambda d: base64.urlsafe_b64encode(_json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc({'exp': exp, 'sub': 'x'})}.firma"


class TokenSettingsTests(GuiBase):
    def test_helpers(self):
        from karl.modio import mask_token, token_expiry
        self.assertEqual(token_expiry(make_jwt(1893456000)), 1893456000)
        self.assertIsNone(token_expiry("non-un-jwt"))
        self.assertIsNone(token_expiry("a.b.c"))
        masked = mask_token("abcdef" + "x" * 50 + "wxyz")
        self.assertTrue(masked.startswith("abcdef") and masked.endswith("wxyz"))
        self.assertNotIn("xxxx", masked)

    def test_token_section(self):
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        dlg = gui.settings.SettingsDialog(app)
        texts = lambda: [w.kw.get("text") for w in self._all(dlg.token_box)]
        self.assertIn("Collegato come pe8086", texts())
        self.assertIn("Scadenza non indicata nel token", texts())
        self.assertNotEqual(dlg.token_text.kw["text"], FakeModio.valid_token)
        dlg._toggle_show()
        self.assertEqual(dlg.token_text.kw["text"], FakeModio.valid_token)
        self.assertEqual(dlg.show_btn.kw["text"], "Nascondi")
        dlg._copy_token()
        self.assertEqual(fake_tk.Widget.clipboard, FakeModio.valid_token)
        dlg._verify_token()
        pump(app)
        self.assertEqual(dlg.token_msg.kw["text"], "✓ Token valido")

        soon, old = make_jwt(int(time.time()) + 3 * 86400), make_jwt(int(time.time()) - 86400)
        FakeModio.valid_token = soon
        app.config_.token = soon
        dlg._build_token_section()
        self.assertIn("rinnovalo presto", dlg.expiry_lbl.kw["text"])
        app.config_.token = old
        dlg._build_token_section()
        self.assertIn("Scaduto", dlg.expiry_lbl.kw["text"])
        dlg._verify_token()
        pump(app)
        self.assertIn("401", dlg.token_msg.kw["text"])

        dlg._remove_token()
        self.assertFalse(app.config_.logged_in)
        self.assertIn("Nessun token: Karl non è collegato al tuo account mod.io.", texts())
        self.assertFalse(app.browse.started)

        dlg._change_token()
        login = [w for w in app.children_ if isinstance(w, gui.dialogs.LoginDialog)][-1]
        login.token.set(soon)
        login.submit()
        pump(app, 80)
        self.assertTrue(app.config_.logged_in)
        self.assertIn("Collegato come pe8086", texts())
        FakeModio.valid_token = "TOKEN-OK"
        app.pool.shutdown(wait=True)

    @staticmethod
    def _all(w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out.extend(TokenSettingsTests._all(c))
        return out


class ModioHostTests(unittest.TestCase):
    def test_hosts_and_errors(self):
        import urllib.error
        from karl.modio import ModioClient
        c = ModioClient("t")
        self.assertEqual(c._base("/games/2475/mods"), "https://g-2475.modapi.io/v1")
        self.assertEqual(c._base("/me/subscribed"), "https://g-2475.modapi.io/v1")
        self.assertEqual(c._base("/games"), "https://api.mod.io/v1")
        self.assertEqual(c.game_id("drg"), 2475)
        body = io.BytesIO(b'{"error": {"code": 401, "error_ref": 11005, '
                          b'"message": "Your access token has expired."}}')
        e = ModioClient._http_error(urllib.error.HTTPError("u", 401, "Unauthorized", {}, body))
        self.assertEqual(e.status, 401)
        self.assertEqual(e.ref, 11005)
        self.assertIn("(401 · 11005)", str(e))
        self.assertIn("Your access token has expired.", str(e))

class ModioRequestTests(unittest.TestCase):
    def test_content_type_on_every_write(self):
        import urllib.request
        sent = []

        class Resp(io.BytesIO):
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=0: (sent.append(req), Resp(b"{}"))[1]
        try:
            c = modio.ModioClient("t")
            c.unsubscribe(2475, 1)
            c.rate(2475, 1, 1)
            c.list_mods(2475, {"_limit": 1})
        finally:
            urllib.request.urlopen = real
        delete, post, get = sent
        self.assertEqual(delete.get_method(), "DELETE")
        self.assertEqual(delete.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(post.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertIsNone(get.get_header("Content-type"))
        self.assertTrue(delete.full_url.startswith("https://g-2475.modapi.io/v1/games/2475/"))


class LoginReadTests(GuiBase):
    def test_token_reads_account_but_not_mods(self):
        app = gui.App()
        pump(app)
        FakeModio.no_read_tokens = {"SOLO-ACCOUNT"}
        FakeModio.valid_token = "SOLO-ACCOUNT"
        try:
            dlg = gui.dialogs.LoginDialog(app)
            dlg.token.set("SOLO-ACCOUNT")
            dlg.submit()
            pump(app)
            self.assertIn("All my mods", dlg.msg.kw["text"])
            self.assertIn("11104", dlg.msg.kw["text"])
            self.assertFalse(app.config_.logged_in)
        finally:
            FakeModio.no_read_tokens = set()
            FakeModio.valid_token = "TOKEN-OK"
        app.pool.shutdown(wait=True)


class KarlInstallTests(GuiBase):
    def test_karl_installs_everything(self):
        FakeModio.subs = {5}
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        app.check_modio_subscriptions()
        pump(app, 160)
        b, mine, lib = app.browse, app.mine, app.lib
        self.assertEqual(FakeModio.subs, set())
        self.assertTrue(self.installed("mod-5").exists())
        self.assertIn(5, b.library_ids())
        self.assertNotIn(1, b.library_ids())

        FakeModio.subs.add(9)
        b._auto_refresh()
        pump(app, 120)
        self.assertFalse(self.installed("mod-9").exists())

        self.assertIn(5, b.library_ids())
        app.pool.shutdown(wait=True)

    def test_foreign_loader_warning(self):
        hook = self.paks.parent.parent / "Binaries" / "Win64" / "x3daudio1_7.dll"
        hook.parent.mkdir(parents=True)
        hook.write_bytes(b"hook")
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 60)
        app.check_foreign_loader()
        self.assertFalse(hook.exists())
        self.assertIn("_karl_backup_altri_loader", app.status.get())
        app.check_foreign_loader()
        app.pool.shutdown(wait=True)

    def test_foreign_loader_kept_if_user_says_no(self):
        hook = self.paks.parent.parent / "Binaries" / "Win64" / "x3daudio1_7.dll"
        hook.parent.mkdir(parents=True)
        hook.write_bytes(b"hook")
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        mb = fake_tk.sys.modules["tkinter.messagebox"]
        asked = []
        old_ask, gui.app.messagebox.askyesno = gui.app.messagebox.askyesno, \
            lambda *a, **k: (asked.append(1), False)[1]
        try:
            app = gui.App()
            pump(app, 60)
            app.check_foreign_loader()
            self.assertTrue(hook.exists())
            self.assertTrue(Config.load().ignore_foreign_loader)
            app.check_foreign_loader()
            self.assertEqual(len(asked), 1)
            app.pool.shutdown(wait=True)
        finally:
            gui.app.messagebox.askyesno = old_ask

    def test_wrong_paks_folder_is_refused(self):
        wrong = Path(tempfile.mkdtemp()) / "a" / "b"
        wrong.mkdir(parents=True)
        lib = Library(Config(token="t", paks_dir=str(wrong)), client_factory=FakeModio)
        with self.assertRaises(modio.ModioError) as cm:
            lib.mods_dir()
        self.assertIn("FSD", str(cm.exception))
        self.assertFalse((wrong.parent.parent / "Mods").exists())


class LanguageTests(GuiBase):
    def tearDown(self):
        from karl import i18n
        i18n.set_language("it")

    def test_match_and_fallback(self):
        from karl import i18n
        cases = {"it_IT.UTF-8": "it", "pt_PT": "pt-BR", "es_MX": "es-419", "es_ES": "es-ES",
                 "es": "es-ES", "zh_TW": "zh-TW", "zh_HK": "zh-TW", "zh_CN": "zh-CN",
                 "de_AT": "de", "sv_SE": "en", "": "en", "C": "en", "uk_UA": "uk"}
        for tag, code in cases.items():
            self.assertEqual(i18n.match_language(tag), code, tag)
        i18n.set_language("en")
        self.assertEqual(i18n.tr("browse.count", n=5), "5 mods")
        self.assertEqual(i18n.tr("chiave.inesistente"), "chiave.inesistente")
        i18n._cache["en"]["browse.reset"] = ""
        try:
            self.assertEqual(i18n.tr("browse.reset"), "Azzera")
        finally:
            i18n._cache.pop("en")
        self.assertEqual(len(i18n.months()), 12)
        self.assertEqual(browse.human_date(1718236800), "Jun 13, 2024")

    def test_switch_language_live(self):
        from karl import i18n
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        self.assertEqual(app.mine.add_btn.kw["text"], "Aggiungi")
        old_browse = app.browse
        app.browse.cards[0].copy_link()
        dlg = gui.settings.SettingsDialog(app)
        dlg.language.set_key("en")
        dlg.save()
        pump(app, 80)
        self.assertEqual(app.mine.links.buf.strip(), "https://mod.io/g/drg/m/mod-1")
        app.mine.clear_links()
        self.assertEqual(i18n.current(), "en")
        self.assertEqual(Config.load().language, "en")
        self.assertIsNot(app.browse, old_browse)
        self.assertTrue(old_browse.destroyed)
        self.assertEqual(app.mine.add_btn.kw["text"], "Add")
        self.assertEqual(app.browse.count_lbl.kw["text"], "60 mods")
        self.assertEqual(app.browse.cards[0].copy_btn.kw["text"], "🔗 Copy link")
        self.assertEqual(app.browse.sort.key, "trending")
        self.assertEqual(app.status.get(), "Settings saved.")
        app.browse.sort.set_key("alpha")
        self.assertEqual(app.browse.sort.var.get(), "Alphabetical")
        self.assertEqual(app.browse.sort.key, "alpha")
        gui.settings.SettingsDialog(app).save()
        app.pool.shutdown(wait=True)


class AllLanguagesTest(GuiBase):
    def tearDown(self):
        from karl import i18n
        i18n.set_language("it")

    def test_every_language_builds(self):
        from karl import i18n
        Config(token=FakeModio.valid_token, username="giuseppe8086").save()
        app = gui.App()
        pump(app, 80)
        for code in i18n.CODES:
            app.change_language(code)
            pump(app, 60)
            self.assertEqual(i18n.current(), code)
            self.assertEqual(len(app.browse.cards), browse.PAGE_SIZE, code)
            self.assertEqual(app.browse.cards[0].copy_btn.kw["text"], "🔗 " + i18n.tr("browse.copy_link"))
            gui.dialogs.LoginDialog(app)
            gui.settings.SettingsDialog(app)
            app.mine._on_links_changed()
        app.pool.shutdown(wait=True)


class ExtractTests(unittest.TestCase):
    def test_extract(self):
        from karl.modio import extract_mod_urls
        urls, ignored = extract_mod_urls(
            "https://mod.io/g/drg/m/a, https://www.mod.io/g/DRG/m/b?x=1\n\n"
            "ciao\nhttps://mod.io/g/drg/m/a\n")
        self.assertEqual(urls, ["https://mod.io/g/drg/m/a", "https://mod.io/g/drg/m/b"])
        self.assertEqual(ignored, ["ciao"])


class TimeMachineGuiTests(GuiBase):
    def test_time_machine_dialog(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="giuseppe8086", ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        tm = lib.time_machine
        dlg = gui.timemachine.TimeMachineDialog(mine)
        self.assertEqual(dlg.tree.get_children(), [])
        dlg.destroy()

        lib.add("https://mod.io/g/drg/m/mod-30")
        lib.add("https://mod.io/g/drg/m/mod-31")
        mine.refresh()
        mine.apply_changes()
        pump(app, 80)
        self.assertEqual(len(tm.snapshots), 1)
        prima = tm.snapshots[-1]["id"]
        mine.tree.click("30", column="#0", region="tree")
        mine.apply_changes()
        pump(app, 80)
        self.assertFalse(self.installed("mod-30").exists())
        self.assertEqual(len(tm.snapshots), 2)

        dlg = gui.timemachine.TimeMachineDialog(mine)
        rows = dlg.tree.get_children()
        self.assertEqual(rows, [tm.snapshots[1]["id"], prima])
        self.assertIn("1", dlg.tree._rows[rows[0]][3])
        self.assertEqual(dlg.tree._rows[prima][3], gui.app.tr("tm.first"))
        dlg.tree.selection_set([prima])
        dlg.restore()
        self.assertTrue(dlg.destroyed)
        pump(app, 80)
        self.assertTrue(lib.mods[30].enabled)
        self.assertTrue(self.installed("mod-30").exists())
        self.assertIn(gui.common.format_when(tm.get(prima)["time"]), app.status.get())


class LinuxWindowTests(GuiBase):
    def test_grab_waits_until_visible(self):
        Config().save()
        app = gui.App()
        pump(app)
        calls = []

        def grab(self_):
            calls.append(1)
            if len(calls) < 3:
                raise fake_tk.TclError("grab failed: window not viewable")

        fake_tk.Widget.grab_set = grab
        self.addCleanup(lambda: delattr(fake_tk.Widget, "grab_set"))
        fake_tk.after_queue.clear()
        dlg = gui.dialogs.LoginDialog(app)
        self.assertTrue(hasattr(dlg, "token"))
        self.assertEqual(len(calls), 1)
        while fake_tk.after_queue:
            fn, args = fake_tk.after_queue.pop(0)
            if fn:
                fn(*args)
        self.assertEqual(len(calls), 3)

    def test_profile_filled_after_cli_token(self):
        Config(token=FakeModio.valid_token).save()
        app = gui.App()
        pump(app, 80)
        cfg = Config.load()
        self.assertEqual(cfg.username, "pe8086")
        self.assertEqual(cfg.profile_url, "https://mod.io/u/giuseppe8086")
        self.assertTrue(cfg.avatar_url)


class QualityTests(GuiBase):
    def test_favorite_note_profile_cache_updates(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        lib.add("https://mod.io/g/drg/m/mod-31")
        mine.refresh()
        self.assertEqual(mine.tree._rows["30"][4], "☆")
        mine.tree.click("30", column="#5")
        self.assertTrue(lib.mods[30].favorite)
        self.assertEqual(mine.tree._rows["30"][4], "★")
        self.assertEqual(mine.wanted, {})
        mine.show_var.set(mine.show_choices[1])
        mine.refresh()
        self.assertEqual(mine.tree.get_children(), ["30"])
        mine._move_selected(1)
        self.assertIn("ordine", app.status.get())
        mine.show_var.set(mine.show_choices[0])
        mine.refresh()
        self.assertEqual(sorted(mine.tree.get_children()), ["30", "31"])
        mine.tree.selection_set(["30"])
        sd = fake_tk.sys.modules["tkinter.simpledialog"]
        sd.answers[:] = ["solo con amici"]
        mine.edit_note()
        self.assertEqual(lib.mods[30].note, "solo con amici")
        self.assertTrue(mine.tree._rows["30"][0].endswith(" ✎"))
        self.assertIn("solo con amici", app.status.get())

        fd = fake_tk.sys.modules["tkinter.filedialog"]
        out = self.paks.parent / "conf.karl"
        fd.answers[:] = [str(out)]
        app.export_profile()
        self.assertTrue(out.exists())
        lib.remove(30)
        self.assertNotIn(30, lib.mods)
        fd.answers[:] = [str(out)]
        app.import_profile()
        pump(app, 120)
        self.assertIn(30, lib.mods)
        self.assertTrue(lib.mods[30].favorite)
        self.assertEqual(lib.mods[30].note, "solo con amici")
        self.assertTrue(self.installed("mod-30").exists())

        dlg = gui.settings.SettingsDialog(app)
        dlg.auto_updates.set(True)
        dlg.karl_updates.set(False)
        dlg.save()
        cfg = Config.load()
        self.assertTrue(cfg.auto_check_updates)
        self.assertFalse(cfg.check_karl_updates)

        cache = gui.dialogs.CacheDialog(app)
        self.assertIn("mods", cache.report)
        cache.clean()
        self.assertIn("Liberati", app.status.get())

        calls = []
        saved = lib.find_updates
        lib.find_updates = lambda **k: calls.append(k.get("ids")) or {"updates": [], "failed": []}
        self.addCleanup(setattr, lib, "find_updates", saved)
        app.auto_check_updates()
        pump(app)
        self.assertEqual(calls, [None])
        mine.check_updates()
        pump(app)
        self.assertEqual(calls[1], [m.mod_id for m in lib.folder_mods()])

        opened = []
        saved_open = gui.app.webbrowser.open
        gui.app.webbrowser.open = opened.append
        self.addCleanup(setattr, gui.app.webbrowser, "open", saved_open)
        app.config_.check_karl_updates = True
        mb = fake_tk.sys.modules["tkinter.messagebox"]
        mb.messages.clear()
        app.check_karl_update(fetch=lambda u: b'{"tag_name": "v9.0.0"}')
        pump(app)
        self.assertEqual(app.update_lbl.kw["text"], "⟳ !")
        self.assertEqual(opened, [])
        self.assertEqual(mb.messages, [])
        app.update_lbl.bindings["<Button-1>"](None)
        self.assertEqual(opened, [gui.app.selfupdate.RELEASES_PAGE])


class FeatureTests(GuiBase):
    def make_app(self):
        FakeModio.subs = set()
        FakeModio.deps = {}
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        pump(app, 80)
        return app

    def messages(self):
        return fake_tk.sys.modules["tkinter.messagebox"].messages

    def test_search_my_mods(self):
        app = self.make_app()
        mine, lib = app.mine, app.lib
        for i in (30, 31, 32):
            lib.add(f"https://mod.io/g/drg/m/mod-{i}")
        lib.set_note(31, "Rocket jump")
        mine.refresh()
        mine.search_var.set("31")
        self.assertEqual(mine.tree.get_children(), ["31"])
        mine.search_var.set("rocket")
        self.assertEqual(mine.tree.get_children(), ["31"])
        mine.search_var.set("  MOD   3 ")
        self.assertEqual(sorted(mine.tree.get_children()), ["30", "31", "32"])
        mine.search_var.set("mod-32")
        self.assertEqual(mine.tree.get_children(), ["32"])
        mine.tree.selection_set(["32"])
        mine._move_selected(-1)
        self.assertIn("ordine", app.status.get())
        mine.search_var.set("")
        self.assertEqual(sorted(mine.tree.get_children()), ["30", "31", "32"])

    def test_details_from_my_mods_and_browse(self):
        app = self.make_app()
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        mine.refresh()
        mine.tree.__dict__["_hit"] = ("cell", "#1", "30")
        self.assertEqual(mine._on_double(types.SimpleNamespace(x=1, y=1)), "break")
        pump(app)
        d = app._details
        self.assertEqual(d.mod_id, 30)
        self.assertEqual(d.name_lbl.kw["text"], "Mod 30")
        self.assertIn("Fixed things", d.files_text.buf)
        self.assertIn("2.0", d.files_text.buf)
        self.assertIn("Una mod di prova", d.desc_text.buf)
        self.assertIn("1.40", d.versions_lbl.kw["text"])
        self.assertIn(("files", 30), FakeModio.calls)
        self.assertEqual(lib.mods[30].deps, [])

        card = app.browse.cards[2]
        card.img.bindings["<Button-1>"](None)
        pump(app)
        self.assertIsNot(app._details, d)
        self.assertTrue(d.destroyed)
        self.assertEqual(app._details.mod_id, card.mod_id)
        self.assertIn("Non è tra le tue mod", app._details.state_lbl.kw["text"])

    def test_html_to_text(self):
        from karl.details import html_to_text
        self.assertEqual(html_to_text("<p>Ciao&nbsp;<b>mondo</b></p><ul><li>uno</li><li>due</li></ul>"),
                         "Ciao mondo\n\n• uno\n\n• due")
        self.assertEqual(html_to_text(""), "")

    def test_missing_dependencies_warning(self):
        app = self.make_app()
        mine = app.mine
        FakeModio.deps = {33: [{"mod_id": 34, "name": "Mod 34", "name_id": "mod-34"}]}
        self.addCleanup(setattr, FakeModio, "deps", {})
        self.messages().clear()
        mine.links.insert("1.0", "https://mod.io/g/drg/m/mod-33\n")
        mine.add_mod()
        pump(app, 80)
        self.assertIn(33, app.lib.mods)
        self.assertNotIn(34, app.lib.mods)
        warns = [m for m in self.messages() if m[0] == "warn"]
        self.assertTrue(any("Mod 34" in m[2] and "mod-34" in m[2] for m in warns))
        self.assertTrue(mine.tree._rows["33"][0].endswith(" ⚠"))
        mine.tree.selection_set(["33"])
        mine.show_note()
        self.assertIn("Mod 34", app.status.get())
        mine.set_states({33: False})
        self.assertFalse(mine.tree._rows["33"][0].endswith(" ⚠"))

    def test_game_update_warning(self):
        app = self.make_app()
        lib = app.lib
        old_tags = FakeModio.mods[34]["tags"]
        FakeModio.mods[34]["tags"] = [{"name": "1.39"}, {"name": "Verified"}]
        self.addCleanup(lambda: FakeModio.mods[34].__setitem__("tags", old_tags))
        lib.add("https://mod.io/g/drg/m/mod-30")
        lib.add("https://mod.io/g/drg/m/mod-35")
        self.messages().clear()
        lib.game_update_check = lambda: False
        app.check_game_update()
        pump(app)
        self.assertEqual(self.messages(), [])
        lib.game_update_check = lambda: True
        app.check_game_update()
        pump(app)
        warn = [m for m in self.messages() if m[0] == "warn"]
        self.assertEqual(len(warn), 1)
        self.assertIn("Mod 35", warn[0][2])
        self.assertNotIn("Mod 30", warn[0][2])
        self.assertIn("1.40", warn[0][2])

    def test_bring_to_front(self):
        app = self.make_app()
        app.bring_to_front()

class ShortcutCompactUpdateTests(GuiBase):
    def make_app(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        pump(app, 80)
        return app

    def test_compact_mode(self):
        app = self.make_app()
        mine = app.mine
        self.assertFalse(mine.compact)
        self.assertEqual(mine.compact_btn.kw["text"], "Nascondi link")
        mine.toggle_compact()
        self.assertTrue(mine.compact)
        self.assertEqual(mine.compact_btn.kw["text"], "Mostra link")
        self.assertTrue(Config.load().compact_links)
        app2 = gui.App()
        self.addCleanup(app2.pool.shutdown, wait=True)
        self.assertTrue(app2.mine.compact)
        self.assertTrue(app2.mine.append_link("https://mod.io/g/drg/m/mod-7"))
        self.assertFalse(app2.mine.compact)
        self.assertFalse(Config.load().compact_links)

    def test_shortcuts(self):
        app = self.make_app()
        mine = app.mine
        for key in ("<Control-f>", "<F5>", "<Control-s>", "<Control-l>"):
            self.assertIn(key, app.bindings)
        self.assertIn("<Delete>", mine.tree.bindings)
        calls = []
        mine.check_updates = lambda: calls.append("check")
        mine.apply_changes = lambda: calls.append("save")
        mine.remove = lambda: calls.append("remove")
        self.assertEqual(app.bindings["<F5>"](None), "break")
        self.assertEqual(app.bindings["<Control-s>"](None), "break")
        mine.tree.bindings["<Delete>"](None)
        app.bindings["<Control-f>"](None)
        app.bindings["<Control-l>"](None)
        self.assertEqual(calls, ["check", "save", "remove"])
        self.assertTrue(mine.compact)

    def test_github_link_and_update_icon(self):
        app = self.make_app()
        opened = []
        saved_open = gui.app.webbrowser.open
        gui.app.webbrowser.open = opened.append
        self.addCleanup(setattr, gui.app.webbrowser, "open", saved_open)
        app.github_lbl.bindings["<Button-1>"](None)
        self.assertEqual(opened, ["https://github.com/p8086/karl"])
        self.assertTrue(app.update_lbl.packed)
        self.assertEqual(app.update_lbl.kw["text"], "⟳")

        dlg = gui.settings.SettingsDialog(app)
        dlg.karl_updates.set(False)
        dlg.save()
        self.assertFalse(app.config_.check_karl_updates)
        self.assertFalse(app.update_lbl.packed)
        app.check_karl_update(fetch=lambda url: b'{"tag_name": "v99.0.0"}')
        pump(app)
        self.assertEqual(app.update_lbl.kw["text"], "⟳")
        self.assertFalse(app.update_lbl.packed)
        dlg = gui.settings.SettingsDialog(app)
        dlg.karl_updates.set(True)
        dlg.save()
        self.assertTrue(app.update_lbl.packed)

        app.karl_update_clicked(fetch=lambda url: b"{}")
        pump(app)
        self.assertIn("Impossibile controllare", app.status.get())
        app.karl_update_clicked(fetch=lambda url: b'{"tag_name": "v0.0.1"}')
        pump(app)
        self.assertIn("aggiornato", app.status.get())
        self.assertEqual(app.update_lbl.kw["text"], "⟳")
        app.karl_update_clicked(fetch=lambda url: b'{"tag_name": "v99.0.0", "html_url": "https://p/new"}')
        pump(app)
        self.assertEqual(app.update_lbl.kw["text"], "⟳ !")
        self.assertIn("99.0.0", app.status.get())
        self.assertEqual(len(opened), 1)
        app.update_lbl.bindings["<Enter>"](None)
        self.assertIn("clicca l'icona", app.status.get())
        app.update_lbl.bindings["<Leave>"](None)
        app.update_lbl.bindings["<Button-1>"](None)
        self.assertEqual(opened[-1], "https://p/new")

        prepared = []
        saved = (gui.app.selfupdate.can_self_update, gui.app.selfupdate.prepare, gui.app.selfupdate.restart)
        self.addCleanup(lambda: setattr(gui.app.selfupdate, "can_self_update", saved[0]) or
                        setattr(gui.app.selfupdate, "prepare", saved[1]) or
                        setattr(gui.app.selfupdate, "restart", saved[2]))
        gui.app.selfupdate.can_self_update = lambda: True
        gui.app.selfupdate.prepare = lambda rel, exe, work, progress=None: (
            prepared.append(rel["version"]) or {"kind": "linux", "exe": str(exe)})
        gui.app.selfupdate.restart = lambda plan: prepared.append(plan["kind"])
        app.update_lbl.bindings["<Button-1>"](None)
        pump(app)
        self.assertEqual(prepared, ["99.0.0", "linux"])
        self.assertTrue(app.destroyed)


    def test_karl_check_at_startup_option(self):
        app = self.make_app()
        calls = []
        app.check_karl_update = lambda fetch=None: calls.append("check")
        self.assertTrue(app.config_.karl_check_startup)
        app.startup_karl_check()
        self.assertEqual(calls, ["check"])
        dlg = gui.settings.SettingsDialog(app)
        self.assertEqual(dlg.karl_startup_check.kw.get("state"), "normal")
        dlg.karl_startup.set(False)
        dlg.save()
        self.assertFalse(Config.load().karl_check_startup)
        app.startup_karl_check()
        self.assertEqual(calls, ["check"])
        self.assertTrue(app.update_lbl.packed)
        dlg = gui.settings.SettingsDialog(app)
        dlg.karl_updates.set(False)
        dlg._sync_karl_startup()
        self.assertEqual(dlg.karl_startup_check.kw.get("state"), "disabled")

class FakeTrayModule:
    def __init__(self):
        import threading
        self.icons = []
        mod = self

        class Menu:
            SEPARATOR = "---"

            def __init__(self, *items):
                self.items = items

            def resolve(self):
                if len(self.items) == 1 and callable(self.items[0]):
                    return list(self.items[0]())
                return list(self.items)

        class MenuItem:
            def __init__(self, text, action, checked=None, radio=False, default=False):
                self.text, self.action, self.checked, self.default = text, action, checked, default

        class Icon:
            def __init__(self, name, image, title, menu):
                self.menu, self.stopped = menu, threading.Event()
                self.updates = 0
                mod.icons.append(self)

            def run(self):
                self.stopped.wait(5)

            def stop(self):
                self.stopped.set()

            def update_menu(self):
                self.updates += 1

        self.Menu, self.MenuItem, self.Icon = Menu, MenuItem, Icon


class BatchFourGuiTests(GuiBase):
    def make_app(self, **cfg):
        FakeModio.subs = set()
        FakeModio.deps = {}
        FakeModio.gone = set()
        FakeModio.votes = {}
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True, **cfg).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        pump(app, 80)
        return app

    def messages(self):
        return fake_tk.sys.modules["tkinter.messagebox"].messages

    def test_play_without_mods(self):
        app = self.make_app()
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        self.assertTrue(self.installed("mod-30").exists())
        state = {"running": False, "launched": 0}
        saved = (gui.app.game.is_running, gui.app.game.launch)
        self.addCleanup(lambda: (setattr(gui.app.game, "is_running", saved[0]),
                                 setattr(gui.app.game, "launch", saved[1])))
        gui.app.game.is_running = lambda: state["running"]
        gui.app.game.launch = lambda: state.__setitem__("launched", state["launched"] + 1)
        self.assertEqual(app.vanilla_btn.kw["text"], "Gioca senza mod")
        mine.play_vanilla()
        pump(app)
        self.assertEqual(state["launched"], 1)
        self.assertFalse(self.installed("mod-30").exists())
        self.assertTrue(app.config_.vanilla_active)
        self.assertIn("senza mod", app.status.get())
        mine._vanilla_tick()
        pump(app)
        self.assertTrue(app.config_.vanilla_active)
        state["running"] = True
        mine._vanilla_tick()
        pump(app)
        self.assertTrue(mine._vanilla_seen)
        state["running"] = False
        mine._vanilla_tick()
        pump(app, 80)
        self.assertFalse(app.config_.vanilla_active)
        self.assertTrue(self.installed("mod-30").exists())
        self.assertIn("di nuovo nel gioco", app.status.get())

        lib.start_vanilla()
        app2 = gui.App()
        self.addCleanup(app2.pool.shutdown, wait=True)
        pump(app2, 80)
        app2.mine.resume_vanilla()
        pump(app2, 80)
        self.assertFalse(app2.config_.vanilla_active)
        self.assertTrue(self.installed("mod-30").exists())

    def test_tags_sort_and_size(self):
        app = self.make_app()
        mine, lib = app.mine, app.lib
        for i in (30, 31, 32):
            lib.add(f"https://mod.io/g/drg/m/mod-{i}")
        lib.mods[31].added_at += 100
        mine.refresh()
        self.assertEqual(mine.tree._rows["30"][2], "3 B")
        mine.tree.selection_set(["30", "32"])
        sd = fake_tk.sys.modules["tkinter.simpledialog"]
        sd.answers[:] = ["HUD, Audio"]
        mine.edit_tags()
        self.assertEqual(lib.mods[32].tags, ["HUD", "Audio"])
        self.assertIn("Tag: HUD", mine.show_choices)
        self.assertTrue(mine.tree._rows["30"][0].endswith("[HUD, Audio]"))
        mine.show_var.set("Tag: HUD")
        mine.refresh()
        self.assertEqual(sorted(mine.tree.get_children()), ["30", "32"])
        mine._move_selected(1)
        self.assertIn("ordine", app.status.get())
        mine.show_var.set(mine.show_choices[0])
        mine.search_var.set("audio")
        self.assertEqual(sorted(mine.tree.get_children()), ["30", "32"])
        mine.search_var.set("")
        mine.sort_var.set(mine.sort_labels[mine.sort_keys.index("added")])
        mine.refresh()
        self.assertEqual(mine.tree.get_children()[0], "31")
        self.assertTrue(mine.filtered())
        mine.sort_var.set(mine.sort_labels[mine.sort_keys.index("name")])
        mine.refresh()
        self.assertEqual(mine.tree.get_children(), ["30", "31", "32"])
        mine.sort_var.set(mine.sort_labels[0])
        self.assertFalse(mine.filtered())
        mine.tree.selection_set(["30", "32"])
        sd.answers[:] = [""]
        mine.edit_tags()
        self.assertEqual(mine.show_choices, [mine.show_choices[0], mine.show_choices[1]])

    def test_drop_files_and_links(self):
        app = self.make_app()
        self.assertFalse(app.dnd)
        src = Path(tempfile.mkdtemp())
        pak = src / "Mia Mod.pak"
        pak.write_bytes(b"PAK")
        added = []
        app.mine.add_mod = lambda: added.append(app.mine.links_text())
        n = app.handle_drop("{" + str(pak) + "} https://mod.io/g/drg/m/mod-40 " + str(src / "x.txt"))
        self.assertEqual(n, 2)
        self.assertIn(str(pak), added[0])
        self.assertIn("https://mod.io/g/drg/m/mod-40", added[0])
        self.assertEqual(app.handle_drop("ciao"), 0)
        self.assertIn("Niente da aggiungere", app.status.get())

    def test_removed_mods_warning(self):
        app = self.make_app()
        lib = app.lib
        lib.add("https://mod.io/g/drg/m/mod-30")
        lib.add("https://mod.io/g/drg/m/mod-31")
        FakeModio.gone = {31}
        self.messages().clear()
        app.check_removed_mods()
        pump(app)
        warns = [m for m in self.messages() if m[0] == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("Mod 31", warns[0][2])
        self.assertNotIn("Mod 30", warns[0][2])
        self.assertIn(" ⊘", app.mine.tree._rows["31"][0])
        app.check_removed_mods()
        pump(app)
        self.assertEqual(len([m for m in self.messages() if m[0] == "warn"]), 1)
        app.config_.warn_removed = False
        FakeModio.gone = {30, 31}
        app.check_removed_mods()
        pump(app)
        self.assertFalse(lib.mods[30].removed)

    def test_vote_from_details(self):
        app = self.make_app()
        FakeModio.mods[29]["stats"]["ratings_positive"] = 10
        self.addCleanup(lambda: FakeModio.mods[29]["stats"].pop("ratings_positive", None))
        app.open_details(2475, 30, FakeModio.mods[29])
        pump(app)
        d = app._details
        self.assertEqual(d.up_btn.kw["text"], "▲ 10")
        d.vote(1)
        pump(app)
        self.assertEqual(FakeModio.votes[30], 1)
        self.assertEqual(d.up_btn.kw["text"], "▲ 11")
        self.assertEqual(d.up_btn.kw["style"], "Accent.TButton")
        d.vote(-1)
        pump(app)
        self.assertEqual(FakeModio.votes[30], -1)
        self.assertEqual(d.up_btn.kw["text"], "▲ 10")
        self.assertEqual(d.down_btn.kw["text"], "▼ 1")
        d.vote(-1)
        pump(app)
        self.assertEqual(FakeModio.votes[30], 0)
        self.assertEqual(d.down_btn.kw["text"], "▼ 0")

    def test_diagnostics_and_log(self):
        app = self.make_app()
        FakeModio.me_calls = 0
        dlg = gui.dialogs.DiagnosticsDialog(app)
        pump(app)
        self.assertIn("[ OK ] Paks folder", dlg.report)
        self.assertNotIn(FakeModio.valid_token, dlg.report)
        dlg.copy()
        self.assertEqual(fake_tk.Widget.clipboard, dlg.report)
        opened = []
        saved = gui.app.open_path
        gui.app.open_path = opened.append
        self.addCleanup(setattr, gui.app, "open_path", saved)
        gui.app.karl_log.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        gui.app.karl_log.LOG_FILE.touch()
        app.open_log()
        self.assertEqual(opened, [gui.app.karl_log.LOG_FILE])

    def test_tray(self):
        fake = FakeTrayModule()
        sys.modules["pystray"] = fake
        self.addCleanup(sys.modules.pop, "pystray", None)
        app = self.make_app(close_to_tray=True)
        self.assertTrue(app.tray.running)
        icon = fake.icons[-1]
        calls = []
        app.mine.play_vanilla = lambda: calls.append("vanilla")
        items = icon.menu.resolve()
        texts = [getattr(i, "text", i) for i in items]
        self.assertEqual(texts[:3], ["Mostra Karl", "▶ Gioca", "Gioca senza mod"])
        self.assertTrue(items[0].default)
        sub = items[3].action.resolve()
        self.assertEqual([m.text for m in sub], list(app.lib.folders))
        self.assertTrue(sub[0].checked(sub[0]))
        items[2].action(icon, items[2])
        pump(app)
        self.assertEqual(calls, ["vanilla"])
        app.on_close()
        self.assertFalse(app.destroyed)
        items[-1].action(icon, items[-1])
        pump(app)
        self.assertTrue(app.destroyed)
        self.assertTrue(icon.stopped.is_set())
        self.assertFalse(app.tray.running)

    def test_vanilla_button_next_to_play(self):
        app = self.make_app()
        self.assertIs(app.vanilla_btn.master, app.play_btn.master)

class InstallsScaleGuiTests(GuiBase):
    def make_app(self, **cfg):
        FakeModio.subs = set()
        FakeModio.deps = {}
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True, **cfg).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        self.addCleanup(paths.use_install, paths.MAIN_INSTALL)
        pump(app, 80)
        return app

    def test_switch_installation_from_settings(self):
        app = self.make_app()
        app.lib.add("https://mod.io/g/drg/m/mod-30")
        self.assertEqual(app.install_lbl.kw["text"], "")
        beta = Path(tempfile.mkdtemp()) / "Beta" / "FSD" / "Content" / "Paks"
        beta.mkdir(parents=True)
        sd = fake_tk.sys.modules["tkinter.simpledialog"]
        dlg = gui.settings.SettingsDialog(app)
        sd.answers[:] = ["Beta"]
        dlg.add_install()
        self.assertEqual(dlg.paks.get(), "")
        dlg.paks.set(str(self.paks))
        mb = fake_tk.sys.modules["tkinter.messagebox"]
        mb.messages.clear()
        dlg.save()
        self.assertEqual(mb.messages[-1][0], "error")
        self.assertEqual(app.config_.active_install, "main")
        dlg.paks.set(str(beta))
        dlg.save()
        pump(app)
        cfg = app.config_
        self.assertNotEqual(cfg.active_install, "main")
        self.assertEqual(cfg.paks_dir, str(beta))
        self.assertEqual(app.lib.mods, {})
        self.assertEqual(app.mine.tree.get_children(), [])
        self.assertEqual(app.install_lbl.kw["text"], "·  Beta")
        self.assertIn("Beta", app.status.get())

        dlg = gui.settings.SettingsDialog(app)
        main_label = dlg._install_label(dlg._install("main"))
        dlg.install_var.set(main_label)
        dlg._pick_install()
        self.assertEqual(dlg.paks.get(), str(self.paks))
        dlg.save()
        pump(app)
        self.assertEqual(app.config_.active_install, "main")
        self.assertIn(30, app.lib.mods)
        self.assertEqual(app.install_lbl.kw["text"], "·  Deep Rock Galactic")

        beta_id = [i["id"] for i in app.config_.installs if i["name"] == "Beta"][0]
        data_dir = paths.install_dirs(beta_id)[0]
        data_dir.mkdir(parents=True, exist_ok=True)
        dlg = gui.settings.SettingsDialog(app)
        dlg.remove_install()
        self.assertEqual(len(dlg.installs), 2)
        dlg.install_var.set(dlg._install_label(dlg._install(beta_id)))
        dlg._pick_install()
        dlg.remove_install()
        dlg.save()
        self.assertEqual([i["id"] for i in app.config_.installs], ["main"])
        self.assertFalse(data_dir.exists())
        self.assertEqual(app.install_lbl.kw["text"], "")

    def test_interface_scale(self):
        app = self.make_app(ui_scale=1.5)
        self.assertEqual(gui.app.ui.SCALE, 1.5)
        self.assertEqual(browse.CARD_W, 435)
        restarted = []
        app.restart = lambda: restarted.append(True)
        dlg = gui.settings.SettingsDialog(app)
        self.assertEqual(dlg.scale_var.get(), "150%")
        dlg.scale_var.set("100%")
        dlg.save()
        self.assertEqual(Config.load().ui_scale, 1.0)
        self.assertEqual(restarted, [True])
        app2 = gui.App()
        self.addCleanup(app2.pool.shutdown, wait=True)
        self.assertEqual(gui.app.ui.SCALE, 1.0)
        self.assertEqual(browse.CARD_W, 290)

class PaksValidationTests(GuiBase):
    def test_settings_refuse_missing_paks(self):
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        pump(app, 80)
        good = app.config_.paks_dir
        dlg = gui.settings.SettingsDialog(app)
        dlg.paks.set(good + "/home/pe8086/.steam/Paks")
        mb = fake_tk.sys.modules["tkinter.messagebox"]
        mb.messages.clear()
        dlg.save()
        self.assertEqual(mb.messages[-1][0], "error")
        self.assertEqual(Config.load().paks_dir, good)
        self.assertFalse(dlg.destroyed)

class CrashBisectNewsGuiTests(GuiBase):
    def make_app(self, **cfg):
        FakeModio.subs = set()
        FakeModio.deps = {}
        if paths.HISTORY_FILE.exists():
            paths.HISTORY_FILE.unlink()
        Config(token=FakeModio.valid_token, username="pe8086", avatar_url="a", profile_url="p",
               ignore_modio_subs=True, last_seen_version=gui.app.VERSION, **cfg).save()
        app = gui.App()
        self.addCleanup(app.pool.shutdown, wait=True)
        pump(app, 80)
        return app

    def patch(self, obj, name, value):
        saved = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, saved)

    def test_sviluppo_section_has_crash_and_bisect(self):
        app = self.make_app()
        texts = []

        def walk(w):
            for c in w.children_:
                if c.kw.get("text"):
                    texts.append(c.kw["text"])
                walk(c)
        walk(app.mine)
        dev = texts.index(gui.app.tr("dev.title"))
        self.assertIn(gui.app.tr("crash.watch"), texts[dev:])
        self.assertIn(gui.app.tr("bisect.button"), texts[dev:])
        app.mine.crash_var.set(False)
        app.mine._crash_setting_changed()
        self.assertFalse(Config.load().crash_watch)

    def test_crash_detection(self):
        app = self.make_app()
        state = {"running": False}
        self.patch(gui.app.game, "is_running", lambda: state["running"])
        self.patch(gui.app.game, "launch", lambda: None)
        shown = []
        self.patch(gui.app, "CrashDialog", lambda a, found: shown.append(found))
        found = {"time": 1.0, "files": ["x"], "detail": "Unhandled Exception: boom", "ue4ss": True}
        result = {"value": found}
        self.patch(gui.app.crash, "find_crash", lambda paks, since: result["value"])
        app.withdraw_called = []
        fronts = []
        self.patch(app, "bring_to_front", lambda: fronts.append(1))
        app.mine._launch()
        self.assertGreater(app.config_.last_launch, 0)
        state["running"] = True
        app._crash_tick()
        pump(app)
        self.assertTrue(app._crash_seen)
        state["running"] = False
        app._crash_tick()
        pump(app)
        self.assertEqual(shown, [found])
        self.assertEqual(fronts, [1])
        self.assertEqual(app.config_.last_launch, 0)
        self.assertEqual(app.lib.history.entries[-1]["kind"], "crash")
        result["value"] = None
        app.mine._launch()
        app._crash_seen = True
        app.game_closed()
        pump(app)
        self.assertEqual(app.lib.history.entries[-1]["kind"], "session_ok")
        app.config_.crash_watch = False
        result["value"] = found
        app.mine._launch()
        app.game_closed()
        pump(app)
        self.assertEqual(len(shown), 1)

        app.config_.crash_watch = True
        app.config_.last_launch = 5.0
        app.config_.save()
        app2 = gui.App()
        self.addCleanup(app2.pool.shutdown, wait=True)
        app2.resume_after_restart()
        pump(app2)
        self.assertEqual(len(shown), 2)

    def test_crash_dialog_and_history_tab(self):
        app = self.make_app()
        app.lib.add("https://mod.io/g/drg/m/mod-30")
        app.lib.history.add("session_ok", folder=app.lib.active_folder)
        app.lib.apply({30: False})
        dlg = gui.troubleshoot.CrashDialog(app, {"time": 1700000000, "files": ["x"],
                                    "detail": "Unhandled Exception: boom", "ue4ss": False})
        self.assertIn("boom", dlg.text.buf)
        self.assertIn("Mod 30", dlg.text.buf)
        tm = gui.timemachine.TimeMachineDialog(app.mine)
        rows = list(tm.hist_tree._rows.values())
        self.assertTrue(any("Mod 30" in r[2] for r in rows))
        tm.hist_search.set("nessuna corrispondenza")
        self.assertEqual(tm.hist_tree.get_children(), [])

    def test_bisect_flow(self):
        app = self.make_app()
        lib = app.lib
        for i in (30, 31, 32, 33, 34):
            lib.add(f"https://mod.io/g/drg/m/mod-{i}")
        lib.apply({34: False})
        lib.mods[31].deps = [{"id": 30, "name": "Mod 30", "url": ""}]
        launched = []
        self.patch(gui.app.game, "launch", lambda: launched.append(1))
        app.mine.open_bisect()
        dlg = app.bisect_win
        self.assertEqual(Config.load().bisect_restore,
                         {"30": True, "31": True, "32": True, "33": True, "34": False})
        culprit = 32
        steps = 0
        dlg.next_test()
        pump(app)
        while not dlg.bs.done and steps < 10:
            on = {m for m, e in lib.mods.items() if e.enabled}
            self.assertNotIn(34, on)
            if 31 in on:
                self.assertIn(30, on)
            dlg.launch()
            dlg.answer(culprit in on)
            pump(app)
            steps += 1
        self.assertEqual(dlg.bs.result(), [culprit])
        self.assertIn("Mod 32", dlg.text.buf)
        self.assertTrue(launched)
        dlg.crash_detected({})
        self.assertIn("crash", dlg.alert.kw["text"].lower())
        dlg.disable_culprit()
        pump(app)
        self.assertEqual({i: e.enabled for i, e in lib.mods.items()},
                         {30: True, 31: True, 32: False, 33: True, 34: False})
        self.assertEqual(Config.load().bisect_restore, {})
        self.assertTrue(dlg.destroyed)
        kinds = [e["kind"] for e in lib.history.entries]
        self.assertIn("culprit", kinds)
        self.assertEqual(kinds.count("off"), 1)

    def test_bisect_restore_after_restart(self):
        app = self.make_app()
        lib = app.lib
        for i in (30, 31):
            lib.add(f"https://mod.io/g/drg/m/mod-{i}")
        app.mine.open_bisect()
        app.bisect_win.next_test()
        pump(app)
        self.assertFalse(all(e.enabled for e in lib.mods.values()))
        app2 = gui.App()
        self.addCleanup(app2.pool.shutdown, wait=True)
        pump(app2, 80)
        app2.resume_after_restart()
        pump(app2, 80)
        self.assertTrue(all(e.enabled for e in app2.lib.mods.values()))
        self.assertEqual(Config.load().bisect_restore, {})

    def test_whats_new(self):
        app = self.make_app()
        shown = []
        orig = gui.dialogs.WhatsNewDialog
        self.patch(gui.mine, "WhatsNewDialog", lambda *a, **k: shown.append(orig(*a, **k)))
        app.lib.add("https://mod.io/g/drg/m/mod-30")
        FakeModio.mods[29]["modfile"]["id"] = 555
        FakeModio.mods[29]["modfile"]["version"] = "2.0"
        self.addCleanup(lambda: FakeModio.mods[29]["modfile"].update(id=129, version="1.0"))
        app.lib.find_updates()
        app.mine.apply_changes()
        pump(app, 80)
        self.assertEqual(len(shown), 1)
        dlg = shown[0]
        pump(app)
        self.assertIn("1.0 → 2.0", dlg.text.buf)
        self.assertIn("Novità 30", dlg.text.buf)
        dlg.hide_var.set(True)
        dlg.close()
        self.assertFalse(Config.load().show_whats_new)
        FakeModio.mods[29]["modfile"]["id"] = 556
        app.lib.find_updates()
        app.mine.apply_changes()
        pump(app, 80)
        self.assertEqual(len(shown), 1)

    def test_karl_notes(self):
        app = self.make_app()
        shown = []
        self.patch(gui.app, "WhatsNewDialog", lambda *a, **k: shown.append(k.get("karl_notes")))
        app.config_.last_seen_version = "0.0.1"
        app.check_karl_notes(fetch=lambda u: b'{"body": "Note di rilascio"}')
        pump(app)
        self.assertEqual(shown, ["Note di rilascio"])
        self.assertEqual(Config.load().last_seen_version, gui.app.VERSION)
        app.check_karl_notes(fetch=lambda u: b'{"body": "x"}')
        pump(app)
        self.assertEqual(len(shown), 1)

if __name__ == "__main__":
    unittest.main()
