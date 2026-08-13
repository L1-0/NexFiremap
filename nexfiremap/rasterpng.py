"""Minimal RGBA PNG encoder - stdlib only (zlib + struct), no Pillow.

The only thing this project needs from an image library is "write an RGBA
grid to a PNG Leaflet can load as an ImageOverlay." That's a ~30-line PNG
writer, so it isn't worth a new dependency for it.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np


def encode_png(rgba: np.ndarray) -> bytes:
    """``rgba``: (height, width, 4) uint8 array -> PNG file bytes."""
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("rgba must be a (H, W, 4) uint8 array")
    height, width, _ = rgba.shape

    def chunk(tag: bytes, data: bytes) -> bytes:
        # PNG chunk framing: big-endian length, 4-byte type tag, the raw
        # data, then a CRC32 over tag+data (not length) - per the PNG spec.
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit depth, color type 6 = RGBA
    # Filter type 0 (None) prefixed to every scanline, per the PNG spec.
    raw = b"".join(b"\x00" + row.tobytes() for row in rgba)
    idat = zlib.compress(raw, level=6)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
