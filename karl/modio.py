from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import i18n
from .i18n import tr

API_BASE = "https://api.mod.io/v1"
GAME_API = "https://g-{gid}.modapi.io/v1"
KNOWN_GAMES = {"drg": 2475}
DEFAULT_GAME = "drg"
_GAME_PATH_RE = re.compile(r"^/games/(\d+)")
USER_AGENT = "karl/0.1"

_URL_RE = re.compile(
    r"^https?://(?:www\.)?mod\.io/g/(?P<game>[A-Za-z0-9_-]+)/m/(?P<mod>[A-Za-z0-9_-]+)"
)


_URL_ANYWHERE_RE = re.compile(
    r"https?://(?:www\.)?mod\.io/g/([A-Za-z0-9_-]+)/m/([A-Za-z0-9_-]+)", re.IGNORECASE)


def extract_mod_urls(text: str) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    seen: set[tuple[str, str]] = set()
    ignored: list[str] = []
    for line in text.splitlines():
        found = _URL_ANYWHERE_RE.findall(line)
        if not found and line.strip():
            ignored.append(line.strip())
        for game, mod in found:
            key = (game.lower(), mod.lower())
            if key not in seen:
                seen.add(key)
                urls.append(f"https://mod.io/g/{key[0]}/m/{key[1]}")
    return urls, ignored


class ModioError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, api_message: str = ""):
        super().__init__(message)
        self.status = status
        self.api_message = api_message


def parse_mod_url(url: str) -> tuple[str, str]:
    url = url.strip()
    m = _URL_RE.match(url)
    if not m:
        raise ValueError(tr("err.invalid_link"))
    return m.group("game").lower(), m.group("mod").lower()


