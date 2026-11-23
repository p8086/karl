from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from .game import STEAM_APP_ID


def needed() -> bool:
    return sys.platform.startswith("linux")


def prefix_for(paks_dir: Path) -> Optional[Path]:
    try:
        steamapps = Path(paks_dir).parents[4]
    except IndexError:
        return None
    pfx = steamapps / "compatdata" / str(STEAM_APP_ID) / "pfx"
    return pfx if (pfx / "user.reg").is_file() else None
