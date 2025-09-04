from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from heapq import heappush, heapreplace
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
class Effect:
    name: str
    level: int
    is_stackable: bool
    is_starting_imbue: bool
    is_starting_skill: bool

    def __str__(self) -> str:
        if not self.level:
            return self.name
        return f"{self.name} +{self.level}"


@dataclass(frozen=True)
class Relic:
    color: Color
    size: int
    name: str
    effects: tuple[Effect, ...]


@dataclass(frozen=True)
class RelicMetadata:
    color: Color
    size: int


@dataclass(frozen=True)
class EffectMetadata:
    name: str
    level: int

    def __post_init__(self) -> None:
        if self.level < 0:
            raise AssertionError(f"Level is negative: {self.level}")


@dataclass
class RelicDatabase:
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
    relic_id_to_info: dict[int, RelicMetadata] = field(
        init=False, default_factory=dict
    )
    relic_names: dict[int, str] = field(init=False, default_factory=dict)
    effect_id_to_info: dict[int, EffectMetadata] = field(
        init=False, default_factory=dict
    )

    def get_effect(self, id: int) -> Effect:
        info = self.effect_id_to_info.get(id)
        if not info:
            raise ValueError(f"database has no effect with id: {id}")
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
            raise ValueError(f"database has no relic with id {data.item_id}")
        if info.size != len(data.effect_ids):
            raise ValueError(
                f"relic id {data.item_id} is size {info.size} but has"
                f" {len(data.effect_ids)} effects."
            )
        if info.size not in range(1, len(type(self).SIZE_NAMES) + 1):
            raise ValueError(
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
            self.relic_id_to_info[int(item_id)] = RelicMetadata(
                color=color, size=size
            )

        suffix_pattern = re.compile(r" \+(?P<level>\d+)$")
        for effect_id, attributes in effect_data.items():
            name = attributes["name"]
            level = 0
            if match := suffix_pattern.search(name):
                level = int(match.group("level"))
                name = name[: match.start()]

            effect_info = EffectMetadata(name, level)
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
    "raider": {
        "Raider's Urn": (Color.RED, Color.GREEN, Color.GREEN),
        "Raider's Goblet": (Color.RED, Color.BLUE, Color.YELLOW),
        "Raider's Chalice": (Color.RED, Color.RED, None),
        "Soot-Covered Raider's Urn": (Color.BLUE, Color.BLUE, Color.GREEN),
        "Sealed Raider's Urn": (Color.GREEN, Color.GREEN, Color.RED),
    },
    "guardian": {
        "Guardian's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
        "Guardian's Goblet": (Color.BLUE, Color.BLUE, Color.GREEN),
        "Guardian's Chalice": (Color.BLUE, Color.YELLOW, None),
        "Soot-Covered Guardian's Urn": (Color.RED, Color.GREEN, Color.GREEN),
        "Sealed Guardian's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
    },
    "executor": {
        "Executor's Urn": (Color.RED, Color.YELLOW, Color.YELLOW),
        "Executor's Goblet": (Color.RED, Color.BLUE, Color.GREEN),
        "Executor's Chalice": (Color.BLUE, Color.YELLOW, None),
        "Soot-Covered Executor's Urn": (Color.RED, Color.RED, Color.BLUE),
        "Sealed Executor's Urn": (Color.YELLOW, Color.YELLOW, Color.RED),
    },
}


