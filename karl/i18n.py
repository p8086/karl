from __future__ import annotations

import json
import locale
import os
import sys
from datetime import datetime
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

AUTO = "auto"
LANGUAGES: list[tuple[str, str]] = [
    ("it", "Italiano"),
    ("en", "English"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es-ES", "Español (España)"),
    ("pl", "Polski"),
    ("pt-BR", "Português (Brasil)"),
    ("ru", "Русский"),
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("uk", "Українська"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("tr", "Türkçe"),
    ("es-419", "Español (Latinoamérica)"),
    ("hu", "Magyar"),
    ("ro", "Română"),
]
CODES = [c for c, _ in LANGUAGES]
BASE, FALLBACK = "it", "en"

_cache: dict[str, dict[str, str]] = {}
_current = BASE


def _load(code: str) -> dict[str, str]:
    if code not in _cache:
        path = LOCALES_DIR / f"{code}.json"
        try:
            _cache[code] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[code] = {}
    return _cache[code]


def system_language() -> str:
    tag = ""
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            tag = locale.windows_locale.get(lcid, "")
        except Exception:
            tag = ""
    if not tag:
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            if os.environ.get(var):
                tag = os.environ[var].split(":")[0]
                break
    if not tag:
        try:
            tag = locale.getlocale()[0] or ""
        except ValueError:
            tag = ""
    return match_language(tag)


def match_language(tag: str) -> str:
    tag = tag.split(".")[0].replace("_", "-")
    if not tag:
        return FALLBACK
    low = tag.lower()
    lang, _, region = low.partition("-")
    if lang == "zh":
        return "zh-TW" if region in ("tw", "hk", "mo", "hant") else "zh-CN"
    if lang == "es":
        return "es-ES" if region in ("", "es") else "es-419"
    if lang == "pt":
        return "pt-BR"
    return lang if lang in CODES else FALLBACK


def set_language(code: str) -> str:
    global _current
    _current = system_language() if code in ("", AUTO) or code not in CODES else code
    return _current


def current() -> str:
    return _current


def tr(key: str, **kw) -> str:
    text = _load(_current).get(key) or _load(FALLBACK).get(key) or _load(BASE).get(key) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return _load(BASE).get(key, key).format(**kw)
    return text


def months() -> list[str]:
    parts = [m.strip() for m in tr("date.months").split(",")]
    return parts if len(parts) == 12 else [m.strip() for m in _load(BASE)["date.months"].split(",")]


def language_name(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def human_count(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n // 1000}K"
    return str(n)


def human_size(b: int) -> str:
    b = int(b or 0)
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def human_date(ts: int) -> str:
    if not ts:
        return "-"
    d = datetime.fromtimestamp(int(ts))
    return tr("date.format", day=d.day, month=months()[d.month - 1], year=d.year)
