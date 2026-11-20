from __future__ import annotations

import base64
import json
import shutil
import zlib
from pathlib import Path
from typing import Callable, Optional

from . import paths
from .entry import ModEntry, SOURCE_MODIO
from .i18n import tr
from .modio import ModioError


class FoldersMixin:
    def _load_folders(self) -> None:
        data = paths.read_json(paths.FOLDERS_FILE)
        if data is not None:
            self.folders = {name: {int(k): bool(v) for k, v in mods.items()}
                            for name, mods in data.get("cartelle", {}).items()}
            self.active_folder = data.get("attiva", "")
            if data.get("versione", 1) < 2:
                for name, f in self.folders.items():
                    self.folders[name] = dict(sorted(
                        f.items(), key=lambda kv: (self.mods[kv[0]].name.lower()
                                                   if kv[0] in self.mods else "")))
        for blank in [n for n in self.folders if not n.strip()]:
            if len(self.folders) > 1:
                del self.folders[blank]
        if not self.folders:
            self.folders = {tr("folder.default"): {
                i: e.enabled for i, e in sorted(self.mods.items(), key=lambda kv: kv[1].name.lower())}}
        if self.active_folder not in self.folders:
            self.active_folder = next(iter(self.folders))
        self._sync_folders()

    def _sync_folders(self) -> None:
        if not self.active_folder:
            return
        folder = self.folders.setdefault(self.active_folder, {})
        in_any = set().union(*self.folders.values()) if self.folders else set()
        for i, e in self.mods.items():
            if i in folder or e.enabled or i not in in_any:
                folder[i] = e.enabled
        for f in self.folders.values():
            for i in [i for i in f if i not in self.mods]:
                del f[i]

    def _save_folders(self) -> None:
        paths.write_json(paths.FOLDERS_FILE, {
            "versione": 2,
            "attiva": self.active_folder,
            "cartelle": {name: {str(k): v for k, v in mods.items()}
                         for name, mods in self.folders.items()},
        })

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
        self.log_event("folder", new=name)

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
            shutil.rmtree(paths.MODS_CACHE / str(i), ignore_errors=True)
        if self.active_folder == name:
            new = next(iter(self.folders))
            self.active_folder = new
            folder = self.folders[new]
            for i, e in self.mods.items():
                e.enabled = bool(folder.get(i, False))
            self.save()
            return new
        self.save()
        return None

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

    PROFILE_FORMAT = "karl-profile"

    def export_profile(self, path: Path) -> int:
        self._sync_folders()
        mods, skipped = {}, set()
        for i, e in self.mods.items():
            if e.source != SOURCE_MODIO:
                skipped.add(i)
                continue
            mods[str(i)] = {"game_id": e.game_id, "slug": e.slug, "name": e.name,
                            "file_id": e.file_id, "hold": e.hold,
                            "favorite": e.favorite, "note": e.note, "tags": e.tags}
        folders = [{"name": n, "mods": [[i, on] for i, on in f.items()
                                        if i in self.mods and i not in skipped]}
                   for n, f in self.folders.items()]
        data = {"format": self.PROFILE_FORMAT, "version": 1, "active": self.active_folder,
                "folders": folders, "mods": mods}
        paths.write_json(path, data)
        return len(skipped)

    @classmethod
    def read_profile(cls, path: Path) -> dict:
        try:
            data = paths.read_json(path)
            if data.get("format") != cls.PROFILE_FORMAT:
                raise ValueError
            mods = {int(k): v for k, v in data["mods"].items()}
            folders = [(str(f["name"]), [(int(i), bool(on)) for i, on in f["mods"]])
                       for f in data["folders"]]
        except Exception as err:
            raise ModioError(tr("profile.invalid")) from err
        if not folders:
            raise ModioError(tr("profile.invalid"))
        return {"mods": mods, "folders": folders, "active": str(data.get("active") or folders[0][0])}

    def import_profile(self, path: Path, progress: Optional[Callable[[int, int], None]] = None,
                       log: Callable[[str], None] = lambda s: None) -> list[str]:
        data = self.read_profile(path)
        self._sync_folders()
        before = {n: dict(f) for n, f in self.folders.items()}
        staging = ".karl-import"
        self.folders[staging] = {}
        self.active_folder = staging
        failed: list[str] = []
        total = len(data["mods"])
        for n, (mid, info) in enumerate(data["mods"].items(), 1):
            name = str(info.get("name") or info.get("slug") or mid)
            log(tr("profile.importing", i=n, n=total, name=name))
            try:
                e = self.mods.get(mid)
                if e is None:
                    gid = int(info["game_id"])
                    mod = self.client.get_mod_by_id(gid, mid)
                    fid = info.get("file_id")
                    if info.get("hold") and fid and (mod.get("modfile") or {}).get("id") != fid:
                        mod = dict(mod, modfile=self.client.get_modfile(gid, mid, int(fid)))
                    e = self.add_mod_object(mod, progress=progress)
                    e.hold = bool(info.get("hold"))
                e.favorite = e.favorite or bool(info.get("favorite"))
                if info.get("note"):
                    e.note = str(info["note"])
                if info.get("tags"):
                    e.tags = self._clean_tags(e.tags + [str(t) for t in info["tags"]])
            except Exception:
                failed.append(name)
        self.folders = {n: {i: on for i, on in f.items() if i in self.mods}
                        for n, f in before.items()}
        for name, mods in data["folders"]:
            self.folders[name] = {i: on for i, on in mods if i in self.mods}
        target = data["active"] if data["active"] in self.folders else data["folders"][0][0]
        self.active_folder = target
        folder = self.folders[target]
        for i, e in self.mods.items():
            e.enabled = bool(folder.get(i, False))
        self.save()
        return failed
