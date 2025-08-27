from __future__ import annotations

import codecs
import logging
from dataclasses import dataclass, field
from functools import cached_property
from typing import ByteString, ClassVar, Iterator

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import bnd4
from .utility import (
    find_utf16_string_end,
    get_resource_json,
    read_int16,
    read_int32,
)

logger = logging.getLogger(__name__)

INVENTORY_EMPTY_SLOT_BYTES = b"\x00\x00\x00\x00\xff\xff\xff\xff"
INVENTORY_SLOT_TYPE_TO_SIZE: dict[int, int] = {
    0x80: 80,  # weapons
    0x90: 16,  # armor (skins?)
    (INVENTORY_SLOT_RELIC := 0xC0): 72,  # relics
    (INVENTORY_SLOT_EMPTY := 0x00): len(INVENTORY_EMPTY_SLOT_BYTES),
}


def get_inventory_slot_type(data: ByteString, offset: int) -> int | None:
    pos = offset + 2
    if pos + 1 < len(data):
        if data[pos] in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85):
            return data[pos + 1]
    if offset + len(INVENTORY_EMPTY_SLOT_BYTES) < len(data):
        if memoryview(data)[
            offset : offset + len(INVENTORY_EMPTY_SLOT_BYTES)
        ] == memoryview(INVENTORY_EMPTY_SLOT_BYTES):
            return INVENTORY_SLOT_EMPTY

    return None


@dataclass
class RelicData:
    EMPTY_EFFECT_ID: ClassVar[int] = 0xFFFFFFFF
    item_id: int
    effect_ids: list[int]


@dataclass(frozen=True, kw_only=True)
class SaveData:
    _MINIMUM_INVENTORY_SIZE: ClassVar[int] = 5  # how many entries must exist
    _MINIMUM_NAME_LENGTH: ClassVar[int] = 3  # smaller matches non-name things

    data: bytes = field(repr=False)
    title: str = ""

    @cached_property
    def murk(self) -> int:
        if self.name_offset:
            return read_int32(self.data, self.name_offset + 52)
        return -1

    @cached_property
    def sigils(self) -> int:
        if self.name_offset:
            return read_int32(self.data, self.name_offset - 64)
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
        view = memoryview(self.data)[self.name_offset :]
        return codecs.decode(view[: find_utf16_string_end(view)], "utf-16le")

    @cached_property
    def inventory_offset(self) -> int | None:
        start_offset = 0
        # my relics start at 20, but allowing for future changes that move it
        search_end = 100
        while start_offset < search_end:
            offset = start_offset
            entries = 0
            while offset < len(self.data):
                slot_type = get_inventory_slot_type(self.data, offset)
                # print(f"got {offset} == {slot_type}")
                if (
                    slot_type is not None
                    and (
                        slot_size := INVENTORY_SLOT_TYPE_TO_SIZE.get(slot_type)
                    )
                    is not None
                ):
                    entries += 1
                    if entries >= SaveData._MINIMUM_INVENTORY_SIZE:
                        return start_offset
                    offset += slot_size
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
            slot_type = get_inventory_slot_type(view, offset)
            if slot_type is None:
                break
            slot_size = INVENTORY_SLOT_TYPE_TO_SIZE[slot_type]
            if slot_type == INVENTORY_SLOT_RELIC:
                relics.append(
                    RelicData(
                        item_id=read_int16(view, offset + 4),
                        effect_ids=[
                            read_int32(view, offset + 16),
                            read_int32(view, offset + 20),
                            read_int32(view, offset + 24),
                        ],
                    )
                )
            else:
                logger.debug(
                    f"non-relic slot encountered, type: {slot_type:x}"
                )
            offset += slot_size
        return tuple(relics)


def load_save(data: ByteString) -> Iterator[SaveData]:
    IV_SIZE = 0x10
    for slot in bnd4.get_entries(data):
        logger.debug(f"processing slot ({len(slot.data)} bytes): {slot.name}")
        slot.data = memoryview(slot.data)
        decryptor = Cipher(
            algorithms.AES(
                b"\x18\xf6\x32\x66\x05\xbd\x17\x8a"
                b"\x55\x24\x52\x3a\xc0\xa0\xc6\x09"
            ),
            modes.CBC(slot.data[:IV_SIZE]),
        ).decryptor()
        yield SaveData(
            title=slot.name,
            data=(
                decryptor.update(slot.data[IV_SIZE:]) + decryptor.finalize()
            ),
        )


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
        print(
            f"Processor: {self.save_data.name}"
            f" (offset {self.save_data.name_offset})"
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
                if effect_id == RelicData.EMPTY_EFFECT_ID:
                    continue
                attributes = self.effect_data.get(str(effect_id))
                if attributes:
                    print(f"- {effect_id} {attributes['name']}")
                else:
                    print(f"- WARNING: couldn't find effect id {effect_id}")

            if not color_filter or color == color_filter:
                matched.append(relic)
        print(f"==== Listed {count} relics. ====")
        return matched
