from __future__ import annotations

import os
import re
from pathlib import Path

from . import APP_NAME


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LIBRARY_FILE = CONFIG_DIR / "mods.json"
FOLDERS_FILE = CONFIG_DIR / "cartelle.json"
SNAPSHOTS_FILE = CONFIG_DIR / "istantanee.json"
SNAPSHOT_BLOBS = CACHE_DIR / "istantanee"
MODS_CACHE = CACHE_DIR
HISTORY_FILE = CONFIG_DIR / "cronologia.json"
MAIN_INSTALL = "main"


def install_dirs(install_id: str) -> tuple[Path, Path]:
    if not install_id or install_id == MAIN_INSTALL:
        return CONFIG_DIR, CACHE_DIR
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", install_id)
    return CONFIG_DIR / "installs" / safe, CACHE_DIR / "installs" / safe


def use_install(install_id: str) -> None:
    global LIBRARY_FILE, FOLDERS_FILE, SNAPSHOTS_FILE, SNAPSHOT_BLOBS, MODS_CACHE, HISTORY_FILE
    cfg_dir, cache_dir = install_dirs(install_id)
    HISTORY_FILE = cfg_dir / "cronologia.json"
    LIBRARY_FILE = cfg_dir / "mods.json"
    FOLDERS_FILE = cfg_dir / "cartelle.json"
    SNAPSHOTS_FILE = cfg_dir / "istantanee.json"
    SNAPSHOT_BLOBS = cache_dir / "istantanee"
    MODS_CACHE = cache_dir
