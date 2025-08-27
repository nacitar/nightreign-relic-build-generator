from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, ByteString


def get_resource_text(name: str) -> str:
    return (files(f"{__package__}.resources") / name).read_text(
        encoding="utf-8"
    )


def get_resource_json(name: str) -> Any:
    return json.loads(get_resource_text(name))


def find_utf16_string_end(data: ByteString) -> int:
    entry_name_end = 0
    for entry_name_end in range(0, len(data) - 2, 2):
        if not data[entry_name_end] and not data[entry_name_end + 1]:
            break
    return entry_name_end


def read_int(data: ByteString, offset: int, *, size: int) -> int:
    return int.from_bytes(data[offset : offset + size], "little")


def read_int32(data: ByteString, offset: int) -> int:
    return read_int(data, offset, size=4)


def read_int16(data: ByteString, offset: int) -> int:
    return read_int(data, offset, size=2)
