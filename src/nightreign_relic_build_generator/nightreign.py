from __future__ import annotations

import logging
import os
import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum, unique
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import ByteString, ClassVar, cast

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import bnd4
from .utility import get_resource_json, read_utf16le_string

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
                            effect_id
                            for effect_id in cast(
                                tuple[int, int, int],
                                struct.unpack_from("<III", view, offset + 16),
                            )
                            if effect_id != type(self)._EMPTY_EFFECT_ID
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


@unique
class Color(StrEnum):
    BLUE = "Blue"
    GREEN = "Green"
    RED = "Red"
    YELLOW = "Yellow"
    UNKNOWN = "UNKNOWN"

    @property
    def alias(self) -> str:
        match self:
            case Color.BLUE:
                return "Drizzly"
            case Color.GREEN:
                return "Tranquil"
            case Color.RED:
                return "Burning"
            case Color.YELLOW:
                return "Luminous"
            case Color.UNKNOWN:
                return "UNKNOWN"
        raise NotImplementedError()


@dataclass(frozen=True)
class Effect:
    name: str
    level: int
    is_stackable: bool
    is_starting_imbue: bool
    is_starting_skill: bool

    @property
    def qualified_name(self) -> str:
        return f"{self.name} +{self.level}"

    def __str__(self) -> str:
        if not self.level:
            return self.name
        return self.qualified_name


@dataclass(frozen=True)
class Relic:
    UNKNOWN_PREFIX: ClassVar[str] = "UNKNOWN_ID_"
    color: Color
    size: int
    name: str
    effects: tuple[Effect, ...]

    @property
    def is_incomplete(self) -> bool:
        return self.name.startswith(type(self).UNKNOWN_PREFIX) or any(
            effect.name.startswith(type(self).UNKNOWN_PREFIX)
            for effect in self.effects
        )

    def __str__(self) -> str:
        lines: list[str] = [f"[{self.color}] {self.name}"]
        for effect in self.effects:
            lines.append(f"- {effect}")
        return os.linesep.join(lines)


