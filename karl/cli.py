from __future__ import annotations

import argparse
import sys

from . import i18n
from .library import Config, Library


def _progress(done: int, total: int) -> None:
    if total:
        print(f"\r  {done * 100 // total:3d}%", end="", flush=True)
        if done >= total:
            print()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="karl", description="Mod manager per Deep Rock Galactic")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="aggiungi mod da link mod.io")
    a.add_argument("urls", nargs="+")
    sub.add_parser("list", help="elenca le mod")
    r = sub.add_parser("remove", help="rimuovi mod (id)")
    r.add_argument("ids", nargs="+", type=int)
    for name in ("enable", "disable"):
        s = sub.add_parser(name)
        s.add_argument("ids", nargs="+", type=int)
    sub.add_parser("update", help="aggiorna le mod")
    c = sub.add_parser("config", help="mostra o imposta la configurazione")
    c.add_argument("--token")
    c.add_argument("--paks")
    args = p.parse_args(argv)

    cfg = Config.load()
    i18n.set_language(cfg.language)
    if args.cmd == "config":
        if args.token:
            cfg.token = args.token
        if args.paks:
            cfg.paks_dir = args.paks
        cfg.save()
        print(f"Cartella Paks:  {cfg.paks_dir or '(non trovata)'}")
        print(f"Token:          {'impostato' if cfg.token else '(manca)'}")
        return 0

    lib = Library(cfg)
    try:
        if args.cmd == "add":
            for url in args.urls:
                lib.add(url, progress=_progress, log=print)
        elif args.cmd == "list":
            if not lib.mods:
                print("Nessuna mod.")
            for m in sorted(lib.mods.values(), key=lambda m: m.name.lower()):
                stato = "on " if m.enabled else "off"
                print(f"[{stato}] {m.mod_id:>8}  {m.name}  (v{m.version or '-'})")
        elif args.cmd == "remove":
            for i in args.ids:
                lib.remove(i, log=print)
        elif args.cmd in ("enable", "disable"):
            for i in args.ids:
                lib.set_enabled(i, args.cmd == "enable", log=print)
        elif args.cmd == "update":
            lib.check_updates(progress=_progress, log=print)
    except KeyError as e:
        print(f"Mod non trovata: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        return 1
    return 0
