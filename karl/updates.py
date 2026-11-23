from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import game, paths
from .entry import ModEntry, SOURCE_FILE, SOURCE_MODIO, approval_of
from .i18n import tr
from .modio import (DEFAULT_GAME, KNOWN_GAMES, ModioClient, ModioError, dependency_info,
                    game_versions_of, mod_name, modfile_info, version_key)


class UpdatesMixin:
    def _keep_previous(self, entry: ModEntry) -> None:
        if not entry.cached_paks or not all(Path(p).is_file() for p in entry.cached_paks):
            return
        mod_cache = paths.MODS_CACHE / str(entry.mod_id)
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

    def can_rollback(self, mod_id: int) -> bool:
        e = self.mods.get(mod_id)
        return bool(e) and (self.has_previous(mod_id)
                            or (e.source != SOURCE_FILE and bool(self.config.token)))

    def previous_modfile(self, e: ModEntry) -> Optional[dict]:
        files = self.client.list_modfiles(e.game_id, e.mod_id, limit=20)
        ids = [f.get("id") for f in files]
        start = ids.index(e.file_id) + 1 if e.file_id in ids else 0
        for f in files[start:]:
            if f.get("id") != e.file_id and (f.get("download") or {}).get("binary_url"):
                return f
        return None

    def available_versions(self, mod_id: int) -> list[dict]:
        e = self.mods.get(mod_id)
        if e is None or e.source == SOURCE_FILE:
            raise ModioError(tr("versions.none"))
        if not self.config.token:
            raise ModioError(tr("err.no_token"), status=401)
        out = []
        for f in self.client.list_modfiles(e.game_id, e.mod_id, limit=50):
            if not (f.get("download") or {}).get("binary_url"):
                continue
            out.append({"file_id": f.get("id"), "version": f.get("version") or "-",
                        "date": f.get("date_added") or 0, "size": f.get("filesize") or 0,
                        "current": f.get("id") == e.file_id})
        if not out:
            raise ModioError(tr("versions.none"))
        return out

    def install_version(self, mod_id: int, file_id: int,
                        log: Callable[[str], None] = lambda s: None,
                        progress: Optional[Callable[[int, int], None]] = None) -> ModEntry:
        e = self.mods[mod_id]
        versions = self.available_versions(mod_id)
        newest = versions[0]["file_id"]
        if file_id != e.file_id:
            mf = self.client.get_modfile(e.game_id, e.mod_id, file_id)
            self._download(e, {"modfile": mf, "name_id": e.slug}, progress)
            if e.enabled:
                self._copy_to_game(e)
        e.hold = file_id != newest
        e.latest_version = ""
        self.save()
        log(tr("versions.installed", name=e.name, version=e.version or "-"))
        return e

    def _fetch_previous(self, e: ModEntry,
                        progress: Optional[Callable[[int, int], None]]) -> None:
        mf = self.previous_modfile(e) if e.source != SOURCE_FILE and self.config.token else None
        if mf is None:
            raise ModioError(tr("rollback.none"))
        info = modfile_info({"modfile": mf, "name_id": e.slug})
        mod_cache = paths.MODS_CACHE / str(e.mod_id)
        archive = self.client.download(info["url"], mod_cache / "download" / info["filename"],
                                       progress, md5=info.get("md5", ""))
        e.prev_paks = [str(p) for p in self.extract_paks(archive, mod_cache / "prev")]
        archive.unlink(missing_ok=True)
        e.prev_version = info["version"]
        e.prev_file_id = info["file_id"]
        e.prev_file_mtime = 0.0

    def rollback(self, mod_id: int, log: Callable[[str], None] = lambda s: None,
                 progress: Optional[Callable[[int, int], None]] = None) -> ModEntry:
        e = self.mods.get(mod_id)
        if e is None:
            raise ModioError(tr("rollback.none"))
        if not self.has_previous(mod_id):
            self._fetch_previous(e, progress)
        mod_cache = paths.MODS_CACHE / str(mod_id)
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
        self.log_event("rollback", e, new=e.version or "-")
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

    def check_removed(self) -> list[ModEntry]:
        by_game: dict[int, list[int]] = {}
        for e in self.mods.values():
            if e.source == SOURCE_MODIO and e.game_id:
                by_game.setdefault(e.game_id, []).append(e.mod_id)
        if not by_game:
            return []
        client = self.client
        alive: set[int] = set()
        for gid, ids in by_game.items():
            alive |= {int(m["id"]) for m in client.mods_by_ids(gid, ids)}
        newly = []
        for ids in by_game.values():
            for i in ids:
                e = self.mods[i]
                gone = i not in alive
                if gone and not e.removed:
                    newly.append(e)
                e.removed = gone
        self._save_list()
        return sorted(newly, key=lambda e: e.name.lower())

    def update_all(self, progress: Optional[Callable[[int, int], None]] = None,
                   log: Callable[[str], None] = lambda s: None,
                   check: bool = False, install_updates: bool = False) -> dict:
        report: dict = {"updated": [], "installed": [], "ok": 0, "failed": [], "changes": []}
        self._changes = []
        if any(e.enabled for e in self.mods.values()):
            self.paks_dir()
        client = self.client if self.config.token else None

        entries = sorted(self.mods.values(), key=lambda m: m.name.lower())
        for n, e in enumerate(entries, 1):
            log(tr("lib.update_step", i=n, n=len(entries), name=e.name))
            try:
                result = self._update_entry(e, client, progress, check, install_updates)
            except Exception as err:
                report["failed"].append((e.name, str(err)))
                continue
            if result == "updated":
                report["updated"].append(e.name)
            elif result == "installed":
                report["installed"].append(e.name)
            else:
                report["ok"] += 1
        self.remove_orphan_paks()
        self.save()
        if not report["failed"]:
            try:
                self.time_machine.take()
            except OSError:
                pass
        log(tr("lib.update_summary", updated=len(report["updated"]),
               installed=len(report["installed"]), ok=report["ok"]))
        report["changes"] = list(self._changes)
        return report

    def _update_entry(self, e: ModEntry, client: Optional[ModioClient],
                      progress: Optional[Callable[[int, int], None]],
                      check: bool = False, install_updates: bool = False) -> Optional[str]:
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
        else:
            wanted_update = install_updates and bool(e.latest_version) and not e.hold
            if check or wanted_update or not cache_ok or e.file_id is None:
                if client is None:
                    raise ModioError(tr("err.no_token"), status=401)
                result = self._fetch_entry(e, client, progress, cache_ok)

        if e.enabled:
            missing = (not e.installed_paks or self.needs_reorder(e)
                       or any(not self._installed_path(n).is_file() for n in e.installed_paks))
            if result == "updated" or missing:
                self._copy_to_game(e)
                result = result or "installed"
            else:
                for name in e.installed_paks:
                    self._make_safe(self._installed_path(name))
        elif e.installed_paks:
            self._remove_from_game(e)
        return result

    def _fetch_entry(self, e: ModEntry, client: ModioClient,
                     progress: Optional[Callable[[int, int], None]], cache_ok: bool) -> Optional[str]:
        mod = client.get_mod_by_id(e.game_id, e.mod_id)
        new_id = (mod.get("modfile") or {}).get("id")
        self._remember_meta(e, mod, client)
        is_new = e.file_id is not None and new_id != e.file_id and not e.hold
        if is_new or not cache_ok or e.file_id is None:
            self._download(e, mod, progress)
            e.latest_version = ""
            return "updated" if is_new else "installed"
        return None

    def find_updates(self, progress: Optional[Callable[[int, int], None]] = None,
                     log: Callable[[str], None] = lambda s: None,
                     ids: Optional[list[int]] = None) -> dict:
        report: dict = {"updates": [], "failed": []}
        client = self.client if self.config.token else None
        pool = [self.mods[i] for i in ids if i in self.mods] if ids is not None else self.mods.values()
        entries = sorted(pool, key=lambda m: m.name.lower())
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
                    self._remember_meta(e, mod, client)
                    new = e.file_id is not None and mf.get("id") not in (None, e.file_id)
                    e.latest_version = (mf.get("version") or "?") if new else ""
            except Exception as err:
                report["failed"].append((e.name, str(err)))
                continue
            if e.latest_version:
                report["updates"].append(e.name)
        self.save()
        return report

    @staticmethod
    def _remember_meta(e: ModEntry, mod: dict, client: Optional[ModioClient]) -> None:
        e.name = mod_name(mod) or e.name
        e.approval = approval_of(mod) or e.approval
        e.game_versions = game_versions_of(mod)
        if "dependencies" in mod and not mod.get("dependencies"):
            e.deps = []
            return
        if client is None:
            return
        try:
            e.deps = [dependency_info(d) for d in client.dependencies(e.game_id, e.mod_id)]
        except (ModioError, KeyError, TypeError, ValueError):
            pass

    def missing_dependencies(self, states: Optional[dict[int, bool]] = None) -> dict[int, list[dict]]:
        states = states or {}
        folder = self.folders.get(self.active_folder, {})
        on = {i for i in folder if i in self.mods and states.get(i, self.mods[i].enabled)}
        out: dict[int, list[dict]] = {}
        for i in on:
            missing = [d for d in self.mods[i].deps or [] if d["id"] not in on]
            if missing:
                out[i] = missing
        return out

    def game_build(self) -> str:
        try:
            steamapps = self.paks_dir().parents[4]
        except (IndexError, ModioError, OSError):
            return ""
        manifest = steamapps / f"appmanifest_{game.STEAM_APP_ID}.acf"
        try:
            m = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(errors="ignore"))
        except OSError:
            return ""
        return m.group(1) if m else ""

    def game_update_check(self) -> bool:
        build = self.game_build()
        if not build or build == self.config.game_build:
            return False
        first = not self.config.game_build
        self.config.game_build = build
        self.config.save()
        return not first

    def outdated_mods(self) -> tuple[str, list[ModEntry]]:
        gid = KNOWN_GAMES[DEFAULT_GAME]
        versions = [t for g in self.client.game_tags(gid) if "version" in g.get("name", "").lower()
                    for t in g.get("tags", [])]
        versions = [v for v in versions if version_key(v)]
        if not versions:
            return "", []
        latest = max(versions, key=version_key)
        ids = [e.mod_id for e in self.folder_mods() if e.enabled and e.source != SOURCE_FILE]
        for mod in self.client.mods_by_ids(gid, ids) if ids else []:
            e = self.mods.get(int(mod["id"]))
            if e:
                e.game_versions = game_versions_of(mod)
        self.save()
        old = [self.mods[i] for i in ids if latest not in self.mods[i].game_versions]
        return latest, sorted(old, key=lambda e: e.name.lower())

    def check_updates(self, progress: Optional[Callable[[int, int], None]] = None,
                      log: Callable[[str], None] = lambda s: None) -> list[str]:
        return self.update_all(progress=progress, log=log, check=True)["updated"]
