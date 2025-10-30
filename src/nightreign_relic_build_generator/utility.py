from __future__ import annotations

import codecs
import csv
import json
import os
import re
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from importlib.resources import files
from io import StringIO
from pathlib import Path
from typing import (
    Any,
    ByteString,
    Callable,
    Iterable,
    Iterator,
    TextIO,
    TypeVar,
    cast,
    get_type_hints,
    overload,
)

RESOURCE_FILES = files(f"{__package__}.resources")

T = TypeVar("T")

TextProvider = str | Path | TextIO | list[str]


def get_text(source: TextProvider) -> str:
    """Return the full text from any supported TextProvider."""
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        return os.linesep.join(source)
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    return source.read()  # TextIO


@contextmanager
def open_text_io(source: TextProvider) -> Iterator[TextIO]:
    """Yield a readable TextIO.  Must always be used as a context manager."""
    if isinstance(source, Path):
        yield source.open(encoding="utf-8")
    elif isinstance(source, (str, list)):
        yield StringIO(get_text(source))
    else:
        yield source  # TextIO; already open, do not close


def checked_cast(value: object, expected_type: type[T]) -> T:
    if not isinstance(value, expected_type):
        raise TypeError(
            f"Expected {expected_type.__name__}, got {type(value).__name__}"
        )
    return value


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


_JSON5_COMMENT_PATTERN = re.compile(
    r"""
    (                               # 1: double-quoted string
        "(?:\\.|[^"\\])*"
    )
  | (                               # 2: single-quoted string
        '(?:\\.|[^'\\])*'
    )
  | (?:[ \t]*//[^\r\n]*)            # remove spaces + single-line comment
  | (?:[ \t]*/\*.*?\*/)             # remove spaces + block comment (ungreedy)
    """,
    re.VERBOSE | re.DOTALL,
)


def json5_load(source: TextProvider) -> Any:
    def comment_replacer(match: re.Match[str]) -> str:
        return match.group(1) or match.group(2) or ""

    no_comments = _JSON5_COMMENT_PATTERN.sub(
        comment_replacer, get_text(source)
    )
    return json.loads(re.sub(r",(?=\s*[\]}])", "", no_comments))


def bool_from_string(value: str) -> bool:
    return value.lower() in ("1", "true", "yes")


def _build_converter(typ: type[T]) -> Callable[[str], Any]:
    """Return a function that converts a CSV cell string to the target type."""
    if typ in (str, int, float):
        return typ
    if typ is bool:
        return bool_from_string
    # TODO: allow hooking?

    if callable(method := getattr(typ, "from_string", None)):
        return lambda string: method(string)

    # fallback: try calling the constructor
    def fallback(value: str) -> Any:
        ctor = cast(Callable[[str], Any], typ)
        return ctor(value)

    return fallback


@overload
def csv_load(
    source: TextProvider,
    *,
    dataclass: type[dict[str, str]] = ...,
    field_metadata_key: str = ...,
) -> Iterable[dict[str, str]]: ...


@overload
def csv_load(
    source: TextProvider,
    *,
    dataclass: type[T] = ...,
    field_metadata_key: str = ...,
) -> Iterable[T]: ...


def csv_load(
    source: TextProvider,
    *,
    dataclass: type[Any] = dict,
    field_metadata_key: str = "csv_key",
) -> Iterable[Any]:
    """Load CSV data into dicts or dataclass instances.

    For custom field types, a classmethod `from_string(cls, s: str)` may be
    implemented to control how an instance is created from a CSV cell string.
    """
    with open_text_io(source) as source_io:
        reader = csv.DictReader(source_io)

        if dataclass is dict:
            yield from reader
            return

        if not is_dataclass(dataclass):
            raise TypeError(f"{dataclass} must be a dataclass or dict")

        type_hints = get_type_hints(dataclass)
        field_map: dict[str, tuple[str, Callable[[str], Any]]] = {}

        for f in fields(dataclass):
            csv_key = f.metadata.get(field_metadata_key, f.name)
            if not csv_key:
                continue
            typ = type_hints.get(f.name, str)
            field_map[f.name] = (csv_key, _build_converter(typ))

        for row in reader:
            values: dict[str, Any] = {}
            for name, (csv_key, conv) in field_map.items():
                raw = row.get(csv_key)
                if raw is not None:
                    values[name] = conv(raw)
            yield dataclass(**values)
