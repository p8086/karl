from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import zipfile
import zlib
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import APP_NAME
from .i18n import tr
from . import game, integration, proton
from .modio import ModioClient, ModioError, modfile_info, parse_mod_url
from .pakfile import PakError, hide_asset_registry, init_classes

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LIBRARY_FILE = CONFIG_DIR / "mods.json"
FOLDERS_FILE = CONFIG_DIR / "cartelle.json"
SNAPSHOTS_FILE = CONFIG_DIR / "istantanee.json"
SNAPSHOT_BLOBS = CACHE_DIR / "istantanee"

PAK_PREFIX = "karl_"
LEGACY_NAME = "drgmm"
GAME_PAK_PREFIX = "FSD-WindowsNoEditor_" + PAK_PREFIX
OLD_GAME_PAK_PREFIX = "FSD-" + PAK_PREFIX
OWNED_PREFIXES = (GAME_PAK_PREFIX, OLD_GAME_PAK_PREFIX, PAK_PREFIX, f"{LEGACY_NAME}_")


def migrate_legacy() -> None:
    old_cfg = CONFIG_DIR.parent / LEGACY_NAME
    old_cache = CACHE_DIR.parent / LEGACY_NAME
    if CONFIG_DIR.exists() or not old_cfg.is_dir():
        return
    shutil.copytree(old_cfg, CONFIG_DIR)
    if old_cache.is_dir() and not CACHE_DIR.exists():
        shutil.copytree(old_cache, CACHE_DIR)
    lib = CONFIG_DIR / "mods.json"
    if lib.exists():
        mods = json.loads(lib.read_text())
        for m in mods:
            m["cached_paks"] = [str(CACHE_DIR / Path(p).relative_to(old_cache))
                                if Path(p).is_relative_to(old_cache) else p
                                for p in m.get("cached_paks", [])]
        lib.write_text(json.dumps(mods, indent=2))

GAME_SUBPATH = Path("steamapps/common/Deep Rock Galactic/FSD/Content/Paks")

MODE_SUBSCRIBE = "iscrizione"
MODE_LOCAL = "locale"

APPROVAL_TAGS = ("Verified", "Approved", "Sandbox")


def approval_of(mod: dict) -> str:
    for t in mod.get("tags") or []:
        name = (t.get("name") if isinstance(t, dict) else str(t)) or ""
        if name in APPROVAL_TAGS:
            return name
    return ""

SOURCE_MODIO = "modio"
SOURCE_FILE = "file"
MOD_FILE_EXT = (".pak", ".zip")


def extract_local_paths(text: str) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    rest: list[str] = []
    for line in text.splitlines():
        cand = line.strip().strip('"').strip("'").strip()
        if cand.lower().endswith(MOD_FILE_EXT) and Path(cand).expanduser().is_file():
            full = str(Path(cand).expanduser().resolve())
            if full not in paths:
                paths.append(full)
        else:
            rest.append(line)
    return paths, rest


@dataclass
class Config:
    token: str = ""
    paks_dir: str = ""
    default_mode: str = MODE_LOCAL
    username: str = ""
    avatar_url: str = ""
    profile_url: str = ""
    refresh_seconds: int = 60
    language: str = "auto"
    install_v2: bool = False
    ignore_foreign_loader: bool = False
    ignore_modio_subs: bool = False
    ignore_integration: bool = False
    dev_mode: bool = False
    dev_restart: bool = False

    @property
    def logged_in(self) -> bool:
        return bool(self.token)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        migrate_legacy()
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        if not cfg.paks_dir:
            found = find_paks_dir()
            cfg.paks_dir = str(found) if found else ""
        if not cfg.install_v2:
            cfg.default_mode = MODE_LOCAL
            cfg.install_v2 = True
            if CONFIG_FILE.exists():
                cfg.save()
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))
        os.chmod(CONFIG_FILE, 0o600)


