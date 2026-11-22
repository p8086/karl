from __future__ import annotations

import io
import json
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import proton

UE4SS_REPO = "UE4SS-RE/RE-UE4SS"
UE4SS_TAG = "v3.0.1"
LOADER_NAME = "KarlLoader"
LOADER_SRC = Path(__file__).resolve().parent / "loader" / "main.lua"


class IntegrationError(Exception):
    pass


def win64_dir(paks_dir: Path) -> Path:
    return paks_dir.parent.parent / "Binaries" / "Win64"


def ue4ss_dir(win64: Path) -> Optional[Path]:
    for d in (win64 / "ue4ss", win64):
        if (d / "UE4SS.dll").is_file():
            return d
    return None


def is_foreign_ue4ss(win64: Path) -> bool:
    return (win64 / "ue4ss" / "UE4SSL.dll").is_file()


def status(paks_dir: Path) -> str:
    win64 = win64_dir(paks_dir)
    d = ue4ss_dir(win64)
    if d is None or not (win64 / "dwmapi.dll").is_file():
        return "no_ue4ss"
    if not (d / "Mods" / LOADER_NAME / "Scripts" / "main.lua").is_file():
        return "no_loader"
    if proton.needed():
        pfx = proton.prefix_for(paks_dir)
        if pfx is None:
            return "no_prefix"
        if not proton.has_override(pfx):
            return "no_override"
    return "ok"


FILES_OK = ("ok", "no_prefix", "no_override")


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Karl-DRG-mod-manager",
                                               "Accept": "application/octet-stream, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ue4ss_zip_url(fetch: Callable[[str], bytes] = _http_get) -> str:
    api = f"https://api.github.com/repos/{UE4SS_REPO}/releases/tags/{UE4SS_TAG}"
    data = json.loads(fetch(api))
    for a in data.get("assets", []):
        name = a.get("name", "")
        if re.match(r"^UE4SS_v[\d.]+.*\.zip$", name):
            return a["browser_download_url"]
    raise IntegrationError(f"nella release {UE4SS_TAG} di UE4SS non trovo lo zip")


def extract_safely(data: bytes, dest: Path) -> list[str]:
    dest = dest.resolve()
    names = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if dest not in target.parents and target != dest:
                raise IntegrationError(f"percorso non valido nello zip: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            names.append(info.filename)
    return names


def quiet_settings(ini: Path) -> None:
    if not ini.is_file():
        return
    text = ini.read_text(encoding="utf-8", errors="replace")
    for key in ("ConsoleEnabled", "GuiConsoleEnabled", "GuiConsoleVisible"):
        text = re.sub(rf"(?m)^(\s*{key}\s*=\s*)\d+", r"\g<1>0", text)
    ini.write_text(text, encoding="utf-8")


def proton_settings(ini: Path) -> bool:
    if not ini.is_file():
        return False
    text = ini.read_text(encoding="utf-8", errors="replace")
    new = re.sub(r"(?m)^(\s*Hook\w+\s*=\s*)(?:1|true)\b", r"\g<1>0", text)
    new = re.sub(r"(?m)^(\s*EnableDumping\s*=\s*)(?:1|true)\b", r"\g<1>0", new)
    if new == text:
        return False
    ini.write_text(new, encoding="utf-8")
    return True


def proton_hooks_on(ini: Path) -> bool:
    if not ini.is_file():
        return False
    text = ini.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(r"(?m)^\s*Hook\w+\s*=\s*(?:1|true)\b", text))


def install_ue4ss(paks_dir: Path, fetch: Callable[[str], bytes] = _http_get,
                  log: Callable[[str], None] = lambda s: None) -> Path:
    win64 = win64_dir(paks_dir)
    if not (win64 / "FSD-Win64-Shipping.exe").is_file() and not win64.is_dir():
        raise IntegrationError(f"non trovo la cartella del gioco {win64}")
    log("Scarico UE4SS...")
    data = fetch(ue4ss_zip_url(fetch))
    extract_safely(data, win64)
    d = ue4ss_dir(win64)
    if d is None:
        raise IntegrationError("UE4SS scaricato, ma non trovo UE4SS.dll")
    quiet_settings(d / "UE4SS-settings.ini")
    if proton.needed():
        proton_settings(d / "UE4SS-settings.ini")
    return d


def install_loader(ue4ss: Path) -> Path:
    mod = ue4ss / "Mods" / LOADER_NAME
    (mod / "Scripts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LOADER_SRC, mod / "Scripts" / "main.lua")
    (mod / "enabled.txt").write_text("", encoding="utf-8")
    mods_txt = ue4ss / "Mods" / "mods.txt"
    lines = mods_txt.read_text(encoding="utf-8", errors="replace").splitlines() \
        if mods_txt.is_file() else []
    lines = [l for l in lines if not re.match(rf"^\s*{LOADER_NAME}\s*:", l)]
    lines.append(f"{LOADER_NAME} : 1")
    mods_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mod


def update_loader(ue4ss: Path) -> bool:
    dest = ue4ss / "Mods" / LOADER_NAME / "Scripts" / "main.lua"
    if not dest.is_file() or dest.read_bytes() == LOADER_SRC.read_bytes():
        return False
    shutil.copyfile(LOADER_SRC, dest)
    return True


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_mod_list(ue4ss: Path, mods: Iterable[tuple[str, dict]]) -> int:
    rows = []
    seen: set[str] = set()
    for name, classes in mods:
        client = "true" if classes.get("client", True) else "false"
        classes = {k: [c for c in classes.get(k, []) if c not in seen] for k in ("spacerig", "cave")}
        seen.update(classes["spacerig"] + classes["cave"])
        if not classes.get("spacerig") and not classes.get("cave"):
            continue
        sr = ", ".join(_lua_str(c) for c in classes.get("spacerig", []))
        cv = ", ".join(_lua_str(c) for c in classes.get("cave", []))
        rows.append(f"  {{ name = {_lua_str(name)}, client = {client}, spacerig = {{ {sr} }}, cave = {{ {cv} }} }},")
    path = ue4ss / "Mods" / LOADER_NAME / "Scripts" / "karl_mods.lua"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("return {\n"
                    + "\n".join(rows) + "\n}\n", encoding="utf-8")
    return len(rows)
