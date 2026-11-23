from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .entry import GAME_PAK_PREFIX

PAK_NAME = f"{GAME_PAK_PREFIX}integration_9999_P.pak"
STAMP_NAME = f"{GAME_PAK_PREFIX}integration.json"
GAME_PAKS = ("FSD-WindowsNoEditor.pak", "FSD-WinGDK.pak")
TOOL = "karl-merge.exe" if sys.platform == "win32" else "karl-merge"
MINT_VERSION = "0.2.10"
APPROVALS = ("Verified", "Approved", "Sandbox")
_HERE = Path(__file__).resolve().parent
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class MergeError(Exception):
    pass


class ToolMissing(MergeError):
    pass


def tool_path() -> Optional[Path]:
    env = os.environ.get("KARL_MERGE_TOOL")
    if env:
        return Path(env) if Path(env).is_file() else None
    for p in (_HERE / "bin" / TOOL, _HERE.parent / "tools" / "merge" / "target" / "release" / TOOL):
        if p.is_file():
            return p
    return None


def game_pak(paks_dir: Path) -> Optional[Path]:
    for name in GAME_PAKS:
        if (paks_dir / name).is_file():
            return paks_dir / name
    return None


def _stat(p: Path) -> list:
    st = p.stat()
    return [p.name, st.st_size, int(st.st_mtime)]


def fingerprint(paks_dir: Path, mods: list[tuple[str, list[Path]]], version: str) -> dict:
    game = game_pak(paks_dir)
    return {"karl": version,
            "game": _stat(game) if game else None,
            "mods": [[name, [_stat(p) for p in paks]] for name, paks in mods]}


def read_stamp(paks_dir: Path) -> dict:
    try:
        return json.loads((paks_dir / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def outdated(paks_dir: Path, mods: list[tuple[str, list[Path]]], version: str) -> bool:
    out = paks_dir / PAK_NAME
    if not mods:
        return out.exists() or (paks_dir / STAMP_NAME).exists()
    if not out.is_file():
        return True
    try:
        want = fingerprint(paks_dir, mods, version)
    except OSError:
        return True
    return read_stamp(paks_dir).get("fingerprint") != want


def remove(paks_dir: Path) -> None:
    for name in (PAK_NAME, STAMP_NAME, PAK_NAME + ".part"):
        (paks_dir / name).unlink(missing_ok=True)


def _ascii(text: str) -> str:
    return text.encode("ascii", "ignore").decode().replace('"', "'").strip() or "mod"


def meta(mods: list[dict]) -> dict:
    return {"version": MINT_VERSION,
            "mods": [{"name": _ascii(m["name"]), "version": _ascii(m.get("version") or "-"),
                      "url": _ascii(m.get("url") or m["name"]), "author": _ascii(m.get("author") or "-"),
                      "approval": m.get("approval") if m.get("approval") in APPROVALS else "Sandbox",
                      "required": False} for m in mods],
            "config": {}}


def build(paks_dir: Path, mods: list[tuple[str, list[Path]]], version: str,
          info: Optional[list[dict]] = None) -> list[tuple[str, str]]:
    if not mods:
        remove(paks_dir)
        return []
    game = game_pak(paks_dir)
    if game is None:
        raise MergeError(f"game pak not found in {paks_dir}")
    tool = tool_path()
    if tool is None:
        raise ToolMissing(TOOL)
    want = fingerprint(paks_dir, mods, version)
    part = paks_dir / (PAK_NAME + ".part")
    job = {"game_pak": str(game), "out": str(part),
           "mods": [{"name": name, "paks": [str(p) for p in paks]} for name, paks in mods],
           "meta": meta(info if info is not None else [{"name": name} for name, _ in mods])}
    fd, job_path = tempfile.mkstemp(prefix="karl-merge-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f)
        try:
            res = subprocess.run([str(tool), job_path], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        except OSError as err:
            raise MergeError(str(err)) from err
    finally:
        os.unlink(job_path)
    skipped: list[tuple[str, str]] = []
    error = ""
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "SKIP" and len(parts) >= 3:
            skipped.append((parts[1], parts[2]))
        elif parts[0] == "ERROR":
            error = "\t".join(parts[1:])
    if res.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        raise MergeError(error or res.stderr.strip() or f"exit code {res.returncode}")
    os.replace(part, paks_dir / PAK_NAME)
    (paks_dir / STAMP_NAME).write_text(
        json.dumps({"fingerprint": want, "skipped": skipped}, ensure_ascii=False), encoding="utf-8")
    return skipped
