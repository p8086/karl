from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import APP_NAME

LOG_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_NAME / "karl.log"
log = logging.getLogger(APP_NAME)


def setup() -> Path:
    if log.handlers:
        return LOG_FILE
    log.setLevel(logging.INFO)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    previous = sys.excepthook

    def hook(kind, value, tb):
        log.error("errore non gestito", exc_info=(kind, value, tb))
        previous(kind, value, tb)

    sys.excepthook = hook
    return LOG_FILE
