from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import ByteString, ClassVar, cast

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import bnd4
from .utility import read_utf16le_string

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class InventorySlotInfo:
    _EMPTY_SLOT_BYTES: ClassVar[bytes] = b"\x00\x00\x00\x00\xff\xff\xff\xff"
    _TYPE_ID_TO_LENGTH: ClassVar[MappingProxyType[int, int]] = (
        MappingProxyType({0x80: 80, 0x90: 16, 0xC0: 72})
    )
    _VALID_SUBTYPES: ClassVar[tuple[int, ...]] = (
        0x80,
        0x81,
        0x82,
        0x83,
        0x84,
        0x85,
    )
    subtype_id: int | None
    type_id: int
    length: int

    @property
    def is_default_subtype(self) -> bool:
        return self.subtype_id == 0x80  # TODO: when is this ever different?

    @property
    def is_relic(self) -> bool:
        return self.type_id == 0xC0

    @property
    def is_weapon(self) -> bool:
        return self.type_id == 0x80

    @property
    def is_armor(self) -> bool:
        return self.type_id == 0x90

    @property
    def is_empty_slot(self) -> bool:
        return self.subtype_id is None and self.type_id == 0

    @classmethod
    def from_data(
        cls, data: ByteString, offset: int
    ) -> InventorySlotInfo | None:
        pos = offset + 2
        if pos + 1 < len(data):
            subtype_id = data[pos]
            if subtype_id in cls._VALID_SUBTYPES:
                type_id = data[pos + 1]
                length = cls._TYPE_ID_TO_LENGTH.get(type_id, 0)
                if length:
                    return InventorySlotInfo(
                        subtype_id=subtype_id, type_id=type_id, length=length
                    )
        empty_slot_len = len(cls._EMPTY_SLOT_BYTES)
        if offset + empty_slot_len < len(data):
            if memoryview(data)[
                offset : offset + empty_slot_len
            ] == memoryview(cls._EMPTY_SLOT_BYTES):
                return InventorySlotInfo(
                    subtype_id=None, type_id=0x00, length=empty_slot_len
                )
        return None


@dataclass(frozen=True)
class RelicData:
    item_id: int
    effect_ids: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class SaveData:
    _EMPTY_EFFECT_ID: ClassVar[int] = 0xFFFFFFFF
    _MINIMUM_INVENTORY_SIZE: ClassVar[int] = 5  # how many entries must exist
    _MINIMUM_NAME_LENGTH: ClassVar[int] = 3  # smaller matches non-name things

    data: bytes = field(repr=False)
    title: str = ""

    @cached_property
    def murk(self) -> int:
        if self.name_offset:
            return cast(
                int,
                struct.unpack_from("<I", self.data, self.name_offset + 52)[0],
            )
        return -1

    @cached_property
    def sigils(self) -> int:
        if self.name_offset:
            return cast(
                int,
                struct.unpack_from("<I", self.data, self.name_offset - 64)[0],
            )
        return -1

    @cached_property
    def name_offset(self) -> int | None:
        name_offset = None
        for i in range(0, len(self.data), 2):
            if 32 <= self.data[i] <= 126 and not self.data[i + 1]:
                if name_offset is None:
                    name_offset = i
                if (i - name_offset + 2) > SaveData._MINIMUM_NAME_LENGTH * 2:
                    return name_offset
            else:
                name_offset = None
        return name_offset

    @cached_property
    def name(self) -> str:
        if self.name_offset is None:
            return ""
        return read_utf16le_string(self.data, self.name_offset)

    @cached_property
    def inventory_offset(self) -> int | None:
        start_offset = 0
        while start_offset < 100:  # my offset is 20, but allowing for changes
            offset = start_offset
            entries = 0
            while offset < len(self.data) and (
                slot_info := InventorySlotInfo.from_data(self.data, offset)
            ):
                entries += 1
                if entries >= SaveData._MINIMUM_INVENTORY_SIZE:
                    return start_offset
                offset += slot_info.length
            start_offset += 2  # only look at 2-byte-aligned offsets
        return None

    @cached_property
    def relics(self) -> tuple[RelicData, ...]:
        offset = self.inventory_offset
        if offset is None:
            return tuple()
        view = memoryview(self.data)
        relics: list[RelicData] = []
        while slot_info := InventorySlotInfo.from_data(view, offset):
            if slot_info.is_relic:
                relics.append(
                    RelicData(
                        item_id=cast(
                            int, struct.unpack_from("<H", view, offset + 4)[0]
                        ),
                        effect_ids=tuple(
                            [
                                effect_id
                                for effect_id in cast(
                                    tuple[int, int, int],
                                    struct.unpack_from(
                                        "<III", view, offset + 16
                                    ),
                                )
                                if effect_id != type(self)._EMPTY_EFFECT_ID
                            ]
                        ),
                    )
                )
            else:
                logger.debug(
                    f"non-relic slot encountered, type: {slot_info.type_id:x}"
                )
            offset += slot_info.length
        return tuple(relics)


def load_save(data: ByteString, entry_name: str) -> SaveData:
    IV_SIZE = 0x10
    for slot in bnd4.get_entries(data):
        logger.debug(f"encountered save slot named: {slot.name}")
        if not entry_name or slot.name == entry_name:
            decryptor = Cipher(
                algorithms.AES(
                    b"\x18\xf6\x32\x66\x05\xbd\x17\x8a"
                    b"\x55\x24\x52\x3a\xc0\xa0\xc6\x09"
                ),
                modes.CBC(slot.data[:IV_SIZE]),
            ).decryptor()
            return SaveData(
                title=slot.name,
                data=(
                    decryptor.update(slot.data[IV_SIZE:])
                    + decryptor.finalize()
                ),
            )
    raise ValueError(f"No entry found named: {entry_name}")


def load_save_file(path: Path, entry_name: str) -> SaveData:
    return load_save(path.read_bytes(), entry_name)
