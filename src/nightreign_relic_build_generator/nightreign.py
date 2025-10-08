from __future__ import annotations

import logging
import os
import struct
from dataclasses import InitVar, dataclass, field
from enum import Enum, StrEnum, auto, unique
from functools import cached_property
from itertools import chain
from pathlib import Path
from types import MappingProxyType
from typing import ByteString, ClassVar, Iterator, Mapping, Sequence, cast

import json5
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import bnd4
from .term_style import TermStyle
from .utility import get_resource_text

logger = logging.getLogger(__name__)


@unique
class EntityType(Enum):
    WEAPON = auto()
    ARMOR = auto()
    RELIC = auto()
    VALID_UNKNOWN_B0 = auto()
    VALID_UNKNOWN_A0 = auto()
    EMPTY_SLOT = auto()

    @classmethod
    def from_identifiers(
        cls, type_id: int, subtype_id: int
    ) -> EntityType | None:
        if subtype_id in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85):
            match type_id:
                case 0x80:
                    return cls.WEAPON
                case 0x90:
                    return cls.ARMOR
                case 0xC0:
                    return cls.RELIC
        elif subtype_id == 0x00:
            match type_id:
                case 0xB0:
                    return cls.VALID_UNKNOWN_B0  # consumables?
                case 0xA0:
                    return cls.VALID_UNKNOWN_A0  # ?? rings in elden ring
                case 0x00:
                    return cls.EMPTY_SLOT
        return None


@dataclass
class EntityHeader:
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<HBB")
    inventory_id: int
    entity_type: EntityType
    data: memoryview

    @classmethod
    def from_data(cls, data: ByteString, offset: int) -> EntityHeader | None:
        try:
            view = memoryview(data)[offset : offset + cls._STRUCT.size]
            header_fields: tuple[int, int, int] = cls._STRUCT.unpack_from(view)
            inventory_id, subtype_id, type_id = header_fields
            entity_type = EntityType.from_identifiers(type_id, subtype_id)
            if entity_type:
                return EntityHeader(
                    inventory_id=inventory_id,
                    entity_type=entity_type,
                    data=view,
                )
        except struct.error:
            pass
        return None


@unique
class Section(Enum):
    INVENTORY = auto()
    METADATA = auto()


@dataclass
class Entity:
    _INVENTORY_BLOCK_SIZE_TABLE: ClassVar[Mapping[EntityType, int]] = (
        MappingProxyType({entity_type: 14 for entity_type in EntityType})
    )
    _INVENTORY_EMPTY_SLOT_BYTES: ClassVar[bytes] = (
        b"\x00" * _INVENTORY_BLOCK_SIZE_TABLE[EntityType.EMPTY_SLOT]
    )
    _METADATA_EMPTY_SLOT_BYTES: ClassVar[bytes] = b"\x00" * 4 + b"\xff" * 4
    _METADATA_BLOCK_SIZE_TABLE: ClassVar[MappingProxyType[EntityType, int]] = (
        MappingProxyType(
            {
                EntityType.WEAPON: 80,
                EntityType.ARMOR: 16,
                EntityType.RELIC: 72,
                EntityType.EMPTY_SLOT: len(_METADATA_EMPTY_SLOT_BYTES),
            }
        )
    )
    header: EntityHeader
    data: memoryview

    @classmethod
    def from_data(
        cls, section: Section, data: ByteString, offset: int
    ) -> Entity | None:
        match section:
            case Section.INVENTORY:
                empty_slot_bytes = cls._INVENTORY_EMPTY_SLOT_BYTES
                block_size_table = cls._INVENTORY_BLOCK_SIZE_TABLE
            case Section.METADATA:
                empty_slot_bytes = cls._METADATA_EMPTY_SLOT_BYTES
                block_size_table = cls._METADATA_BLOCK_SIZE_TABLE
            case _:
                raise NotImplementedError()
        header = EntityHeader.from_data(data, offset)
        if header:
            size = block_size_table.get(header.entity_type)
            if size:
                view = memoryview(data)[offset : offset + size]
                if len(view) == size:
                    if (
                        header.entity_type is EntityType.EMPTY_SLOT
                        and view != empty_slot_bytes
                    ):
                        header = None
                    if header:
                        return Entity(header=header, data=view)
        return None

    @classmethod
    def find_offset(
        cls,
        section: Section,
        data: ByteString,
        *,
        offset: int,
        required_non_empty_count: int,
        max_offset: int | None = None,
        step_size: int = 1,
    ) -> int | None:
        if step_size < 1:
            raise ValueError("step_size must be a positive integer.")
        if required_non_empty_count < 1:
            raise ValueError(
                "required_non_empty_count must be a positive integer."
            )
        while max_offset is None or offset <= max_offset:
            entry_offset = offset
            entries = 0
            while entry_offset < len(data) and (
                entry := cls.from_data(section, data, entry_offset)
            ):
                if entry.header.entity_type is not EntityType.EMPTY_SLOT:
                    entries += 1
                    if entries >= required_non_empty_count:
                        return offset
                entry_offset += len(entry.data)
            offset += step_size
        return None