@dataclass
class Database:
    @dataclass(frozen=True)
    class _RelicMetadata:
        color: Color
        size: int

    @dataclass(frozen=True)
    class _EffectMetadata:
        name: str
        level: int

        def __post_init__(self) -> None:
            if self.level < 0:
                raise AssertionError(f"Level is negative: {self.level}")

    STACKABLE_REGEX: ClassVar[list[re.Pattern[str]]] = [
        re.compile(
            "^Improved (.+ )?("
            + "|".join(
                [
                    "Attack Power",
                    "Resistance",
                    "Damage Negation",
                    "Incantations",
                    "Sorcery",
                    "Damage",
                ]
            )
            + ")( at (Low|Full) HP)?$"
        ),
        re.compile(
            "^("
            + "|".join(
                [
                    "Dexterity",
                    "Endurance",
                    "Faith",
                    "Intelligence",
                    "Mind",
                    "Poise",
                    "Strength",
                    "Vigor",
                    "Arcane",
                ]
            )
            + ")$"
        ),
        re.compile(
            "^Improved ("
            + "|".join(
                [
                    "Guard Counters",
                    "Initial Standard Attack",
                    "Perfuming Arts",
                    "Roar & Breath Attacks",
                    "Stance-Breaking when .+",
                ]
            )
            + ")$"
        ),
        re.compile("^Boosts Attack Power of Added Affinity Attacks$"),
        re.compile("^FP Restoration upon Successive Attacks$"),
        re.compile(
            "^(?!Stonesword Key).* in possession at start of expedition$"
        ),  # NOT STONESWORD KEY
        re.compile("^Character Skill Cooldown Reduction$"),
        re.compile("^Increased rune acquisition for self and allies$"),
        re.compile("^Ultimate Art Gauge$"),
    ]
    STARTING_IMBUE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        "^Starting armament (deals|inflicts) .+$"
    )
    STARTING_SKILL_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        "^Changes compatible armament's skill to .+$"
    )

    SIZE_NAMES: ClassVar[tuple[str, ...]] = ("Delicate", "Polished", "Grand")
    relic_id_to_info: dict[int, _RelicMetadata] = field(
        init=False, default_factory=dict
    )
    relic_names: dict[int, str] = field(init=False, default_factory=dict)
    effect_id_to_info: dict[int, _EffectMetadata] = field(
        init=False, default_factory=dict
    )

    def get_effect(self, id: int) -> Effect:
        info = self.effect_id_to_info.get(id)
        if not info:
            return Effect(
                name=f"{Relic.UNKNOWN_PREFIX}EFFECT:{id}",
                level=0,
                is_stackable=False,
                is_starting_imbue=False,
                is_starting_skill=False,
            )
        return Effect(
            name=info.name,
            level=info.level,
            is_stackable=any(
                pattern.match(info.name)
                for pattern in type(self).STACKABLE_REGEX
            ),
            is_starting_imbue=bool(
                type(self).STARTING_IMBUE_REGEX.match(info.name)
            ),
            is_starting_skill=bool(
                type(self).STARTING_SKILL_REGEX.match(info.name)
            ),
        )

    def get_relic(self, data: RelicData) -> Relic:
        info = self.relic_id_to_info.get(data.item_id)
        if not info:
            return Relic(
                color=Color.UNKNOWN,
                size=len(data.effect_ids),
                name=f"{Relic.UNKNOWN_PREFIX}RELIC:{data.item_id}",
                effects=tuple(self.get_effect(id) for id in data.effect_ids),
            )
        if info.size != len(data.effect_ids):
            raise AssertionError(
                f"relic id {data.item_id} is size {info.size} but has"
                f" {len(data.effect_ids)} effects."
            )
        if info.size not in range(1, len(type(self).SIZE_NAMES) + 1):
            raise AssertionError(
                f"database has invalid size {info.size}"
                f" for relic id {data.item_id}"
            )

        standard_name = " ".join(
            [type(self).SIZE_NAMES[info.size - 1], info.color.alias, "Scene"]
        )
        name = self.relic_names.get(data.item_id)
        if not name:
            name = standard_name
        elif name != standard_name:
            logger.debug(
                f"database has non-standard name for relic id"
                f" {data.item_id}: {name}"
            )
        return Relic(
            color=info.color,
            size=info.size,
            name=self.relic_names.get(data.item_id, standard_name),
            effects=tuple(self.get_effect(id) for id in data.effect_ids),
        )

    def load_from_save_editor(self) -> None:
        effect_data: dict[str, dict[str, str]] = get_resource_json(
            "effects.json"
        )
        item_data: dict[str, dict[str, str]] = get_resource_json("items.json")

        for item_id, attributes in item_data.items():
            color_str = attributes.get("color", "")
            try:
                color = Color[color_str.upper()]
            except KeyError:
                logger.error(f'Skipping {item_id}: bad color "{color_str}"')
                continue

            name = attributes.get("name", "")
            if not name:
                logger.error(f"Skipping {item_id}: no name provided")
                continue
            try:
                size = type(self).SIZE_NAMES.index(name.split(" ", 1)[0]) + 1
            except ValueError:
                size = {
                    "Torn Braided Cord": 2,
                    "Old Pocketwatch": 2,
                    "Small Makeup Brush": 2,
                    "Slate Whetstone": 2,
                    "Golden Dew": 2,
                    "Night of the Beast": 2,
                    "Vestige of Night": 2,
                    "Blessed Flowers": 2,
                    "Stone Stake": 2,
                    "Cracked Sealing Wax": 2,
                    "Third Volume": 2,
                    "Crown Medal": 2,
                    "Besmirched Frame": 2,
                }.get(name, 3)
                logger.debug(f"Assuming {item_id} has {size} effects: {name}")
            self.relic_id_to_info[int(item_id)] = type(self)._RelicMetadata(
                color=color, size=size
            )

        suffix_pattern = re.compile(r" \+(?P<level>\d+)$")
        for effect_id, attributes in effect_data.items():
            name = attributes["name"]
            level = 0
            if match := suffix_pattern.search(name):
                level = int(match.group("level"))
                name = name[: match.start()]

            effect_info = type(self)._EffectMetadata(name, level)
            self.effect_id_to_info[int(effect_id)] = effect_info
            logger.debug(f"Added effect: {effect_id} {effect_info}")

    def __post_init__(self) -> None:
        self.load_from_save_editor()


