import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(TMP / "cfg")
os.environ["XDG_CACHE_HOME"] = str(TMP / "cache")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karl import game, merge, pakfile
from karl.config import Config
from karl.library import Library
from karl.modio import ModioError


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


FAKE_TOOL = """import json, sys
job = json.load(open(sys.argv[1], encoding="utf-8"))
for m in job["mods"]:
    if any("broken" in p for p in m["paks"]):
        print("SKIP\\t" + m["name"] + "\\tbad pak")
with open(job["out"], "w", encoding="utf-8") as f:
    json.dump({"game": job["game_pak"], "mods": [m["name"] for m in job["mods"]],
               "paks": [p for m in job["mods"] for p in m["paks"]], "meta": job["meta"]}, f)
print("OK\\t" + str(len(job["mods"])))
"""


def fake_tool(folder: Path) -> Path:
    script = folder / "fake_merge.py"
    script.write_text(FAKE_TOOL, encoding="utf-8")
    tool = folder / "karl-merge"
    tool.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n", encoding="utf-8")
    tool.chmod(0o755)
    return tool


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        import shutil
        shutil.rmtree(TMP / "cfg", ignore_errors=True)
        shutil.rmtree(TMP / "cache", ignore_errors=True)
        self.root = Path(tempfile.mkdtemp())
        self.steamapps = self.root / "steamapps"
        self.fsd = self.steamapps / "common" / "Deep Rock Galactic" / "FSD"
        self.paks = self.fsd / "Content" / "Paks"
        self.paks.mkdir(parents=True)
        (self.paks / "FSD-WindowsNoEditor.pak").write_bytes(b"gioco")
        self.win64 = self.fsd / "Binaries" / "Win64"
        self.win64.mkdir(parents=True)
        (self.win64 / "FSD-Win64-Shipping.exe").write_bytes(b"exe")
        self.running = False
        saved = game.is_running
        game.is_running = lambda: self.running
        self.addCleanup(setattr, game, "is_running", saved)
        os.environ["KARL_MERGE_TOOL"] = str(fake_tool(self.root))
        self.addCleanup(os.environ.pop, "KARL_MERGE_TOOL", None)

    def built(self) -> dict:
        return json.loads((self.paks / merge.PAK_NAME).read_text(encoding="utf-8"))

    def add(self, lib, name, mount="../../../FSD/Content/X/", files=("InitCave.uasset",)):
        src = self.root / "src"
        src.mkdir(exist_ok=True)
        (src / f"{name}.pak").write_bytes(make_pak(mount, {"/": list(files)}))
        return lib.add_local_file(str(src / f"{name}.pak"))

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

    @unittest.skipIf(sys.platform == "win32", "strumento finto solo POSIX")
    def test_integration_pak_follows_priority(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        hud = self.add(lib, "hud", "../../../FSD/Content/HUD/", ("InitSpacerig.uasset", "InitCave.uasset"))
        tex = self.add(lib, "tex", "../../../", ("T.uasset",))
        self.assertFalse(lib.integration_outdated())
        built = self.built()
        self.assertEqual(built["game"], str(self.paks / "FSD-WindowsNoEditor.pak"))
        self.assertEqual(sorted(built["mods"]), ["hud", "tex"])
        self.assertTrue(all(Path(p).parent == self.paks for p in built["paks"]))
        self.assertEqual(lib.blueprint_mods()[0][0], "hud")
        self.assertEqual(built["meta"]["version"], merge.MINT_VERSION)
        self.assertEqual(sorted(m["name"] for m in built["meta"]["mods"]), ["hud", "tex"])
        self.assertEqual({m["approval"] for m in built["meta"]["mods"]}, {"Sandbox"})
        self.assertFalse(any(m["required"] for m in built["meta"]["mods"]))

        lib.set_order([hud.mod_id, tex.mod_id])
        lib.apply({})
        self.assertEqual(self.built()["mods"], ["hud", "tex"])
        lib.set_order([tex.mod_id, hud.mod_id])
        self.assertEqual(self.built()["mods"], ["hud", "tex"])
        lib.apply({})
        self.assertEqual(self.built()["mods"], ["tex", "hud"])

        lib.set_enabled(tex.mod_id, False)
        self.assertEqual(self.built()["mods"], ["hud"])
        lib.set_enabled(hud.mod_id, False)
        self.assertFalse((self.paks / merge.PAK_NAME).exists())
        self.assertFalse((self.paks / merge.STAMP_NAME).exists())
        self.assertFalse(lib.integration_outdated())

    @unittest.skipIf(sys.platform == "win32", "strumento finto solo POSIX")
    def test_vanilla_and_game_running(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        self.running = True
        self.add(lib, "hud")
        self.assertFalse((self.paks / merge.PAK_NAME).exists())
        self.assertTrue(lib.integration_outdated())
        with self.assertRaises(ModioError):
            lib.install_integration()
        self.running = False
        self.assertEqual(lib.install_integration(), 1)
        self.assertEqual(self.built()["mods"], ["hud"])
        lib.start_vanilla()
        self.assertFalse((self.paks / merge.PAK_NAME).exists())
        lib.end_vanilla()
        self.assertEqual(self.built()["mods"], ["hud"])

    @unittest.skipIf(sys.platform == "win32", "strumento finto solo POSIX")
    def test_skipped_mods_and_missing_tool(self):
        lib = Library(Config(paks_dir=str(self.paks)))
        self.add(lib, "broken")
        self.assertEqual(lib.integration_skipped(), [["broken", "bad pak"]])
        os.environ["KARL_MERGE_TOOL"] = str(self.root / "nothing")
        self.add(lib, "other")
        self.assertTrue(lib.integration_error)
        with self.assertRaises(ModioError):
            lib.install_integration()

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
