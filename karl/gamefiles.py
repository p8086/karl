from __future__ import annotations

import re
import shutil
import struct
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import VERSION, game, merge, pakfile, paths
from .entry import ModEntry, approval_of
from .i18n import tr
from .modio import ModioError, mod_name
from .pakfile import PakError, init_classes
from .paths import CACHE_DIR


class GameFilesMixin:
    FOREIGN_LOADER_FILES = ("Binaries/Win64/x3daudio1_7.dll",
                            "Binaries/Win64/dwmapi.dll",
                            "Content/Paks/FSD-WindowsNoEditor_Mods.pak",
                            "Content/Paks/mods_P.pak")

    BACKUP_DIR = "_karl_backup_altri_loader"

    def foreign_loader_files(self) -> list[Path]:
        try:
            fsd = self.mods_dir().parent
        except ModioError:
            return []
        return [fsd / rel for rel in self.FOREIGN_LOADER_FILES if (fsd / rel).is_file()]

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

    def start_vanilla(self) -> int:
        n = 0
        for e in self.mods.values():
            if e.installed_paks:
                self._remove_from_game(e)
                n += 1
        self.config.vanilla_active = True
        self.config.save()
        self.save()
        return n

    def end_vanilla(self, progress: Optional[Callable[[int, int], None]] = None,
                    log: Callable[[str], None] = lambda s: None) -> dict:
        self.config.vanilla_active = False
        self.config.save()
        return self.update_all(progress=progress, log=log)

    CACHE_KINDS = ("unused", "previous", "covers")

    @staticmethod
    def _du(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        total = 0
        if path.is_dir():
            for p in path.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _cache_targets(self) -> dict[str, list[Path]]:
        used_dirs = {Path(p).parent for e in self.mods.values() for p in e.cached_paks}
        prev_dirs = {Path(p).parent for e in self.mods.values() for p in e.prev_paks} - used_dirs
        unused: list[Path] = []
        if paths.MODS_CACHE.is_dir():
            for d in paths.MODS_CACHE.iterdir():
                if d.is_dir() and d.name.isdigit():
                    if int(d.name) not in self.mods:
                        unused.append(d)
                    elif (d / "download").exists():
                        unused.append(d / "download")
        return {"unused": unused, "previous": sorted(prev_dirs), "covers": [CACHE_DIR / "thumbs"]}

    def cache_report(self) -> dict[str, int]:
        report = {k: sum(self._du(p) for p in ps) for k, ps in self._cache_targets().items()}
        report["mods"] = sum(self._du(Path(p)) for e in self.mods.values() for p in e.cached_paks)
        report["snapshots"] = self._du(paths.SNAPSHOT_BLOBS)
        return report

    def clean_cache(self, kinds: set[str]) -> int:
        freed = 0
        targets = self._cache_targets()
        for kind in self.CACHE_KINDS:
            if kind not in kinds:
                continue
            for p in targets[kind]:
                size = self._du(p)
                shutil.rmtree(p, ignore_errors=True)
                if not p.exists():
                    freed += size
            if kind == "previous":
                for e in self.mods.values():
                    if e.prev_paks and not all(Path(p).is_file() for p in e.prev_paks):
                        e.prev_paks, e.prev_version = [], ""
                        e.prev_file_id, e.prev_file_mtime = None, 0.0
                self._save_list()
        return freed

    CONFLICT_IGNORE = {"fsd/assetregistry.bin", "fsd/assetregistry.krl"}

    def _pak_contents(self, pak: str) -> frozenset:
        cache = self.__dict__.setdefault("_pak_cache", {})
        try:
            st = Path(pak).stat()
        except OSError:
            return frozenset()
        key = (pak, st.st_mtime, st.st_size)
        if key not in cache:
            try:
                files = pakfile.list_files(Path(pak))
            except (PakError, OSError, struct.error, ValueError):
                files = []
            cache[key] = frozenset(f.lower() for f in files) - self.CONFLICT_IGNORE
        return cache[key]

    def conflicts(self, states: Optional[dict[int, bool]] = None) -> dict[int, list[dict]]:
        states = states or {}
        order = [i for i in self.folders.get(self.active_folder, {})
                 if i in self.mods and states.get(i, self.mods[i].enabled)]
        owners: dict[str, list[int]] = {}
        for i in order:
            files: set = set()
            for pak in self.mods[i].cached_paks:
                files |= self._pak_contents(pak)
            for f in files:
                owners.setdefault(f, []).append(i)
        shared: dict[tuple[int, int], int] = {}
        for ids in owners.values():
            if len(ids) < 2:
                continue
            for x in range(len(ids)):
                for y in range(x + 1, len(ids)):
                    shared[(ids[x], ids[y])] = shared.get((ids[x], ids[y]), 0) + 1
        out: dict[int, list[dict]] = {}
        for (a, b), n in shared.items():
            out.setdefault(a, []).append({"id": b, "files": n, "winner": a})
            out.setdefault(b, []).append({"id": a, "files": n, "winner": a})
        return out

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
                    mod_id=mid, game_id=gid, name=mod_name(mod) or str(mid),
                    slug=mod.get("name_id") or str(mid), url=mod.get("profile_url") or "",
                    approval=approval_of(mod))
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

    def blueprint_mods(self) -> list[tuple[str, dict]]:
        out = []
        for e in self.mods.values():
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

    def integration_mods(self) -> list[tuple[str, list[Path]]]:
        if self.config.vanilla_active:
            return []
        out = []
        entries = sorted((e for e in self.mods.values() if e.enabled and e.installed_paks),
                         key=lambda e: -self._installed_priority(e))
        for e in entries:
            paks = [self._installed_path(n) for n in e.installed_paks]
            paks = [p for p in paks if p.is_file()]
            if paks:
                out.append((e.name, paks))
        return out

    def integration_info(self) -> list[dict]:
        by_name = {e.name: e for e in self.mods.values() if e.enabled}
        out = []
        for name, _ in self.integration_mods():
            e = by_name.get(name)
            out.append({"name": name, "version": e.version if e else "", "url": e.url if e else "",
                        "author": getattr(e, "author", "") if e else "",
                        "approval": (e.approval or "Sandbox") if e else "Sandbox"})
        return out

    @staticmethod
    def _installed_priority(e: ModEntry) -> int:
        found = [int(m.group(1)) for n in e.installed_paks
                 for m in [re.search(r"_(\d+)_P\.pak$", n)] if m]
        return max(found, default=0)

    def integration_outdated(self) -> bool:
        if not self.config.paks_dir:
            return False
        try:
            paks = self.paks_dir()
        except ModioError:
            return False
        return merge.outdated(paks, self.integration_mods(), VERSION)

    def integration_skipped(self) -> list:
        try:
            return merge.read_stamp(self.paks_dir()).get("skipped", [])
        except ModioError:
            return []

    def sync_integration(self, force: bool = False,
                         log: Callable[[str], None] = lambda s: None) -> Optional[list]:
        if not self.config.paks_dir:
            return None
        paks = self.paks_dir()
        mods = self.integration_mods()
        if not force and not merge.outdated(paks, mods, VERSION):
            return None
        if game.is_running():
            return None
        if force:
            (paks / merge.STAMP_NAME).unlink(missing_ok=True)
        return merge.build(paks, mods, VERSION, self.integration_info())

    def install_integration(self, log: Callable[[str], None] = lambda s: None) -> int:
        if game.is_running():
            raise ModioError(tr("mine.close_game_first"))
        try:
            skipped = self.sync_integration(force=True, log=log) or []
        except merge.ToolMissing:
            raise ModioError(tr("integ.no_tool"))
        except merge.MergeError as err:
            raise ModioError(tr("integ.failed", error=err))
        for name, err in skipped:
            log(tr("integ.skipped", name=name, error=err))
        return len(self.integration_mods())
