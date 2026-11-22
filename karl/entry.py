from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional


GAME_PAK_PREFIX = "FSD-WindowsNoEditor_karl_"
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
class ModEntry:
    mod_id: int
    game_id: int
    name: str
    slug: str
    url: str
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
    favorite: bool = False
    note: str = ""
    deps: Optional[list] = None
    online: bool = False
    game_versions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    removed: bool = False
    added_at: float = 0.0
    updated_at: float = 0.0

    @property
    def has_update(self) -> bool:
        return bool(self.latest_version)


ENTRY_KEYS = {f.name for f in fields(ModEntry)}
