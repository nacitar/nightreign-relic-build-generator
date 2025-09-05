from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from heapq import heappush, heapreplace
from itertools import chain, permutations
from typing import Generator, Iterable, Sequence

from tqdm import tqdm

from .nightreign import Color, Effect, Relic

logger = logging.getLogger(__name__)


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

    def __str__(self) -> str:
        lines: list[str] = []
        lines.append(f"SCORE: {self.score}")
        for relic in self.relics:
            lines.append(str(relic))
        return os.linesep.join(lines)


def get_scored_effects(
    effects: Sequence[Effect], *, score_table: dict[str, int]
) -> ScoredEffects:
    score = 0
    seen: set[tuple[str, int]] = set()
    active: list[Effect] = []
    has_starting_imbue = False
    has_starting_skill = False
    for effect in effects:
        seen_key = (effect.name, effect.level)
        if effect.is_stackable or seen_key not in seen:
            seen.add(seen_key)
            if (not effect.is_starting_imbue or not has_starting_imbue) and (
                not effect.is_starting_skill or not has_starting_skill
            ):
                has_starting_imbue |= effect.is_starting_imbue
                has_starting_skill |= effect.is_starting_skill
                active.append(effect)
                # allow for scores from "EffectName +N" specifically
                effect_score = score_table.get(effect.qualified_name.lower())
                if effect_score is None:
                    # fallback to general score for "EffectName"
                    effect_score = score_table.get(effect.name.lower(), 0) * (
                        effect.level + 1
                    )
                score += effect_score
    return ScoredEffects(active_effects=tuple(active), score=score)


def get_relic_permutations(
    relics: Sequence[Relic],
    urns: set[tuple[Color | None, Color | None, Color | None]],
    *,
    score_table: dict[str, int],
    prune: int,
    progress_bar: bool,
) -> Generator[tuple[Relic, ...], None, None]:
    # first, score and prune out any relics that provide no value
    relics = [
        relic
        for relic in relics
        if get_scored_effects(relic.effects, score_table=score_table).score
        >= prune
    ]
    count = len(relics)
    logger.info(f"Relics left after pruning: {count}")
    rng = range(1, min(3, count) + 1)

    iterable: Iterable[tuple[Relic, ...]] = chain.from_iterable(
        permutations(relics, r) for r in rng
    )
    if progress_bar:
        # 'total' passed for a finite progress bar; it needs to know the count
        iterable = tqdm(iterable, total=sum(math.perm(count, r) for r in rng))

    for build in iterable:
        build_colors: list[Color | None] = [relic.color for relic in build]

        while len(build_colors) < 3:
            build_colors.append(None)

        if len(build_colors) != 3:
            raise AssertionError()

        if not urns.isdisjoint(
            {
                (build_colors[0], build_colors[1], build_colors[2]),
                (build_colors[0], build_colors[1], None),
                (build_colors[0], None, build_colors[2]),
                (None, build_colors[1], build_colors[2]),
                (build_colors[0], None, None),
                (None, build_colors[1], None),
                (None, None, build_colors[2]),
                (None, None, None),
            }
        ):
            yield build


def get_builds(
    relics: Sequence[Relic],
    urns: set[tuple[Color | None, Color | None, Color | None]],
    *,
    score_table: dict[str, int],
    prune: int,
    minimum: int,
    progress_bar: bool,
) -> Generator[Build, None, None]:
    for combination in get_relic_permutations(
        relics,
        urns,
        score_table=score_table,
        prune=prune,
        progress_bar=progress_bar,
    ):
        scored_effects = get_scored_effects(
            [effect for relic in combination for effect in relic.effects],
            score_table=score_table,
        )
        if scored_effects.score >= minimum:
            yield Build(
                active_effects=scored_effects.active_effects,
                score=scored_effects.score,
                relics=combination,
            )


def get_top_builds(
    relics: Sequence[Relic],
    urns: set[tuple[Color | None, Color | None, Color | None]],
    *,
    score_table: dict[str, int],
    count: int,
    prune: int,
    minimum: int,
    progress_bar: bool,
) -> list[Build]:
    top = BuildHeap(count)
    for build in get_builds(
        relics=relics,
        urns=urns,
        score_table=score_table,
        prune=prune,
        minimum=minimum,
        progress_bar=progress_bar,
    ):
        top.consider(build)
    return top.results_desc()
