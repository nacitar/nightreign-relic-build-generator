from __future__ import annotations

import codecs
import json
from importlib.resources import files
from typing import Any, ByteString


def get_resource_text(name: str) -> str:
    return (files(f"{__package__}.resources") / name).read_text(
        encoding="utf-8"
    )


def get_resource_json(name: str) -> Any:
    return json.loads(get_resource_text(name))


def read_utf16le_string(data: ByteString, offset: int = 0) -> str:
    end_offset = offset
    for end_offset in range(offset, len(data) - 2, 2):
        if not data[end_offset] and not data[end_offset + 1]:
            break
    return codecs.decode(memoryview(data)[offset:end_offset], "utf-16le")
