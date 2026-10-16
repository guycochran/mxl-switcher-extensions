#!/usr/bin/env python3
"""Pure layout and label helpers for the direct 4K eight-up RTMPS output."""

from __future__ import annotations

import dataclasses
import pathlib
import struct
import zlib


@dataclasses.dataclass(frozen=True)
class Input:
    label: str
    flow_id: str


FONT = {
    " ": [0, 0, 0, 0, 0, 0, 0], "-": [0, 0, 0, 31, 0, 0, 0],
    "?": [14, 17, 1, 2, 4, 0, 4], "0": [14, 17, 19, 21, 25, 17, 14],
    "1": [4, 12, 4, 4, 4, 4, 14], "2": [14, 17, 1, 2, 4, 8, 31],
    "3": [30, 1, 1, 14, 1, 1, 30], "4": [2, 6, 10, 18, 31, 2, 2],
    "5": [31, 16, 16, 30, 1, 1, 30], "6": [14, 16, 16, 30, 17, 17, 14],
    "7": [31, 1, 2, 4, 8, 8, 8], "8": [14, 17, 17, 14, 17, 17, 14],
    "9": [14, 17, 17, 15, 1, 1, 14], "A": [14, 17, 17, 31, 17, 17, 17],
    "B": [30, 17, 17, 30, 17, 17, 30], "C": [14, 17, 16, 16, 16, 17, 14],
    "D": [30, 17, 17, 17, 17, 17, 30], "E": [31, 16, 16, 30, 16, 16, 31],
    "F": [31, 16, 16, 30, 16, 16, 16], "G": [14, 17, 16, 23, 17, 17, 15],
    "H": [17, 17, 17, 31, 17, 17, 17], "I": [14, 4, 4, 4, 4, 4, 14],
    "J": [7, 2, 2, 2, 18, 18, 12], "K": [17, 18, 20, 24, 20, 18, 17],
    "L": [16, 16, 16, 16, 16, 16, 31], "M": [17, 27, 21, 21, 17, 17, 17],
    "N": [17, 25, 21, 19, 17, 17, 17], "O": [14, 17, 17, 17, 17, 17, 14],
    "P": [30, 17, 17, 30, 16, 16, 16], "Q": [14, 17, 17, 17, 21, 18, 13],
    "R": [30, 17, 17, 30, 20, 18, 17], "S": [15, 16, 16, 14, 1, 1, 30],
    "T": [31, 4, 4, 4, 4, 4, 4], "U": [17, 17, 17, 17, 17, 17, 14],
    "V": [17, 17, 17, 17, 17, 10, 4], "W": [17, 17, 17, 21, 21, 21, 10],
    "X": [17, 17, 10, 4, 10, 17, 17], "Y": [17, 17, 10, 4, 4, 4, 4],
    "Z": [31, 1, 2, 4, 8, 16, 31], "|": [4, 4, 4, 4, 4, 4, 4],
}


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload)
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def write_label_png(path: pathlib.Path, width: int, height: int, label: str) -> None:
    pixels = bytearray(width * height * 3)
    for y in range(height):
        shade = 18 if y < height - 8 else 25
        for x in range(width):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes((shade, shade + 7, shade + 17))
    for y in range(height - 8, height):
        for x in range(width):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = b"\x24\xc2\xff"
    text = label.upper()[:32]
    scale = max(4, min(12, (height - 20) // 7))
    text_width = len(text) * 6 * scale
    origin_x = max(20, (width - text_width) // 2)
    origin_y = max(8, (height - 7 * scale) // 2 - 3)
    for char_index, char in enumerate(text):
        for gy, row in enumerate(FONT.get(char, FONT["?"])):
            for gx in range(5):
                if row & (1 << (4 - gx)):
                    for sy in range(scale):
                        for sx in range(scale):
                            x = origin_x + (char_index * 6 + gx) * scale + sx
                            y = origin_y + gy * scale + sy
                            if x < width and y < height:
                                offset = (y * width + x) * 3
                                pixels[offset:offset + 3] = b"\xf4\xf7\xff"
    raw = b"".join(
        b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3])
        for y in range(height)
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def parse_input(value: str) -> Input:
    if "=" not in value:
        raise ValueError("inputs must be LABEL=FLOW_UUID")
    label, flow_id = (part.strip() for part in value.split("=", 1))
    if not label or not flow_id:
        raise ValueError("input label and flow UUID cannot be empty")
    return Input(label, flow_id)


def tile_positions() -> list[tuple[int, int]]:
    """Return the nine 1280x720 cell origins in a 3840x2160 canvas."""
    return [((index % 3) * 1280, (index // 3) * 720) for index in range(9)]
