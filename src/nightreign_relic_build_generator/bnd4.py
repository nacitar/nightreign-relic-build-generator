from __future__ import annotations

import codecs
import logging
import struct
from dataclasses import dataclass
from typing import ByteString, Iterator

logger = logging.getLogger(__name__)


ARCHIVE_IDENTIFIER: bytes = b"BND4"
ARCHIVE_HEADER_LENGTH: int = 64
ENTRY_IDENTIFIER: bytes = b"\x40\x00\x00\x00\xff\xff\xff\xff"
ENTRY_HEADER_LENGTH: int = 32


@dataclass
class Entry:
    name: str
    data: ByteString


def get_entries(data: ByteString) -> Iterator[Entry]:
    view = memoryview(data)
    if ARCHIVE_HEADER_LENGTH > len(view):
        raise ValueError("not enough data to hold archive header")
    if view[0 : len(ARCHIVE_IDENTIFIER)] != memoryview(ARCHIVE_IDENTIFIER):
        raise ValueError("identifier not in header.")
    entry_count = struct.unpack("<i", view[12:16])[0]

    offset = ARCHIVE_HEADER_LENGTH
    print(f"Processing {entry_count} entries...")
    while entry_count:
        entry_count -= 1
        next_offset = offset + ENTRY_HEADER_LENGTH
        entry_header = view[offset:next_offset]
        offset = next_offset
        if len(entry_header) != ENTRY_HEADER_LENGTH:
            raise ValueError("data appears to be truncated")

        if entry_header[0 : len(ENTRY_IDENTIFIER)] != memoryview(
            ENTRY_IDENTIFIER
        ):
            raise ValueError("identifier not in entry header")

        entry_length = struct.unpack("<I", entry_header[8:12])[0]
        entry_data_offset = struct.unpack("<I", entry_header[16:20])[0]
        entry_name_offset = struct.unpack("<I", entry_header[20:24])[0]

        entry_view = view[entry_data_offset : entry_data_offset + entry_length]
        if len(entry_view) != entry_length:
            raise ValueError("entry data offset/length points beyond data")

        # find end of name string
        for entry_name_end in range(entry_name_offset, len(view) - 2, 2):
            if not view[entry_name_end] and not view[entry_name_end + 1]:
                break
        yield Entry(
            name=codecs.decode(
                view[entry_name_offset:entry_name_end], "utf-16le"
            ),
            data=entry_view,
        )