class ModioClient:
    def __init__(self, token: str, timeout: int = 30):
        if not token:
            raise ModioError(tr("err.no_token"), status=401)
        self.token = token.strip()
        self.timeout = timeout
        self._game_ids: dict[str, int] = {}

    def _open(self, method: str, url: str, data: Optional[dict] = None):
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Accept-Language": i18n.current(),
            "User-Agent": USER_AGENT,
        }
        if data is not None or method != "GET":
            body = urllib.parse.urlencode(data or {}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from None
        except urllib.error.URLError as e:
            raise ModioError(tr("err.network", reason=e.reason)) from None

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> ModioError:
        msg, ref = "", None
        try:
            err = json.loads(e.read().decode()).get("error", {})
            msg, ref = err.get("message", ""), err.get("error_ref")
        except Exception:
            pass
        finally:
            e.close()
        text = {401: tr("err.unauthorized"), 403: msg or tr("err.forbidden"),
                404: msg or tr("err.not_found"), 429: tr("err.rate_limit")}.get(
            e.code, tr("err.http", msg=msg or e.reason))
        code = f"{e.code} · {ref}" if ref else str(e.code)
        if msg and e.code in (401, 429):
            text += f" mod.io: {msg}"
        err = ModioError(f"{text} ({code})", status=e.code, api_message=msg)
        err.ref = ref
        return err

    def _base(self, path: str) -> str:
        m = _GAME_PATH_RE.match(path)
        if m:
            return GAME_API.format(gid=m.group(1))
        if path.startswith("/me"):
            return GAME_API.format(gid=KNOWN_GAMES[DEFAULT_GAME])
        return API_BASE

    def _json(self, method: str, path: str, params: Optional[dict] = None,
              data: Optional[dict] = None) -> dict:
        url = self._base(path) + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with self._open(method, url, data) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def game_id(self, game_slug: str) -> int:
        if game_slug in KNOWN_GAMES:
            return KNOWN_GAMES[game_slug]
        if game_slug not in self._game_ids:
            res = self._json("GET", "/games", {"name_id": game_slug})
            items = res.get("data", [])
            if not items:
                raise ModioError(tr("err.game_not_found", slug=game_slug), status=404)
            self._game_ids[game_slug] = int(items[0]["id"])
        return self._game_ids[game_slug]

    def get_mod(self, game_slug: str, mod_slug: str) -> dict:
        gid = self.game_id(game_slug)
        res = self._json("GET", f"/games/{gid}/mods", {"name_id": mod_slug})
        items = res.get("data", [])
        if not items:
            raise ModioError(tr("err.mod_not_found", slug=mod_slug), status=404)
        return items[0]

    def get_mod_by_id(self, game_id: int, mod_id: int) -> dict:
        return self._json("GET", f"/games/{game_id}/mods/{mod_id}")

    def get_modfile(self, game_id: int, mod_id: int, file_id: int) -> dict:
        return self._json("GET", f"/games/{game_id}/mods/{mod_id}/files/{file_id}")

    def list_modfiles(self, game_id: int, mod_id: int, limit: int = 10) -> list[dict]:
        res = self._json("GET", f"/games/{game_id}/mods/{mod_id}/files",
                         {"_sort": "-date_added", "_limit": limit})
        return res.get("data", [])

    def dependencies(self, game_id: int, mod_id: int) -> list[dict]:
        res = self._json("GET", f"/games/{game_id}/mods/{mod_id}/dependencies")
        return res.get("data", [])

    def mods_by_ids(self, game_id: int, ids: list[int]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            page, _ = self.list_mods(game_id, {"id-in": ",".join(str(x) for x in chunk),
                                               "_limit": 100})
            out.extend(page)
        return out

    def rate(self, game_id: int, mod_id: int, rating: int) -> None:
        self._json("POST", f"/games/{game_id}/mods/{mod_id}/ratings", data={"rating": rating})

    def my_rating(self, game_id: int, mod_id: int) -> int:
        res = self._json("GET", "/me/ratings", {"game_id": game_id, "mod_id": mod_id})
        for r in res.get("data", []):
            if int(r.get("mod_id", 0)) == int(mod_id):
                return int(r.get("rating") or 0)
        return 0

    def me(self) -> dict:
        return self._json("GET", "/me")

    def check_access(self) -> dict:
        me = self.me()
        try:
            self.list_mods(self.game_id(DEFAULT_GAME), {"_limit": 1})
        except ModioError as e:
            e.read_failed = True
            raise
        return me

    def game_tags(self, game_id: int) -> list[dict]:
        res = self._json("GET", f"/games/{game_id}/tags", {"show_hidden_tags": "true"})
        return res.get("data", [])

    def list_mods(self, game_id: int, params: dict) -> tuple[list[dict], int]:
        res = self._json("GET", f"/games/{game_id}/mods", params)
        return res.get("data", []), int(res.get("result_total", 0))

    def subscribed_mods(self, game_id: int) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            res = self._json("GET", "/me/subscribed",
                             {"game_id": game_id, "_limit": 100, "_offset": offset})
            page = res.get("data", [])
            out.extend(page)
            offset += len(page)
            if not page or offset >= int(res.get("result_total", 0)):
                return out

    def unsubscribe(self, game_id: int, mod_id: int) -> None:
        self._json("DELETE", f"/games/{game_id}/mods/{mod_id}/subscribe")

    def download(self, url: str, dest: Path,
                 progress: Optional[Callable[[int, int], None]] = None,
                 md5: str = "") -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        digest = hashlib.md5()
        with self._open("GET", url) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if md5 and digest.hexdigest().lower() != md5.strip().lower():
            tmp.unlink(missing_ok=True)
            raise ModioError(tr("err.md5", name=dest.name))
        tmp.replace(dest)
        return dest


def token_expiry(token: str) -> Optional[int]:
    import base64
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return int(exp) if exp else None
    except (ValueError, TypeError, AttributeError):
        return None


def mask_token(token: str) -> str:
    token = (token or "").strip()
    if len(token) <= 12:
        return "•" * len(token)
    return f"{token[:6]}{'•' * 12}{token[-4:]}"


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def modfile_info(mod: dict) -> dict:
    mf = mod.get("modfile") or {}
    dl = mf.get("download") or {}
    if not dl.get("binary_url"):
        raise ModioError(tr("err.no_file"))
    return {
        "file_id": mf.get("id"),
        "version": mf.get("version") or "",
        "filename": mf.get("filename") or f"{mod.get('name_id', 'mod')}.zip",
        "url": dl["binary_url"],
        "size": mf.get("filesize") or 0,
        "md5": (mf.get("filehash") or {}).get("md5") or "",
        "date": mf.get("date_added") or 0,
    }


VERSION_TAG_RE = re.compile(r"^\d+(?:\.\d+)+$")


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def game_versions_of(mod: dict) -> list[str]:
    return sorted({t.get("name", "") for t in mod.get("tags") or []
                   if VERSION_TAG_RE.match(t.get("name", ""))}, key=version_key)


def mod_name(mod: dict) -> str:
    import html
    return html.unescape(str(mod.get("name") or "")).strip()


def dependency_info(dep: dict) -> dict:
    mid = int(dep.get("mod_id") or dep.get("id"))
    slug = dep.get("name_id") or ""
    url = dep.get("profile_url") or (f"https://mod.io/g/drg/m/{slug}" if slug else "")
    import html
    name = html.unescape(str(dep.get("name") or "")).strip()
    return {"id": mid, "name": name or slug or str(mid), "url": url}
