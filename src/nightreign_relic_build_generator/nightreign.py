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
from .utility import get_resource_json, read_utf16le_string

logger = logging.getLogger(__name__)


@dataclass
class InventorySlotInfo:
    _EMPTY_SLOT_BYTES: ClassVar[bytes] = b"\x00\x00\x00\x00\xff\xff\xff\xff"
    _TYPE_ID_TO_LENGTH: ClassVar[MappingProxyType[int, int]] = (
        MappingProxyType(
            {0x80: 80, 0x90: 16, 0xC0: 72}  # weapon  # armor  # relic
        )
    )
    type_id: int
    length: int

    def is_relic(self) -> bool:
        return self.type_id == 0xC0

    @classmethod
    def from_data(
        cls, data: ByteString, offset: int
    ) -> InventorySlotInfo | None:
        pos = offset + 2
        if pos + 1 < len(data):
            if data[pos] in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85):
                type_id = data[pos + 1]
                length = cls._TYPE_ID_TO_LENGTH.get(type_id, 0)
                if length:
                    return InventorySlotInfo(type_id=type_id, length=length)
        empty_slot_len = len(cls._EMPTY_SLOT_BYTES)
        if offset + empty_slot_len < len(data):
            if memoryview(data)[
                offset : offset + empty_slot_len
            ] == memoryview(cls._EMPTY_SLOT_BYTES):
                return InventorySlotInfo(type_id=0x00, length=empty_slot_len)
        return None


@dataclass(frozen=True)
class RelicData:
    item_id: int
    effect_ids: list[int]


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
        # my relics start at 20, but allowing for future changes that move it
        search_end = 100
        while start_offset < search_end:
            offset = start_offset
            entries = 0
            while offset < len(self.data):
                slot_info = InventorySlotInfo.from_data(self.data, offset)
                if slot_info is not None:
                    entries += 1
                    if entries >= SaveData._MINIMUM_INVENTORY_SIZE:
                        return start_offset
                    offset += slot_info.length
                else:
                    break
            start_offset += 2
        return None

    @cached_property
    def relics(self) -> tuple[RelicData, ...]:
        if self.inventory_offset is None:
            return tuple()
        relics: list[RelicData] = []

        offset = self.inventory_offset
        view = memoryview(self.data)
        while True:
            slot_info = InventorySlotInfo.from_data(view, offset)
            if slot_info is None:
                break
            if slot_info.is_relic():
                relics.append(
                    RelicData(
                        item_id=cast(
                            int, struct.unpack_from("<H", view, offset + 4)[0]
                        ),
                        effect_ids=[
                            effect_id
                            for effect_id in cast(
                                tuple[int, int, int],
                                struct.unpack_from("<III", view, offset + 16),
                            )
                            if effect_id != self.__class__._EMPTY_EFFECT_ID
                        ],
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
        if not entry_name or slot.name == entry_name:
            slot.data = memoryview(slot.data)
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


@dataclass
class RelicProcessor:
    save_data: SaveData
    effect_data: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False
    )
    item_data: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        self.effect_data: dict[str, dict[str, str]] = get_resource_json(
            "effects.json"
        )
        self.item_data: dict[str, dict[str, str]] = get_resource_json(
            "items.json"
        )

    def relic_report(self, color_filter: str = "") -> list[RelicData]:
        matched: list[RelicData] = []
        count = 0
        for relic in self.save_data.relics:
            count += 1
            attributes = self.item_data.get(
                str(relic.item_id)
            )  # TODO: why str?
            color = "MISSING"
            if attributes:
                name = attributes["name"]
                color = attributes.get("color", color)
                print(f"RELIC {relic.item_id}: [{color}] {name}")
            else:
                print(f"MISSING RELIC: couldn't find id {relic.item_id}")

            for effect_id in relic.effect_ids:
                attributes = self.effect_data.get(str(effect_id))
                if attributes:
                    print(f"- {effect_id} {attributes['name']}")
                else:
                    print(f"- WARNING: couldn't find effect id {effect_id}")

            if not color_filter or color == color_filter:
                matched.append(relic)
        print(f"==== Listed {count} relics. ====")
        return matched
