from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from heapq import heappush, heapreplace
from typing import Literal, NamedTuple, Sequence, Union

from .nightreign import Color, Effect, Relic, UrnTree

logger = logging.getLogger(__name__)


@dataclass
class BuildHeap:
    """Best by score, deduping on (score, set[Relic], set[Effect]."""

    max_size: int

    @dataclass(order=True)
    class _Entry:
        score: int
        build: Build = field(compare=False)

    _Signature = tuple[int, frozenset[Relic | None], frozenset[Effect]]

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
    urn_name: str
    relics: tuple[Relic | None, ...]

    def __str__(self) -> str:
        lines: list[str] = []
        lines.append(f"SCORE: {self.score} - {self.urn_name}")
        for relic in self.relics:
            if relic is not None:
                lines.append(str(relic))
            else:
                lines.append("<Empty Relic Slot>")
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


# TODO
################################################
# EVERYTHING BELOW IS FROM GPT AND WILL BE
# UNDERSTOOD, CLEANED UP AND IMPROVED OVER TIME.
################################################

# -------------------------------
# Incremental scorer (order-aware)
# -------------------------------

# assume Effect and Relic are your types


class ScoreChange(NamedTuple):
    kind: Literal["score"]
    value: int


class SeenChange(NamedTuple):
    kind: Literal["seen"]
    value: tuple[str, int]


class ImbueFlagChange(NamedTuple):
    kind: Literal["imbue_flag"]


class SkillFlagChange(NamedTuple):
    kind: Literal["skill_flag"]


class PushEffectChange(NamedTuple):
    kind: Literal["push_effect"]
    value: Effect


Change = Union[
    ScoreChange, SeenChange, ImbueFlagChange, SkillFlagChange, PushEffectChange
]


@dataclass
class IncrementalScorer:
    """Maintain 'seen' state + running score with push/pop for backtracking."""

    score_table: dict[str, int]

    current_score: int = 0
    seen_keys: set[tuple[str, int]] = field(default_factory=set)
    has_starting_imbue: bool = False
    has_starting_skill: bool = False
    active_effects_stack: list[Effect] = field(default_factory=list)

    _change_log: list[Change] = field(default_factory=list)

    def _score_of(self, effect: Effect) -> int:
        q = effect.qualified_name.lower()
        direct = self.score_table.get(q)
        if direct is not None:
            return direct
        base = self.score_table.get(effect.name.lower(), 0)
        return base * (effect.level + 1)

    def push_relic(self, relic: Relic) -> int:
        """Apply relic effects in order; return delta added now."""
        delta = 0
        for effect in relic.effects:
            seen_key = (effect.name, effect.level)

            if (not effect.is_stackable) and (seen_key in self.seen_keys):
                continue
            if effect.is_starting_imbue and self.has_starting_imbue:
                continue
            if effect.is_starting_skill and self.has_starting_skill:
                continue

            if not effect.is_stackable:
                self.seen_keys.add(seen_key)
                self._change_log.append(SeenChange("seen", seen_key))

            if effect.is_starting_imbue and not self.has_starting_imbue:
                self.has_starting_imbue = True
                self._change_log.append(ImbueFlagChange("imbue_flag"))

            if effect.is_starting_skill and not self.has_starting_skill:
                self.has_starting_skill = True
                self._change_log.append(SkillFlagChange("skill_flag"))

            s = self._score_of(effect)
            if s:
                delta += s
                self.current_score += s
                self._change_log.append(ScoreChange("score", s))

            # keep effect to reflect order/blocks even if s == 0
            self.active_effects_stack.append(effect)
            self._change_log.append(PushEffectChange("push_effect", effect))

        return delta

    def push_context(self) -> int:
        """Mark a boundary; returns a token to pop back to."""
        return len(self._change_log)

    def pop_context(self, token: int) -> None:
        """Undo to the provided token (exactly reverse changes)."""
        while len(self._change_log) > token:
            change = self._change_log.pop()
            if isinstance(change, ScoreChange):
                self.current_score -= change.value
            elif isinstance(change, SeenChange):
                self.seen_keys.remove(change.value)
            elif isinstance(change, ImbueFlagChange):
                self.has_starting_imbue = False
            elif isinstance(change, SkillFlagChange):
                self.has_starting_skill = False
            elif isinstance(change, PushEffectChange):
                self.active_effects_stack.pop()
            else:
                raise AssertionError("unreachable")

    @property
    def active_effects(self) -> tuple[Effect, ...]:
        return tuple(self.active_effects_stack)