UNIVERSAL_URNS: dict[str, tuple[Color | None, Color | None, Color | None]] = {
    "Sacred Erdtree Grail": (Color.YELLOW, Color.YELLOW, Color.YELLOW),
    "Spirit Shelter Grail": (Color.GREEN, Color.GREEN, Color.GREEN),
    "Giant's Cradle Grail": (Color.BLUE, Color.BLUE, Color.BLUE),
}

CLASS_URNS: dict[
    str, dict[str, tuple[Color | None, Color | None, Color | None]]
] = {
    "duchess": {
        "Duchess' Urn": (Color.RED, Color.BLUE, Color.BLUE),
        "Duchess' Goblet": (Color.YELLOW, Color.YELLOW, Color.GREEN),
        "Duchess' Chalice": (Color.BLUE, Color.YELLOW, None),
        "Soot-Covered Duchess' Urn": (Color.RED, Color.RED, Color.GREEN),
        "Sealed Duchess' Urn": (Color.BLUE, Color.BLUE, Color.RED),
    },
    "executor": {
        "Executor's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
        "Executor's Goblet": (Color.RED, Color.BLUE, Color.GREEN),
        "Executor's Chalice": (Color.BLUE, Color.YELLOW, None),
        "Soot-Covered Executor's Urn": (Color.RED, Color.RED, Color.BLUE),
        "Sealed Executor's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
    },
    "guardian": {
        "Guardian's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
        "Guardian's Goblet": (Color.BLUE, Color.BLUE, Color.GREEN),
        "Guardian's Chalice": (Color.BLUE, Color.YELLOW, None),
        "Soot-Covered Guardian's Urn": (Color.RED, Color.GREEN, Color.GREEN),
        "Sealed Guardian's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
    },
    "ironeye": {
        "Ironeye's Urn": (Color.YELLOW, Color.GREEN, Color.GREEN),
        "Ironeye's Goblet": (Color.RED, Color.BLUE, Color.YELLOW),
        "Ironeye's Chalice": (Color.RED, Color.GREEN, None),
        "Soot-Covered Ironeye's Urn": (Color.BLUE, Color.YELLOW, Color.YELLOW),
        "Sealed Ironeye's Urn": (Color.GREEN, Color.GREEN, Color.YELLOW),
    },
    "raider": {
        "Raider's Urn": (Color.RED, Color.GREEN, Color.GREEN),
        "Raider's Goblet": (Color.RED, Color.BLUE, Color.YELLOW),
        "Raider's Chalice": (Color.RED, Color.RED, None),
        "Soot-Covered Raider's Urn": (Color.BLUE, Color.BLUE, Color.GREEN),
        "Sealed Raider's Urn": (Color.GREEN, Color.GREEN, Color.RED),
    },
    "recluse": {
        "Recluse's Urn": (Color.BLUE, Color.BLUE, Color.GREEN),
        "Recluse's Goblet": (Color.RED, Color.BLUE, Color.YELLOW),
        "Recluse's Chalice": (Color.YELLOW, Color.GREEN, None),
        "Soot-Covered Recluse's Urn": (Color.RED, Color.RED, Color.YELLOW),
        "Sealed Recluse's Urn": (Color.GREEN, Color.BLUE, Color.BLUE),
    },
    "revenant": {
        "Revenant's Urn": (Color.BLUE, Color.BLUE, Color.YELLOW),
        "Revenant's Goblet": (Color.RED, Color.RED, Color.GREEN),
        "Revenant's Chalice": (Color.BLUE, Color.GREEN, None),
        "Soot-Covered Revenant's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
        "Sealed Revenant's Urn": (Color.YELLOW, Color.BLUE, Color.BLUE),
    },
    "wylder": {
        "Wylder's Urn": (Color.RED, Color.RED, Color.BLUE),
        "Wylder's Goblet": (Color.YELLOW, Color.GREEN, Color.GREEN),
        "Wylder's Chalice": (Color.RED, Color.YELLOW, None),
        "Soot-Covered Wylder's Urn": (Color.BLUE, Color.BLUE, Color.YELLOW),
        "Sealed Wylder's Urn": (Color.BLUE, Color.RED, Color.RED),
    },
}
