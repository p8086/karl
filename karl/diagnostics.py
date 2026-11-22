from __future__ import annotations

import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from . import VERSION, game, integration, log as karl_log, proton
from .entry import SOURCE_FILE
from .i18n import human_size
from .modio import mask_token

if TYPE_CHECKING:
    from .library import Library

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


def _writable(folder: Path) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix=".karl-test-", dir=folder)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def run(lib: "Library", network: bool = True) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    add = lambda level, text: out.append((level, text))
    _system(lib, add)
    paks = _paks_folder(lib.config.paks_dir, add)
    _game(lib, add)
    _mods(lib, paks, add)
    _requirements(lib, add)
    if paks is not None:
        _game_folder(lib, paks, add)
    _modio(lib, network, add)
    _disk_and_log(lib, add)
    return out


def _system(lib: "Library", add) -> None:
    cfg = lib.config
    add(INFO, f"Karl {VERSION} ({'exe' if getattr(sys, 'frozen', False) else 'source'})")
    add(INFO, f"System: {platform.platform()} · Python {platform.python_version()}")
    add(INFO, f"Language: {cfg.language} · Interface size: {int(cfg.ui_scale * 100)}% · "
              f"Date: {datetime.now():%Y-%m-%d %H:%M}")
    if len(cfg.installs) > 1:
        names = ", ".join(i.get("name", "?") for i in cfg.installs)
        add(INFO, f"Installations: {names} · active: {cfg.current_install().get('name')}")


def _paks_folder(paks_dir: str, add) -> Optional[Path]:
    if not paks_dir:
        add(FAIL, "Paks folder: not set")
        return None
    p = Path(paks_dir).expanduser()
    if not p.is_dir():
        add(FAIL, f"Paks folder: missing ({p})")
        return None
    add(OK, f"Paks folder: {p}")
    if (p / "FSD-WindowsNoEditor.pak").is_file():
        add(OK, "Game files: FSD-WindowsNoEditor.pak found")
    else:
        add(WARN, "Game files: FSD-WindowsNoEditor.pak not found in the Paks folder")
    if _writable(p):
        add(OK, "Paks folder writable")
    else:
        add(FAIL, "Paks folder: no write permission")
    return p


def _game(lib: "Library", add) -> None:
    build = lib.game_build()
    add(INFO, f"Game build: {build or 'unknown'} · running: {'yes' if game.is_running() else 'no'}")
    if lib.config.vanilla_active:
        add(WARN, "Playing without mods is active: mods are out of the game until DRG closes")


def _names(entries) -> str:
    return ", ".join(e.name for e in list(entries)[:15])


def _mods(lib: "Library", paks: Optional[Path], add) -> None:
    folder_mods = lib.folder_mods()
    on = [e for e in folder_mods if e.enabled]
    add(INFO, f"Mods: {len(lib.mods)} in total · folder '{lib.active_folder}': "
              f"{len(folder_mods)} ({len(on)} on) · folders: {len(lib.folders)}")
    no_cache = [e for e in lib.mods.values()
                if not e.cached_paks or not all(Path(c).is_file() for c in e.cached_paks)]
    if no_cache:
        add(WARN, "Mods without downloaded files: " + _names(no_cache))
    if paks is not None and not lib.config.vanilla_active:
        missing = [e for e in on if not e.installed_paks
                   or any(not lib._installed_path(n).is_file() for n in e.installed_paks)]
        if missing:
            add(FAIL, "Mods on but not in the game (press Install and save): " + _names(missing))
        else:
            add(OK, "All mods turned on are in the game")
        reorder = [e for e in on if lib.needs_reorder(e)]
        if reorder:
            add(WARN, "Load order not applied yet for: " + _names(reorder))
    files = [e for e in lib.mods.values() if e.source == SOURCE_FILE]
    if files:
        add(INFO, "Mods from .pak files: " + _names(files))
    removed = [e for e in lib.mods.values() if e.removed]
    if removed:
        add(WARN, "Removed from mod.io (Karl keeps the copy): " + _names(removed))


def _requirements(lib: "Library", add) -> None:
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


def _game_folder(lib: "Library", paks: Path, add) -> None:
    foreign = lib.foreign_loader_files()
    if foreign:
        add(WARN, "Other mod loaders found: " + ", ".join(str(f.name) for f in foreign))
    st = integration.status(paks)
    add(OK if st == "ok" else WARN, f"Blueprint mod support (UE4SS + Karl loader): {st}")
    ue = integration.ue4ss_dir(integration.win64_dir(paks))
    if ue is not None:
        _loader_log(ue, add)
    if not proton.needed():
        return
    if ue is not None:
        hooks = integration.proton_hooks_on(ue / "UE4SS-settings.ini")
        add(WARN if hooks else OK,
            "Proton: UE4SS hooks " + ("ON (DRG can crash at startup)" if hooks else "off"))
    pfx = proton.prefix_for(paks)
    if pfx is None:
        add(WARN, "Proton: prefix not found (start DRG once with Steam)")
    elif proton.has_override(pfx):
        add(OK, "Proton: dwmapi override set")
    else:
        add(WARN, "Proton: dwmapi override missing")


def _loader_log(ue: Path, add) -> None:
    try:
        lines = (ue / "UE4SS.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    starts = [i for i, ln in enumerate(lines) if "[Karl] KarlLoader" in ln]
    if not starts:
        return
    run = lines[starts[-1]:]
    started = sum(1 for ln in run if "[Karl] avviata " in ln)
    failed = sorted({ln.split("non riesco ad avviare", 1)[1].strip()
                     for ln in run if "non riesco ad avviare" in ln})
    msg = f"Karl loader (last game start): {started} blueprint(s) started"
    if failed:
        msg += ", not started: " + ", ".join(failed[:10])
    add(WARN if failed else OK, msg)


def _modio(lib: "Library", network: bool, add) -> None:
    token = lib.config.token
    if not token:
        add(WARN, "mod.io: not signed in")
        return
    add(INFO, f"mod.io token: {mask_token(token)}")
    if network:
        try:
            me = lib.client.me()
            add(OK, f"mod.io: signed in as {me.get('username') or me.get('name_id') or '?'}")
        except Exception as err:
            add(FAIL, f"mod.io: {err}")


def _disk_and_log(lib: "Library", add) -> None:
    try:
        rep = lib.cache_report()
        add(INFO, "Disk: " + ", ".join(f"{k} {human_size(v)}" for k, v in rep.items()))
    except Exception as err:
        add(WARN, f"Disk: {err}")
    add(INFO, f"Log file: {karl_log.LOG_FILE}")
    try:
        lines = karl_log.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    since = karl_log.session_start
    found = [ln for ln in lines
             if ("WARNING" in ln or "ERROR" in ln) and ln[:19] >= since][-10:]
    if not found:
        add(OK, "Log: no warnings or errors since Karl started")
    for ln in found:
        add(INFO, "log: " + ln)


def format_report(items: list[tuple[str, str]]) -> str:
    marks = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", INFO: "[INFO]"}
    return "\n".join(f"{marks.get(level, level)} {text}" for level, text in items)
