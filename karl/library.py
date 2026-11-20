from __future__ import annotations

import html
import re
import shutil
import struct
import time
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import paths
from .config import Config
from .entry import ENTRY_KEYS, GAME_PAK_PREFIX, MOD_FILE_EXT, ModEntry, SOURCE_FILE, approval_of
from .folders import FoldersMixin
from .gamefiles import GameFilesMixin
from .i18n import tr
from .modio import ModioClient, ModioError, mod_name, modfile_info, parse_mod_url
from .pakfile import PakError, hide_asset_registry
from .paths import MAIN_INSTALL, use_install
from .updates import UpdatesMixin


class Library(FoldersMixin, UpdatesMixin, GameFilesMixin):
    def __init__(self, config: Config, client_factory: Callable[[str], ModioClient] = ModioClient):
        self.config = config
        self.client_factory = client_factory
        use_install(getattr(config, "active_install", MAIN_INSTALL))
        self.mods: dict[int, ModEntry] = {}
        self.folders: dict[str, dict[int, bool]] = {}
        self.active_folder: str = ""
        self._changes: list[dict] = []
        self.load()
        from .history import History
        self.history = History(paths.HISTORY_FILE)
        from .timemachine import TimeMachine
        self.time_machine = TimeMachine(self, paths.SNAPSHOTS_FILE, paths.SNAPSHOT_BLOBS)

    def load(self) -> None:
        for raw in paths.read_json(paths.LIBRARY_FILE, []):
            e = ModEntry(**{k: v for k, v in raw.items() if k in ENTRY_KEYS})
            e.name = html.unescape(e.name)
            self.mods[e.mod_id] = e
        self._load_folders()

    def save(self) -> None:
        self._sync_folders()
        self._save_folders()
        self._save_list()
        try:
            self.sync_loader()
        except Exception:
            pass

    def _save_list(self) -> None:
        paths.write_json(paths.LIBRARY_FILE, [asdict(m) for m in self.mods.values()])

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
        changed = list(new) != list(folder)
        self.folders[self.active_folder] = new
        self.save()
        if changed:
            self.log_event("order")

    def needs_reorder(self, e: ModEntry) -> bool:
        return bool(e.enabled and e.installed_paks and e.installed_paks != self._pak_names(e))

    def _installed_path(self, name: str) -> Path:
        return self.mods_dir() / name if "/" in name else self.paks_dir() / name

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

    def _remove_from_game(self, entry: ModEntry) -> None:
        if not entry.installed_paks:
            return
        for name in entry.installed_paks:
            if not name.split("/")[0].startswith(GAME_PAK_PREFIX):
                continue
            path = self._installed_path(name)
            path.unlink(missing_ok=True)
            if "/" in name:
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        entry.installed_paks = []

    def _download(self, entry: ModEntry, mod: dict,
                  progress: Optional[Callable[[int, int], None]]) -> None:
        info = modfile_info(mod)
        mod_cache = paths.MODS_CACHE / str(entry.mod_id)
        updating = entry.file_id is not None and info["file_id"] != entry.file_id
        old_version = entry.version
        if updating:
            self._keep_previous(entry)
        archive = self.client.download(info["url"], mod_cache / "download" / info["filename"],
                                       progress, md5=info.get("md5", ""))
        paks = self.extract_paks(archive, mod_cache / "paks")
        archive.unlink(missing_ok=True)
        entry.cached_paks = [str(p) for p in paks]
        entry.file_id = info["file_id"]
        entry.version = info["version"]
        entry.updated_at = float(info.get("date") or time.time())
        if updating:
            self.log_event("updated", entry, old=old_version or "-", new=entry.version or "-")
            self._changes.append({"mod_id": entry.mod_id, "game_id": entry.game_id,
                                  "name": entry.name, "old": old_version, "new": entry.version,
                                  "file_id": entry.file_id})

    def add(self, url: str,
            progress: Optional[Callable[[int, int], None]] = None,
            log: Callable[[str], None] = lambda s: None) -> ModEntry:
        game_slug, mod_slug = parse_mod_url(url)
        log(tr("lib.searching", slug=mod_slug))
        mod = self.client.get_mod(game_slug, mod_slug)
        return self.add_mod_object(mod, progress=progress, log=log)

    def add_mod_object(self, mod: dict,
                       progress: Optional[Callable[[int, int], None]] = None,
                       log: Callable[[str], None] = lambda s: None) -> ModEntry:
        mod_id, game_id = int(mod["id"]), int(mod["game_id"])
        slug = mod.get("name_id") or str(mod_id)
        old = self.mods.get(mod_id)
        if old is not None:
            if not old.enabled:
                self.set_enabled(mod_id, True, log=log)
            else:
                log(tr("lib.already", name=old.name))
            return old

        entry = ModEntry(mod_id=mod_id, game_id=game_id, name=mod_name(mod) or slug,
                         slug=slug, url=mod.get("profile_url") or "",
                         approval=approval_of(mod), added_at=time.time())
        self._remember_meta(entry, mod, self.client)
        log(tr("lib.downloading"))
        self._download(entry, mod, progress)
        self._copy_to_game(entry)

        self.mods[mod_id] = entry
        self.save()
        self.log_event("added", entry, new=entry.version)
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
        is_new = entry is None
        if entry is None:
            new_id = min([m.mod_id for m in self.mods.values() if m.mod_id < 0], default=0) - 1
            entry = ModEntry(mod_id=new_id, game_id=0, name=stem, slug=slug, url="",
                             source=SOURCE_FILE, added_at=time.time())
        self._import_file(entry, src)
        entry.enabled = True
        self._copy_to_game(entry)
        self.mods[entry.mod_id] = entry
        self.save()
        self.log_event("added" if is_new else "updated", entry, new=entry.version)
        log(tr("lib.added", name=entry.name))
        return entry

    def log_event(self, kind: str, entry: Optional[ModEntry] = None, **kw) -> None:
        history = getattr(self, "history", None)
        if history is None:
            return
        if kind in ("on", "off") and getattr(self, "quiet_toggles", False):
            return
        if entry is not None:
            kw.setdefault("name", entry.name)
            kw.setdefault("mod_id", entry.mod_id)
        history.add(kind, folder=self.active_folder, **kw)

    def _import_file(self, entry: ModEntry, src: Path) -> None:
        if entry.cached_paks and src.is_file() and src.stat().st_mtime != entry.file_mtime:
            self._keep_previous(entry)
        paks = self.extract_paks(src, paths.MODS_CACHE / str(entry.mod_id) / "paks")
        entry.cached_paks = [str(p) for p in paks]
        entry.file_path = str(src.resolve())
        entry.file_mtime = src.stat().st_mtime
        entry.updated_at = entry.file_mtime
        entry.version = datetime.fromtimestamp(entry.file_mtime).strftime("%Y-%m-%d %H:%M")

    def set_favorite(self, ids: list[int], value: Optional[bool] = None) -> bool:
        entries = [self.mods[i] for i in ids if i in self.mods]
        if not entries:
            return False
        if value is None:
            value = not all(e.favorite for e in entries)
        for e in entries:
            e.favorite = value
        self._save_list()
        return value

    @staticmethod
    def _clean_tags(tags) -> list[str]:
        out: list[str] = []
        for t in tags:
            t = " ".join(str(t).replace("#", " ").split())[:30]
            if t and t.lower() not in {x.lower() for x in out}:
                out.append(t)
        return out

    def set_tags(self, ids: list[int], text: str) -> None:
        tags = self._clean_tags(text.split(","))
        for i in ids:
            if i in self.mods:
                self.mods[i].tags = list(tags)
        self._save_list()

    def all_tags(self) -> list[str]:
        seen: dict[str, str] = {}
        for e in self.mods.values():
            for t in e.tags:
                seen.setdefault(t.lower(), t)
        return sorted(seen.values(), key=str.lower)

    def mod_size(self, e: ModEntry) -> int:
        total = 0
        for p in e.cached_paks:
            try:
                total += Path(p).stat().st_size
            except OSError:
                pass
        return total

    def set_note(self, mod_id: int, text: str) -> None:
        self.mods[mod_id].note = " ".join((text or "").split())
        self._save_list()

    def remove(self, mod_id: int, log: Callable[[str], None] = lambda s: None) -> None:
        folder = self.folders.get(self.active_folder, {})
        folder.pop(mod_id, None)
        if any(mod_id in f for f in self.folders.values()):
            e = self.mods[mod_id]
            e.enabled = False
            self._remove_from_game(e)
            self.save()
            self.log_event("removed", e)
            log(tr("lib.removed", name=e.name))
            return
        e = self.mods.pop(mod_id)
        try:
            self._remove_from_game(e)
        finally:
            shutil.rmtree(paths.MODS_CACHE / str(mod_id), ignore_errors=True)
            self.save()
        self.log_event("removed", e)
        log(tr("lib.removed", name=e.name))

    def apply(self, wanted: dict[int, bool],
              progress: Optional[Callable[[int, int], None]] = None,
              log: Callable[[str], None] = lambda s: None) -> dict:
        for mid, on in wanted.items():
            if mid in self.mods:
                if self.mods[mid].enabled != on:
                    self.log_event("on" if on else "off", self.mods[mid])
                self.mods[mid].enabled = on
        report = self.update_all(progress=progress, log=log, install_updates=True)
        failed = {name for name, _ in report["failed"]}
        for e in self.mods.values():
            if e.name not in failed:
                e.latest_version = ""
        self.save()
        return report

    def needs_install(self) -> list[int]:
        return [e.mod_id for e in self.mods.values()
                if e.enabled and not e.installed_paks]
