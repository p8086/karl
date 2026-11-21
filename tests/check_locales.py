import json
import string
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "karl" / "locales"
KEEP_IN_ENGLISH = ["User actions", "Read", "Write", "Permissions", "Scope", "Expiry",
                   "personal access token", "Karl", "Access"]


def placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def check(code: str, base: dict) -> list[str]:
    path = LOCALES / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"{code}: file mancante"]
    except ValueError as e:
        return [f"{code}: JSON non valido: {e}"]
    errors = []
    missing = sorted(set(base) - set(data))
    extra = sorted(set(data) - set(base))
    if missing:
        errors.append(f"{code}: chiavi mancanti: {missing}")
    if extra:
        errors.append(f"{code}: chiavi in piu': {extra}")
    for key, text in data.items():
        if key not in base:
            continue
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{code}: '{key}' vuoto")
            continue
        try:
            got = placeholders(text)
        except ValueError as e:
            errors.append(f"{code}: '{key}' graffe non valide: {e}")
            continue
        if got != placeholders(base[key]):
            errors.append(f"{code}: '{key}' segnaposto {sorted(got)} invece di "
                          f"{sorted(placeholders(base[key]))}")
    months = [m.strip() for m in data.get("date.months", "").split(",")]
    if len(months) != 12 or not all(months):
        errors.append(f"{code}: date.months deve avere 12 voci separate da virgole")
    steps = data.get("login.steps", "")
    for word in KEEP_IN_ENGLISH:
        if word not in steps:
            errors.append(f"{code}: login.steps deve contenere '{word}' in inglese")
    if "https://mod.io/g/drg/m/" not in data.get("err.invalid_link", ""):
        errors.append(f"{code}: err.invalid_link deve contenere il link di esempio")
    return errors


def main(codes: list[str]) -> int:
    sys.path.insert(0, str(LOCALES.parents[1]))
    from karl.i18n import CODES
    base = json.loads((LOCALES / "it.json").read_text(encoding="utf-8"))
    errors = []
    for code in codes or CODES:
        errors += check(code, base)
    for e in errors:
        print(e)
    print("OK" if not errors else f"{len(errors)} problemi")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
