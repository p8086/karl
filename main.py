#!/usr/bin/env python3
import os
import sys

CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
)


def fix_certificates() -> None:
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux") \
            and not os.environ.get("SSL_CERT_FILE"):
        for path in CA_BUNDLES:
            if os.path.isfile(path):
                os.environ["SSL_CERT_FILE"] = path
                return


def check_imagetk() -> int:
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
        root = tk.Tk()
        photo = ImageTk.PhotoImage(Image.new("RGBA", (8, 8), (224, 169, 58, 255)))
        ok = photo.width() == 8
        root.destroy()
    except Exception as e:
        print(f"ImageTk non disponibile: {e}")
        return 1
    print("ImageTk ok" if ok else "ImageTk non funziona")
    return 0 if ok else 1


def run() -> int:
    fix_certificates()
    if sys.argv[1:] == ["--check-imagetk"]:
        return check_imagetk()
    if len(sys.argv) > 1:
        from karl.cli import main
        return main(sys.argv[1:])
    try:
        from karl.gui import main
    except ModuleNotFoundError as e:
        if e.name in ("tkinter", "_tkinter"):
            print("Manca Tkinter. Su Ubuntu: sudo apt install python3-tk")
            return 1
        raise
    main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
