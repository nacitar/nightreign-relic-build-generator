from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from functools import cached_property
from itertools import chain, permutations
from typing import ClassVar, Generator, Sequence

from .nightreign import RelicData
from .utility import get_resource_json

logger = logging.getLogger(__name__)


@unique
class Color(StrEnum):
    BLUE = "Blue"
    GREEN = "Green"
    RED = "Red"
    YELLOW = "Yellow"

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
        raise NotImplementedError()


@dataclass(frozen=True)
class RelicInfo:
    SIZE_NAMES: ClassVar[tuple[str, ...]] = ("Delicate", "Polished", "Grand")
    color: Color
    size: int

    def __post_init__(self) -> None:
        if self.size < 1 or self.size > len(type(self).SIZE_NAMES):
            raise AssertionError(f"Invalid relic size: {self.size}")

    @property
    def standard_name(self) -> str:
        return f"{type(self).SIZE_NAMES[self.size-1]} {self.color.alias} Scene"


# TODO: cache/pool these
@dataclass(frozen=True)
class EffectInfo:
    STACKABLE_REGEX: ClassVar[list[re.Pattern[str]]] = [
        re.compile(
            "^Improved .? (Attack Power|Resistance|Damage Negation|Incantations|Sorcery|Damage)( at (Low|Full) HP)?$"
        ),
        re.compile(
            "^(Dexterity|Endurance|Faith|Intelligence|Mind|Poise|Strength|Vigor|Arcane)$"
        ),
        re.compile(
            "^Improved (Guard Counters|Initial Standard Attack|Perfuming Arts|Roar & Breath Attacks|Stance-Breaking when .?)$"
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
        "^Starting armament (deals|inflicts) .?$"
    )
    STARTING_SKILL_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        "^Changes compatible armament's skill to .?$"
    )

    name: str
    level: int

    @cached_property
    def is_stackable(self) -> bool:
        for pattern in type(self).STACKABLE_REGEX:
            if pattern.match(self.name):
                return True
        return False

    @cached_property
    def is_starting_imbue(self) -> bool:
        return bool(type(self).STARTING_IMBUE_REGEX.match(self.name))

    @cached_property
    def is_starting_skill(self) -> bool:
        return bool(type(self).STARTING_SKILL_REGEX.match(self.name))

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
                color = Color[color_str.upper()]
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


UNIVERSAL_URNS: dict[str, tuple[Color | None, Color | None, Color | None]] = {
    "Sacred Erdtree Grail": (Color.YELLOW, Color.YELLOW, Color.YELLOW),
    "Spirit Shelter Grail": (Color.GREEN, Color.GREEN, Color.GREEN),
    "Giant's Cradle Grail": (Color.BLUE, Color.BLUE, Color.BLUE),
}

RAIDER_URNS: dict[str, tuple[Color | None, Color | None, Color | None]] = {
    "Raider's Urn": (Color.RED, Color.GREEN, Color.GREEN),
    "Raider's Goblet": (Color.RED, Color.BLUE, Color.YELLOW),
    "Raider's Chalice": (Color.RED, Color.RED, None),
    "Soot-Covered Raider's Urn": (Color.BLUE, Color.BLUE, Color.GREEN),
    "Sealed Raider's Urn": (Color.GREEN, Color.GREEN, Color.RED),
}

GUARDIAN_URNS: dict[str, tuple[Color | None, Color | None, Color | None]] = {
    "Guardian's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
    "Guardian's Goblet": (Color.BLUE, Color.BLUE, Color.GREEN),
    "Guardian's Chalice": (Color.BLUE, Color.YELLOW, None),
    "Soot-Covered Guardian's Urn": (Color.RED, Color.GREEN, Color.GREEN),
    "Sealed Guardian's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
}

EXECUTOR_URNS: dict[str, tuple[Color | None, Color | None, Color | None]] = {
    "Executor's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
    "Executor's Goblet": (Color.RED, Color.BLUE, Color.GREEN),
    "Executor's Chalice": (Color.BLUE, Color.YELLOW, None),
    "Soot-Covered Executor's Urn": (Color.RED, Color.RED, Color.BLUE),
    "Sealed Executor's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
}


@dataclass
class RelicProcessor:
    database: RelicDatabase
    score_table: dict[str, int]

    def get_effect_score(self, effect_ids: Sequence[int]) -> int:
        # doesn't score things that don't stack
        # TODO: keep up with skill/imbue?  factor in class.
        total_score = 0
        seen: set[str] = set()
        has_starting_imbue = False
        has_starting_skill = False
        for effect_id in effect_ids:
            info = self.database.effect_id_to_info.get(effect_id)
            if info is None:
                logger.warning(f"Unknown effect id: {effect_id}")
            elif info.is_stackable or info.name not in seen:
                seen.add(info.name)
                if (not info.is_starting_imbue or not has_starting_imbue) and (
                    not info.is_starting_skill or not has_starting_skill
                ):
                    has_starting_imbue |= info.is_starting_imbue
                    has_starting_skill |= info.is_starting_skill
                    if score := self.score_table.get(info.name, 0):
                        total_score += score + info.level
        return total_score

    def get_relic_score(self, relic: RelicData) -> int:
        return self.get_effect_score(relic.effect_ids)

    def relic_permutations(
        self,
        relics: Sequence[RelicData],
        target_urns: dict[
            str, tuple[Color | None, Color | None, Color | None]
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
            build_colors: list[Color | None] = []
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
                tuple[Color | None, Color | None, Color | None]
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
            if not possibilities.isdisjoint(urn_color_set):
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
        logger.info(f"Listed {count} relics.")
