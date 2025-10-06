from __future__ import annotations

import codecs
import re
from importlib.resources import files
from pathlib import Path
from typing import Any, ByteString

import json5

RESOURCE_FILES = files(f"{__package__}.resources")


def list_resources() -> set[str]:
    return set(
        entry.name for entry in RESOURCE_FILES.iterdir() if entry.is_file()
    )


def get_resource_text(name: str) -> str:
    return (RESOURCE_FILES / name).read_text(encoding="utf-8")


def get_resource_json(name: str) -> Any:
    return json5.loads(get_resource_text(name))


def read_utf16le_string(data: ByteString, offset: int = 0) -> str:
    end_offset = offset
    for end_offset in range(offset, len(data) - 2, 2):
        if not data[end_offset] and not data[end_offset + 1]:
            break
    return codecs.decode(memoryview(data)[offset:end_offset], "utf-16le")


def _parse_scores_json_data(data: Any) -> dict[str, int]:
    if not isinstance(data, dict):
        raise ValueError(f"root element not a dict: {type(data).__name__}")
    score_table: dict[str, int] = {}
    for tier_score, effects in data.items():
        if not isinstance(effects, list):
            raise ValueError(
                f"effects not a list: {type(effects).__name__} = {effects}"
            )
        for effect in effects:
            score_table[str(effect).lower()] = int(tier_score)
    return score_table


SCORE_RESOURCE_PATTERN = re.compile(
    r"^scores_(?P<name>.+)\.json$", re.IGNORECASE
)


def get_builtin_scores(name: str) -> dict[str, int]:
    return _parse_scores_json_data(get_resource_json(f"scores_{name}.json"))


def load_scores(path: Path) -> dict[str, int]:
    return _parse_scores_json_data(
        json5.loads(path.read_text(encoding="utf-8"))
    )


def list_builtin_score_resources() -> list[str]:
    return [
        match.group("name")
        for resource_name in list_resources()
        if (match := SCORE_RESOURCE_PATTERN.fullmatch(resource_name))
    ]
