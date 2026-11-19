import io
import os
import sys
import tempfile
import time
import unittest
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

from karl import browse, gui, library
from karl.browse import LIB_ALL, LIB_NOT_SUB, LIB_SUB, build_query
from karl.library import MODE_LOCAL, MODE_SUBSCRIBE, Config, Library

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
            raise library.ModioError("Manca il token")
        self.token = token

    def _log(self, *a):
        FakeModio.calls.append(a)

    def me(self):
        if self.token != FakeModio.valid_token:
            raise library.ModioError("Token mod.io non valido o scaduto (401).")
        return {"username": "pe8086", "name_id": "giuseppe8086",
                "profile_url": "https://mod.io/u/giuseppe8086",
                "avatar": {"thumb_50x50": "https://img/avatar.png"}}

    no_read_tokens: set = set()

    def check_access(self):
        me = self.me()
        if self.token in FakeModio.no_read_tokens:
            e = library.ModioError("Token mod.io non valido o scaduto. (401 · 11104)", status=401)
            e.read_failed = True
            raise e
        return me

    def game_id(self, slug):
        return 2475

    def get_mod(self, game, slug):
        for m in FakeModio.mods:
            if m["name_id"] == slug:
                return m
        raise library.ModioError(f"Mod '{slug}' non trovata.")

    def game_tags(self, gid):
        self._log("tags")
        return TAGS

    def list_mods(self, gid, params):
        self._log("list", dict(params))
        off, lim = params["_offset"], params["_limit"]
        return FakeModio.mods[off:off + lim], len(FakeModio.mods)

    def subscribed_mods(self, gid):
        return [m for m in FakeModio.mods if m["id"] in FakeModio.subs]

    def subscribe(self, gid, mid):
        FakeModio.subs.add(mid)

    def unsubscribe(self, gid, mid):
        FakeModio.subs.discard(mid)

    def download(self, url, dest, progress=None):
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


