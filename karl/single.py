from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

HOST = "127.0.0.1"
PORT = 47391
MESSAGE = b"karl:show\n"


class SingleInstance:
    def __init__(self, port: int = PORT):
        self.port = port
        self.sock: Optional[socket.socket] = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, self.port))
            s.listen(4)
        except OSError:
            s.close()
            return False
        self.sock = s
        return True

    def notify_existing(self, timeout: float = 2.0) -> bool:
        try:
            with socket.create_connection((HOST, self.port), timeout=timeout) as c:
                c.sendall(MESSAGE)
                c.settimeout(timeout)
                return c.recv(16).startswith(b"ok")
        except OSError:
            return False

    def serve(self, on_show: Callable[[], None]) -> None:
        if self.sock is None:
            return

        def loop():
            while self.sock is not None:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return
                with conn:
                    try:
                        conn.settimeout(2.0)
                        data = conn.recv(64)
                        if data.startswith(MESSAGE.strip()):
                            conn.sendall(b"ok\n")
                            on_show()
                    except OSError:
                        pass

        threading.Thread(target=loop, name="karl-single", daemon=True).start()

    def close(self) -> None:
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
