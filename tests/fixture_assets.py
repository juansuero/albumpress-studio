from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

from app.brand import MONOGRAM_SOURCE_NAME


def write_png(path: Path, width: int = 64, height: int = 64) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\0" + b"\x28\x6f\x91" * width
    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(row * height, 9))
    payload += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def make_brand_library(root: Path) -> Path:
    brand_root = root / "branding"
    library = brand_root / "exports" / "approved" / "sp-lockup-v1"
    runtime = library / "lockup.png"
    vector = library / "lockup.svg"
    monogram = brand_root / "source" / MONOGRAM_SOURCE_NAME
    write_png(runtime, 800, 200)
    write_png(monogram, 1600, 1600)
    vector.parent.mkdir(parents=True, exist_ok=True)
    vector.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10H0z"/></svg>', encoding="utf-8")

    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest().upper()
    (library / "manifest.json").write_text(json.dumps({
        "status": "approved",
        "revision": "sp-lockup-v1",
        "runtime": {"path": runtime.name, "sha256": digest(runtime)},
        "vectorMaster": {"path": vector.name, "sha256": digest(vector)},
    }), encoding="utf-8")
    return library


def system_test_font() -> Path:
    path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    if not path.is_file():
        raise RuntimeError(f"Windows test font is unavailable: {path}")
    return path