@dataclass(frozen=True)
class RelicDebugData:
    metadata_offset: int
    metadata_data: ByteString
    inventory_offset: int
    inventory_data: ByteString

    def __str__(self) -> str:
        return os.linesep.join(
            [
                f"^ metadata offset: {self.metadata_offset}",
                f"  = {self.metadata_data.hex()}",
                f"^ inventory offset: {self.inventory_offset}",
                f"  = {self.inventory_data.hex()}",
            ]
        )


@dataclass(frozen=True)
class RelicData:
    inventory_id: int
    relic_id: int
    effect_ids: tuple[int, ...]
    curse_ids: tuple[int, ...]
    debug_data: RelicDebugData


@dataclass(frozen=True, kw_only=True)
class SaveData:
    _REQUIRED_NON_EMPTY_COUNT: ClassVar[int] = 5

    data: bytes = field(repr=False)
    title: str = ""

    @cached_property
    def metadata_offset(self) -> int | None:
        return Entity.find_offset(
            Section.METADATA,
            self.data,
            offset=0,
            max_offset=100,
            required_non_empty_count=type(self)._REQUIRED_NON_EMPTY_COUNT,
            step_size=2,
        )

    @cached_property
    def metadata_relic_table_and_end_offset(
        self,
    ) -> tuple[Mapping[int, RelicData], int | None]:
        offset = self.metadata_offset
        relics: dict[int, RelicData] = {}
        if offset is not None:
            view = memoryview(self.data)
            while entity := Entity.from_data(Section.METADATA, view, offset):
                if entity.header.entity_type is EntityType.RELIC:
                    relics[entity.header.inventory_id] = RelicData(
                        inventory_id=entity.header.inventory_id,
                        relic_id=cast(
                            int, struct.unpack_from("<I", entity.data, 4)[0]
                        )
                        & 0x7FFFFFFF,  # clear "flag-bit" for "equippable"
                        effect_ids=tuple(
                            effect_id
                            for effect_id in cast(
                                tuple[int, ...],
                                struct.unpack_from("<III", entity.data, 16),
                            )
                        ),
                        curse_ids=tuple(
                            effect_id
                            for effect_id in cast(
                                tuple[int, ...],
                                struct.unpack_from("<III", entity.data, 56),
                            )
                        ),
                        debug_data=RelicDebugData(
                            metadata_offset=offset,
                            metadata_data=entity.data,
                            inventory_offset=0,
                            inventory_data=b"",
                        ),
                    )
                else:
                    logger.debug(
                        "non-relic item encountered,"
                        f" type: {entity.header.entity_type.name}"
                    )
                offset += len(entity.data)
        return (MappingProxyType(relics), offset)

    @property
    def metadata_end_offset(self) -> int | None:
        return self.metadata_relic_table_and_end_offset[1]

    @property
    def metadata_relic_table(self) -> Mapping[int, RelicData]:
        return self.metadata_relic_table_and_end_offset[0]

    @cached_property
    def inventory_offset(self) -> int | None:
        offset = self.metadata_end_offset
        if offset is not None:
            return Entity.find_offset(
                Section.INVENTORY,
                self.data,
                offset=offset,
                required_non_empty_count=type(self)._REQUIRED_NON_EMPTY_COUNT,
                step_size=2,
            )
        return None

    @cached_property
    def relics(self) -> tuple[RelicData, ...]:
        relics: list[RelicData] = []
        offset = self.inventory_offset
        metadata_relic_table = self.metadata_relic_table
        if offset is not None:
            view = memoryview(self.data)
            while entity := Entity.from_data(Section.INVENTORY, view, offset):
                if entity.header.entity_type is EntityType.RELIC:
                    relic_data = metadata_relic_table.get(
                        entity.header.inventory_id
                    )
                    if relic_data:
                        relics.append(
                            RelicData(
                                inventory_id=entity.header.inventory_id,
                                relic_id=relic_data.relic_id,
                                effect_ids=relic_data.effect_ids,
                                curse_ids=relic_data.curse_ids,
                                debug_data=RelicDebugData(
                                    metadata_offset=(
                                        relic_data.debug_data.metadata_offset
                                    ),
                                    metadata_data=(
                                        relic_data.debug_data.metadata_data
                                    ),
                                    inventory_offset=offset,
                                    inventory_data=entity.data,
                                ),
                            )
                        )
                    else:
                        logger.error(
                            "skipping inventory relic with"
                            f" no metadata: {entity.header.inventory_id}"
                        )
                else:
                    logger.debug(
                        "non-relic entry encountered,"
                        f" type: {entity.header.entity_type}"
                    )
                offset += len(entity.data)
        return tuple(relics)