class SyncTests(unittest.TestCase):
    def setUp(self):
        for f in (library.LIBRARY_FILE, library.FOLDERS_FILE, library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        self.lib = Library(Config(token="t", paks_dir=str(TMP), default_mode=MODE_SUBSCRIBE),
                           client_factory=FakeModio)

    def test_sync(self):
        lib = self.lib
        lib.mods[99] = library.ModEntry(99, 2475, "Locale", "loc", "", MODE_LOCAL)
        self.assertTrue(lib.sync_subscriptions([make_mod(1), make_mod(2)]))
        self.assertEqual(lib.mods[1].mode, MODE_SUBSCRIBE)
        self.assertTrue(lib.mods[1].enabled)
        self.assertTrue(lib.sync_subscriptions([make_mod(2)]))
        self.assertFalse(lib.mods[1].enabled)
        self.assertTrue(lib.mods[99].enabled)
        self.assertFalse(lib.sync_subscriptions([make_mod(2)]))
        self.assertTrue(lib.sync_subscriptions([make_mod(1), make_mod(2)]))
        self.assertTrue(lib.mods[1].enabled)


class GuiBase(unittest.TestCase):
    def setUp(self):
        for f in (library.CONFIG_FILE, library.LIBRARY_FILE, library.FOLDERS_FILE,
                  library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        FakeModio.subs = {5}
        FakeModio.calls = []
        gui.ModioClient = FakeModio
        library.ModioClient = FakeModio
        Library.__init__.__defaults__ = (FakeModio,)
        browse.fetch_bytes = lambda url, timeout=20: png_bytes()
        browse.ImageTk.PhotoImage = lambda im: ("foto", im.size)
        fake_tk.sys.modules["tkinter.messagebox"].messages.clear()
        fake_tk.Widget.clipboard = None
        self.paks = Path(tempfile.mkdtemp()) / "Deep Rock Galactic" / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        self.mods = self.paks.parent.parent / "Mods"
        library.find_paks_dir = lambda: self.paks

    def installed(self, slug):
        found = sorted(self.paks.glob(f"FSD-WindowsNoEditor_karl_{slug}_*_P.pak"))
        return found[0] if found else self.paks / f"FSD-WindowsNoEditor_karl_{slug}_0_P.pak"


class GuiTests(GuiBase):
    def test_full_flow(self):
        app = gui.App()
        pump(app)
        b = app.browse
        self.assertFalse(b.started)

        dlg = gui.LoginDialog(app)
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
        saved_open = gui.webbrowser.open
        self.addCleanup(setattr, gui.webbrowser, "open", saved_open)
        gui.webbrowser.open = opened.append
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
        self.assertEqual(b.sub_ids, set())
        self.assertNotIn(5, app.lib.mods)
        self.assertIsNone(b.mod_state(5))
        self.assertIsNone(b.mod_state(1))
        self.assertEqual(len(b.sidebar.vars["Type"]), 4)

        b._maybe_load_more(0.99)
        pump(app)
        self.assertEqual(len(b.cards), 2 * browse.PAGE_SIZE)
        b._maybe_load_more(0.99)
        pump(app)
        self.assertEqual(len(b.cards), 60)
        self.assertFalse(b.has_more())

        b.cards[0].toggle_subscription()
        pump(app)
        self.assertNotIn(1, FakeModio.subs)
        self.assertEqual(app.lib.mods[1].mode, MODE_LOCAL)
        self.assertEqual(b.mod_state(1), "installed")
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
        self.assertIsNone(b.mod_state(7))
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
        gui.SettingsDialog(app).save()
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
        old = gui.messagebox.askyesno
        gui.messagebox.askyesno = lambda *a, **k: False
        app.on_close()
        self.assertFalse(getattr(app, "destroyed", False))
        gui.messagebox.askyesno = old
        app.pool.shutdown(wait=True)


class FolderTests(GuiBase):
    def test_folders_gui(self):
        FakeModio.subs = set()
        Config(token=FakeModio.valid_token, username="giuseppe8086", ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30", MODE_LOCAL)
        lib.add("https://mod.io/g/drg/m/mod-31", MODE_LOCAL)
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
        saved = (game.launch, game.is_running, game.close, gui.time)
        def restore():
            game.launch, game.is_running, game.close, gui.time = saved
        self.addCleanup(restore)
        game.launch = lambda: launched.append(1)
        game.is_running = lambda: running[0]
        game.close = lambda: running.__setitem__(0, False) or True
        gui.time = fake_tk.types.SimpleNamespace(sleep=lambda s: None, time=time.time)
        Config(token=FakeModio.valid_token, username="giuseppe8086", ignore_modio_subs=True).save()
        app = gui.App()
        pump(app, 80)
        mine, lib = app.mine, app.lib
        lib.add("https://mod.io/g/drg/m/mod-30", MODE_LOCAL)
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
        e = lib.add("https://mod.io/g/drg/m/mod-30", MODE_LOCAL)
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
        self.assertEqual(file_row[2], "file .pak")
        fake_tk.sys.modules["tkinter.filedialog"].askopenfilenames = \
            lambda **k: (str(src / "Altra.pak"),)
        gui.filedialog.askopenfilenames = fake_tk.sys.modules["tkinter.filedialog"].askopenfilenames
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
        app.lib.add("https://mod.io/g/drg/m/mod-30", MODE_LOCAL)
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
        dlg = gui.SettingsDialog(app)
        texts = lambda: [w.kw.get("text") for w in self._all(dlg.token_box)]
        self.assertIn("Collegato come giuseppe8086", texts())
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
        login = [w for w in app.children_ if isinstance(w, gui.LoginDialog)][-1]
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
        from karl import modio
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
            c.subscribe(2475, 1)
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
            dlg = gui.LoginDialog(app)
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
        self.assertEqual(app.config_.default_mode, MODE_LOCAL)

        self.assertEqual(FakeModio.subs, set())
        self.assertEqual(lib.mods[5].mode, MODE_LOCAL)
        self.assertTrue(self.installed("mod-5").exists())
        self.assertEqual(b.mod_state(5), "installed")

        card = b.cards[0]
        self.assertIsNone(b.mod_state(1))
        card.toggle_subscription()
        pump(app)
        self.assertTrue(self.installed("mod-1").exists())
        self.assertEqual(lib.mods[1].mode, MODE_LOCAL)
        self.assertNotIn(1, FakeModio.subs)
        self.assertEqual(b.mod_state(1), "installed")
        self.assertIn("installata da Karl", app.status.get())
        card.toggle_subscription()
        pump(app)
        self.assertNotIn(1, lib.mods)
        self.assertFalse(self.installed("mod-1").exists())
        self.assertIsNone(b.mod_state(1))

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
        old_ask, gui.messagebox.askyesno = gui.messagebox.askyesno, \
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
            gui.messagebox.askyesno = old_ask

    def test_wrong_paks_folder_is_refused(self):
        wrong = Path(tempfile.mkdtemp()) / "a" / "b"
        wrong.mkdir(parents=True)
        lib = Library(Config(token="t", paks_dir=str(wrong)), client_factory=FakeModio)
        with self.assertRaises(library.ModioError) as cm:
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
        dlg = gui.SettingsDialog(app)
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
        gui.SettingsDialog(app).save()
        self.assertEqual(Config.load().default_mode, MODE_LOCAL)
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
            gui.LoginDialog(app)
            gui.SettingsDialog(app)
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
        dlg = gui.TimeMachineDialog(mine)
        self.assertEqual(dlg.tree.get_children(), [])
        dlg.destroy()

        lib.add("https://mod.io/g/drg/m/mod-30", MODE_LOCAL)
        lib.add("https://mod.io/g/drg/m/mod-31", MODE_LOCAL)
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

        dlg = gui.TimeMachineDialog(mine)
        rows = dlg.tree.get_children()
        self.assertEqual(rows, [tm.snapshots[1]["id"], prima])
        self.assertIn("1", dlg.tree._rows[rows[0]][3])
        self.assertEqual(dlg.tree._rows[prima][3], gui.tr("tm.first"))
        dlg.tree.selection_set([prima])
        dlg.restore()
        self.assertTrue(dlg.destroyed)
        pump(app, 80)
        self.assertTrue(lib.mods[30].enabled)
        self.assertTrue(self.installed("mod-30").exists())
        self.assertIn(gui.format_when(tm.get(prima)["time"]), app.status.get())


if __name__ == "__main__":
    unittest.main()
