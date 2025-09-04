from __future__ import annotations

import logging
from dataclasses import dataclass, field
from heapq import heappush, heapreplace
from itertools import chain, permutations
from typing import Generator, Sequence

from .nightreign import Color, Effect, Relic

logger = logging.getLogger(__name__)


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
