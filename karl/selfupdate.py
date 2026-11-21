from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import GITHUB_REPO, VERSION
from .game import child_env

RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Karl-DRG-mod-manager",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        e.close()
        raise


def _get_json(url: str, fetch: Optional[Callable[[str], bytes]]) -> dict:
    try:
        data = json.loads((fetch or _http_get)(url))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_version(text: str) -> tuple[int, ...]:
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str = VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a) and a > b


WINDOWS_ASSET = "Karl.exe"
LINUX_ASSET = "karl-linux-x86_64.tar.gz"


def latest_release(fetch: Optional[Callable[[str], bytes]] = None) -> Optional[dict]:
    data = _get_json(API, fetch)
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        return None
    assets = {str(a.get("name")): str(a.get("browser_download_url"))
              for a in data.get("assets") or [] if a.get("name") and a.get("browser_download_url")}
    return {"version": tag.lstrip("vV"), "assets": assets,
            "page": str(data.get("html_url") or RELEASES_PAGE)}


def release_notes(version: str, fetch: Optional[Callable[[str], bytes]] = None) -> Optional[str]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/v{version.lstrip('vV')}"
    data = _get_json(url, fetch)
    body = str(data.get("body") or "").strip()
    return body or None


def asset_name(platform: str = sys.platform) -> str:
    if platform == "win32":
        return WINDOWS_ASSET
    if platform.startswith("linux"):
        return LINUX_ASSET
    return ""


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False)) and bool(asset_name())


def download(url: str, dest: Path, progress: Optional[Callable[[int, int], None]] = None,
             timeout: int = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Karl-DRG-mod-manager",
                                               "Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as out:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise OSError("empty download")
    os.replace(tmp, dest)
    return dest


def extract_linux_binary(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        member = next((m for m in tar.getmembers()
                       if m.isfile() and m.name.rsplit("/", 1)[-1] == "karl"), None)
        if member is None:
            raise OSError("karl binary not found in the archive")
        src = tar.extractfile(member)
        if src is None:
            raise OSError("karl binary not found in the archive")
        with src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    os.chmod(dest, 0o755)
    return dest


def prepare(release: dict, exe: Path, workdir: Path,
            progress: Optional[Callable[[int, int], None]] = None,
            platform: str = sys.platform) -> dict:
    name = asset_name(platform)
    url = release["assets"].get(name)
    if not url:
        raise OSError(f"{name} not found in the release")
    if platform == "win32":
        new = exe.with_name(exe.stem + ".new" + exe.suffix)
        download(url, new, progress)
        script = workdir / "karl-update.cmd"
        script.write_text(
            "@echo off\r\n"
            "set n=0\r\n"
            ":wait\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            f"move /y \"{new}\" \"{exe}\" >nul 2>&1 && goto done\r\n"
            "set /a n+=1\r\n"
            "if %n% lss 60 goto wait\r\n"
            "goto end\r\n"
            ":done\r\n"
            f"start \"\" \"{exe}\"\r\n"
            ":end\r\n"
            "del \"%~f0\"\r\n", encoding="utf-8")
        return {"kind": "windows", "script": str(script), "exe": str(exe)}
    archive = download(url, workdir / name, progress)
    new = exe.with_name(exe.name + ".new")
    try:
        extract_linux_binary(archive, new)
    finally:
        archive.unlink(missing_ok=True)
    os.replace(new, exe)
    return {"kind": "linux", "exe": str(exe)}


def restart(plan: dict) -> None:
    if plan["kind"] == "windows":
        flags = 0x00000008 | 0x08000000
        subprocess.Popen(["cmd", "/c", plan["script"]], creationflags=flags, close_fds=True,
                         env=child_env())
    else:
        subprocess.Popen([plan["exe"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, env=child_env())
