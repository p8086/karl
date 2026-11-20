from __future__ import annotations

import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import VERSION, game, integration, proton
from .log import LOG_FILE
from .modio import ModioError, mask_token

if TYPE_CHECKING:
    from .library import Library

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _writable(folder: Path) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix=".karl-test-", dir=folder)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def run(lib: "Library", network: bool = True) -> list[tuple[str, str]]:
    from .library import SOURCE_FILE, SOURCE_MODIO
    out: list[tuple[str, str]] = []
    add = lambda level, text: out.append((level, text))
    cfg = lib.config

    add(INFO, f"Karl {VERSION} ({'exe' if getattr(sys, 'frozen', False) else 'source'})")
    add(INFO, f"System: {platform.platform()} · Python {platform.python_version()}")
    add(INFO, f"Language: {cfg.language} · Date: {datetime.now():%Y-%m-%d %H:%M}")

    paks = None
    if not cfg.paks_dir:
        add(FAIL, "Paks folder: not set")
    else:
        p = Path(cfg.paks_dir).expanduser()
        if not p.is_dir():
            add(FAIL, f"Paks folder: missing ({p})")
        else:
            paks = p
            add(OK, f"Paks folder: {p}")
            if (p / "FSD-WindowsNoEditor.pak").is_file():
                add(OK, "Game files: FSD-WindowsNoEditor.pak found")
            else:
                add(WARN, "Game files: FSD-WindowsNoEditor.pak not found in the Paks folder")
            add(OK if _writable(p) else FAIL,
                "Paks folder writable" if _writable(p) else "Paks folder: no write permission")

    build = lib.game_build()
    add(INFO, f"Game build: {build or 'unknown'} · running: {'yes' if game.is_running() else 'no'}")
    if cfg.vanilla_active:
        add(WARN, "Playing without mods is active: mods are out of the game until DRG closes")

    folder_mods = lib.folder_mods()
    on = [e for e in folder_mods if e.enabled]
    add(INFO, f"Mods: {len(lib.mods)} in total · folder '{lib.active_folder}': "
              f"{len(folder_mods)} ({len(on)} on) · folders: {len(lib.folders)}")
    no_cache = [e.name for e in lib.mods.values()
                if not e.cached_paks or not all(Path(c).is_file() for c in e.cached_paks)]
    if no_cache:
        add(WARN, "Mods without downloaded files: " + ", ".join(no_cache[:15]))
    if paks is not None and not cfg.vanilla_active:
        missing = [e.name for e in on if not e.installed_paks
                   or any(not lib._installed_path(n).is_file() for n in e.installed_paks)]
        if missing:
            add(FAIL, "Mods on but not in the game (press Install and save): "
                      + ", ".join(missing[:15]))
        else:
            add(OK, "All mods turned on are in the game")
        reorder = [e.name for e in on if lib.needs_reorder(e)]
        if reorder:
            add(WARN, "Load order not applied yet for: " + ", ".join(reorder[:15]))
    files = [e.name for e in lib.mods.values() if e.source == SOURCE_FILE]
    if files:
        add(INFO, "Mods from .pak files: " + ", ".join(files[:15]))
    removed = [e.name for e in lib.mods.values() if e.removed]
    if removed:
        add(WARN, "Removed from mod.io (Karl keeps the copy): " + ", ".join(removed[:15]))

    deps = lib.missing_dependencies()
    if deps:
        add(WARN, "Missing required mods: " + "; ".join(
            f"{lib.mods[i].name} needs {', '.join(d['name'] for d in ds)}"
            for i, ds in list(deps.items())[:10]))
    else:
        add(OK, "Required mods: nothing missing")
    clashes = lib.conflicts()
    if clashes:
        pairs = sorted({tuple(sorted((i, c["id"]))) for i, cs in clashes.items() for c in cs})
        add(WARN, f"Conflicts: {len(pairs)} pair(s) of mods change the same files: " + "; ".join(
            f"{lib.mods[a].name} / {lib.mods[b].name}" for a, b in pairs[:10]))
    else:
        add(OK, "Conflicts: none")

    if paks is not None:
        foreign = lib.foreign_loader_files()
        if foreign:
            add(WARN, "Other mod loaders found: " + ", ".join(str(f.name) for f in foreign))
        st = integration.status(paks)
        level = OK if st == "ok" else WARN
        add(level, f"Blueprint mod support (UE4SS + Karl loader): {st}")
        if proton.needed():
            pfx = proton.prefix_for(paks)
            if pfx is None:
                add(WARN, "Proton: prefix not found (start DRG once with Steam)")
            else:
                add(OK if proton.has_override(pfx) else WARN,
                    "Proton: dwmapi override " + ("set" if proton.has_override(pfx) else "missing"))

    if not cfg.token:
        add(WARN, "mod.io: not signed in")
    else:
        add(INFO, f"mod.io token: {mask_token(cfg.token)}")
        if network:
            try:
                me = lib.client.me()
                add(OK, f"mod.io: signed in as {me.get('username') or me.get('name_id') or '?'}")
            except ModioError as err:
                add(FAIL, f"mod.io: {err}")
            except Exception as err:
                add(FAIL, f"mod.io: {err}")

    try:
        rep = lib.cache_report()
        add(INFO, "Disk: " + ", ".join(f"{k} {_size(v)}" for k, v in rep.items()))
    except Exception as err:
        add(WARN, f"Disk: {err}")

    add(INFO, f"Log file: {LOG_FILE}")
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = [ln for ln in lines if "WARNING" in ln or "ERROR" in ln][-10:]
        for ln in tail:
            add(INFO, "log: " + ln)
    except OSError:
        pass
    return out


def format_report(items: list[tuple[str, str]]) -> str:
    marks = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", INFO: "[INFO]"}
    return "\n".join(f"{marks.get(level, level)} {text}" for level, text in items)
