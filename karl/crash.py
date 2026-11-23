from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from . import proton

LOG_NAME = "FSD.log"
CRITICAL = "=== Critical error: ==="


def fsd_dir(paks_dir: Path) -> Path:
    return Path(paks_dir).parent.parent


def crash_places(paks_dir: Path) -> list[Path]:
    fsd = fsd_dir(paks_dir)
    places = [fsd / "Saved" / "Crashes"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        places.append(Path(local) / "FSD" / "Saved" / "Crashes")
    pfx = proton.prefix_for(paks_dir)
    if pfx is not None:
        places.append(pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "FSD" /
                      "Saved" / "Crashes")
    return places


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _log_error(log: Path) -> tuple[bool, str]:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, ""
    if CRITICAL not in text:
        return False, ""
    tail = text[text.rfind(CRITICAL):]
    m = re.search(r"(Unhandled Exception:[^\r\n]*|Assertion failed:[^\r\n]*|Fatal error:[^\r\n]*\S)",
                  tail)
    if m is None:
        m = re.search(r"Error:\s*(\S[^\r\n]*)", tail[len(CRITICAL):])
    return True, (m.group(1) if m else "").strip()


def find_crash(paks_dir: Path, since: float) -> Optional[dict]:
    if not paks_dir:
        return None
    fsd = fsd_dir(paks_dir)
    found: dict = {"time": 0.0, "files": [], "detail": ""}
    log = fsd / "Saved" / "Logs" / LOG_NAME
    if _mtime(log) >= since:
        crashed, detail = _log_error(log)
        if crashed:
            found["time"] = _mtime(log)
            found["detail"] = detail
            found["files"].append(str(log))
    win64 = fsd / "Binaries" / "Win64"
    try:
        dumps = [p for p in win64.glob("crash_*.dmp") if _mtime(p) >= since]
    except OSError:
        dumps = []
    if dumps:
        found["files"] += [str(p) for p in dumps]
        found["time"] = max([found["time"]] + [_mtime(p) for p in dumps])
    for place in crash_places(paks_dir):
        try:
            dirs = [d for d in place.iterdir() if d.is_dir() and _mtime(d) >= since]
        except OSError:
            continue
        for d in dirs:
            found["files"].append(str(d))
            found["time"] = max(found["time"], _mtime(d))
    return found if found["files"] else None
