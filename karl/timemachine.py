from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .library import Library

MAX_SNAPSHOTS = 50
ENTRY_FIELDS = ("mod_id", "game_id", "name", "slug", "url", "file_id", "version",
                "source", "file_path", "file_mtime", "approval")


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class TimeMachine:
    def __init__(self, lib: "Library", index_file: Path, blob_dir: Path):
        self.lib = lib
        self.index_file = index_file
        self.blob_dir = blob_dir
        self.snapshots: list[dict] = []
        self._hash_cache: dict[tuple, str] = {}
        if index_file.exists():
            try:
                self.snapshots = json.loads(index_file.read_text(encoding="utf-8"))
            except ValueError:
                self.snapshots = []

    def _blob(self, pak: Path) -> str:
        st = pak.stat()
        key = (str(pak), st.st_size, st.st_mtime)
        digest = self._hash_cache.get(key) or _hash(pak)
        self._hash_cache[key] = digest
        dest = self.blob_dir / f"{digest}.pak"
        if not dest.exists():
            self.blob_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.link(pak, dest)
            except OSError:
                shutil.copyfile(pak, dest)
        return digest

    def _state(self) -> dict:
        lib = self.lib
        folder = lib.folders.get(lib.active_folder, {})
        mods = []
        for i, on in folder.items():
            e = lib.mods.get(i)
            if e is None:
                continue
            paks = []
            for p in e.cached_paks:
                pp = Path(p)
                if pp.is_file():
                    paks.append({"name": pp.name, "hash": self._blob(pp)})
            row = {k: getattr(e, k) for k in ENTRY_FIELDS}
            row.update({"on": bool(on), "paks": paks})
            mods.append(row)
        return {"folder": lib.active_folder, "mods": mods}

    @staticmethod
    def _signature(state: dict) -> str:
        return json.dumps([state["folder"], [(m["mod_id"], m["on"], [p["hash"] for p in m["paks"]])
                                            for m in state["mods"]]])

    def take(self) -> Optional[dict]:
        state = self._state()
        if self.snapshots and self._signature(self.snapshots[-1]) == self._signature(state):
            return None
        now = datetime.now()
        state["id"] = now.strftime("%Y%m%d-%H%M%S-%f")
        state["time"] = now.isoformat(timespec="seconds")
        self.snapshots.append(state)
        self._trim(state["folder"])
        self._save()
        self._prune_blobs()
        return state

    def _trim(self, folder: str) -> None:
        own = [s for s in self.snapshots if s["folder"] == folder]
        drop = {id(s) for s in own[:-MAX_SNAPSHOTS]}
        if drop:
            self.snapshots = [s for s in self.snapshots if id(s) not in drop]

    def rename_folder(self, old: str, new: str) -> None:
        changed = False
        for s in self.snapshots:
            if s["folder"] == old:
                s["folder"] = new
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(self.snapshots, indent=1, ensure_ascii=False),
                                   encoding="utf-8")

    def _prune_blobs(self) -> None:
        used = {p["hash"] for s in self.snapshots for m in s["mods"] for p in m["paks"]}
        if self.blob_dir.is_dir():
            for f in self.blob_dir.glob("*.pak"):
                if f.stem not in used:
                    f.unlink(missing_ok=True)

    def get(self, snap_id: str) -> dict:
        for s in self.snapshots:
            if s["id"] == snap_id:
                return s
        raise KeyError(snap_id)

    @staticmethod
    def changes(prev: Optional[dict], cur: dict) -> dict:
        if prev is None or prev["folder"] != cur["folder"]:
            return {"first": True}
        a = {m["mod_id"]: m for m in prev["mods"]}
        b = {m["mod_id"]: m for m in cur["mods"]}
        common = [i for i in b if i in a]
        hashes = lambda m: [p["hash"] for p in m["paks"]]
        return {
            "added": sum(1 for i in b if i not in a),
            "removed": sum(1 for i in a if i not in b),
            "versions": sum(1 for i in common if hashes(a[i]) != hashes(b[i])),
            "toggled": sum(1 for i in common if a[i]["on"] != b[i]["on"]),
            "order": [i for i in a if i in b] != [i for i in b if i in a],
        }

    def listing(self) -> list[tuple[dict, dict]]:
        out, last_by_folder = [], {}
        for s in self.snapshots:
            out.append((s, self.changes(last_by_folder.get(s["folder"]), s)))
            last_by_folder[s["folder"]] = s
        return list(reversed(out))

    def restore(self, snap_id: str) -> list[str]:
        from . import library
        from .library import ModEntry
        lib = self.lib
        snap = self.get(snap_id)
        missing: list[str] = []
        name = snap["folder"]
        if name not in lib.folders:
            lib.folders[name] = {}
        new_folder: dict[int, bool] = {}
        for row in snap["mods"]:
            blobs = [self.blob_dir / f"{p['hash']}.pak" for p in row["paks"]]
            if not blobs or not all(b.is_file() for b in blobs):
                missing.append(row["name"])
                continue
            i = row["mod_id"]
            e = lib.mods.get(i)
            if e is None:
                e = ModEntry(**{k: row[k] for k in ENTRY_FIELDS if k in row})
                lib.mods[i] = e
            current = [_hash(Path(p)) if Path(p).is_file() else "" for p in e.cached_paks]
            wanted = [p["hash"] for p in row["paks"]]
            if current != wanted:
                lib._keep_previous(e)
                cache = library.MODS_CACHE / str(i) / "paks"
                if cache.exists():
                    shutil.rmtree(cache)
                cache.mkdir(parents=True)
                e.cached_paks = []
                for p, blob in zip(row["paks"], blobs):
                    shutil.copyfile(blob, cache / p["name"])
                    e.cached_paks.append(str(cache / p["name"]))
                for k in ("version", "file_id", "file_mtime", "approval"):
                    setattr(e, k, row[k])
                e.hold = True
                e.latest_version = ""
            new_folder[i] = bool(row["on"])
        lib.folders[name] = new_folder
        lib.active_folder = ""
        lib.switch_folder(name)
        return missing
