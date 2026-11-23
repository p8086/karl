from __future__ import annotations

from pathlib import Path
from typing import Optional


def win64_dir(paks_dir: Path) -> Path:
    return paks_dir.parent.parent / "Binaries" / "Win64"


def ue4ss_dir(win64: Path) -> Optional[Path]:
    for d in (win64 / "ue4ss", win64):
        if (d / "UE4SS.dll").is_file():
            return d
    return None


def is_foreign_ue4ss(win64: Path) -> bool:
    return (win64 / "ue4ss" / "UE4SSL.dll").is_file()
