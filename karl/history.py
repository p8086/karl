from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

MAX_ENTRIES = 2000
CHANGE_KINDS = ("added", "removed", "updated", "on", "off", "rollback", "order")


class History:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [e for e in data if isinstance(e, dict) and e.get("kind")]
        except (OSError, ValueError, TypeError):
            self.entries = []

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.entries[-MAX_ENTRIES:], ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def add(self, kind: str, folder: str = "", name: str = "", mod_id: Optional[int] = None,
            old: str = "", new: str = "", text: str = "", when: Optional[float] = None,
            save: bool = True) -> dict:
        entry = {"t": float(when if when is not None else time.time()), "kind": kind,
                 "folder": folder}
        for key, value in (("name", name), ("old", old), ("new", new), ("text", text)):
            if value:
                entry[key] = str(value)
        if mod_id is not None:
            entry["mod_id"] = int(mod_id)
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
        if save:
            self.save()
        return entry

    def listing(self, folder: Optional[str] = None, search: str = "") -> list[dict]:
        words = search.lower().split()
        out = []
        for e in reversed(self.entries):
            if folder and e.get("folder") != folder:
                continue
            hay = " ".join(str(e.get(k, "")) for k in ("name", "old", "new", "text", "kind",
                                                         "folder")).lower()
            if all(w in hay for w in words):
                out.append(e)
        return out

    def changes_since_good(self, folder: str) -> list[dict]:
        out: list[dict] = []
        for e in reversed(self.entries):
            if e.get("folder") not in (folder, ""):
                continue
            if e["kind"] == "session_ok":
                break
            if e["kind"] in CHANGE_KINDS:
                out.append(e)
        return list(reversed(out))
