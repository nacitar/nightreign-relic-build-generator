from __future__ import annotations

import codecs
import re
from importlib.resources import files
from typing import ByteString

RESOURCE_FILES = files(f"{__package__}.resources")


def list_resources() -> set[str]:
    return set(
        entry.name for entry in RESOURCE_FILES.iterdir() if entry.is_file()
    )


def get_resource_text(name: str) -> str:
    return (RESOURCE_FILES / name).read_text(encoding="utf-8")


def read_utf16le_string(data: ByteString, offset: int = 0) -> str:
    end_offset = offset
    for end_offset in range(offset, len(data) - 2, 2):
        if not data[end_offset] and not data[end_offset + 1]:
            break
    return codecs.decode(memoryview(data)[offset:end_offset], "utf-16le")


SCORE_RESOURCE_PATTERN = re.compile(
    r"^scores_(?P<name>.+)\.json$", re.IGNORECASE
)


def get_builtin_score_text(name: str) -> str:
    return get_resource_text(f"scores_{name}.json")


def list_builtin_score_resources() -> list[str]:
    return [
        match.group("name")
        for resource_name in list_resources()
        if (match := SCORE_RESOURCE_PATTERN.fullmatch(resource_name))
    ]
