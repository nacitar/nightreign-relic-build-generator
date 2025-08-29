from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from itertools import chain, permutations
from typing import ClassVar, Generator, Sequence

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


RAIDER_URNS: dict[
    str, tuple[RelicColor | None, RelicColor | None, RelicColor | None]
] = {
    "Raider's Urn": (RelicColor.RED, RelicColor.GREEN, RelicColor.GREEN),
    "Raider's Goblet": (RelicColor.RED, RelicColor.BLUE, RelicColor.YELLOW),
    "Raider's Chalice": (RelicColor.RED, RelicColor.RED, None),
    "Soot-Covered Raider's Urn": (
        RelicColor.BLUE,
        RelicColor.BLUE,
        RelicColor.GREEN,
    ),
    "Sealed Raider's Urn": (
        RelicColor.GREEN,
        RelicColor.GREEN,
        RelicColor.RED,
    ),
    "Sacred Erdtree Grail": (
        RelicColor.YELLOW,
        RelicColor.YELLOW,
        RelicColor.YELLOW,
    ),
    "Spirit Shelter Grail": (
        RelicColor.GREEN,
        RelicColor.GREEN,
        RelicColor.GREEN,
    ),
    "Giant's Cradle Grail": (
        RelicColor.BLUE,
        RelicColor.BLUE,
        RelicColor.BLUE,
    ),
}


@dataclass
class RelicProcessor:
    database: RelicDatabase
    score_table: dict[str, int]

    def get_effect_score(self, effect_ids: Sequence[int]) -> int:
        # TODO: eliminate duplicates/incompatible things/doesn't stack/...
        total_score = 0
        for effect_id in effect_ids:
            effect_info = self.database.effect_id_to_info.get(effect_id)
            if not effect_info:
                logger.warning(f"Skipping unknown effect: {effect_id}")
                continue
            effect_score = self.score_table.get(effect_info.name, 0)
            if effect_score:
                effect_score += effect_info.level
            total_score += effect_score
        return total_score

    def get_relic_score(self, relic: RelicData) -> int:
        return self.get_effect_score(relic.effect_ids)

    def relic_permutations(
        self,
        relics: Sequence[RelicData],
        target_urns: dict[
            str, tuple[RelicColor | None, RelicColor | None, RelicColor | None]
        ],
        *,
        minimum_per_relic: int = 1,
    ) -> Generator[tuple[RelicData, ...], None, None]:
        # first, score and prune out any relics that provide no value

        pruned: list[RelicData] = []
        for relic in relics:
            score = self.get_relic_score(relic)
            if score >= minimum_per_relic:
                pruned.append(relic)

        urn_color_set = set(target_urns.values())
        print(f"Pruned: {len(pruned)}")

        for build in chain.from_iterable(
            permutations(pruned, r) for r in range(1, min(3, len(pruned)) + 1)
        ):
            missing_data = False
            build_colors: list[RelicColor | None] = []
            for relic in build:
                relic_info = self.database.relic_id_to_info.get(relic.item_id)
                if not relic_info:
                    logger.error(f"Missing info for relic: {relic.item_id}")
                    missing_data = True
                    break
                build_colors.append(relic_info.color)
            if missing_data:
                continue
            while len(build_colors) < 3:
                build_colors.append(None)

            if len(build_colors) != 3:
                raise AssertionError()

            possibilities: set[
                tuple[RelicColor | None, RelicColor | None, RelicColor | None]
            ] = {
                (build_colors[0], build_colors[1], build_colors[2]),
                (build_colors[0], build_colors[1], None),
                (build_colors[0], None, build_colors[2]),
                (None, build_colors[1], build_colors[2]),
                (build_colors[0], None, None),
                (None, build_colors[1], None),
                (None, None, build_colors[2]),
                (None, None, None),
            }
            if possibilities & urn_color_set:
                yield build

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
