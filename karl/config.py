from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .paths import CONFIG_FILE, MAIN_INSTALL, read_json, write_json


GAME_SUBPATH = Path("steamapps/common/Deep Rock Galactic/FSD/Content/Paks")


@dataclass
class Config:
    token: str = ""
    paks_dir: str = ""
    username: str = ""
    avatar_url: str = ""
    profile_url: str = ""
    refresh_seconds: int = 60
    language: str = "auto"
    ignore_foreign_loader: bool = False
    ignore_modio_subs: bool = False
    ignore_integration: bool = False
    dev_mode: bool = False
    dev_restart: bool = False
    auto_check_updates: bool = False
    check_karl_updates: bool = True
    karl_check_startup: bool = True
    warn_removed: bool = True
    vanilla_active: bool = False
    tray_icon: bool = True
    close_to_tray: bool = False
    game_build: str = ""
    compact_links: bool = False
    ui_scale: float = 1.0
    crash_watch: bool = True
    show_whats_new: bool = True
    last_launch: float = 0.0
    last_seen_version: str = ""
    bisect_restore: dict = field(default_factory=dict)
    installs: list = field(default_factory=list)
    active_install: str = MAIN_INSTALL

    @property
    def logged_in(self) -> bool:
        return bool(self.token)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        for k, v in read_json(CONFIG_FILE, {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if not cfg.paks_dir and cfg.active_install == MAIN_INSTALL:
            found = find_paks_dir()
            cfg.paks_dir = str(found) if found else ""
        cfg.ensure_installs()
        return cfg

    def save(self) -> None:
        self.ensure_installs()
        self.store_active()
        write_json(CONFIG_FILE, asdict(self), mode=0o600)

    def ensure_installs(self) -> None:
        self.installs = [dict(i) for i in self.installs if isinstance(i, dict) and i.get("id")]
        if not any(i["id"] == MAIN_INSTALL for i in self.installs):
            main = {"id": MAIN_INSTALL, "name": "Deep Rock Galactic", "paks_dir": "",
                    "game_build": ""}
            if self.active_install in ("", MAIN_INSTALL):
                main.update(paks_dir=self.paks_dir, game_build=self.game_build)
            self.installs.insert(0, main)
        if not any(i["id"] == self.active_install for i in self.installs):
            self.active_install = MAIN_INSTALL
            main = self.install(MAIN_INSTALL)
            self.paks_dir, self.game_build = main["paks_dir"], main.get("game_build", "")

    def install(self, install_id: str) -> dict:
        for i in self.installs:
            if i["id"] == install_id:
                return i
        raise KeyError(install_id)

    def current_install(self) -> dict:
        self.ensure_installs()
        return self.install(self.active_install)

    def store_active(self) -> None:
        cur = next((i for i in self.installs if i["id"] == self.active_install), None)
        if cur is not None:
            cur["paks_dir"] = self.paks_dir
            cur["game_build"] = self.game_build

    def activate(self, install_id: str) -> None:
        self.ensure_installs()
        self.store_active()
        target = self.install(install_id)
        self.active_install = install_id
        self.paks_dir = target.get("paks_dir", "")
        self.game_build = target.get("game_build", "")
        self.save()

    def add_install(self, name: str, paks_dir: str = "") -> str:
        import uuid
        self.ensure_installs()
        install_id = uuid.uuid4().hex[:10]
        self.installs.append({"id": install_id, "name": name.strip() or install_id,
                              "paks_dir": paks_dir, "game_build": ""})
        return install_id


def steam_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.insert(0, Path(os.environ[env]) / "Steam")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.insert(0, Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
    extra: list[Path] = []
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.exists():
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                extra.append(Path(p.replace("\\\\", "\\")))
    seen, out = set(), []
    for p in roots + extra:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def find_paks_dir() -> Optional[Path]:
    for root in steam_roots():
        cand = root / GAME_SUBPATH
        if (cand / "FSD-WindowsNoEditor.pak").exists() or cand.is_dir():
            return cand
    return None
