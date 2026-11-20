from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .i18n import tr
from .log import log

if TYPE_CHECKING:
    from .gui import App

ICON = Path(__file__).resolve().parent / "assets" / "icon_64.png"


def available() -> bool:
    try:
        import pystray
        from PIL import Image
    except Exception:
        return False
    return True


class Tray:
    def __init__(self, app: "App"):
        self.app = app
        self.icon = None
        self.thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.icon is not None

    def _call(self, fn, *args):
        return lambda icon=None, item=None: self.app.post(fn, *args)

    def menu_spec(self) -> list[tuple]:
        app = self.app
        lib = app.lib
        folders = [(name, self._call(app.mine.choose_folder_from_tray, name),
                    name == lib.active_folder) for name in lib.folders]
        return [
            ("show", tr("tray.show"), self._call(app.bring_to_front), True),
            ("play", tr("play.button"), self._call(app.mine.play), False),
            ("vanilla", tr("vanilla.button"), self._call(app.mine.play_vanilla), False),
            ("folders", tr("folder.label"), folders, False),
            ("quit", tr("tray.quit"), self._call(app.quit_app), False),
        ]

    def _menu(self, pystray):
        def build():
            items = []
            for key, text, action, default in self.menu_spec():
                if key == "folders":
                    sub = pystray.Menu(*[
                        pystray.MenuItem(name, cb, checked=(lambda _i, on=on: on), radio=True)
                        for name, cb, on in action])
                    items.append(pystray.MenuItem(text, sub))
                    continue
                if key == "quit":
                    items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem(text, action, default=default))
            return items
        return pystray.Menu(lambda: build())

    def start(self) -> bool:
        if self.icon is not None:
            return True
        try:
            import pystray
            from PIL import Image
            image = Image.open(ICON)
            image.load()
            self.icon = pystray.Icon("karl", image, "Karl", self._menu(pystray))
        except Exception as err:
            log.warning("icona nell'area di notifica non disponibile: %s", err)
            self.icon = None
            return False

        def loop(icon=self.icon):
            try:
                icon.run()
            except Exception as err:
                log.warning("icona nell'area di notifica fermata: %s", err)
                self.app.post(self._crashed, icon)

        self.thread = threading.Thread(target=loop, name="karl-tray", daemon=True)
        self.thread.start()
        return True

    def _crashed(self, icon):
        if self.icon is icon:
            self.icon = None

    def refresh(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
