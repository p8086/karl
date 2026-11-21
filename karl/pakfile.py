from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

PAK_MAGIC = 0x5A6F12E1
FOOTER_SIZES = (221, 189, 61)


class PakError(Exception):
    pass


def _fstring(b: bytes, o: int) -> tuple[str, int]:
    n = struct.unpack_from("<i", b, o)[0]
    o += 4
    if n == 0:
        return "", o
    if n < 0:
        n = -n
        return b[o:o + 2 * n].decode("utf-16-le", "replace").rstrip("\0"), o + 2 * n
    return b[o:o + n].decode("utf-8", "replace").rstrip("\0"), o + n


def _footer(f) -> tuple[int, bool, int, int]:
    ver, encrypted, off, isz, _pos = _footer_at(f)
    return ver, encrypted, off, isz


def _footer_at(f) -> tuple[int, bool, int, int, int]:
    f.seek(0, 2)
    size = f.tell()
    for fs in FOOTER_SIZES:
        if size < fs:
            continue
        f.seek(size - fs)
        ft = f.read(fs)
        for start in (17, 0):
            if start + 44 > len(ft):
                continue
            magic, ver, off, isz = struct.unpack_from("<IIqq", ft, start)
            if magic == PAK_MAGIC:
                encrypted = start == 17 and ft[16] != 0
                return ver, encrypted, off, isz, size - fs + start
    raise PakError("non e' un file .pak di Unreal Engine")


def list_files(path: Path) -> list[str]:
    with open(path, "rb") as f:
        ver, encrypted, off, isz = _footer(f)
        if encrypted:
            raise PakError("indice criptato")
        f.seek(off)
        idx = f.read(isz)
        mount, o = _fstring(idx, 0)
        mount = mount.replace("\\", "/")
        while mount.startswith("../"):
            mount = mount[3:]
        if ver >= 10:
            count, _seed = struct.unpack_from("<iQ", idx, o)
            o += 12
            if struct.unpack_from("<I", idx, o)[0]:
                o += 4 + 8 + 8 + 20
            else:
                o += 4
            if not struct.unpack_from("<I", idx, o)[0]:
                raise PakError("manca l'elenco dei file")
            fdi_off, fdi_size = struct.unpack_from("<qq", idx, o + 4)
            f.seek(fdi_off)
            fdi = f.read(fdi_size)
            o = 0
            files = []
            (ndirs,) = struct.unpack_from("<i", fdi, o)
            o += 4
            for _ in range(ndirs):
                d, o = _fstring(fdi, o)
                (nf,) = struct.unpack_from("<i", fdi, o)
                o += 4
                for _ in range(nf):
                    name, o = _fstring(fdi, o)
                    o += 4
                    files.append((mount + d.lstrip("/") + name).replace("//", "/"))
            return files
        found = re.findall(rb"[\x20-\x7e]{1,255}\.(?:uasset|uexp|umap|ubulk|ufont)\x00", idx[o:])
        return [(mount + n[:-1].decode()).replace("//", "/") for n in found]


def asset_path(file_path: str) -> str:
    p = re.sub(r"\.uasset$", "", file_path)
    p = re.sub(r"^(?:FSD/)?Content/", "/Game/", p)
    return p if p.startswith("/") else "/" + p


def init_classes(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"spacerig": [], "cave": []}
    for fp in list_files(path):
        name = fp.rsplit("/", 1)[-1]
        key = {"initspacerig.uasset": "spacerig", "initcave.uasset": "cave"}.get(name.lower())
        if key:
            ap = asset_path(fp)
            out[key].append(f"{ap}.{ap.rsplit('/', 1)[-1]}_C")
    return out


FNV_BASIS = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
REGISTRY = "AssetRegistry.bin"
REGISTRY_OFF = "AssetRegistry.krl"


def path_hash(rel_path: str, seed: int) -> int:
    h = (FNV_BASIS + seed) & 0xFFFFFFFFFFFFFFFF
    for b in rel_path.lower().encode("utf-16-le"):
        h ^= b
        h = (h * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def _rel_to_mount(mount: str) -> str:
    m = mount.replace("\\", "/")
    while m.startswith("../"):
        m = m[3:]
    target = "FSD/" + REGISTRY
    if not m:
        return target
    m = m.rstrip("/") + "/"
    return target[len(m):] if target.lower().startswith(m.lower()) else ""


def _replace_name(buf: bytearray, rel: str) -> int:
    old = REGISTRY.encode() + b"\0"
    new = REGISTRY_OFF.encode() + b"\0"
    changed, i = 0, 0
    while True:
        i = buf.find(old, i)
        if i < 0:
            return changed
        for start in range(i, max(i - 300, 0) - 1, -1):
            if start >= 4 and struct.unpack_from("<i", buf, start - 4)[0] == i + len(old) - start:
                name = bytes(buf[start:i + len(old) - 1]).decode("utf-8", "replace")
                if rel.lower().endswith(name.lower()):
                    buf[i:i + len(old)] = new
                    changed += 1
                break
        i += len(old)


def hide_asset_registry(path: Path) -> bool:
    with open(path, "r+b") as f:
        ver, encrypted, off, isz, magic_pos = _footer_at(f)
        if encrypted:
            return False
        f.seek(off)
        idx = bytearray(f.read(isz))
        mount, o = _fstring(idx, 0)
        rel = _rel_to_mount(mount)
        if not rel:
            return False
        if ver < 10:
            if not _replace_name(idx, rel):
                return False
        else:
            count, seed = struct.unpack_from("<iQ", idx, o)
            o += 12
            changed = False
            if struct.unpack_from("<I", idx, o)[0]:
                phi_off, phi_size = struct.unpack_from("<qq", idx, o + 4)
                f.seek(phi_off)
                phi = bytearray(f.read(phi_size))
                old_h = struct.pack("<Q", path_hash(rel, seed))
                (n,) = struct.unpack_from("<i", phi, 0)
                for k in range(n):
                    pos = 4 + 12 * k
                    if phi[pos:pos + 8] == old_h:
                        new_rel = rel[:-len(REGISTRY)] + REGISTRY_OFF
                        struct.pack_into("<Q", phi, pos, path_hash(new_rel, seed))
                        changed = True
                _replace_name(phi, rel)
                f.seek(phi_off)
                f.write(phi)
                idx[o + 20:o + 40] = hashlib.sha1(phi).digest()
                o += 40
            else:
                o += 4
            if struct.unpack_from("<I", idx, o)[0]:
                fdi_off, fdi_size = struct.unpack_from("<qq", idx, o + 4)
                f.seek(fdi_off)
                fdi = bytearray(f.read(fdi_size))
                if _replace_name(fdi, rel):
                    changed = True
                f.seek(fdi_off)
                f.write(fdi)
                idx[o + 20:o + 40] = hashlib.sha1(fdi).digest()
            if not changed:
                return False
        f.seek(off)
        f.write(idx)
        f.seek(magic_pos + 24)
        f.write(hashlib.sha1(bytes(idx)).digest())
    return True
