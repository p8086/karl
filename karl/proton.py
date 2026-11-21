from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from .game import STEAM_APP_ID

SECTION = r"[Software\\Wine\\DllOverrides]"
OVERRIDE = '"dwmapi"="native,builtin"'
BACKUP_SUFFIX = ".karl-backup"


def needed() -> bool:
    return sys.platform.startswith("linux")


def prefix_for(paks_dir: Path) -> Optional[Path]:
    try:
        steamapps = Path(paks_dir).parents[4]
    except IndexError:
        return None
    pfx = steamapps / "compatdata" / str(STEAM_APP_ID) / "pfx"
    return pfx if (pfx / "user.reg").is_file() else None


def _section_bounds(lines: list[str]) -> Optional[tuple[int, int]]:
    start = None
    for i, line in enumerate(lines):
        if line.startswith("["):
            if start is not None:
                return start, i
            if line.split("]")[0] + "]" == SECTION:
                start = i
    return (start, len(lines)) if start is not None else None


def has_override(pfx: Path) -> bool:
    lines = (pfx / "user.reg").read_text(encoding="latin-1").splitlines()
    b = _section_bounds(lines)
    if b is None:
        return False
    for line in lines[b[0] + 1:b[1]]:
        m = re.match(r'^"dwmapi"="([^"]*)"', line.strip(), re.I)
        if m:
            return m.group(1).lower().startswith("native")
    return False


def set_override(pfx: Path) -> bool:
    reg = pfx / "user.reg"
    if has_override(pfx):
        return False
    text = reg.read_text(encoding="latin-1")
    backup = reg.with_name(reg.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(reg, backup)
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    b = _section_bounds(lines)
    if b is None:
        now = int(time.time())
        filetime = (now + 11644473600) * 10_000_000
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{SECTION} {now}", f"#time={filetime:x}", OVERRIDE]
    else:
        values = [i for i in range(b[0] + 1, b[1]) if re.match(r'^"dwmapi"=', lines[i], re.I)]
        if values:
            lines[values[0]] = OVERRIDE
        else:
            at = b[0] + 1
            while at < b[1] and lines[at].startswith("#"):
                at += 1
            lines.insert(at, OVERRIDE)
    reg.write_text(nl.join(lines) + nl, encoding="latin-1")
    return True
