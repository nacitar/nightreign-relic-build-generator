from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import ClassVar, Sequence

from .nightreign import RelicData
from .utility import get_resource_json

logger = logging.getLogger(__name__)


@unique
class RelicColor(StrEnum):
    BLUE = "Blue"
    GREEN = "Green"
    RED = "Red"
    YELLOW = "Yellow"

    @property
    def alias(self) -> str:
        match self:
            case RelicColor.BLUE:
                return "Drizzly"
            case RelicColor.GREEN:
                return "Tranquil"
            case RelicColor.RED:
                return "Burning"
            case RelicColor.YELLOW:
                return "Luminous"
        raise NotImplementedError()


@dataclass(frozen=True)
class RelicInfo:
    SIZE_NAMES: ClassVar[tuple[str, ...]] = ("Delicate", "Polished", "Grand")
    color: RelicColor
    size: int

    def __post_init__(self) -> None:
        if self.size < 1 or self.size > len(type(self).SIZE_NAMES):
            raise AssertionError(f"Invalid relic size: {self.size}")

    @property
    def standard_name(self) -> str:
        return f"{type(self).SIZE_NAMES[self.size-1]} {self.color.alias} Scene"


@dataclass
class EffectInfo:
    name: str
    level: int

    def __post_init__(self) -> None:
        if self.level < 0:
            raise AssertionError("Level is negative: {self.level}")

    def __str__(self) -> str:
        if not self.level:
            return self.name
        return f"{self.name} +{self.level}"


@dataclass
class RelicDatabase:
    relic_id_to_info: dict[int, RelicInfo] = field(
        init=False, default_factory=dict
    )
    relic_names: dict[int, str] = field(init=False, default_factory=dict)
    effect_id_to_info: dict[int, EffectInfo] = field(
        init=False, default_factory=dict
    )

    def load_from_save_editor(self) -> None:
        effect_data: dict[str, dict[str, str]] = get_resource_json(
            "effects.json"
        )
        item_data: dict[str, dict[str, str]] = get_resource_json("items.json")

        for item_id, attributes in item_data.items():
            color_str = attributes.get("color", "")
            try:
                color = RelicColor[color_str.upper()]
            except KeyError:
                logger.error(f'Skipping {item_id}: bad color "{color_str}"')
                continue

            name = attributes.get("name", "")
            if not name:
                logger.error(f"Skipping {item_id}: no name provided")
                continue
            try:
                size = RelicInfo.SIZE_NAMES.index(name.split(" ", 1)[0]) + 1
            except ValueError:
                size = 3
                logger.debug(f"Assuming {item_id} has {size} effects: {name}")
            relic_info = RelicInfo(color=color, size=size)
            self.relic_id_to_info[int(item_id)] = relic_info
            if relic_info.standard_name != name:
                self.relic_names[int(item_id)] = name
                logger.debug(f"Non-standard name: {name}")

        # TODO: process effects
        suffix_pattern = re.compile(r" \+(?P<level>\d+)$")
        for effect_id, attributes in effect_data.items():
            name = attributes["name"]
            level = 0
            if match := suffix_pattern.search(name):
                level = int(match.group("level"))
                name = name[: match.start()]

            effect_info = EffectInfo(name, level)
            self.effect_id_to_info[int(effect_id)] = effect_info
            logger.debug(f"Added effect: {effect_id} {effect_info}")

    def __post_init__(self) -> None:
        self.load_from_save_editor()

        # for effect_id, attributes in self.effect_data.items():
        #    if attributes["color"]


@dataclass
class RelicProcessor:
    database: RelicDatabase

    def relic_report(self, relics: Sequence[RelicData]) -> None:
        count = 0
        for relic in relics:
            count += 1
            relic_info = self.database.relic_id_to_info.get(relic.item_id)
            if relic_info:
                name = self.database.relic_names.get(relic.item_id)
                if not name:
                    name = relic_info.standard_name
                print(f"RELIC {relic.item_id}: [{relic_info.color}] {name}")
            else:
                print(f"MISSING RELIC: couldn't find id {relic.item_id}")

            for effect_id in relic.effect_ids:
                effect_info = self.database.effect_id_to_info.get(effect_id)
                if effect_info:
                    print(f"- {effect_info}")
                else:
                    print(f"- WARNING: couldn't find effect id {effect_id}")
        print(f"==== Listed {count} relics. ====")
