import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_CACHE_HOME"] = str(TMP / "cache")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karl import library
from karl.library import MODE_LOCAL, MODE_SUBSCRIBE, Config, Library
from karl.modio import parse_mod_url


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeClient:
    version = {"file_id": 1, "v": "1.0"}
    subs: set = set()

    def __init__(self, token):
        self.token = token

    def _mod(self):
        v = FakeClient.version
        return {"id": 42, "game_id": 2475, "name": "Test Mod", "name_id": "test-mod",
                "modfile": {"id": v["file_id"], "version": v["v"], "filename": "test.zip",
                            "download": {"binary_url": "https://x/test.zip"}}}

    def get_mod(self, game, slug):
        return self._mod()

    def get_mod_by_id(self, g, m):
        return self._mod()

    def get_modfile(self, g, m, f):
        return {"id": f, "version": f"{f}.0", "filename": "test.zip",
                "download": {"binary_url": "https://x/test.zip"}}

    def subscribed_mods(self, g):
        return [self._mod()] if 42 in FakeClient.subs else []

    def subscribe(self, g, m):
        FakeClient.subs.add(m)

    def unsubscribe(self, g, m):
        FakeClient.subs.discard(m)

    deps: dict = {}
    md5_seen: list = []

    def dependencies(self, g, m):
        return FakeClient.deps.get(m, [])

    def list_modfiles(self, g, m, limit=10):
        return []

    def download(self, url, dest, progress=None, md5=""):
        FakeClient.md5_seen.append(md5)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = make_zip({"cartella/../../evil/Test.pak": b"PAK" + FakeClient.version["v"].encode(),
                         "readme.txt": b"ciao"})
        dest.write_bytes(data)
        if progress:
            progress(len(data), len(data))
        return dest