@dataclass
class BuildHeap:
    """Best by score, deduping on (score, set[Relic], set[Effect]."""

    max_size: int

    @dataclass(order=True)
    class _Entry:
        score: int
        build: Build = field(compare=False)

    _Signature = tuple[int, frozenset[Relic], frozenset[Effect]]

    _heap: list[_Entry] = field(default_factory=list, init=False, repr=False)
    _signatures: set[_Signature] = field(
        default_factory=set, init=False, repr=False
    )

    def _signature(self, build: Build) -> _Signature:
        return (
            build.score,
            frozenset(build.relics),
            frozenset(build.active_effects),
        )

    def consider(self, build: Build) -> None:
        sig = self._signature(build)
        if sig in self._signatures:
            return
        entry = self._Entry(build.score, build)
        if len(self._heap) < self.max_size:
            heappush(self._heap, entry)
            self._signatures.add(sig)
            return
        if build.score > self._heap[0].score:  # strictly better replaces
            evicted = heapreplace(self._heap, entry)
            self._signatures.discard(self._signature(evicted.build))
            self._signatures.add(sig)

    def results_desc(self) -> list[Build]:
        return [
            e.build
            for e in sorted(self._heap, key=lambda e: e.score, reverse=True)
        ]


@dataclass(frozen=True)
class ScoredEffects:
    active_effects: tuple[Effect, ...]
    score: int


@dataclass(frozen=True)
class Build(ScoredEffects):
    relics: tuple[Relic, ...]


@dataclass
class RelicProcessor:
    database: RelicDatabase

    def get_scored_effects(
        self, effects: Sequence[Effect], *, score_table: dict[str, int]
    ) -> ScoredEffects:
        score = 0
        seen: set[str] = set()
        active: list[Effect] = []
        has_starting_imbue = False
        has_starting_skill = False
        for effect in effects:
            if effect.is_stackable or effect.name not in seen:
                seen.add(effect.name)
                if (
                    not effect.is_starting_imbue or not has_starting_imbue
                ) and (not effect.is_starting_skill or not has_starting_skill):
                    has_starting_imbue |= effect.is_starting_imbue
                    has_starting_skill |= effect.is_starting_skill
                    active.append(effect)
                    score += score_table.get(effect.name, 0) * (
                        effect.level + 1
                    )
        return ScoredEffects(active_effects=tuple(active), score=score)

    def builds(
        self,
        relics: Sequence[Relic],
        urns: set[tuple[Color | None, Color | None, Color | None]],
        *,
        score_table: dict[str, int],
        prune: int,
    ) -> Generator[Build, None, None]:
        for combination in self.relic_permutations(
            relics, urns, score_table=score_table, prune=prune
        ):
            scored_effects = self.get_scored_effects(
                [effect for relic in combination for effect in relic.effects],
                score_table=score_table,
            )
            yield Build(
                active_effects=scored_effects.active_effects,
                score=scored_effects.score,
                relics=combination,
            )

    def top_builds(
        self,
        relics: Sequence[Relic],
        urns: set[tuple[Color | None, Color | None, Color | None]],
        *,
        score_table: dict[str, int],
        count: int,
        prune: int,
    ) -> list[Build]:
        top = BuildHeap(count)
        for build in self.builds(
            relics=relics, urns=urns, score_table=score_table, prune=prune
        ):
            top.consider(build)
        return top.results_desc()

    def relic_permutations(
        self,
        relics: Sequence[Relic],
        urns: set[tuple[Color | None, Color | None, Color | None]],
        *,
        score_table: dict[str, int],
        prune: int,
    ) -> Generator[tuple[Relic, ...], None, None]:
        # first, score and prune out any relics that provide no value
        relics = [
            relic
            for relic in relics
            if self.get_scored_effects(
                relic.effects, score_table=score_table
            ).score
            >= prune
        ]
        logger.info(f"Relics left after pruning: {len(relics)}")
        # TODO: calculate number of combinations?  progress bar?

        for build in chain.from_iterable(
            permutations(relics, r) for r in range(1, min(3, len(relics)) + 1)
        ):
            missing_data = False
            build_colors: list[Color | None] = [relic.color for relic in build]

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
            if not possibilities.isdisjoint(urns):
                yield build

    def relic_report(self, relics: Sequence[Relic]) -> None:
        for relic in relics:
            print(f"[{relic.color}] {relic.name}")
            for effect in relic.effects:
                print(f"- {effect}")
        logger.info(f"Listed {len(relics)} relics.")
