from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable, Optional

from . import GITHUB_REPO, VERSION

RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Karl-DRG-mod-manager",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_version(text: str) -> tuple[int, ...]:
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str = VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a) and a > b


def latest_version(fetch: Callable[[str], bytes] = _http_get) -> Optional[str]:
    try:
        data = json.loads(fetch(API))
    except Exception:
        return None
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    return tag.lstrip("vV") if parse_version(tag) else None