# -------------------------------------------------
# Fast search: branch-and-bound with upper bounding
# -------------------------------------------------


def get_top_builds(
    relics: Sequence[Relic],
    urn_tree: UrnTree,
    *,
    score_table: dict[str, int],
    count: int,
    prune: int,
    minimum: int,
    progress_bar: bool = False,
) -> list[Build]:
    """
    Branch-and-bound search that integrates scoring while walking the UrnTree.
    This aggressively prunes and returns top K builds much faster.
    """
    # 0) Filter useless relics (same as your get_builds)
    filtered_relics: list[Relic] = []
    standalone_score_cache: list[int] = []
    for relic in relics:
        # quick score for pruning/filtering: how much this relic
        # can contribute alone
        se = get_scored_effects(relic.effects, score_table=score_table)
        if se.score >= prune:
            filtered_relics.append(relic)
            standalone_score_cache.append(se.score)

    # 1) Index candidates by color and also all non-deep for wildcard
    positions_by_color: dict[Color, list[int]] = {}
    all_non_deep_positions: list[int] = []
    for index, relic in enumerate(filtered_relics):
        positions_by_color.setdefault(relic.color, []).append(index)
        if not relic.color.is_deep:
            all_non_deep_positions.append(index)

    # 2) Pre-sorted candidate orders to try high-value relics first
    for lst in positions_by_color.values():
        lst.sort(key=lambda i: standalone_score_cache[i], reverse=True)
    all_non_deep_positions.sort(
        key=lambda i: standalone_score_cache[i], reverse=True
    )

    # 3) Heap for top results + signature set (same behavior as your BuildHeap)
    top = BuildHeap(count)

    # 4) Integrated DFS with scoring + bound
    used: list[bool] = [False] * len(filtered_relics)
    chosen_indices: list[int | None] = []
    scorer = IncrementalScorer(score_table)

    # Cheap optimistic bound: sum of the best "standalone" scores
    # for remaining slots using available (unused) relics.
    # This *overestimates* (good for pruning safety).
    def optimistic_bound(
        remaining_slots: int,
        wildcard: bool,
        required_color: Color | None = None,
    ) -> int:
        if remaining_slots <= 0:
            return 0
        # gather candidate indices
        if wildcard:
            pool = [i for i in all_non_deep_positions if not used[i]]
        else:
            assert required_color is not None
            pool = [
                i
                for i in positions_by_color.get(required_color, [])
                if not used[i]
            ]

        if not pool:
            return 0

        # Take the top-k scores from pool (k = remaining_slots), sum them.
        # This ignores future conflicts and order -> safe upper bound.
        # If pool is smaller than remaining_slots, just sum all.
        best_scores: list[int] = []
        taken = 0
        for i in pool:
            best_scores.append(standalone_score_cache[i])
            taken += 1
            if taken >= remaining_slots:
                break
        return sum(best_scores)

    # We also want a quick multi-slot bound for a *path of slots*.
    # For simplicity, we compute “best k among all non-deep unused”
    # (wildcard path), and for fixed-color paths we do it per-step.
    def path_bound(node: UrnTree, depth_from_here: int) -> int:
        """
        Very cheap bound: treat each step independently, sum upper-bounds.
        This is intentionally optimistic and fast.
        """
        if depth_from_here <= 0:
            return 0
        total = 0
        # Traverse at most 'depth_from_here' levels greedily on keys that
        # exist, adding the best possible contribution each time.
        # NOTE: This is a simplification — we do not enumerate all
        # branches for bound.
        # We just add a big “best next step” repeatedly to stay cheap.
        levels = 0
        nodes_to_consider = [node]
        # We pessimistically limit to depth_from_here steps; the trie might
        # branch, but bound can stay optimistic by assuming the best branch
        # each time.
        while nodes_to_consider and levels < depth_from_here:
            next_nodes: list[UrnTree] = []
            # among all edges at this "level", pick the best possible
            # single-step bound
            step_best = 0
            seen_any = False
            for nd in nodes_to_consider:
                for required_color in nd.next.keys():
                    seen_any = True
                    if required_color is None:
                        step_best = max(
                            step_best, optimistic_bound(1, wildcard=True)
                        )
                    else:
                        step_best = max(
                            step_best,
                            optimistic_bound(
                                1,
                                wildcard=False,
                                required_color=required_color,
                            ),
                        )
                    next_nodes.append(nd.next[required_color])
            if not seen_any:
                break
            total += step_best
            nodes_to_consider = next_nodes
            levels += 1
        return total

    def remaining_depth(node: UrnTree) -> int:
        """
        Longest path to a leaf from this node
        (small int; your urns are length 6).
        """
        if not node.next:
            return 0
        return 1 + max(remaining_depth(child) for child in node.next.values())

    # cache remaining depths per node so we do not recompute
    _remaining_depth_cache: dict[UrnTree, int] = {}

    def depth_cached(node: UrnTree) -> int:
        d = _remaining_depth_cache.get(node)
        if d is not None:
            return d
        d = remaining_depth(node)
        _remaining_depth_cache[node] = d
        return d

    def depth_first_search(node: UrnTree) -> None:
        # If this node names a completed urn, consider the current
        # partial build too.
        if node.name:
            current_build = Build(
                urn_name=node.name,
                active_effects=scorer.active_effects,
                score=scorer.current_score,
                relics=tuple(
                    (filtered_relics[i] if i is not None else None)
                    for i in chosen_indices
                ),
            )
            if current_build.score >= minimum:
                top.consider(current_build)

        if not node.next:
            return

        # compute a quick bar for pruning
        bar = minimum
        if len(top._heap) == top.max_size:
            bar = max(bar, top._heap[0].score)

        # upper bound for this subtree
        rem_depth = depth_cached(node)
        optimistic_future = path_bound(node, rem_depth)
        if scorer.current_score + optimistic_future < bar:
            return  # prune: even best case cannot beat current bar

        # Traverse deterministically: concrete colors first, then wildcard
        for required_color in sorted(
            node.next.keys(), key=lambda k: (k is None, str(k))
        ):
            child = node.next[required_color]

            if required_color is None:
                candidates = all_non_deep_positions
            else:
                candidates = positions_by_color.get(required_color, [])

            used_any = False
            # try high-value candidates first
            for index in candidates:
                if used[index]:
                    continue
                used_any = True

                # optimistic pruning at the “choice” level too:
                # if even picking this best single candidate cannot help,
                # skip deeper.
                # (Lightweight extra cut; path_bound already does the heavy
                # lift.)
                # We still do it after push so the score increment reflects
                # conflicts.
                token = scorer.push_context()
                used[index] = True
                chosen_indices.append(index)

                scorer.push_relic(filtered_relics[index])

                # prune again with updated partial score
                rem_depth_child = depth_cached(child)
                optimistic_future_child = path_bound(child, rem_depth_child)
                if scorer.current_score + optimistic_future_child >= bar:
                    depth_first_search(child)

                # backtrack
                chosen_indices.pop()
                used[index] = False
                scorer.pop_context(token)

            # allow “empty” slot for None if nothing was usable
            if required_color is None and not used_any:
                chosen_indices.append(None)
                depth_first_search(child)
                chosen_indices.pop()

    depth_first_search(urn_tree)
    return top.results_desc()
