import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(TMP / "cfg")
os.environ["XDG_CACHE_HOME"] = str(TMP / "cache")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karl import game, integration, pakfile, proton
from karl.config import Config
from karl.library import Library


def fstring(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<i", len(b)) + b


def make_pak(mount: str, files: dict[str, list[str]], seed: int = 0x1234) -> bytes:
    import hashlib
    fdi = struct.pack("<i", len(files))
    phi_pairs = []
    k = 0
    for d, names in files.items():
        fdi += fstring(d) + struct.pack("<i", len(names))
        for n in names:
            fdi += fstring(n) + struct.pack("<i", k)
            phi_pairs.append((pakfile.path_hash((d + n).lstrip("/"), seed), k))
            k += 1
    phi = struct.pack("<i", len(phi_pairs)) + b"".join(struct.pack("<Qi", h, e) for h, e in phi_pairs)
    phi += struct.pack("<i", 0)
    phi_off, fdi_off = 0, len(phi)
    index = (fstring(mount) + struct.pack("<iQ", k, seed)
             + struct.pack("<Iqq", 1, phi_off, len(phi)) + hashlib.sha1(phi).digest()
             + struct.pack("<Iqq", 1, fdi_off, len(fdi)) + hashlib.sha1(fdi).digest()
             + struct.pack("<i", 0) + struct.pack("<i", 0))
    fdi = phi + fdi
    idx_off = len(fdi)
    footer = (b"\0" * 16 + b"\0" + struct.pack("<IIqq", pakfile.PAK_MAGIC, 11, idx_off, len(index))
              + b"\0" * 20 + b"Zlib".ljust(32, b"\0") + b"\0" * 128)
    footer = footer[:41] + hashlib.sha1(index).digest() + footer[61:]
    assert len(footer) == 221
    return fdi + index + footer


def ue4ss_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dwmapi.dll", b"proxy")
        z.writestr("ue4ss/UE4SS.dll", b"ue4ss")
        z.writestr("ue4ss/UE4SS-settings.ini",
                   "[Debug]\nConsoleEnabled = 1\nGuiConsoleEnabled = 1\nGuiConsoleVisible = 1\n")
        z.writestr("ue4ss/Mods/mods.txt", "BPModLoaderMod : 1\n")
    return buf.getvalue()


def fake_fetch(url: str) -> bytes:
    if "api.github.com" in url:
        return json.dumps({"assets": [
            {"name": "zDEV-UE4SS_v3.0.1.zip", "browser_download_url": "https://x/dev.zip"},
            {"name": "UE4SS_v3.0.1.zip", "browser_download_url": "https://x/user.zip"}]}).encode()
    assert url == "https://x/user.zip", url
    return ue4ss_zip()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.steamapps = self.root / "steamapps"
        self.fsd = self.steamapps / "common" / "Deep Rock Galactic" / "FSD"
        self.paks = self.fsd / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        (self.paks / "FSD-WindowsNoEditor.pak").write_bytes(b"gioco")
        self.win64 = self.fsd / "Binaries" / "Win64"
        self.win64.mkdir(parents=True)
        (self.win64 / "FSD-Win64-Shipping.exe").write_bytes(b"exe")
        saved = proton.needed
        proton.needed = lambda: False
        self.addCleanup(setattr, proton, "needed", saved)

    def test_pak_reader(self):
        pak = self.root / "m.pak"
        pak.write_bytes(make_pak("../../../FSD/Content/MiaMod/",
                                 {"/": ["InitCave.uasset", "InitCave.uexp", "WBP.uasset"],
                                  "Sub/": ["InitSpacerig.uasset"]}))
        self.assertIn("FSD/Content/MiaMod/InitCave.uasset", pakfile.list_files(pak))
        self.assertEqual(pakfile.init_classes(pak), {
            "spacerig": ["/Game/MiaMod/Sub/InitSpacerig.InitSpacerig_C"],
            "cave": ["/Game/MiaMod/InitCave.InitCave_C"]})
        bad = self.root / "x.pak"
        bad.write_bytes(b"non sono un pak" * 30)
        with self.assertRaises(pakfile.PakError):
            pakfile.list_files(bad)

    def test_install_and_mod_list(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        self.assertEqual(integration.status(self.paks), "no_ue4ss")
        src = self.root / "src"
        src.mkdir()
        (src / "hud.pak").write_bytes(make_pak("../../../FSD/Content/HUD/",
                                               {"/": ["InitSpacerig.uasset", "InitCave.uasset"]}))
        (src / "tex.pak").write_bytes(make_pak("../../../", {"FSD/Content/Tex/": ["T.uasset"]}))
        hud = lib.add_local_file(str(src / "hud.pak"))
        lib.add_local_file(str(src / "tex.pak"))
        self.assertEqual(lib.needs_integration(), 1)

        (self.win64 / "x3daudio1_7.dll").write_bytes(b"hook")
        (self.win64 / "dwmapi.dll").write_bytes(b"altro-proxy")
        (self.win64 / "ue4ss").mkdir()
        (self.win64 / "ue4ss" / "UE4SSL.dll").write_bytes(b"ue4ssl")
        (self.paks / "FSD-WindowsNoEditor_Mods.pak").write_bytes(b"bundle")

        n = lib.install_integration(fetch=fake_fetch)
        self.assertEqual(n, 1)
        self.assertEqual(integration.status(self.paks), "ok")
        backup = self.fsd / Library.BACKUP_DIR
        self.assertEqual((backup / "Binaries" / "Win64" / "dwmapi.dll").read_bytes(), b"altro-proxy")
        self.assertTrue((backup / "Binaries" / "Win64" / "ue4ss" / "UE4SSL.dll").exists())
        self.assertFalse((self.paks / "FSD-WindowsNoEditor_Mods.pak").exists())
        self.assertEqual((self.win64 / "dwmapi.dll").read_bytes(), b"proxy")
        ue = self.win64 / "ue4ss"
        ini = (ue / "UE4SS-settings.ini").read_text()
        self.assertIn("GuiConsoleVisible = 0", ini)
        self.assertIn("ConsoleEnabled = 0", ini)
        mods_txt = (ue / "Mods" / "mods.txt").read_text()
        self.assertIn("KarlLoader : 1", mods_txt)
        self.assertIn("BPModLoaderMod : 1", mods_txt)
        self.assertTrue((ue / "Mods" / "KarlLoader" / "Scripts" / "main.lua").exists())
        lua = (ue / "Mods" / "KarlLoader" / "Scripts" / "karl_mods.lua").read_text()
        self.assertIn('"/Game/HUD/InitCave.InitCave_C"', lua)
        self.assertIn('"/Game/HUD/InitSpacerig.InitSpacerig_C"', lua)
        self.assertIn('name = "hud", client = true,', lua.replace("hud.pak", "hud"))
        self.assertNotIn("Tex", lua)
        self.assertEqual(lib.needs_integration(), 0)
        self.assertEqual(lib.foreign_loader_files(), [])

        lib.set_enabled(hud.mod_id, False)
        lua = (ue / "Mods" / "KarlLoader" / "Scripts" / "karl_mods.lua").read_text()
        self.assertNotIn("HUD", lua)
        lib.install_integration(fetch=lambda u: self.fail("non deve riscaricare"))
        self.assertEqual((ue / "Mods" / "mods.txt").read_text().count("KarlLoader"), 1)

    def test_spawn_order_puts_libraries_first(self):
        from karl.entry import ModEntry
        def entry(i, name, deps=None):
            return ModEntry(mod_id=i, game_id=1, name=name, slug="", url="", deps=[{"id": d} for d in deps] if deps is not None else None)
        mods = [entry(1, "A HUD", [3]), entry(2, "B Tool", [4]), entry(3, "Mod Hub", [5]),
                entry(4, "Z Lib"), entry(5, "DRGLib", []), entry(6, "C Plain"), entry(7, "Loop", [8]),
                entry(8, "Loop2", [7])]
        names = [e.name for e in Library.spawn_order(mods)]
        self.assertEqual(len(names), len(mods))
        for lib_name, user in (("Mod Hub", "A HUD"), ("DRGLib", "Mod Hub"), ("Z Lib", "B Tool")):
            self.assertLess(names.index(lib_name), names.index(user))
        self.assertLess(names.index("Mod Hub"), names.index("C Plain"))
        self.assertLess(names.index("Z Lib"), names.index("C Plain"))

    def test_mod_list_marks_host_only_mods(self):
        ue = self.root / "ue"
        n = integration.write_mod_list(ue, [
            ("Hub", {"spacerig": ["/Game/Hub/InitSpacerig.InitSpacerig_C"], "cave": [], "client": True}),
            ("Selector", {"spacerig": [], "cave": ["/Game/Sel/InitCave.InitCave_C"], "client": False}),
            ("Old", {"spacerig": ["/Game/Old/InitSpacerig.InitSpacerig_C"], "cave": []})])
        self.assertEqual(n, 3)
        lua = (ue / "Mods" / "KarlLoader" / "Scripts" / "karl_mods.lua").read_text()
        self.assertIn('name = "Hub", client = true,', lua)
        self.assertIn('name = "Selector", client = false,', lua)
        self.assertIn('name = "Old", client = true,', lua)

    def test_online_switch_changes_client_flag(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        src = self.root / "src"
        src.mkdir()
        (src / "sel.pak").write_bytes(make_pak("../../../FSD/Content/Sel/", {"/": ["InitCave.uasset"]}))
        e = lib.add_local_file(str(src / "sel.pak"))
        lib.install_integration(fetch=fake_fetch)
        lua = lambda: (self.win64 / "ue4ss" / "Mods" / "KarlLoader" / "Scripts" / "karl_mods.lua").read_text()
        self.assertIsNone(lib.set_online([e.mod_id]))
        e.source, e.approval = "modio", "Approved"
        lib.sync_loader()
        self.assertIn("client = false", lua())
        self.assertTrue(lib.set_online([e.mod_id]))
        self.assertIn("client = true", lua())
        self.assertFalse(lib.set_online([e.mod_id]))
        self.assertIn("client = false", lua())

    def test_loader_script_is_updated(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        src = self.root / "src"
        src.mkdir()
        (src / "hud.pak").write_bytes(make_pak("../../../FSD/Content/HUD/", {"/": ["InitCave.uasset"]}))
        lib.add_local_file(str(src / "hud.pak"))
        lib.install_integration(fetch=fake_fetch)
        script = self.win64 / "ue4ss" / "Mods" / "KarlLoader" / "Scripts" / "main.lua"
        script.write_text('local VERSION = "2.0"\n', encoding="utf-8")
        self.assertEqual(lib.sync_loader(), 1)
        self.assertEqual(script.read_bytes(), integration.LOADER_SRC.read_bytes())
        self.assertFalse(integration.update_loader(self.win64 / "ue4ss"))
        text = script.read_text(encoding="utf-8")
        self.assertIn("ExecuteWithDelay", text)
        self.assertNotIn("on_player_ready", text)
        start = text[text.index("local function start("):text.index("RegisterLoadMapPostHook")]
        self.assertIn("pcall(attempt, key, st)", start)
        self.assertNotIn("schedule(", start)
        self.assertNotIn("ExecuteWithDelay", start)

    def test_diagnostics_reads_loader_log(self):
        from karl import diagnostics
        ue = self.root / "ue"
        ue.mkdir()
        out = []
        add = lambda level, msg: out.append((level, msg))
        diagnostics._loader_log(ue, add)
        self.assertEqual(out, [])
        (ue / "UE4SS.log").write_text(
            "[Lua] [Karl] KarlLoader 2.0: 2 mod con blueprint\n"
            "[Lua] [Karl] non riesco ad avviare Old (/Game/Old/X.X_C)\n"
            "[Lua] [Karl] KarlLoader 2.1: 2 mod con blueprint\n"
            "[Lua] [Karl] avviata Mod Hub (/Game/Hub/InitSpacerig.InitSpacerig_C)\n"
            "[Lua] [Karl] non riesco ad avviare Secondary (/Game/Sec/InitCave.InitCave_C)\n"
            "[Lua] [Karl] non riesco ad avviare Secondary (/Game/Sec/InitCave.InitCave_C)\n",
            encoding="utf-8")
        diagnostics._loader_log(ue, add)
        self.assertEqual(out, [(diagnostics.WARN, "Karl loader (last game start): 1 blueprint(s) started, "
                                "not started: Secondary (/Game/Sec/InitCave.InitCave_C)")])
        out.clear()
        (ue / "UE4SS.log").write_text(
            "[Lua] [Karl] KarlLoader 2.2: 1 mod con blueprint\n"
            "[Lua] [Karl] avviata Hub (/Game/Hub/InitCave.InitCave_C)\n"
            "[Lua] [Karl] cave, client, ClientRestart, tentativo 1: avviate 1, in attesa 0, saltate 0\n",
            encoding="utf-8")
        diagnostics._loader_log(ue, add)
        self.assertEqual(out, [(diagnostics.OK, "Karl loader (last game start): 1 blueprint(s) started")])

    def test_hide_asset_registry(self):
        import hashlib
        pak = self.root / "r.pak"
        pak.write_bytes(make_pak("../../../FSD/", {"/": ["AssetRegistry.bin"],
                                                  "Content/HUD/": ["InitCave.uasset"]}))
        self.assertTrue(pakfile.hide_asset_registry(pak))
        self.assertFalse(pakfile.hide_asset_registry(pak))
        files = pakfile.list_files(pak)
        self.assertIn("FSD/AssetRegistry.krl", files)
        self.assertNotIn("FSD/AssetRegistry.bin", files)
        self.assertEqual(pakfile.init_classes(pak)["cave"], ["/Game/HUD/InitCave.InitCave_C"])
        data = pak.read_bytes()
        phi_n = struct.unpack_from("<i", data, 0)[0]
        keys = {struct.unpack_from("<Q", data, 4 + 12 * i)[0] for i in range(phi_n)}
        self.assertIn(pakfile.path_hash("AssetRegistry.krl", 0x1234), keys)
        self.assertNotIn(pakfile.path_hash("AssetRegistry.bin", 0x1234), keys)
        with open(pak, "rb") as f:
            ver, enc, off, isz = pakfile._footer(f)
        self.assertEqual(hashlib.sha1(data[off:off + isz]).digest(), data[-221 + 41:-221 + 61])
        other = self.root / "o.pak"
        other.write_bytes(make_pak("../../../FSD/Content/X/", {"/": ["AssetRegistry.bin"]}))
        self.assertFalse(pakfile.hide_asset_registry(other))

    def test_installed_copy_is_safe(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        src = self.root / "src2"
        src.mkdir()
        (src / "hub.pak").write_bytes(make_pak("../../../FSD/", {"/": ["AssetRegistry.bin"],
                                                               "Content/Hub/": ["InitSpacerig.uasset"]}))
        e = lib.add_local_file(str(src / "hub.pak"))
        installed = self.paks / e.installed_paks[0]
        self.assertIn("FSD/AssetRegistry.krl", pakfile.list_files(installed))
        self.assertIn("FSD/AssetRegistry.bin", pakfile.list_files(Path(e.cached_paks[0])))

    def test_zip_path_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.dll", b"x")
        with self.assertRaises(integration.IntegrationError):
            integration.extract_safely(buf.getvalue(), self.win64)


USER_REG = """WINE REGISTRY Version 2
;; All keys relative to \\User\\S-1-5-21-0-0-0-1000

#arch=win64

[Control Panel\\Desktop] 1700000000
#time=1da1b2c3d4e5f60
"FontSmoothing"="2"

[Software\\Wine\\Drivers] 1700000000
#time=1da1b2c3d4e5f60
"Audio"="pulse"
"""


class ProtonTests(IntegrationTests):
    def setUp(self):
        super().setUp()
        proton.needed = lambda: True
        self.running = False
        saved = game.is_running
        game.is_running = lambda: self.running
        self.addCleanup(setattr, game, "is_running", saved)
        self.pfx = self.steamapps / "compatdata" / "548430" / "pfx"

    def make_prefix(self, text=USER_REG):
        self.pfx.mkdir(parents=True)
        (self.pfx / "user.reg").write_text(text)

    def test_install_and_mod_list(self):
        self.make_prefix()
        super().test_install_and_mod_list()
        self.assertTrue(proton.has_override(self.pfx))

    def test_proton_override(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        src = self.root / "src"
        src.mkdir()
        (src / "hud.pak").write_bytes(make_pak("../../../FSD/Content/HUD/",
                                               {"/": ["InitSpacerig.uasset"]}))
        lib.add_local_file(str(src / "hud.pak"))
        self.assertEqual(lib.needs_integration(), 1)
        lib.install_integration(fetch=fake_fetch)
        self.assertEqual(integration.status(self.paks), "no_prefix")
        self.assertTrue(lib.waiting_first_launch())
        self.assertEqual(lib.needs_integration(), 0)
        self.make_prefix()
        self.assertEqual(integration.status(self.paks), "no_override")
        self.assertEqual(lib.needs_integration(), 1)
        self.running = True
        self.assertFalse(lib.fix_proton())
        self.assertEqual(lib.sync_loader(), 1)
        self.assertEqual(integration.status(self.paks), "no_override")
        self.running = False
        lib.sync_loader()
        self.assertEqual(integration.status(self.paks), "ok")
        reg = (self.pfx / "user.reg").read_text()
        self.assertIn('[Software\\\\Wine\\\\DllOverrides]', reg)
        self.assertIn('"dwmapi"="native,builtin"', reg)
        self.assertIn('"Audio"="pulse"', reg)
        self.assertEqual((self.pfx / "user.reg.karl-backup").read_text(), USER_REG)
        self.assertFalse(proton.set_override(self.pfx))

    def test_existing_section(self):
        self.make_prefix(USER_REG + '\n[Software\\\\Wine\\\\DllOverrides] 1700000001\n'
                         '#time=1da1b2c3d4e5f61\n"d3d11"="native"\n"dwmapi"="builtin"\n')
        self.assertFalse(proton.has_override(self.pfx))
        self.assertTrue(proton.set_override(self.pfx))
        reg = (self.pfx / "user.reg").read_text()
        self.assertIn('"d3d11"="native"', reg)
        self.assertIn('"dwmapi"="native,builtin"', reg)
        self.assertNotIn('"dwmapi"="builtin"', reg)
        self.assertEqual(reg.count("DllOverrides]"), 1)


class GameProcessTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "solo Linux")
    def test_linux_process(self):
        import subprocess
        import time
        launcher = subprocess.Popen(["bash", "-c", "exec -a 'proton waitforexitandrun "
                                     "Z:\\FSD.exe' sleep 30"])
        self.addCleanup(lambda: (launcher.kill(), launcher.wait()))
        time.sleep(0.2)
        self.assertFalse(game.is_running())
        drg = subprocess.Popen(["bash", "-c", "exec -a "
                                "'Z:\\Steam\\steamapps\\common\\Deep Rock Galactic\\FSD\\"
                                "Binaries\\Win64\\FSD-Win64-Shipping.exe' sleep 30"])
        self.addCleanup(lambda: (drg.kill(), drg.wait()))
        time.sleep(0.2)
        self.assertTrue(game.is_running())
        self.assertTrue(game.close())
        drg.wait(5)
        self.assertFalse(game.is_running())


if __name__ == "__main__":
    unittest.main()