def load_save(data: ByteString, entry_name: str) -> SaveData:
    IV_LENGTH = 0x10
    for slot in bnd4.get_entries(data):
        logger.debug(f"encountered save slot named: {slot.name}")
        if not entry_name or slot.name == entry_name:
            decryptor = Cipher(
                algorithms.AES(
                    b"\x18\xf6\x32\x66\x05\xbd\x17\x8a"
                    b"\x55\x24\x52\x3a\xc0\xa0\xc6\x09"
                ),
                modes.CBC(slot.data[:IV_LENGTH]),
            ).decryptor()
            return SaveData(
                title=slot.name,
                data=(
                    decryptor.update(slot.data[IV_LENGTH:])
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
    DEEP_BLUE = "DeepBlue"
    DEEP_GREEN = "DeepGreen"
    DEEP_RED = "DeepRed"
    DEEP_YELLOW = "DeepYellow"

    UNKNOWN = "UNKNOWN"

    @property
    def alias(self) -> str:
        match self:
            case Color.BLUE | Color.DEEP_BLUE:
                return "Drizzly"
            case Color.GREEN | Color.DEEP_GREEN:
                return "Tranquil"
            case Color.RED | Color.DEEP_RED:
                return "Burning"
            case Color.YELLOW | Color.DEEP_YELLOW:
                return "Luminous"
            case Color.UNKNOWN:
                return "UNKNOWN"
        raise NotImplementedError()

    @property
    def is_deep(self) -> bool:
        return self in (
            Color.DEEP_BLUE,
            Color.DEEP_GREEN,
            Color.DEEP_RED,
            Color.DEEP_YELLOW,
        )


@dataclass(frozen=True, kw_only=True)
class Effect:
    _EMPTY_EFFECT_ID: ClassVar[int] = 0xFFFFFFFF
    name: str
    level: int
    id: int
    stackable: bool
    exclusive: str

    def __post_init__(self) -> None:
        if self.level < 0:
            raise AssertionError(f"Level is negative: {self.level}")

    @property
    def qualified_name(self) -> str:
        return f"{self.name} +{self.level}"

    @property
    def is_empty(self) -> bool:
        return self.id == type(self)._EMPTY_EFFECT_ID

    def __str__(self) -> str:
        if not self.level:
            return self.name
        return self.qualified_name


@dataclass(frozen=True)
class Relic:
    UNKNOWN_PREFIX: ClassVar[str] = "UNKNOWN_ID_"
    SIZE_NAMES: ClassVar[tuple[str, ...]] = ("Delicate", "Polished", "Grand")
    color: Color
    size: int
    name: str
    effects: tuple[Effect, ...]
    curses: tuple[Effect, ...]
    id: int
    debug_data: RelicDebugData  # only used for debugging

    def __post_init__(self) -> None:
        if len(self.effects) != len(self.curses):
            raise ValueError("number of effects and curses must match.")
        for i in range(len(self.effects)):
            if self.effects[i].is_empty and not self.curses[i].is_empty:
                raise ValueError("You can't have a curse on an empty effect.")
        if not (1 <= self.size <= len(type(self).SIZE_NAMES)):
            raise ValueError(f"Invalid size: {self.size}")

    @cached_property
    def effects_and_curses(self) -> tuple[Effect, ...]:
        return self.effects + self.curses

    @classmethod
    def standard_name(cls, color: Color, size: int) -> str:
        name = " ".join([cls.SIZE_NAMES[size - 1], color.alias, "Scene"])
        if color.is_deep:
            name = f"Deep {name}"
        return name

    @property
    def is_incomplete(self) -> bool:
        return self.name.startswith(type(self).UNKNOWN_PREFIX) or any(
            effect.name.startswith(type(self).UNKNOWN_PREFIX)
            for effect in chain(self.effects, self.curses)
        )

    def str_lines(self, *, color_prefix: bool = True) -> list[str]:
        lines: list[str] = []
        prefix = f"[{self.color}] " if color_prefix else ""
        lines.append(f"{TermStyle.BOLD}{prefix}{self.name}{TermStyle.RESET}")
        for i in range(len(self.effects)):
            effect = self.effects[i]
            curse = self.curses[i]
            if not effect.is_empty:
                lines.append(
                    f"{TermStyle.GREEN}  {effect}{TermStyle.RESET_COLOR}"
                )
                if not curse.is_empty:
                    lines.append(
                        f"{TermStyle.BLUE}  - {curse}{TermStyle.RESET_COLOR}"
                    )
        return lines

    def __str__(self) -> str:
        return os.linesep.join(self.str_lines())


@dataclass
class Database:
    @dataclass(frozen=True)
    class _RelicMetadata:
        color: Color
        size: int

    SIZE_NAMES: ClassVar[tuple[str, ...]] = ("Delicate", "Polished", "Grand")
    relic_id_to_info: dict[int, _RelicMetadata] = field(
        init=False, default_factory=dict
    )
    relic_names: dict[int, str] = field(init=False, default_factory=dict)
    effect_id_to_effect: dict[int, Effect] = field(
        init=False, default_factory=dict
    )

    def effects_as_dict(self) -> dict[str, dict[str, str | bool | int]]:
        output: dict[str, dict[str, str | bool | int]] = {}
        for id in sorted(self.effect_id_to_effect.keys()):
            effect = self.get_effect(id)
            entry: dict[str, str | bool | int] = {}
            entry["name"] = effect.name
            if effect.level:
                entry["level"] = effect.level
            entry["stackable"] = effect.stackable
            if effect.exclusive:
                entry["exclusive"] = effect.exclusive
            output[str(id)] = entry
        return output

    def items_as_dict(self) -> dict[str, dict[str, str | int]]:
        output: dict[str, dict[str, str | int]] = {}
        for id in sorted(self.relic_id_to_info.keys()):
            metadata = self.relic_id_to_info[id]
            entry: dict[str, str | int] = {}
            if name := self.relic_names.get(id, ""):
                entry["name"] = name
            entry["size"] = metadata.size
            entry["color"] = metadata.color.value
            output[str(id)] = entry
        return output

    def get_effect(self, id: int) -> Effect:
        info = self.effect_id_to_effect.get(id)
        if not info:
            return Effect(
                name=f"{Relic.UNKNOWN_PREFIX}EFFECT:{id}",
                level=0,
                id=id,
                stackable=False,
                exclusive="",
            )
        return info

    def get_relic(self, data: RelicData) -> Relic:
        info = self.relic_id_to_info.get(data.relic_id)
        effects = tuple(self.get_effect(id) for id in data.effect_ids)
        filled_effect_count = sum(
            1 for effect in effects if not effect.is_empty
        )

        if not info:
            return Relic(
                color=Color.UNKNOWN,
                size=len(data.effect_ids),
                name=f"{Relic.UNKNOWN_PREFIX}RELIC:{data.relic_id}",
                effects=tuple(self.get_effect(id) for id in data.effect_ids),
                curses=tuple(self.get_effect(id) for id in data.curse_ids),
                id=data.relic_id,
                debug_data=data.debug_data,
            )
        if info.size != filled_effect_count:
            logger.debug(str(data.debug_data))
            raise AssertionError(
                f"relic id {data.relic_id} is size {info.size} but has"
                f" {filled_effect_count} effects."
            )
        if info.size not in range(1, len(type(self).SIZE_NAMES) + 1):
            raise AssertionError(
                f"database has invalid size {info.size}"
                f" for relic id {data.relic_id}"
            )

        name = self.relic_names.get(data.relic_id, "")
        if not name:
            name = Relic.standard_name(info.color, info.size)
        return Relic(
            color=info.color,
            size=info.size,
            name=name,
            effects=tuple(self.get_effect(id) for id in data.effect_ids),
            curses=tuple(self.get_effect(id) for id in data.curse_ids),
            id=data.relic_id,
            debug_data=data.debug_data,
        )

    def __post_init__(self) -> None:
        effect_id_data: dict[str, dict[str, str | int | bool]] = json5.loads(
            get_resource_text("effects.json")
        )
        relic_id_data: dict[str, dict[str, str | int]] = json5.loads(
            get_resource_text("relic-ids.json")
        )

        for relic_id, attributes in relic_id_data.items():
            color_str = str(attributes["color"])
            try:
                color = Color(color_str)
            except KeyError:
                logger.error(f'Skipping {relic_id}: bad color "{color_str}"')
                continue

            size = int(attributes["size"])
            if not (1 <= size <= 3):
                logger.error(f'Skipping {relic_id}: bad size "{size}"')
                continue

            standard_name = Relic.standard_name(color, size)
            name = str(attributes.get("name", ""))
            if name != standard_name:
                self.relic_names[int(relic_id)] = name
            self.relic_id_to_info[int(relic_id)] = type(self)._RelicMetadata(
                color=color, size=size
            )

        for effect_id, effect_attributes in effect_id_data.items():
            effect_info = Effect(
                name=str(effect_attributes["name"]),
                level=int(effect_attributes.get("level", 0)),
                id=int(effect_id),
                stackable=bool(effect_attributes["stackable"]),
                exclusive=str(effect_attributes.get("exclusive", "")),
            )
            self.effect_id_to_effect[effect_info.id] = effect_info
            logger.debug(f"Added effect: {effect_id} {effect_info}")


@dataclass(eq=False)  # no eq so object provides hashability
class VesselTree:
    """
    Trie-like tree of color requirements. Each edge key is a Color or None
    (wildcard).  A node with no children is a leaf and represents a complete
    pattern.
    """

    name: str = field(init=False, default="")
    next: dict[Color | None, VesselTree] = field(
        init=False, default_factory=dict
    )

    name_to_colors: InitVar[dict[str, Sequence[Color | None]] | None] = None

    def __post_init__(
        self, name_to_colors: dict[str, Sequence[Color | None]] | None
    ) -> None:
        if name_to_colors:
            self.add(name_to_colors)

    def add_single(self, name: str, colors: Sequence[Color | None]) -> None:
        current = self
        for color in colors:
            try:
                next_tree = current.next[color]
            except KeyError:
                next_tree = VesselTree()
                current.next[color] = next_tree
            current = next_tree
        current.name = name

    def add(self, name_to_colors: dict[str, Sequence[Color | None]]) -> None:
        for name, colors in name_to_colors.items():
            self.add_single(name, colors)

    def get_permutations(
        self, relics: Sequence[Relic]
    ) -> Iterator[tuple[str, tuple[Relic | None, ...]]]:
        count = len(relics)
        positions_by_color: dict[Color, list[int]] = {}
        all_non_deep_positions: list[int] = []
        for position, relic in enumerate(relics):
            positions_by_color.setdefault(relic.color, []).append(position)
            if not relic.color.is_deep:
                all_non_deep_positions.append(position)
        position_in_use: list[bool] = [False] * count
        chosen_positions: list[int | None] = []

        def depth_first_search(
            current_node: VesselTree,
        ) -> Iterator[tuple[str, tuple[Relic | None, ...]]]:
            # Leaf → emit the concrete selection for this path.
            if current_node.name:
                yield (
                    current_node.name,
                    tuple(
                        relics[position] if position is not None else None
                        for position in chosen_positions
                    ),
                )
            # if not current_node.next:
            #    return
            # Deterministic traversal; None (wildcard) after concrete colors.
            for required_color in sorted(
                current_node.next.keys(),
                key=lambda key: (key is None, str(key)),
            ):
                child_node = current_node.next[required_color]
                if required_color is None:
                    candidate_positions = all_non_deep_positions
                else:
                    candidate_positions = positions_by_color.get(
                        required_color, []
                    )
                at_least_one = False
                for position in candidate_positions:
                    if position_in_use[position]:
                        continue
                    at_least_one = True
                    position_in_use[position] = True
                    chosen_positions.append(position)
                    yield from depth_first_search(child_node)
                    chosen_positions.pop()
                    position_in_use[position] = False
                if not at_least_one:
                    chosen_positions.append(None)
                    yield from depth_first_search(child_node)
                    chosen_positions.pop()

        yield from depth_first_search(self)


UNIVERSAL_VESSELS: dict[str, Sequence[Color | None]] = {
    "Sacred Erdtree Grail": (
        Color.YELLOW,
        Color.YELLOW,
        Color.YELLOW,
        Color.DEEP_YELLOW,
        Color.DEEP_YELLOW,
        Color.DEEP_YELLOW,
    ),
    "Spirit Shelter Grail": (
        Color.GREEN,
        Color.GREEN,
        Color.GREEN,
        Color.DEEP_GREEN,
        Color.DEEP_GREEN,
        Color.DEEP_GREEN,
    ),
    "Giant's Cradle Grail": (
        Color.BLUE,
        Color.BLUE,
        Color.BLUE,
        Color.DEEP_BLUE,
        Color.DEEP_BLUE,
        Color.DEEP_BLUE,
    ),
}
CLASS_VESSELS: dict[str, VesselTree] = {
    "universal": VesselTree(UNIVERSAL_VESSELS),
    "duchess": VesselTree(
        {
            "Duchess' Urn": (
                Color.RED,
                Color.BLUE,
                Color.BLUE,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
            ),
            "Duchess' Goblet": (
                Color.YELLOW,
                Color.YELLOW,
                Color.GREEN,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
                Color.DEEP_GREEN,
            ),
            "Duchess' Chalice": (
                Color.BLUE,
                Color.YELLOW,
                None,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Soot-Covered Duchess' Urn": (
                Color.RED,
                Color.RED,
                Color.GREEN,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_GREEN,
            ),
            "Sealed Duchess' Urn": (
                Color.BLUE,
                Color.BLUE,
                Color.RED,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
                Color.DEEP_YELLOW,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "executor": VesselTree(
        {
            "Executor's Urn": (
                Color.RED,
                Color.YELLOW,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
            "Executor's Goblet": (
                Color.RED,
                Color.BLUE,
                Color.GREEN,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
            ),
            "Executor's Chalice": (
                Color.BLUE,
                Color.YELLOW,
                None,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
                Color.DEEP_GREEN,
            ),
            "Soot-Covered Executor's Urn": (
                Color.RED,
                Color.RED,
                Color.BLUE,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
            ),
            "Sealed Executor's Urn": (
                Color.YELLOW,
                Color.YELLOW,
                Color.RED,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
                Color.DEEP_BLUE,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "guardian": VesselTree(
        {
            "Guardian's Urn": (
                Color.RED,
                Color.YELLOW,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
            "Guardian's Goblet": (
                Color.BLUE,
                Color.BLUE,
                Color.GREEN,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
            ),
            "Guardian's Chalice": (
                Color.BLUE,
                Color.YELLOW,
                None,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Soot-Covered Guardian's Urn": (
                Color.RED,
                Color.GREEN,
                Color.GREEN,
                Color.DEEP_RED,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
            ),
            "Sealed Guardian's Urn": (
                Color.YELLOW,
                Color.YELLOW,
                Color.RED,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
                Color.DEEP_BLUE,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "ironeye": VesselTree(
        {
            "Ironeye's Urn": (
                Color.YELLOW,
                Color.GREEN,
                Color.GREEN,
                Color.DEEP_YELLOW,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
            ),
            "Ironeye's Goblet": (
                Color.RED,
                Color.BLUE,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Ironeye's Chalice": (
                Color.RED,
                Color.GREEN,
                None,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_GREEN,
            ),
            "Soot-Covered Ironeye's Urn": (
                Color.BLUE,
                Color.YELLOW,
                Color.YELLOW,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
            "Sealed Ironeye's Urn": (
                Color.GREEN,
                Color.GREEN,
                Color.YELLOW,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_RED,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "raider": VesselTree(
        {
            "Raider's Urn": (
                Color.RED,
                Color.GREEN,
                Color.GREEN,
                Color.DEEP_RED,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
            ),
            "Raider's Goblet": (
                Color.RED,
                Color.BLUE,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Raider's Chalice": (
                Color.RED,
                Color.RED,
                None,
                Color.DEEP_RED,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
            "Soot-Covered Raider's Urn": (
                Color.BLUE,
                Color.BLUE,
                Color.GREEN,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
            ),
            "Sealed Raider's Urn": (
                Color.GREEN,
                Color.GREEN,
                Color.RED,
                Color.DEEP_YELLOW,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "recluse": VesselTree(
        {
            "Recluse's Urn": (
                Color.BLUE,
                Color.BLUE,
                Color.GREEN,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
            ),
            "Recluse's Goblet": (
                Color.RED,
                Color.BLUE,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Recluse's Chalice": (
                Color.YELLOW,
                Color.GREEN,
                None,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
            ),
            "Soot-Covered Recluse's Urn": (
                Color.RED,
                Color.RED,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_YELLOW,
            ),
            "Sealed Recluse's Urn": (
                Color.GREEN,
                Color.BLUE,
                Color.BLUE,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
                Color.DEEP_RED,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "revenant": VesselTree(
        {
            "Revenant's Urn": (
                Color.BLUE,
                Color.BLUE,
                Color.YELLOW,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Revenant's Goblet": (
                Color.RED,
                Color.RED,
                Color.GREEN,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_GREEN,
            ),
            "Revenant's Chalice": (
                Color.BLUE,
                Color.GREEN,
                None,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
                Color.DEEP_GREEN,
            ),
            "Soot-Covered Revenant's Urn": (
                Color.RED,
                Color.YELLOW,
                Color.YELLOW,
                Color.DEEP_RED,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
            "Sealed Revenant's Urn": (
                Color.YELLOW,
                Color.BLUE,
                Color.BLUE,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
                Color.DEEP_RED,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
    "wylder": VesselTree(
        {
            "Wylder's Urn": (
                Color.RED,
                Color.RED,
                Color.BLUE,
                Color.DEEP_RED,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
            ),
            "Wylder's Goblet": (
                Color.YELLOW,
                Color.GREEN,
                Color.GREEN,
                Color.DEEP_YELLOW,
                Color.DEEP_GREEN,
                Color.DEEP_GREEN,
            ),
            "Wylder's Chalice": (
                Color.RED,
                Color.YELLOW,
                None,
                Color.DEEP_RED,
                Color.DEEP_BLUE,
                Color.DEEP_GREEN,
            ),
            "Soot-Covered Wylder's Urn": (
                Color.BLUE,
                Color.BLUE,
                Color.YELLOW,
                Color.DEEP_BLUE,
                Color.DEEP_BLUE,
                Color.DEEP_YELLOW,
            ),
            "Sealed Wylder's Urn": (
                Color.BLUE,
                Color.RED,
                Color.RED,
                Color.DEEP_GREEN,
                Color.DEEP_YELLOW,
                Color.DEEP_YELLOW,
            ),
        }
        | UNIVERSAL_VESSELS
    ),
}
