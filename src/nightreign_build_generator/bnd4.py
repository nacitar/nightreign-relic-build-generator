from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import ByteString, Iterator, cast

from .utility import read_utf16le_string

logger = logging.getLogger(__name__)


_ARCHIVE_IDENTIFIER: bytes = b"BND4"
_ARCHIVE_HEADER_LENGTH: int = 64
_ENTRY_IDENTIFIER: bytes = b"\x40\x00\x00\x00\xff\xff\xff\xff"
_ENTRY_HEADER_LENGTH: int = 32


@dataclass
class Entry:
    name: str
    data: memoryview[int]


def get_entries(data: ByteString) -> Iterator[Entry]:
    view = memoryview(data)
    if _ARCHIVE_HEADER_LENGTH > len(view):
        raise ValueError("not enough data to hold archive header")
    if view[0 : len(_ARCHIVE_IDENTIFIER)] != memoryview(_ARCHIVE_IDENTIFIER):
        raise ValueError("identifier not in header.")
    entry_count = cast(int, struct.unpack_from("<I", view, 12)[0])

    offset = _ARCHIVE_HEADER_LENGTH
    logger.debug(f"Processing {entry_count} entries...")
    while entry_count:
        entry_count -= 1
        next_offset = offset + _ENTRY_HEADER_LENGTH
        entry_header = view[offset:next_offset]
        offset = next_offset
        if len(entry_header) != _ENTRY_HEADER_LENGTH:
            raise ValueError("data appears to be truncated")

        if entry_header[0 : len(_ENTRY_IDENTIFIER)] != memoryview(
            _ENTRY_IDENTIFIER
        ):
            raise ValueError("identifier not in entry header")

        entry_data_length, _, entry_data_offset, entry_name_offset = cast(
            tuple[int, int, int, int],
            struct.unpack_from("<IIII", entry_header, 8),
        )
        entry_data_view = view[
            entry_data_offset : entry_data_offset + entry_data_length
        ]
        if len(entry_data_view) != entry_data_length:
            raise ValueError("entry data offset/length points beyond data")
        yield Entry(
            name=read_utf16le_string(view, entry_name_offset),
            data=entry_data_view,
        )