class Tests(unittest.TestCase):
    def inst(self, slug):
        found = sorted(self.paks.glob(f"FSD-WindowsNoEditor_karl_{slug}_*_P.pak"))
        return found[0] if found else self.paks / f"FSD-WindowsNoEditor_karl_{slug}_0_P.pak"

    def setUp(self):
        self.paks = Path(tempfile.mkdtemp()) / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        self.mods = self.paks.parent.parent / "Mods"
        (self.paks / "FSD-WindowsNoEditor.pak").write_bytes(b"originale")
        self.cfg = Config(token="t", paks_dir=str(self.paks))
        for f in (library.LIBRARY_FILE, library.FOLDERS_FILE, library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        FakeClient.version = {"file_id": 1, "v": "1.0"}
        FakeClient.subs = set()
        self.lib = Library(self.cfg, client_factory=FakeClient)

    def test_parse_url(self):
        self.assertEqual(parse_mod_url("https://mod.io/g/drg/m/sandbox-utilities#description"),
                         ("drg", "sandbox-utilities"))
        self.assertEqual(parse_mod_url("  https://www.mod.io/g/DRG/m/peHUD/  "), ("drg", "pehud"))
        with self.assertRaises(ValueError):
            parse_mod_url("https://example.com/mod.pak")

    def test_local_install_toggle_remove(self):
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        pak = self.inst("test-mod")
        self.assertTrue(pak.exists())
        self.assertEqual(pak.read_bytes(), b"PAK1.0")
        self.assertFalse((TMP / "evil").exists())
        self.lib.set_enabled(e.mod_id, False)
        self.assertFalse(pak.exists())
        self.lib.set_enabled(e.mod_id, True)
        self.assertTrue(pak.exists())
        self.assertIn(42, Library(self.cfg, client_factory=FakeClient).mods)
        self.lib.remove(e.mod_id)
        self.assertFalse(pak.exists())
        self.assertEqual(self.lib.mods, {})
        self.assertTrue((self.paks / "FSD-WindowsNoEditor.pak").exists())

    def test_update_local(self):
        self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        self.assertEqual(self.lib.check_updates(), [])
        FakeClient.version = {"file_id": 2, "v": "2.0"}
        self.assertEqual(self.lib.check_updates(), ["Test Mod"])
        self.assertEqual((self.inst("test-mod")).read_bytes(), b"PAK2.0")
        self.assertEqual(self.lib.mods[42].version, "2.0")

    def test_never_subscribes(self):
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_SUBSCRIBE)
        self.assertEqual(e.mode, MODE_LOCAL)
        self.assertEqual(FakeClient.subs, set())
        self.assertTrue((self.inst("test-mod")).exists())
        self.assertFalse(self.mods.exists())
        self.lib.set_enabled(e.mod_id, False)
        self.lib.set_enabled(e.mod_id, True)
        self.lib.remove(e.mod_id)
        self.assertEqual(FakeClient.subs, set())

    def test_migrate_legacy(self):
        import json, shutil
        shutil.rmtree(library.CONFIG_DIR, ignore_errors=True)
        shutil.rmtree(library.CACHE_DIR, ignore_errors=True)
        old_cfg = library.CONFIG_DIR.parent / "drgmm"
        old_cache = library.CACHE_DIR.parent / "drgmm"
        (old_cache / "42" / "paks").mkdir(parents=True)
        (old_cache / "42" / "paks" / "Test.pak").write_bytes(b"PAK1.0")
        old_cfg.mkdir(parents=True)
        (old_cfg / "config.json").write_text(json.dumps({"token": "vecchio", "paks_dir": str(self.paks)}))
        (self.paks / "drgmm_test-mod_P.pak").write_bytes(b"PAK1.0")
        (old_cfg / "mods.json").write_text(json.dumps([{
            "mod_id": 42, "game_id": 2475, "name": "Test Mod", "slug": "test-mod",
            "url": "u", "mode": MODE_LOCAL, "file_id": 1, "version": "1.0",
            "cached_paks": [str(old_cache / "42" / "paks" / "Test.pak")],
            "installed_paks": ["drgmm_test-mod_P.pak"]}]))
        cfg = Config.load()
        self.assertEqual(cfg.token, "vecchio")
        lib = Library(cfg, client_factory=FakeClient)
        self.assertTrue(Path(lib.mods[42].cached_paks[0]).exists())
        lib.set_enabled(42, False)
        self.assertFalse((self.paks / "drgmm_test-mod_P.pak").exists())
        lib.set_enabled(42, True)
        self.assertTrue((self.inst("test-mod")).exists())
        shutil.rmtree(old_cfg)
        shutil.rmtree(old_cache)

    def test_local_file(self):
        src = Path(tempfile.mkdtemp())
        pak = src / "peHUD_P.pak"
        pak.write_bytes(b"PAK-v1")
        e = self.lib.add_local_file(str(pak))
        self.assertLess(e.mod_id, 0)
        self.assertEqual(e.name, "peHUD")
        self.assertEqual(e.source, library.SOURCE_FILE)
        installed = self.inst("file-pehud")
        self.assertEqual(installed.read_bytes(), b"PAK-v1")
        pak.write_bytes(b"PAK-v2")
        e2 = self.lib.add_local_file(str(pak))
        self.assertEqual(e2.mod_id, e.mod_id)
        self.assertEqual(len([m for m in self.lib.mods.values() if m.source == "file"]), 1)
        self.assertEqual(installed.read_bytes(), b"PAK-v2")
        z = src / "pacchetto.zip"
        z.write_bytes(make_zip({"a/Uno.pak": b"1", "Due.pak": b"2", "leggimi.txt": b"x"}))
        e3 = self.lib.add_local_file(str(z))
        self.assertNotEqual(e3.mod_id, e.mod_id)
        self.assertEqual(len(e3.installed_paks), 2)
        self.lib.set_enabled(e.mod_id, False)
        self.assertFalse(installed.exists())
        self.lib.set_enabled(e.mod_id, True)
        self.assertTrue(installed.exists())
        self.assertEqual(self.lib.check_updates(), [])
        self.assertIn(e.mod_id, Library(self.cfg, client_factory=FakeClient).mods)
        self.lib.remove(e.mod_id)
        self.assertFalse(installed.exists())
        with self.assertRaises(library.ModioError):
            self.lib.add_local_file(str(src / "non-esiste.pak"))
        paths, rest = library.extract_local_paths(
            f'"{pak}"\nhttps://mod.io/g/drg/m/x\n{pak}\n{src / "no.pak"}')
        self.assertEqual(paths, [str(pak.resolve())])
        self.assertEqual(rest, ["https://mod.io/g/drg/m/x", str(src / "no.pak")])

    def test_update_all(self):
        import shutil
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        pak = self.inst("test-mod")
        src = Path(tempfile.mkdtemp())
        mine = src / "MiaMod.pak"
        mine.write_bytes(b"v1")
        f = lib.add_local_file(str(mine))
        fpak = self.inst("file-miamod")

        r = lib.update_all()
        self.assertEqual((r["updated"], r["installed"], r["ok"], r["failed"]), ([], [], 2, []))

        pak.unlink()
        fpak.unlink()
        r = lib.update_all()
        self.assertEqual(sorted(r["installed"]), ["MiaMod", "Test Mod"])
        self.assertTrue(pak.exists() and fpak.exists())

        shutil.rmtree(library.CACHE_DIR / str(e.mod_id))
        pak.unlink()
        r = lib.update_all()
        self.assertIn("Test Mod", r["installed"])
        self.assertEqual(pak.read_bytes(), b"PAK1.0")

        FakeClient.version = {"file_id": 2, "v": "2.0"}
        r = lib.update_all()
        self.assertEqual(r["updated"], [])
        self.assertEqual(pak.read_bytes(), b"PAK1.0")
        self.assertEqual(lib.find_updates()["updates"], ["Test Mod"])
        mine.write_bytes(b"v2")
        os.utime(mine, (mine.stat().st_atime, mine.stat().st_mtime + 60))
        r = lib.update_all(install_updates=True)
        self.assertEqual(sorted(r["updated"]), ["MiaMod", "Test Mod"])
        self.assertEqual(pak.read_bytes(), b"PAK2.0")
        self.assertEqual(fpak.read_bytes(), b"v2")

        lib.set_enabled(e.mod_id, False)
        pak.write_bytes(b"rimesso a mano")
        lib.mods[e.mod_id].installed_paks = [pak.name]
        lib.update_all()
        self.assertFalse(pak.exists())
        lib.set_enabled(e.mod_id, True)

        mine.unlink()
        shutil.rmtree(library.CACHE_DIR / str(f.mod_id))
        FakeClient.version = {"file_id": 3, "v": "3.0"}
        r = lib.update_all(check=True)
        self.assertEqual([n for n, _ in r["failed"]], ["MiaMod"])
        self.assertEqual(r["updated"], ["Test Mod"])

        mine.write_bytes(b"v4")
        lib.config.token = ""
        r = lib.update_all(check=True)
        self.assertEqual([n for n, _ in r["failed"]], ["Test Mod"])
        self.assertEqual(r["updated"], ["MiaMod"])
        lib.config.token = "t"

    def test_migrate_to_paks(self):
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        new = self.inst("test-mod")
        self.lib._remove_from_game(e)
        old = self.mods / "karl_test-mod" / "karl_test-mod.pak"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"PAK1.0")
        e.installed_paks = ["karl_test-mod/karl_test-mod.pak"]
        self.lib.mods[7] = library.ModEntry(7, 2475, "Iscritta", "iscritta", "", MODE_SUBSCRIBE)
        self.assertEqual(self.lib.migrate_install_location(), 2)
        self.assertFalse(old.exists() or old.parent.exists())
        self.assertEqual(new.read_bytes(), b"PAK1.0")
        self.assertEqual(self.lib.mods[7].mode, MODE_LOCAL)
        self.assertEqual(self.lib.needs_install(), [7])
        self.assertEqual(self.lib.migrate_install_location(), 0)
        self.assertTrue((self.paks / "FSD-WindowsNoEditor.pak").exists())

    def test_folders(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        src = self.paks.parent / "src"
        src.mkdir()
        (src / "MiaHUD.pak").write_bytes(b"HUD")
        f = lib.add_local_file(str(src / "MiaHUD.pak"))
        first = lib.active_folder
        self.assertEqual({m.mod_id for m in lib.folder_mods()}, {e.mod_id, f.mod_id})
        pak_e = self.inst("test-mod")
        pak_f = self.inst("file-miahud")

        lib.create_folder("Solo HUD")
        with self.assertRaises(Exception):
            lib.create_folder("solo hud")
        lib.switch_folder("Solo HUD")
        lib.update_all()
        self.assertEqual(lib.folder_mods(), [])
        self.assertFalse(pak_e.exists() or pak_f.exists())

        lib.add_local_file(str(src / "MiaHUD.pak"))
        self.assertEqual([m.mod_id for m in lib.folder_mods()], [f.mod_id])
        self.assertTrue(self.inst("file-miahud").exists())
        self.assertFalse(pak_e.exists())

        lib.switch_folder(first)
        lib.update_all()
        self.assertTrue(pak_e.exists() and pak_f.exists())

        lib.set_enabled(f.mod_id, False)
        self.assertFalse(lib.folders[first][f.mod_id])
        self.assertTrue(lib.folders["Solo HUD"][f.mod_id])

        lib.remove(f.mod_id)
        self.assertIn(f.mod_id, lib.mods)
        self.assertNotIn(f.mod_id, lib.folders[first])

        again = Library(Config(paks_dir=str(self.paks)))
        self.assertEqual(set(again.folders), {first, "Solo HUD"})
        self.assertEqual(again.active_folder, first)

        lib.rename_folder("Solo HUD", "HUD")
        self.assertIn("HUD", lib.folders)
        lib.delete_folder("HUD")
        self.assertNotIn(f.mod_id, lib.mods)
        with self.assertRaises(Exception):
            lib.delete_folder(first)

    def test_time_machine(self):
        lib = self.lib
        tm = lib.time_machine
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        src = self.paks.parent / "tm"
        src.mkdir()
        (src / "Hud.pak").write_bytes(b"hud")
        f = lib.add_local_file(str(src / "Hud.pak"))
        lib.update_all()
        self.assertEqual(len(tm.snapshots), 1)
        lib.update_all()
        self.assertEqual(len(tm.snapshots), 1)
        ieri = tm.snapshots[-1]["id"]

        FakeClient.version = {"file_id": 2, "v": "2.0"}
        lib.set_enabled(f.mod_id, False)
        lib.find_updates()
        lib.update_all(install_updates=True)
        self.assertEqual(len(tm.snapshots), 2)
        (snap, ch), _ = tm.listing()[0], None
        self.assertEqual((ch["versions"], ch["toggled"], ch["added"]), (1, 1, 0))
        lib.remove(e.mod_id)
        self.assertNotIn(e.mod_id, lib.mods)

        missing = tm.restore(ieri)
        lib.update_all()
        self.assertEqual(missing, [])
        self.assertIn(e.mod_id, lib.mods)
        self.assertEqual(lib.mods[e.mod_id].version, "1.0")
        self.assertTrue(lib.mods[e.mod_id].hold)
        self.assertEqual(self.inst("test-mod").read_bytes(), b"PAK1.0")
        self.assertTrue(lib.mods[f.mod_id].enabled)
        self.assertTrue(self.inst("file-hud").exists())
        blobs = list(library.SNAPSHOT_BLOBS.glob("*.pak"))
        self.assertEqual(len(blobs), 3)
        again = Library(Config(paks_dir=str(self.paks)), client_factory=FakeClient)
        self.assertEqual(len(again.time_machine.snapshots), len(tm.snapshots))

    def test_time_machine_limit_per_folder(self):
        from karl import timemachine
        lib, tm = self.lib, self.lib.time_machine
        old = timemachine.MAX_SNAPSHOTS
        timemachine.MAX_SNAPSHOTS = 3
        self.addCleanup(setattr, timemachine, "MAX_SNAPSHOTS", old)
        src = self.paks.parent / "lim"
        src.mkdir()
        (src / "A.pak").write_bytes(b"a")
        a = lib.add_local_file(str(src / "A.pak"))
        lib.update_all()
        main = lib.active_folder
        lib.create_folder("Altra")
        lib.switch_folder("Altra")
        lib.update_all()
        lib.switch_folder(main)
        for i in range(5):
            lib.set_enabled(a.mod_id, i % 2 == 1)
            lib.update_all()
        per = lambda f: sum(1 for s in tm.snapshots if s["folder"] == f)
        self.assertEqual(per(main), 3)
        self.assertEqual(per("Altra"), 1)
        lib.rename_folder("Altra", "Nuovo nome")
        self.assertEqual(per("Nuovo nome"), 1)
        self.assertEqual(per("Altra"), 0)

    def test_favorites_and_notes(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        self.assertTrue(lib.set_favorite([e.mod_id]))
        lib.set_note(e.mod_id, "  solo   con amici ")
        again = Library(self.cfg, client_factory=FakeClient)
        self.assertTrue(again.mods[e.mod_id].favorite)
        self.assertEqual(again.mods[e.mod_id].note, "solo con amici")
        self.assertFalse(lib.set_favorite([e.mod_id]))

    def test_profile_export_import(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        src = self.paks.parent / "prof"
        src.mkdir()
        (src / "Mio.pak").write_bytes(b"mio")
        lib.add_local_file(str(src / "Mio.pak"))
        lib.set_favorite([e.mod_id])
        lib.set_note(e.mod_id, "nota")
        e.hold = True
        lib.create_folder("Amici", copy_current=True)
        lib.folders["Amici"][e.mod_id] = False
        lib.update_all()
        out = self.paks.parent / "conf.karl"
        self.assertEqual(lib.export_profile(out), 1)

        for f in (library.LIBRARY_FILE, library.FOLDERS_FILE, library.SNAPSHOTS_FILE):
            f.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(library.CACHE_DIR, ignore_errors=True)
        for p in self.paks.glob("FSD-WindowsNoEditor_karl_*"):
            p.unlink()
        FakeClient.version = {"file_id": 2, "v": "2.0"}
        other = Library(self.cfg, client_factory=FakeClient)
        local = other.create_folder("Mia")
        failed = other.import_profile(out)
        self.assertEqual(failed, [])
        m = other.mods[e.mod_id]
        self.assertEqual((m.version, m.hold, m.favorite, m.note), ("1.0", True, True, "nota"))
        self.assertIn("Amici", other.folders)
        self.assertFalse(other.folders["Amici"][e.mod_id])
        self.assertIn(local, other.folders)
        self.assertEqual(other.folders[local], {})
        self.assertNotIn(".karl-import", other.folders)
        other.update_all()
        self.assertTrue(self.inst("test-mod").exists())
        bad = self.paks.parent / "bad.karl"
        bad.write_text("{}")
        with self.assertRaises(library.ModioError):
            other.read_profile(bad)

    def test_no_network_when_switching(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        calls = []
        orig = FakeClient.get_mod_by_id
        FakeClient.get_mod_by_id = lambda self, g, m: calls.append(m) or orig(self, g, m)
        self.addCleanup(setattr, FakeClient, "get_mod_by_id", orig)
        FakeClient.version = {"file_id": 2, "v": "2.0"}
        other = lib.create_folder("Altra")
        lib.switch_folder(other)
        lib.update_all()
        lib.switch_folder(next(n for n in lib.folders if n != other))
        lib.update_all()
        self.assertEqual(calls, [])
        self.assertEqual(lib.mods[e.mod_id].version, "1.0")
        self.assertEqual(lib.find_updates(ids=[])["updates"], [])
        self.assertEqual(calls, [])
        self.assertEqual(lib.find_updates(ids=[e.mod_id])["updates"], ["Test Mod"])
        self.assertEqual(calls, [e.mod_id])
        lib.update_all()
        self.assertEqual(lib.mods[e.mod_id].version, "1.0")
        lib.apply({})
        self.assertEqual(lib.mods[e.mod_id].version, "2.0")
        self.assertEqual(lib.mods[e.mod_id].latest_version, "")

    def test_cache_cleanup(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        FakeClient.version = {"file_id": 2, "v": "2.0"}
        lib.find_updates()
        lib.update_all(install_updates=True)
        self.assertTrue(lib.has_previous(e.mod_id))
        orphan = library.CACHE_DIR / "999" / "paks"
        orphan.mkdir(parents=True)
        (orphan / "x.pak").write_bytes(b"x" * 100)
        thumbs = library.CACHE_DIR / "thumbs"
        thumbs.mkdir(parents=True, exist_ok=True)
        (thumbs / "a.img").write_bytes(b"y" * 50)
        rep = lib.cache_report()
        self.assertGreaterEqual(rep["unused"], 100)
        self.assertGreaterEqual(rep["covers"], 50)
        self.assertGreater(rep["previous"], 0)
        self.assertGreater(rep["mods"], 0)
        freed = lib.clean_cache({"unused", "covers"})
        self.assertGreaterEqual(freed, 150)
        self.assertFalse(orphan.exists())
        self.assertTrue(lib.has_previous(e.mod_id))
        lib.clean_cache({"previous"})
        self.assertFalse(lib.has_previous(e.mod_id))
        self.assertTrue(all(Path(p).is_file() for p in lib.mods[e.mod_id].cached_paks))
        self.assertTrue(self.inst("test-mod").exists())

    def test_selfupdate(self):
        from karl import selfupdate
        self.assertTrue(selfupdate.is_newer("0.2.0", "0.1.1"))
        self.assertTrue(selfupdate.is_newer("v0.1.10", "0.1.9"))
        self.assertFalse(selfupdate.is_newer("0.1.1", "0.1.1"))
        self.assertFalse(selfupdate.is_newer("boh", "0.1.1"))
        import json as _json
        self.assertEqual(selfupdate.latest_version(lambda u: _json.dumps({"tag_name": "v0.3.0"}).encode()), "0.3.0")
        self.assertIsNone(selfupdate.latest_version(lambda u: _json.dumps({"tag_name": "v9", "prerelease": True}).encode()))
        def boom(u):
            raise OSError("404")
        self.assertIsNone(selfupdate.latest_version(boom))

    def test_rollback(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        pak = self.inst("test-mod")
        self.assertFalse(lib.has_previous(e.mod_id))
        FakeClient.version = {"file_id": 2, "v": "2.0"}
        lib.find_updates()
        lib.update_all(install_updates=True)
        self.assertEqual(pak.read_bytes(), b"PAK2.0")
        self.assertTrue(lib.has_previous(e.mod_id))
        lib.rollback(e.mod_id)
        self.assertEqual(pak.read_bytes(), b"PAK1.0")
        self.assertEqual((e.version, e.hold), ("1.0", True))
        lib.update_all()
        self.assertEqual(pak.read_bytes(), b"PAK1.0")
        self.assertEqual(lib.find_updates()["updates"], [])
        lib.rollback(e.mod_id)
        self.assertEqual(pak.read_bytes(), b"PAK2.0")
        self.assertEqual((e.version, e.hold), ("2.0", False))

    def test_dev_reimport(self):
        import os, time
        src = self.paks.parent / "dev"
        src.mkdir()
        f = src / "MiaHUD.pak"
        f.write_bytes(b"v1")
        e = self.lib.add_local_file(str(f))
        self.assertEqual(self.lib.changed_files(), [])
        f.write_bytes(b"v2")
        os.utime(f, (time.time() + 5, time.time() + 5))
        self.assertEqual([m.mod_id for m in self.lib.changed_files()], [e.mod_id])
        self.assertEqual(self.lib.reimport_changed(), ["MiaHUD"])
        self.assertEqual((self.inst("file-miahud")).read_bytes(), b"v2")
        self.assertTrue(self.lib.has_previous(e.mod_id))
        self.assertEqual(self.lib.changed_files(), [])

    def test_share_folder(self):
        lib = self.lib
        e = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        src = self.paks.parent / "share"
        src.mkdir()
        (src / "Mia.pak").write_bytes(b"x")
        lib.add_local_file(str(src / "Mia.pak"))
        lib.set_enabled(e.mod_id, False)
        code, skipped = lib.export_folder()
        self.assertTrue(code.startswith("KARL1:"))
        self.assertEqual(skipped, 1)
        data = Library.parse_share_code("  " + code[:20] + "\n" + code[20:] + " ")
        self.assertEqual(data["mods"], [(2475, 42, "test-mod", False)])
        with self.assertRaises(Exception):
            Library.parse_share_code("KARL1:non-valido")
        import shutil
        shutil.rmtree(library.CONFIG_DIR, ignore_errors=True)
        friend = Library(Config(token="t", paks_dir=str(self.paks)), client_factory=FakeClient)
        self.assertEqual(friend.mods, {})
        name, failed = friend.import_folder(code)
        self.assertEqual(failed, [])
        self.assertEqual(friend.active_folder, name)
        self.assertIn(42, friend.mods)
        self.assertFalse(friend.mods[42].enabled)
        self.assertEqual(friend.folders[name], {42: False})
        name2, _ = friend.import_folder(code)
        self.assertNotEqual(name, name2)

    def test_rename_old_paks(self):
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        current = self.inst("test-mod")
        for old_name in ("karl_test-mod_P.pak", "FSD-karl_test-mod_P.pak"):
            old = self.paks / old_name
            self.inst("test-mod").rename(old)
            e.installed_paks = [old_name]
            self.assertGreater(self.lib.fix_installed_paks(), 0)
            self.assertFalse(old.exists())
            self.lib.update_all()
            self.assertEqual(self.inst("test-mod"), current)
            self.assertTrue(current.exists())
        self.assertIn("fsd-windowsnoeditor", current.name.lower())
        self.assertNotEqual(current.name.lower(), "fsd-windowsnoeditor.pak")
        self.lib.remove(e.mod_id)
        self.assertEqual([p.name for p in self.paks.glob("*karl_*")], [])

    def test_load_order(self):
        lib = self.lib
        a = lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        src = self.paks.parent / "ord"
        src.mkdir()
        (src / "B.pak").write_bytes(b"b")
        b = lib.add_local_file(str(src / "B.pak"))
        self.assertGreater(lib.priority(a.mod_id), lib.priority(b.mod_id))
        pa, pb = self.inst("test-mod"), self.inst("file-b")
        lib.set_order([b.mod_id, a.mod_id])
        self.assertGreater(lib.priority(b.mod_id), lib.priority(a.mod_id))
        self.assertTrue(lib.needs_reorder(a) and lib.needs_reorder(b))
        lib.update_all()
        self.assertFalse(pa.exists() or pb.exists())
        na, nb = self.inst("test-mod"), self.inst("file-b")
        prio = lambda path: int(path.name.rsplit("_", 2)[1])
        self.assertGreater(prio(nb), prio(na))
        self.assertFalse(lib.needs_reorder(a) or lib.needs_reorder(b))

    def test_adopt_local_mods(self):
        local = self.mods / "MiaHUD"
        local.mkdir(parents=True)
        (local / "MiaHUD.pak").write_bytes(b"PAKLOCAL")
        (self.mods / "README.txt").write_text("leggimi")
        (self.mods / "Vuota").mkdir()
        self.assertEqual([d.name for d in self.lib.local_game_mods()], ["MiaHUD"])
        self.assertEqual(self.lib.adopt_local_mods(), ["MiaHUD"])
        self.assertEqual(self.lib.local_game_mods(), [])
        backup = self.paks.parent.parent / Library.LOCAL_BACKUP / "MiaHUD" / "MiaHUD.pak"
        self.assertEqual(backup.read_bytes(), b"PAKLOCAL")
        self.assertTrue((self.inst("file-miahud")).exists())
        e = next(m for m in self.lib.mods.values() if m.name == "MiaHUD")
        self.assertEqual(Path(e.file_path), backup.resolve())

    def test_approval_and_unsubscribe(self):
        self.assertEqual(library.approval_of({"tags": [{"name": "1.39"}, {"name": "Sandbox"}]}),
                         "Sandbox")
        self.assertEqual(library.approval_of({}), "")
        FakeClient.subs = {42}
        subs = [{"id": 42, "game_id": 2475, "name": "Test Mod", "name_id": "test-mod",
                 "tags": [{"name": "Approved"}]}]
        self.assertEqual(self.lib.unsubscribe_all(subs), 1)
        self.assertEqual(FakeClient.subs, set())
        self.assertEqual(self.lib.mods[42].approval, "Approved")
        self.assertEqual(self.lib.not_verified(), ["Test Mod"])
        self.assertEqual(self.lib.needs_install(), [42])

    def test_foreign_loader(self):
        fsd = self.paks.parent.parent
        (fsd / "Binaries" / "Win64").mkdir(parents=True)
        hook = fsd / "Binaries" / "Win64" / "x3daudio1_7.dll"
        hook.write_bytes(b"hook")
        bundle = self.paks / "FSD-WindowsNoEditor_Mods.pak"
        bundle.write_bytes(b"bundle")
        ue4ss = fsd / "Binaries" / "Win64" / "dwmapi.dll"
        ue4ss.write_bytes(b"ue4ss")
        self.assertEqual(sorted(p.name for p in self.lib.foreign_loader_files()),
                         ["FSD-WindowsNoEditor_Mods.pak", "x3daudio1_7.dll"])
        backup = self.lib.disable_foreign_loader()
        self.assertFalse(hook.exists() or bundle.exists())
        self.assertEqual((backup / "Binaries" / "Win64" / "x3daudio1_7.dll").read_bytes(), b"hook")
        self.assertEqual((backup / "Content" / "Paks" / "FSD-WindowsNoEditor_Mods.pak").read_bytes(),
                         b"bundle")
        self.assertTrue(ue4ss.exists())
        self.assertTrue((self.paks / "FSD-WindowsNoEditor.pak").exists())
        self.assertEqual(self.lib.foreign_loader_files(), [])
        hook.write_bytes(b"hook2")
        self.lib.disable_foreign_loader()
        self.assertEqual(len(list((backup / "Binaries" / "Win64").glob("x3daudio1_7*.dll"))), 2)


class NewFeatureTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.steamapps = root / "steamapps"
        self.paks = self.steamapps / "common" / "Deep Rock Galactic" / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        (self.paks / "FSD-WindowsNoEditor.pak").write_bytes(b"originale")
        for f in (library.CONFIG_FILE, library.LIBRARY_FILE, library.FOLDERS_FILE,
                  library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        FakeClient.version = {"file_id": 1, "v": "1.0"}
        FakeClient.deps = {}
        FakeClient.md5_seen = []
        self.cfg = Config(token="t", paks_dir=str(self.paks))
        self.lib = Library(self.cfg, client_factory=FakeClient)

    def test_md5_passed_and_checked(self):
        from karl import modio
        info = modio.modfile_info({"modfile": {"id": 3, "filehash": {"md5": "ABC"},
                                               "download": {"binary_url": "u"}}})
        self.assertEqual(info["md5"], "ABC")
        self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        self.assertEqual(FakeClient.md5_seen, [""])

        payload = b"contenuto della mod"

        class Resp(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        client = modio.ModioClient("t")
        client._open = lambda method, url, data=None: Resp(payload)
        dest = Path(tempfile.mkdtemp()) / "mod.zip"
        import hashlib
        good = hashlib.md5(payload).hexdigest()
        self.assertEqual(client.download("u", dest, md5=good.upper()).read_bytes(), payload)
        dest.unlink()
        with self.assertRaises(modio.ModioError):
            client.download("u", dest, md5="0" * 32)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix(".zip.part").exists())

    def test_dependencies_reported_not_installed(self):
        FakeClient.deps = {42: [{"mod_id": 7, "name": "DRGLib", "name_id": "drglib"}]}
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        self.assertEqual(e.deps, [{"id": 7, "name": "DRGLib", "url": "https://mod.io/g/drg/m/drglib"}])
        self.assertNotIn(7, self.lib.mods)
        self.assertEqual(list(self.lib.missing_dependencies()), [42])
        self.assertEqual(self.lib.missing_dependencies({42: False}), {})
        self.lib.set_enabled(42, False)
        self.assertEqual(self.lib.missing_dependencies(), {})

    def test_no_dependency_call_when_flag_false(self):
        calls = []
        self.addCleanup(setattr, FakeClient, "dependencies", FakeClient.dependencies)
        FakeClient.dependencies = lambda self, g, m: calls.append(m) or []
        saved = FakeClient._mod
        FakeClient._mod = lambda self: dict(saved(self), dependencies=False)
        self.addCleanup(setattr, FakeClient, "_mod", saved)
        e = self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        self.assertEqual(e.deps, [])
        self.assertEqual(calls, [])

    def test_game_update_detection(self):
        manifest = self.steamapps / "appmanifest_548430.acf"
        self.assertFalse(self.lib.game_update_check())
        manifest.write_text('"AppState"\n{\n\t"appid"\t\t"548430"\n\t"buildid"\t\t"100"\n}\n')
        self.assertEqual(self.lib.game_build(), "100")
        self.assertFalse(self.lib.game_update_check())
        self.assertEqual(self.cfg.game_build, "100")
        self.assertFalse(self.lib.game_update_check())
        manifest.write_text('"buildid"\t\t"101"')
        self.assertTrue(self.lib.game_update_check())
        self.assertFalse(self.lib.game_update_check())

    def test_outdated_mods(self):
        self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        FakeClient.game_tags = lambda self, g: [
            {"name": "Game Version", "tags": ["1.38", "1.39", "1.40"]},
            {"name": "Type", "tags": ["QoL"]}]
        tagged = {"tags": [{"name": "1.39"}, {"name": "QoL"}]}
        FakeClient.mods_by_ids = lambda self, g, ids: [dict(self._mod(), **tagged)]
        self.addCleanup(delattr, FakeClient, "game_tags")
        self.addCleanup(delattr, FakeClient, "mods_by_ids")
        latest, old = self.lib.outdated_mods()
        self.assertEqual(latest, "1.40")
        self.assertEqual([e.mod_id for e in old], [42])
        self.assertEqual(self.lib.mods[42].game_versions, ["1.39"])
        tagged["tags"].append({"name": "1.40"})
        self.assertEqual(self.lib.outdated_mods()[1], [])

    def test_single_instance(self):
        import socket
        import threading
        from karl import single
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        first = single.SingleInstance(port)
        self.assertTrue(first.acquire())
        self.addCleanup(first.close)
        shown = threading.Event()
        first.serve(shown.set)
        second = single.SingleInstance(port)
        self.assertFalse(second.acquire())
        self.assertTrue(second.notify_existing())
        self.assertTrue(shown.wait(2))
        first.close()
        third = single.SingleInstance(port)
        self.assertTrue(third.acquire())
        third.close()

class FolderBugTests(unittest.TestCase):
    def setUp(self):
        self.paks = Path(tempfile.mkdtemp()) / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        for f in (library.CONFIG_FILE, library.LIBRARY_FILE, library.FOLDERS_FILE,
                  library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        FakeClient.version = {"file_id": 1, "v": "1.0"}
        self.cfg = Config(token="t", paks_dir=str(self.paks))
        self.lib = Library(self.cfg, client_factory=FakeClient)

    def test_delete_active_folder_leaves_no_blank_folder(self):
        self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        first = self.lib.active_folder
        code, _ = self.lib.export_folder()
        imported, _ = self.lib.import_folder(code)
        self.assertEqual(imported, f"{first} (2)")
        self.assertEqual(self.lib.active_folder, imported)
        self.assertEqual(self.lib.delete_folder(imported), first)
        self.assertEqual(list(self.lib.folders), [first])
        self.lib.create_folder("prove")
        self.lib.switch_folder("prove")
        self.assertEqual(self.lib.delete_folder("prove"), first)
        self.assertEqual(list(self.lib.folders), [first])
        self.assertTrue(self.lib.mods[42].enabled)
        self.assertIn(42, self.lib.mods)

    def test_blank_folder_removed_on_load(self):
        self.lib.add("https://mod.io/g/drg/m/test-mod", MODE_LOCAL)
        first = self.lib.active_folder
        library.FOLDERS_FILE.write_text(json.dumps({
            "versione": 2, "attiva": "",
            "cartelle": {first: {"42": True}, "": {"42": True}}}), encoding="utf-8")
        lib = Library(self.cfg, client_factory=FakeClient)
        self.assertEqual(list(lib.folders), [first])
        self.assertEqual(lib.active_folder, first)

class ConflictAndUpdateTests(unittest.TestCase):
    def setUp(self):
        self.paks = Path(tempfile.mkdtemp()) / "FSD" / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        for f in (library.CONFIG_FILE, library.LIBRARY_FILE, library.FOLDERS_FILE,
                  library.SNAPSHOTS_FILE):
            if f.exists():
                f.unlink()
        self.cfg = Config(token="t", paks_dir=str(self.paks))
        self.lib = Library(self.cfg, client_factory=FakeClient)

    def test_conflicts(self):
        from karl import pakfile
        contents = {"alfa.pak": ["FSD/Content/X.uasset", "FSD/Content/Y.uasset", "FSD/AssetRegistry.bin"],
                    "beta.pak": ["FSD/Content/x.uasset", "FSD/AssetRegistry.bin"],
                    "gamma.pak": ["FSD/Content/Z.uasset", "FSD/AssetRegistry.bin"]}
        saved = pakfile.list_files
        pakfile.list_files = lambda p: contents.get(Path(p).name, [])
        self.addCleanup(setattr, pakfile, "list_files", saved)
        src = Path(tempfile.mkdtemp())
        ids = {}
        for name in contents:
            (src / name).write_bytes(b"PAK" + name.encode())
            ids[name] = self.lib.add_local_file(str(src / name)).mod_id
        a, b, c = ids["alfa.pak"], ids["beta.pak"], ids["gamma.pak"]
        found = self.lib.conflicts()
        self.assertEqual(found[a], [{"id": b, "files": 1, "winner": a}])
        self.assertEqual(found[b], [{"id": a, "files": 1, "winner": a}])
        self.assertNotIn(c, found)
        self.lib.set_order([b, a, c])
        self.assertEqual(self.lib.conflicts()[a][0]["winner"], b)
        self.assertEqual(self.lib.conflicts({b: False}), {})

    def test_latest_release_and_prepare(self):
        import json as _json
        import tarfile
        from karl import selfupdate
        payload = {"tag_name": "v9.1.0", "html_url": "https://github.com/p8086/karl/releases/tag/v9.1.0",
                   "assets": [{"name": "Karl.exe", "browser_download_url": "https://d/Karl.exe"},
                              {"name": "karl-linux-x86_64.tar.gz",
                               "browser_download_url": "https://d/karl.tar.gz"}]}
        rel = selfupdate.latest_release(fetch=lambda url: _json.dumps(payload).encode())
        self.assertEqual(rel["version"], "9.1.0")
        self.assertEqual(rel["assets"]["Karl.exe"], "https://d/Karl.exe")
        self.assertIsNone(selfupdate.latest_release(fetch=lambda url: b"{}"))
        self.assertIsNone(selfupdate.latest_release(
            fetch=lambda url: _json.dumps(dict(payload, prerelease=True)).encode()))
        self.assertFalse(selfupdate.can_self_update())

        work = Path(tempfile.mkdtemp())
        pkg = work / "pkg" / "karl"
        pkg.mkdir(parents=True)
        (pkg / "karl").write_bytes(b"NUOVO")
        (pkg / "install.sh").write_bytes(b"#!/bin/sh")
        tgz = work / "src.tar.gz"
        with tarfile.open(tgz, "w:gz") as tar:
            tar.add(pkg, arcname="karl")
        saved = selfupdate.download
        self.addCleanup(setattr, selfupdate, "download", saved)

        def fake_download(url, dest, progress=None, timeout=60):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tgz.read_bytes() if url.endswith("tar.gz") else b"EXE")
            return dest

        selfupdate.download = fake_download
        exe = work / "bin" / "karl"
        exe.parent.mkdir()
        exe.write_bytes(b"VECCHIO")
        plan = selfupdate.prepare(rel, exe, work, platform="linux")
        self.assertEqual(plan, {"kind": "linux", "exe": str(exe)})
        self.assertEqual(exe.read_bytes(), b"NUOVO")
        self.assertTrue(os.access(exe, os.X_OK))
        self.assertFalse((work / "karl-linux-x86_64.tar.gz").exists())

        wexe = work / "win" / "Karl.exe"
        wexe.parent.mkdir()
        wexe.write_bytes(b"OLD")
        plan = selfupdate.prepare(rel, wexe, work, platform="win32")
        self.assertEqual(plan["kind"], "windows")
        self.assertEqual((work / "win" / "Karl.new.exe").read_bytes(), b"EXE")
        script = Path(plan["script"]).read_text(encoding="utf-8")
        self.assertIn(f'move /y "{work / "win" / "Karl.new.exe"}" "{wexe}"', script)
        self.assertIn(f'start "" "{wexe}"', script)
        with self.assertRaises(OSError):
            selfupdate.prepare(dict(rel, assets={}), exe, work, platform="linux")

if __name__ == "__main__":
    unittest.main()
