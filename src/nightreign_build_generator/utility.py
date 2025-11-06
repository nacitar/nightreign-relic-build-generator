from __future__ import annotations

import codecs
import csv
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import InitVar, is_dataclass
from importlib.resources import files
from inspect import signature
from io import StringIO
from pathlib import Path
from typing import (
    Any,
    ByteString,
    Callable,
    Iterator,
    Sequence,
    TextIO,
    TypeVar,
    get_type_hints,
    overload,
)

logger = logging.getLogger(__name__)

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


_CONVERTER_CACHE: dict[type[Any], Callable[[str], Any]] = {
    str: str,
    int: int,
    float: float,
    bool: bool_from_string,
}
_CONVERTER_LOCK = threading.Lock()


def register_converter(
    typ: type[Any], converter: Callable[[str], Any]
) -> None:
    """Register a new converter for a type. Does not replace existing ones."""
    with _CONVERTER_LOCK:
        if typ in _CONVERTER_CACHE:
            raise KeyError(
                f"Converter already registered for type: {typ.__name__}"
            )
        _CONVERTER_CACHE[typ] = converter


def _build_converter(typ: type[T]) -> Callable[[str], Any]:
    """Return a function that converts a CSV cell string to the target type."""
    with _CONVERTER_LOCK:
        if (converter := _CONVERTER_CACHE.get(typ)) is None:
            if callable(method := getattr(typ, "from_string", None)):

                def converter(value: str) -> Any:
                    return method(value)  # call T.from_string

                _CONVERTER_CACHE[typ] = converter
            else:
                raise TypeError(f"No conversion registered for: {typ}")
    return converter


class ColumnSubsetError(Exception):
    pass


def get_callable_argument_hints(
    function: Callable[..., Any],
) -> dict[str, type]:
    type_hints = {
        member: (
            member_type
            if not isinstance(member_type, InitVar)
            else member_type.type
        )
        for member, member_type in get_type_hints(function).items()
    }
    return {
        member: type_hints[member]
        for member in signature(function).parameters.keys()
        if member != "return"
    }


@overload
def csv_load(
    source: TextProvider,
    *,
    delimiter: str = ...,
    column_names: Sequence[str] | None = ...,
    dataclass: None = ...,
    field_metadata_key: str = ...,
    field_to_column_name: dict[str, str] | None = ...,
    init_function: Callable[..., dict[str, str]] | None = ...,
    allow_column_subset: bool = ...,
) -> Iterator[dict[str, str]]: ...


@overload
def csv_load(
    source: TextProvider,
    *,
    delimiter: str = ...,
    column_names: Sequence[str] | None = ...,
    dataclass: type[T] = ...,
    field_metadata_key: str = ...,
    field_to_column_name: dict[str, str] | None = ...,
    init_function: Callable[..., T] | None = ...,
    allow_column_subset: bool = ...,
) -> Iterator[T]: ...


# TODO: what if init_function's return doesn't match dataclass?
def csv_load(
    source: TextProvider,
    *,
    delimiter: str = ",",
    column_names: Sequence[str] | None = None,
    dataclass: type[T] | None = None,
    field_metadata_key: str = "csv_key",
    field_to_column_name: dict[str, str] | None = None,
    init_function: Callable[..., T | dict[str, str]] | None = None,
    allow_column_subset: bool = True,
) -> Iterator[T] | Iterator[dict[str, str]]:
    """Load CSV data into dicts or dataclass instances.

    For custom field types, a classmethod `from_string(cls, s: str)` may be
    implemented to control how an instance is created from a CSV cell string.
    """
    with open_text_io(source) as source_io:
        reader = csv.reader(source_io, delimiter=delimiter)
        if column_names is None:
            column_names = next(reader)
        if init_function is not None:
            type_hints = get_callable_argument_hints(init_function)
            if field_to_column_name is None:
                field_to_column_name = {key: key for key in type_hints}
        else:
            type_hints = None
        column_indices = {name: i for i, name in enumerate(column_names)}
        if dataclass is not None:
            if not is_dataclass(dataclass):
                raise TypeError(
                    f"dataclass argument isn't a dataclass: {dataclass}"
                )
            if init_function is None:
                init_function = dataclass
            if type_hints is None:
                type_hints = get_callable_argument_hints(dataclass)
            if field_to_column_name is None:
                field_to_column_name = {
                    name: dataclass.__dataclass_fields__[name].metadata.get(
                        field_metadata_key, name
                    )
                    for name in type_hints
                }
        else:
            if init_function is None:
                init_function = dict
            if type_hints is None:
                type_hints = {}
            if field_to_column_name is None:
                field_to_column_name = {
                    column_name: column_name for column_name in column_names
                }

        field_to_index_and_converter = {
            field_name: (
                index,
                _build_converter(type_hints.get(field_name, str)),
            )
            for field_name, column_name in field_to_column_name.items()
            if (index := column_indices.get(column_name)) is not None
        }
        if len(field_to_column_name) < len(column_names):
            message = (
                f"Only {len(field_to_index_and_converter)} fields"
                f" read of the {len(column_names)} present in CSV data."
            )
            logger.debug(message)
            if not allow_column_subset:
                raise ColumnSubsetError(message)

        yield from (
            init_function(
                **{
                    name: conv(row[index])
                    for name, (
                        index,
                        conv,
                    ) in field_to_index_and_converter.items()
                }
            )
            for row in reader
        )