def steam_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.insert(0, Path(os.environ[env]) / "Steam")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.insert(0, Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
    extra: list[Path] = []
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.exists():
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                extra.append(Path(p.replace("\\\\", "\\")))
    seen, out = set(), []
    for p in roots + extra:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def find_paks_dir() -> Optional[Path]:
    for root in steam_roots():
        cand = root / GAME_SUBPATH
        if (cand / "FSD-WindowsNoEditor.pak").exists() or cand.is_dir():
            return cand
    return None


@dataclass
class ModEntry:
    mod_id: int
    game_id: int
    name: str
    slug: str
    url: str
    mode: str
    enabled: bool = True
    file_id: Optional[int] = None
    version: str = ""
    cached_paks: list[str] = field(default_factory=list)
    installed_paks: list[str] = field(default_factory=list)
    source: str = SOURCE_MODIO
    file_path: str = ""
    file_mtime: float = 0.0
    approval: str = ""
    latest_version: str = ""
    prev_paks: list[str] = field(default_factory=list)
    prev_version: str = ""
    prev_file_id: Optional[int] = None
    prev_file_mtime: float = 0.0
    hold: bool = False

    @property
    def has_update(self) -> bool:
        return bool(self.latest_version)


class Library:
    def __init__(self, config: Config, client_factory: Callable[[str], ModioClient] = ModioClient):
        self.config = config
        self.client_factory = client_factory
        self.mods: dict[int, ModEntry] = {}
        self.folders: dict[str, dict[int, bool]] = {}
        self.active_folder: str = ""
        self.load()
        from .timemachine import TimeMachine
        self.time_machine = TimeMachine(self, SNAPSHOTS_FILE, SNAPSHOT_BLOBS)

    def load(self) -> None:
        if LIBRARY_FILE.exists():
            for raw in json.loads(LIBRARY_FILE.read_text()):
                e = ModEntry(**raw)
                self.mods[e.mod_id] = e
        self._load_folders()

    def _load_folders(self) -> None:
        if FOLDERS_FILE.exists():
            data = json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
            self.folders = {name: {int(k): bool(v) for k, v in mods.items()}
                            for name, mods in data.get("cartelle", {}).items()}
            self.active_folder = data.get("attiva", "")
            if data.get("versione", 1) < 2:
                for name, f in self.folders.items():
                    self.folders[name] = dict(sorted(
                        f.items(), key=lambda kv: (self.mods[kv[0]].name.lower()
                                                   if kv[0] in self.mods else "")))
        if not self.folders:
            self.folders = {tr("folder.default"): {
                i: e.enabled for i, e in sorted(self.mods.items(), key=lambda kv: kv[1].name.lower())}}
        if self.active_folder not in self.folders:
            self.active_folder = next(iter(self.folders))
        self._sync_folders()

    def _sync_folders(self) -> None:
        folder = self.folders.setdefault(self.active_folder, {})
        in_any = set().union(*self.folders.values()) if self.folders else set()
        for i, e in self.mods.items():
            if i in folder or e.enabled or i not in in_any:
                folder[i] = e.enabled
        for f in self.folders.values():
            for i in [i for i in f if i not in self.mods]:
                del f[i]

    def _save_folders(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        FOLDERS_FILE.write_text(json.dumps({
            "versione": 2,
            "attiva": self.active_folder,
            "cartelle": {name: {str(k): v for k, v in mods.items()}
                         for name, mods in self.folders.items()},
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    def folder_mods(self) -> list[ModEntry]:
        folder = self.folders.get(self.active_folder, {})
        return [self.mods[i] for i in folder if i in self.mods]

    def switch_folder(self, name: str) -> None:
        if name not in self.folders:
            raise ModioError(tr("folder.missing", name=name))
        self._sync_folders()
        self.active_folder = name
        folder = self.folders[name]
        for i, e in self.mods.items():
            e.enabled = bool(folder.get(i, False))
        self.save()

    def _check_name(self, name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise ModioError(tr("folder.empty_name"))
        if any(n.lower() == name.lower() for n in self.folders):
            raise ModioError(tr("folder.exists", name=name))
        return name

    def create_folder(self, name: str, copy_current: bool = False) -> str:
        name = self._check_name(name)
        self._sync_folders()
        self.folders[name] = dict(self.folders[self.active_folder]) if copy_current else {}
        self._save_folders()
        return name

    def rename_folder(self, old: str, new: str) -> str:
        if old not in self.folders:
            raise ModioError(tr("folder.missing", name=old))
        if new.strip().lower() != old.lower():
            new = self._check_name(new)
        else:
            new = new.strip()
        self.folders = {(new if k == old else k): v for k, v in self.folders.items()}
        if self.active_folder == old:
            self.active_folder = new
        self._save_folders()
        self.time_machine.rename_folder(old, new)
        return new

    def delete_folder(self, name: str) -> Optional[str]:
        if name not in self.folders:
            raise ModioError(tr("folder.missing", name=name))
        if len(self.folders) == 1:
            raise ModioError(tr("folder.last"))
        self._sync_folders()
        gone = self.folders.pop(name)
        others = set().union(*self.folders.values())
        for i in [i for i in gone if i not in others and i in self.mods]:
            e = self.mods.pop(i)
            self._remove_from_game(e)
            shutil.rmtree(CACHE_DIR / str(i), ignore_errors=True)
        if self.active_folder == name:
            new = next(iter(self.folders))
            self.active_folder = ""
            self.switch_folder(new)
            return new
        self.save()
        return None

    def save(self) -> None:
        self._sync_folders()
        self._save_folders()
        self._save_list()
        try:
            self.sync_loader()
        except Exception:
            pass

    def _save_list(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LIBRARY_FILE.write_text(json.dumps([asdict(m) for m in self.mods.values()], indent=2))

    @property
    def client(self) -> ModioClient:
        return self.client_factory(self.config.token)

    def paks_dir(self) -> Path:
        if not self.config.paks_dir:
            raise ModioError(tr("err.no_paks"))
        p = Path(self.config.paks_dir).expanduser()
        if not p.is_dir():
            raise ModioError(tr("err.paks_missing", path=p))
        return p

    @staticmethod
    def extract_paks(archive: Path, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.pak"):
            old.unlink()
        if archive.suffix.lower() == ".pak":
            dest = out_dir / archive.name
            shutil.copyfile(archive, dest)
            return [dest]
        if not zipfile.is_zipfile(archive):
            raise ModioError(tr("err.unknown_format", name=archive.name))
        found: list[Path] = []
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".pak"):
                    continue
                name = Path(info.filename).name
                dest = out_dir / name
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                found.append(dest)
        if not found:
            raise ModioError(tr("err.no_pak"))
        return sorted(found)

    def mods_dir(self) -> Path:
        paks = self.paks_dir()
        content, fsd = paks.parent, paks.parent.parent
        if paks.name.lower() != "paks" or content.name.lower() != "content" or not fsd.is_dir():
            raise ModioError(tr("err.not_paks", path=paks))
        return fsd / "Mods"

    PRIORITY_BASE = 1000

    def priority(self, mod_id: int) -> int:
        order = list(self.folders.get(self.active_folder, {}))
        pos = order.index(mod_id) if mod_id in order else len(order)
        return max(1, self.PRIORITY_BASE - pos)

    def _pak_names(self, entry: ModEntry) -> list[str]:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", entry.slug)
        n = len(entry.cached_paks)
        prio = self.priority(entry.mod_id)
        return [f"{GAME_PAK_PREFIX}{safe}{'' if n == 1 else f'-{i}'}_{prio}_P.pak" for i in range(n)]

    def set_order(self, ids: list[int]) -> None:
        folder = self.folders[self.active_folder]
        new = {i: folder[i] for i in ids if i in folder}
        new.update({i: v for i, v in folder.items() if i not in new})
        self.folders[self.active_folder] = new
        self.save()

    def needs_reorder(self, e: ModEntry) -> bool:
        return bool(e.enabled and e.installed_paks and e.installed_paks != self._pak_names(e))

    def _installed_path(self, name: str) -> Path:
        return self.mods_dir() / name if "/" in name else self.paks_dir() / name

    @staticmethod
    def _is_legacy(entry: ModEntry) -> bool:
        return any("/" in n for n in entry.installed_paks)

    def _copy_to_game(self, entry: ModEntry) -> None:
        paks = self.paks_dir()
        self._remove_from_game(entry)
        names = self._pak_names(entry)
        for src, name in zip(entry.cached_paks, names):
            shutil.copyfile(src, paks / name)
            self._make_safe(paks / name)
        entry.installed_paks = names

    @staticmethod
    def _make_safe(pak: Path) -> bool:
        try:
            return hide_asset_registry(pak)
        except (OSError, PakError, struct.error):
            return False

    def fix_installed_paks(self) -> int:
        fixed = 0
        paks = self.paks_dir()
        for e in self.mods.values():
            names = []
            for name in e.installed_paks:
                old = None
                if "/" not in name and name.startswith(OLD_GAME_PAK_PREFIX):
                    old = name[len(OLD_GAME_PAK_PREFIX):]
                elif "/" not in name and name.startswith(PAK_PREFIX):
                    old = name[len(PAK_PREFIX):]
                if old is not None:
                    new = GAME_PAK_PREFIX + old
                    if (paks / name).is_file() and not (paks / new).exists():
                        (paks / name).rename(paks / new)
                    if (paks / new).is_file():
                        name = new
                        fixed += 1
                names.append(name)
                if "/" not in name and self._make_safe(paks / name):
                    fixed += 1
            e.installed_paks = names
        if fixed:
            self._save_list()
        return fixed

    def _remove_from_game(self, entry: ModEntry) -> None:
        if not entry.installed_paks:
            return
        for name in entry.installed_paks:
            if not name.split("/")[0].startswith(OWNED_PREFIXES):
                continue
            path = self._installed_path(name)
            path.unlink(missing_ok=True)
            if "/" in name:
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        entry.installed_paks = []

    FOREIGN_LOADER_FILES = ("Binaries/Win64/x3daudio1_7.dll",
                            "Content/Paks/FSD-WindowsNoEditor_Mods.pak",
                            "Content/Paks/mods_P.pak")
    BACKUP_DIR = "_karl_backup_altri_loader"

    def foreign_loader_files(self) -> list[Path]:
        try:
            fsd = self.mods_dir().parent
        except ModioError:
            return []
        found = [fsd / rel for rel in self.FOREIGN_LOADER_FILES if (fsd / rel).is_file()]
        win64 = fsd / "Binaries" / "Win64"
        if integration.is_foreign_ue4ss(win64):
            found += [p for p in (win64 / "dwmapi.dll", win64 / "ue4ss") if p.exists()]
        return found

    def disable_foreign_loader(self) -> Path:
        fsd = self.mods_dir().parent
        backup = fsd / self.BACKUP_DIR
        for path in self.foreign_loader_files():
            dest = backup / path.relative_to(fsd)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}_{datetime.now():%Y%m%d%H%M%S}{dest.suffix}")
            shutil.move(str(path), str(dest))
        return backup

    def migrate_install_location(self) -> int:
        try:
            self.fix_installed_paks()
        except ModioError:
            pass
        moved = len(self.convert_to_local())
        for e in self.mods.values():
            if e.installed_paks and self._is_legacy(e):
                if e.enabled and e.cached_paks and all(Path(p).is_file() for p in e.cached_paks):
                    self._copy_to_game(e)
                else:
                    self._remove_from_game(e)
                moved += 1
        if moved:
            self.save()
        return moved

    def _download(self, entry: ModEntry, mod: dict,
                  progress: Optional[Callable[[int, int], None]]) -> None:
        info = modfile_info(mod)
        mod_cache = CACHE_DIR / str(entry.mod_id)
        if entry.file_id is not None and info["file_id"] != entry.file_id:
            self._keep_previous(entry)
        archive = self.client.download(info["url"], mod_cache / "download" / info["filename"], progress)
        paks = self.extract_paks(archive, mod_cache / "paks")
        archive.unlink(missing_ok=True)
        entry.cached_paks = [str(p) for p in paks]
        entry.file_id = info["file_id"]
        entry.version = info["version"]

    def add(self, url: str, mode: Optional[str] = None,
            progress: Optional[Callable[[int, int], None]] = None,
            log: Callable[[str], None] = lambda s: None) -> ModEntry:
        game_slug, mod_slug = parse_mod_url(url)
        log(tr("lib.searching", slug=mod_slug))
        mod = self.client.get_mod(game_slug, mod_slug)
        return self.add_mod_object(mod, mode, progress=progress, log=log)

    def add_mod_object(self, mod: dict, mode: Optional[str] = None,
                       progress: Optional[Callable[[int, int], None]] = None,
                       log: Callable[[str], None] = lambda s: None) -> ModEntry:
        mode = MODE_LOCAL
        mod_id, game_id = int(mod["id"]), int(mod["game_id"])
        slug = mod.get("name_id") or str(mod_id)

        if mod_id in self.mods:
            old = self.mods[mod_id]
            if old.mode == mode:
                if not old.enabled:
                    self.set_enabled(mod_id, True, log=log)
                else:
                    log(tr("lib.already", name=old.name))
                return old
            self.remove(mod_id, log=log)  

        entry = ModEntry(mod_id=mod_id, game_id=game_id, name=mod.get("name", slug),
                         slug=slug, url=mod.get("profile_url") or "", mode=mode,
                         approval=approval_of(mod))
        log(tr("lib.downloading"))
        self._download(entry, mod, progress)
        self._copy_to_game(entry)

        self.mods[mod_id] = entry
        self.save()
        log(tr("lib.added", name=entry.name))
        return entry

    def set_enabled(self, mod_id: int, enabled: bool,
                    log: Callable[[str], None] = lambda s: None) -> None:
        e = self.mods[mod_id]
        self._copy_to_game(e) if enabled else self._remove_from_game(e)
        e.enabled = enabled
        self.save()
        log(tr("lib.enabled" if enabled else "lib.disabled", name=e.name))

    def add_local_file(self, path: str, log: Callable[[str], None] = lambda s: None) -> ModEntry:
        src = Path(path).expanduser()
        if not src.is_file():
            raise ModioError(tr("err.file_missing", path=src))
        if src.suffix.lower() not in MOD_FILE_EXT:
            raise ModioError(tr("err.unknown_format", name=src.name))
        stem = re.sub(r"_P$", "", src.stem, flags=re.IGNORECASE)
        slug = "file-" + re.sub(r"[^A-Za-z0-9_-]", "_", stem).lower()

        entry = next((m for m in self.mods.values()
                      if m.source == SOURCE_FILE and m.slug == slug), None)
        if entry is None:
            new_id = min([m.mod_id for m in self.mods.values() if m.mod_id < 0], default=0) - 1
            entry = ModEntry(mod_id=new_id, game_id=0, name=stem, slug=slug, url="",
                             mode=MODE_LOCAL, source=SOURCE_FILE)
        self._import_file(entry, src)
        entry.enabled = True
        self._copy_to_game(entry)
        self.mods[entry.mod_id] = entry
        self.save()
        log(tr("lib.added", name=entry.name))
        return entry

    def _import_file(self, entry: ModEntry, src: Path) -> None:
        if entry.cached_paks and src.is_file() and src.stat().st_mtime != entry.file_mtime:
            self._keep_previous(entry)
        paks = self.extract_paks(src, CACHE_DIR / str(entry.mod_id) / "paks")
        entry.cached_paks = [str(p) for p in paks]
        entry.file_path = str(src.resolve())
        entry.file_mtime = src.stat().st_mtime
        entry.version = datetime.fromtimestamp(entry.file_mtime).strftime("%Y-%m-%d %H:%M")

    def _keep_previous(self, entry: ModEntry) -> None:
        if not entry.cached_paks or not all(Path(p).is_file() for p in entry.cached_paks):
            return
        mod_cache = CACHE_DIR / str(entry.mod_id)
        prev = mod_cache / "prev"
        shutil.rmtree(prev, ignore_errors=True)
        prev.mkdir(parents=True)
        entry.prev_paks = []
        for p in entry.cached_paks:
            dest = prev / Path(p).name
            shutil.copyfile(p, dest)
            entry.prev_paks.append(str(dest))
        entry.prev_version = entry.version
        entry.prev_file_id = entry.file_id
        entry.prev_file_mtime = entry.file_mtime

    def has_previous(self, mod_id: int) -> bool:
        e = self.mods.get(mod_id)
        return bool(e and e.prev_paks and all(Path(p).is_file() for p in e.prev_paks))

    def rollback(self, mod_id: int, log: Callable[[str], None] = lambda s: None) -> ModEntry:
        if not self.has_previous(mod_id):
            raise ModioError(tr("rollback.none"))
        e = self.mods[mod_id]
        mod_cache = CACHE_DIR / str(mod_id)
        cur_dir, prev_dir, tmp = mod_cache / "paks", mod_cache / "prev", mod_cache / "swap"
        shutil.rmtree(tmp, ignore_errors=True)
        cur_dir.rename(tmp)
        prev_dir.rename(cur_dir)
        tmp.rename(prev_dir)
        e.cached_paks, e.prev_paks = ([str(cur_dir / Path(p).name) for p in e.prev_paks],
                                      [str(prev_dir / Path(p).name) for p in e.cached_paks])
        e.version, e.prev_version = e.prev_version, e.version
        e.file_id, e.prev_file_id = e.prev_file_id, e.file_id
        e.file_mtime, e.prev_file_mtime = e.prev_file_mtime, e.file_mtime
        e.hold = not e.hold
        e.latest_version = ""
        if e.enabled:
            self._copy_to_game(e)
        self.save()
        log(tr("rollback.done", name=e.name, version=e.version or "-"))
        return e

    def changed_files(self) -> list[ModEntry]:
        out = []
        for e in self.mods.values():
            if e.source == SOURCE_FILE and not e.hold and e.file_path:
                try:
                    if Path(e.file_path).stat().st_mtime != e.file_mtime:
                        out.append(e)
                except OSError:
                    pass
        return out

    def reimport_changed(self, log: Callable[[str], None] = lambda s: None) -> list[str]:
        names = []
        for e in self.changed_files():
            self._import_file(e, Path(e.file_path))
            if e.enabled:
                self._copy_to_game(e)
            names.append(e.name)
        if names:
            self.save()
            log(tr("dev.reimported", names=", ".join(names)))
        return names

    SHARE_PREFIX = "KARL1:"

    def export_folder(self, name: Optional[str] = None) -> tuple[str, int]:
        self._sync_folders()
        name = name or self.active_folder
        folder = self.folders[name]
        mods, skipped = [], 0
        for i, on in folder.items():
            e = self.mods.get(i)
            if e is None:
                continue
            if e.source != SOURCE_MODIO:
                skipped += 1
                continue
            mods.append([e.game_id, e.mod_id, e.slug, 1 if on else 0])
        raw = json.dumps({"n": name, "m": mods}, separators=(",", ":"), ensure_ascii=False)
        code = base64.urlsafe_b64encode(zlib.compress(raw.encode("utf-8"), 9)).decode().rstrip("=")
        return self.SHARE_PREFIX + code, skipped

    @classmethod
    def parse_share_code(cls, text: str) -> dict:
        text = "".join((text or "").split())
        i = text.find(cls.SHARE_PREFIX)
        if i < 0:
            raise ModioError(tr("share.invalid"))
        body = text[i + len(cls.SHARE_PREFIX):]
        try:
            raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            data = json.loads(raw.decode("utf-8"))
            mods = [(int(g), int(m), str(s), bool(on)) for g, m, s, on in data["m"]]
        except Exception as err:
            raise ModioError(tr("share.invalid")) from err
        return {"name": str(data.get("n") or tr("share.default_name")), "mods": mods}

    def unique_folder_name(self, base: str) -> str:
        names = {n.lower() for n in self.folders}
        if base.lower() not in names:
            return base
        n = 2
        while f"{base} ({n})".lower() in names:
            n += 1
        return f"{base} ({n})"

    def import_folder(self, code: str, progress: Optional[Callable[[int, int], None]] = None,
                      log: Callable[[str], None] = lambda s: None) -> tuple[str, list[str]]:
        data = self.parse_share_code(code)
        name = self.create_folder(self.unique_folder_name(data["name"]))
        self.switch_folder(name)
        failed: list[str] = []
        total = len(data["mods"])
        for n, (gid, mid, slug, on) in enumerate(data["mods"], 1):
            log(tr("share.importing", i=n, n=total, name=slug))
            try:
                e = self.mods.get(mid)
                if e is None:
                    e = self.add_mod_object(self.client.get_mod_by_id(gid, mid), progress=progress)
                e.enabled = on
                self.folders[name][mid] = on
            except Exception:
                failed.append(slug)
        self.save()
        return name, failed

    def remove(self, mod_id: int, log: Callable[[str], None] = lambda s: None) -> None:
        folder = self.folders.get(self.active_folder, {})
        folder.pop(mod_id, None)
        if any(mod_id in f for f in self.folders.values()):
            e = self.mods[mod_id]
            e.enabled = False
            self._remove_from_game(e)
            self.save()
            log(tr("lib.removed", name=e.name))
            return
        e = self.mods.pop(mod_id)
        try:
            self._remove_from_game(e)
        finally:
            shutil.rmtree(CACHE_DIR / str(mod_id), ignore_errors=True)
            self.save()
        log(tr("lib.removed", name=e.name))

    def update_all(self, progress: Optional[Callable[[int, int], None]] = None,
                   log: Callable[[str], None] = lambda s: None) -> dict:
        report: dict = {"updated": [], "installed": [], "ok": 0, "failed": [], "sub_ids": None}
        self.convert_to_local()
        client = self.client if self.config.token else None

        entries = sorted(self.mods.values(), key=lambda m: m.name.lower())
        for n, e in enumerate(entries, 1):
            log(tr("lib.update_step", i=n, n=len(entries), name=e.name))
            try:
                result = self._update_entry(e, client, progress)
            except Exception as err:
                report["failed"].append((e.name, str(err)))
                continue
            if result == "updated":
                report["updated"].append(e.name)
            elif result == "installed":
                report["installed"].append(e.name)
            else:
                report["ok"] += 1
        self.save()
        if not report["failed"]:
            try:
                self.time_machine.take()
            except OSError:
                pass
        log(tr("lib.update_summary", updated=len(report["updated"]),
               installed=len(report["installed"]), ok=report["ok"]))
        return report

    def _update_entry(self, e: ModEntry, client: Optional[ModioClient],
                      progress: Optional[Callable[[int, int], None]]) -> Optional[str]:
        result: Optional[str] = None
        cache_ok = bool(e.cached_paks) and all(Path(p).is_file() for p in e.cached_paks)

        if e.source == SOURCE_FILE:
            src = Path(e.file_path)
            changed = src.is_file() and src.stat().st_mtime != e.file_mtime and not e.hold
            if src.is_file() and (changed or not cache_ok):
                self._import_file(e, src)
                result = "updated"
            elif not cache_ok:
                raise ModioError(tr("err.file_missing", path=src))
        elif e.mode == MODE_LOCAL:
            if client is None:
                raise ModioError(tr("err.no_token"), status=401)
            mod = client.get_mod_by_id(e.game_id, e.mod_id)
            new_id = (mod.get("modfile") or {}).get("id")
            e.name = mod.get("name") or e.name
            e.approval = approval_of(mod) or e.approval
            is_new = e.file_id is not None and new_id != e.file_id and not e.hold
            if is_new or not cache_ok or e.file_id is None:
                self._download(e, mod, progress)
                result = "updated" if is_new else "installed"
        else:
            if client is not None and e.enabled:
                mod = client.get_mod_by_id(e.game_id, e.mod_id)
                mf = mod.get("modfile") or {}
                if mf.get("id") != e.file_id:
                    e.file_id, e.version = mf.get("id"), mf.get("version") or ""
                    result = "updated"
            return result

        if e.enabled:
            missing = (not e.installed_paks or self._is_legacy(e) or self.needs_reorder(e)
                       or any(not self._installed_path(n).is_file() for n in e.installed_paks))
            if result == "updated" or missing:
                self._copy_to_game(e)
                result = result or "installed"
        elif e.installed_paks:
            self._remove_from_game(e)
        return result

    def find_updates(self, progress: Optional[Callable[[int, int], None]] = None,
                     log: Callable[[str], None] = lambda s: None) -> dict:
        report: dict = {"updates": [], "failed": []}
        client = self.client if self.config.token else None
        entries = sorted(self.mods.values(), key=lambda m: m.name.lower())
        for n, e in enumerate(entries, 1):
            log(tr("lib.update_step", i=n, n=len(entries), name=e.name))
            if progress:
                progress(n, len(entries))
            if e.hold:
                e.latest_version = ""
                continue
            try:
                if e.source == SOURCE_FILE:
                    src = Path(e.file_path)
                    new = src.is_file() and src.stat().st_mtime != e.file_mtime
                    e.latest_version = (datetime.fromtimestamp(src.stat().st_mtime)
                                        .strftime("%Y-%m-%d %H:%M") if new else "")
                else:
                    if client is None:
                        raise ModioError(tr("err.no_token"), status=401)
                    mod = client.get_mod_by_id(e.game_id, e.mod_id)
                    mf = mod.get("modfile") or {}
                    e.name = mod.get("name") or e.name
                    e.approval = approval_of(mod) or e.approval
                    new = e.file_id is not None and mf.get("id") not in (None, e.file_id)
                    e.latest_version = (mf.get("version") or "?") if new else ""
            except Exception as err:
                report["failed"].append((e.name, str(err)))
                continue
            if e.latest_version:
                report["updates"].append(e.name)
        self.save()
        return report

    def apply(self, wanted: dict[int, bool],
              progress: Optional[Callable[[int, int], None]] = None,
              log: Callable[[str], None] = lambda s: None) -> dict:
        for mid, on in wanted.items():
            if mid in self.mods:
                self.mods[mid].enabled = on
        report = self.update_all(progress=progress, log=log)
        failed = {name for name, _ in report["failed"]}
        for e in self.mods.values():
            if e.name not in failed:
                e.latest_version = ""
        self.save()
        return report

    def check_updates(self, progress: Optional[Callable[[int, int], None]] = None,
                      log: Callable[[str], None] = lambda s: None) -> list[str]:
        return self.update_all(progress=progress, log=log)["updated"]

    def sync_subscriptions(self, subscribed: list[dict]) -> bool:
        changed = False
        sub_ids = set()
        for mod in subscribed:
            mid = int(mod["id"])
            sub_ids.add(mid)
            e = self.mods.get(mid)
            mf = mod.get("modfile") or {}
            if e is None:
                self.mods[mid] = ModEntry(
                    mod_id=mid, game_id=int(mod["game_id"]), name=mod.get("name", str(mid)),
                    slug=mod.get("name_id") or str(mid), url=mod.get("profile_url") or "",
                    mode=self.config.default_mode,
                    file_id=mf.get("id") if self.config.default_mode == MODE_SUBSCRIBE else None,
                    version=mf.get("version") or "")
                changed = True
            elif e.mode == MODE_SUBSCRIBE and not e.enabled:
                e.enabled = True
                changed = True
        for e in self.mods.values():
            if e.mode == MODE_SUBSCRIBE and e.enabled and e.mod_id not in sub_ids:
                e.enabled = False
                changed = True
        if changed:
            self.save()
        return changed

    def account_subscriptions(self) -> list[dict]:
        client = self.client
        return client.subscribed_mods(client.game_id("drg"))

    def unsubscribe_all(self, subs: list[dict]) -> int:
        client, n = self.client, 0
        for mod in subs:
            mid, gid = int(mod["id"]), int(mod["game_id"])
            client.unsubscribe(gid, mid)
            n += 1
            if mid not in self.mods:
                self.mods[mid] = ModEntry(
                    mod_id=mid, game_id=gid, name=mod.get("name", str(mid)),
                    slug=mod.get("name_id") or str(mid), url=mod.get("profile_url") or "",
                    mode=MODE_LOCAL, approval=approval_of(mod))
        self.save()
        return n

    LOCAL_BACKUP = "_karl_backup_mods"

    def local_game_mods(self) -> list[Path]:
        try:
            mods = self.mods_dir()
        except ModioError:
            return []
        if not mods.is_dir():
            return []
        return sorted(d for d in mods.iterdir()
                      if d.is_dir() and any(d.rglob("*.pak")))

    def adopt_local_mods(self, log: Callable[[str], None] = lambda s: None) -> list[str]:
        fsd = self.mods_dir().parent
        backup = fsd / self.LOCAL_BACKUP
        names = []
        for d in self.local_game_mods():
            dest = backup / d.name
            if dest.exists():
                dest = backup / f"{d.name}_{datetime.now():%Y%m%d%H%M%S}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(dest))
            for pak in sorted(dest.rglob("*.pak")):
                e = self.add_local_file(str(pak), log=log)
                names.append(e.name)
        return names

    def not_verified(self, ids: Optional[list[int]] = None) -> list[str]:
        return sorted(e.name for e in self.mods.values()
                      if e.enabled and e.approval in ("Approved", "Sandbox")
                      and (ids is None or e.mod_id in ids))

    def blueprint_mods(self) -> list[tuple[str, dict]]:
        out = []
        for e in sorted(self.mods.values(), key=lambda m: m.name.lower()):
            if not e.enabled:
                continue
            classes: dict[str, list[str]] = {"spacerig": [], "cave": []}
            for name in e.installed_paks:
                try:
                    c = init_classes(self._installed_path(name))
                except (OSError, PakError, ValueError, ModioError):
                    continue
                classes["spacerig"] += c["spacerig"]
                classes["cave"] += c["cave"]
            if classes["spacerig"] or classes["cave"]:
                out.append((e.name, classes))
        return out

    def integration_status(self) -> str:
        return integration.status(self.paks_dir())

    def needs_integration(self) -> int:
        try:
            if self.integration_status() in ("ok", "no_prefix"):
                return 0
        except ModioError:
            return 0
        return len(self.blueprint_mods())

    def waiting_first_launch(self) -> bool:
        try:
            return self.integration_status() == "no_prefix" and bool(self.blueprint_mods())
        except ModioError:
            return False

    def fix_proton(self, log: Callable[[str], None] = lambda s: None) -> bool:
        if not proton.needed():
            return True
        pfx = proton.prefix_for(self.paks_dir())
        if pfx is None:
            return False
        if proton.has_override(pfx):
            return True
        if game.is_running():
            return False
        proton.set_override(pfx)
        log(tr("integ.proton_set"))
        return True

    def sync_loader(self) -> int:
        if not self.config.paks_dir:
            return -1
        paks = self.paks_dir()
        st = integration.status(paks)
        if st not in integration.FILES_OK:
            return -1
        if st == "no_override":
            self.fix_proton()
        ue = integration.ue4ss_dir(integration.win64_dir(paks))
        return integration.write_mod_list(ue, self.blueprint_mods())

    def install_integration(self, fetch=None, log: Callable[[str], None] = lambda s: None) -> int:
        paks = self.paks_dir()
        if self.foreign_loader_files():
            self.disable_foreign_loader()
        win64 = integration.win64_dir(paks)
        ue = integration.ue4ss_dir(win64)
        if ue is None or not (win64 / "dwmapi.dll").is_file():
            kw = {"fetch": fetch} if fetch else {}
            ue = integration.install_ue4ss(paks, log=log, **kw)
        integration.install_loader(ue)
        self.fix_proton(log=log)
        return self.sync_loader()

    def needs_install(self) -> list[int]:
        return [e.mod_id for e in self.mods.values()
                if e.mode == MODE_LOCAL and e.enabled and not e.installed_paks]

    def convert_to_local(self, ids: Optional[list[int]] = None) -> list[int]:
        converted = []
        for e in self.mods.values():
            if (ids is None or e.mod_id in ids) and e.mode == MODE_SUBSCRIBE:
                e.mode = MODE_LOCAL
                e.enabled = True
                e.file_id = None
                e.cached_paks = []
                converted.append(e.mod_id)
        if converted:
            self.save()
        return converted

    def orphan_paks(self) -> list[str]:
        known = {n for m in self.mods.values() for n in m.installed_paks}
        legacy = [p.name for p in self.paks_dir().glob("*.pak")]
        mods = self.mods_dir()
        current = [f"{p.parent.name}/{p.name}" for p in mods.glob("*/*.pak")] if mods.is_dir() else []
        return sorted(n for n in legacy + current
                      if n.split("/")[0].startswith(OWNED_PREFIXES) and n not in known)
