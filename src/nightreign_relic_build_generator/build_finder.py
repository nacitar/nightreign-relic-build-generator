from __future__ import annotations

import logging
import os
from dataclasses import InitVar, dataclass, field
from heapq import heappush, heapreplace
from types import MappingProxyType
from typing import Literal, Mapping, NamedTuple, Never, Sequence, Union

import json5
from tqdm.std import tqdm as Tqdm

from .nightreign import Color, Effect, Relic, VesselTree
from .term_style import TermStyle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoredEffects:
    active_effects: tuple[Effect, ...]
    score: int


@dataclass(frozen=True)
class Build(ScoredEffects):
    vessel_name: str
    relic_indexes: tuple[int | None, ...]


@dataclass
class BuildHeap:
    """Best by score, deduping on (score, set[Relic], set[Effect]."""

    max_size: int

    @dataclass(order=True)
    class _Entry:
        score: int
        build: Build = field(compare=False)

    _Signature = tuple[int, frozenset[int | None], frozenset[Effect]]

    _heap: list[_Entry] = field(default_factory=list, init=False, repr=False)
    _signatures: set[_Signature] = field(
        default_factory=set, init=False, repr=False
    )

    def _signature(self, build: Build) -> _Signature:
        return (
            build.score,
            frozenset(build.relic_indexes),
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


def get_scored_effects(
    effects: Sequence[Effect], *, score_table: Mapping[str, int]
) -> ScoredEffects:
    score = 0
    seen: set[tuple[str, int]] = set()
    active: list[Effect] = []
    seen_exclusive: set[str] = set()  # tracks applied exclusive categories
    for effect in effects:
        seen_key = (effect.name, effect.level)
        # respect non-stackable duplicates by (name, level)
        if effect.stackable or seen_key not in seen:
            seen.add(seen_key)
            # respect exclusive categories (e.g. "imbue", "skill", ...)
            if not effect.exclusive or effect.exclusive not in seen_exclusive:
                if effect.exclusive:
                    seen_exclusive.add(effect.exclusive)
                active.append(effect)
                # allow for scores from "EffectName +N" specifically
                if (
                    effect_score := score_table.get(
                        effect.qualified_name.lower()
                    )
                ) is None:
                    if (
                        effect_score := score_table.get(
                            f"{effect.name.lower()} +*"
                        )
                    ) is None:
                        # fallback to general score for "EffectName"
                        effect_score = score_table.get(
                            effect.name.lower(), 0
                        ) * (effect.level + 1)
                score += effect_score
    return ScoredEffects(active_effects=tuple(active), score=score)


# -------------------------------
# Incremental scorer (order-aware)
# -------------------------------
class ScoreChange(NamedTuple):
    kind: Literal["score"]
    value: int


class SeenChange(NamedTuple):
    kind: Literal["seen"]
    value: tuple[str, int]


class ExclusiveFlagChange(NamedTuple):
    kind: Literal["exclusive_flag"]
    tag: str  # the exclusive category we marked as taken


class PushEffectChange(NamedTuple):
    kind: Literal["push_effect"]
    value: Effect


Change = Union[ScoreChange, SeenChange, ExclusiveFlagChange, PushEffectChange]


@dataclass
class IncrementalScorer:
    """Maintain 'seen' state + running score with push/pop for backtracking."""

    score_table: Mapping[str, int]

    current_score: int = 0
    seen_keys: set[tuple[str, int]] = field(default_factory=set)
    exclusive_taken: set[str] = field(default_factory=set)
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
        for effect in relic.effects_and_curses:
            seen_key = (effect.name, effect.level)

            # block repeated non-stackable (name, level)
            if (not effect.stackable) and (seen_key in self.seen_keys):
                continue

            # block additional effects of an already-taken exclusive category
            if effect.exclusive and (effect.exclusive in self.exclusive_taken):
                continue

            # record newly seen non-stackable
            if not effect.stackable:
                self.seen_keys.add(seen_key)
                self._change_log.append(SeenChange("seen", seen_key))

            # record exclusive category if present and not previously taken
            if (
                effect.exclusive
                and effect.exclusive not in self.exclusive_taken
            ):
                self.exclusive_taken.add(effect.exclusive)
                self._change_log.append(
                    ExclusiveFlagChange("exclusive_flag", effect.exclusive)
                )

            # apply score
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
            elif isinstance(change, ExclusiveFlagChange):
                # undo the exclusive category we marked as taken
                self.exclusive_taken.remove(change.tag)
            elif isinstance(change, PushEffectChange):
                self.active_effects_stack.pop()
            else:
                raise AssertionError("unreachable")

    @property
    def active_effects(self) -> tuple[Effect, ...]:
        return tuple(self.active_effects_stack)


@dataclass(frozen=True)
class ScoredRelic:
    relic: Relic
    score: int


@dataclass(frozen=True, kw_only=True)
class BuildFinder:
    relics: InitVar[Sequence[Relic]]
    score_json: InitVar[str]
    prune: InitVar[int]

    score_table: Mapping[str, int] = field(init=False)
    scored_relics: tuple[ScoredRelic, ...] = field(init=False)

    def __post_init__(
        self, relics: Sequence[Relic], score_json: str, prune: int
    ) -> None:
        data = json5.loads(score_json)
        if not isinstance(data, dict):
            raise ValueError(f"root element not a dict: {type(data).__name__}")

        object.__setattr__(
            self,
            "score_table",
            MappingProxyType(
                {
                    str(effect).lower(): int(group_score)
                    for group_score, effects in data.items()
                    for effect in (
                        effects if isinstance(effects, list) else [effects]
                    )
                }
            ),
        )
        object.__setattr__(
            self,
            "scored_relics",
            tuple(
                ScoredRelic(relic, score)
                for relic in relics
                if (
                    score := get_scored_effects(
                        relic.effects_and_curses, score_table=self.score_table
                    ).score
                )
                >= prune
            ),
        )

    def build_to_str(self, build: Build) -> str:
        lines: list[str] = []
        lines.append(
            f"{TermStyle.BOLD}"
            f"{build.vessel_name} [{build.score}]"
            f"{TermStyle.RESET}"
        )
        for i in build.relic_indexes:
            if i is not None:
                lines.extend(
                    f"  {line}"
                    for line in self.scored_relics[i].relic.str_lines()
                )
            else:
                lines.append(
                    f"{TermStyle.BOLD}"
                    "  <Empty Relic Slot>"
                    f"{TermStyle.RESET}"
                )
        return os.linesep.join(lines)

    def builds_to_tree_str(self, builds: Sequence[Build]) -> str:
        if not builds:
            return ""
        by_vessel: dict[str, list[Build]] = {}
        # best vessels first, so the keys are ordered as such
        for build in sorted(
            builds, key=lambda build: build.score, reverse=True
        ):
            by_vessel.setdefault(build.vessel_name, []).append(build)
        lines: list[str] = []
        # reversed so vessels are printed lowest-max-score first
        for vessel_name in reversed(by_vessel.keys()):
            # show builds worst to best
            # vessel_builds = list(reversed(by_vessel[vessel_name]))
            vessel_builds = by_vessel[vessel_name]
            if lines:
                lines.append("")
            min_score = vessel_builds[-1].score
            max_score = vessel_builds[0].score
            lines.append(
                f"{TermStyle.BOLD}"
                f"{vessel_name} [{min_score}, {max_score}]"
                f"{TermStyle.RESET}"
            )

            # Assume all builds in a vessel share the same slot count
            slot_count = len(vessel_builds[0].relic_indexes)
            # collect unique relic indexes in the order they appear
            for slot in range(slot_count):
                seen: set[int] = set()
                slot_relic_indexes: list[int] = []
                color_name: str = ""

                for build in vessel_builds:
                    i = build.relic_indexes[slot]
                    if i is None:
                        continue
                    if i not in seen:
                        seen.add(i)
                        slot_relic_indexes.append(i)
                        if not color_name:
                            relic = self.scored_relics[i].relic
                            color_name = str(relic.color)
                if color_name:
                    lines.append(
                        f"  {TermStyle.BOLD}[{color_name}]{TermStyle.RESET}"
                    )
                    # reversed so indexes from higher-score builds go last
                    for i in reversed(slot_relic_indexes):
                        relic = self.scored_relics[i].relic
                        lines.extend(
                            f"    {line}"
                            for line in relic.str_lines(color_prefix=False)
                        )
                else:
                    lines.append(
                        f"{TermStyle.BOLD}"
                        "  <Empty Relic Slot>"
                        f"{TermStyle.RESET}"
                    )
        return os.linesep.join(lines)

    # -------------------------------------------------
    # Fast search: branch-and-bound with upper bounding
    # -------------------------------------------------
    def top_builds(
        self,
        vessel_tree: VesselTree,
        *,
        count: int,
        minimum: int,
        progress_bar: Tqdm[Never] | None = None,
    ) -> list[Build]:
        """
        Branch-and-bound search that integrates scoring while walking the
        VesselTree.  This aggressively prunes and returns top K builds much
        faster.
        """
        # 1) Index candidates by color and also all non-deep for wildcard
        positions_by_color: dict[Color, list[int]] = {}
        all_non_deep_positions: list[int] = []
        for index, scored_relic in enumerate(self.scored_relics):
            positions_by_color.setdefault(scored_relic.relic.color, []).append(
                index
            )
            if not scored_relic.relic.color.is_deep:
                all_non_deep_positions.append(index)

        # 2) Pre-sorted candidate orders to try high-value relics first
        for lst in positions_by_color.values():
            lst.sort(key=lambda i: self.scored_relics[i].score, reverse=True)
        all_non_deep_positions.sort(
            key=lambda i: self.scored_relics[i].score, reverse=True
        )

        # 3) Heap for top results + signature set
        top = BuildHeap(count)

        # 4) Integrated DFS with scoring + bound
        used: list[bool] = [False] * len(self.scored_relics)
        chosen_indices: list[int | None] = []
        scorer = IncrementalScorer(self.score_table)

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
                best_scores.append(self.scored_relics[i].score)
                taken += 1
                if taken >= remaining_slots:
                    break
            return sum(best_scores)

        # We also want a quick multi-slot bound for a *path of slots*.
        # For simplicity, we compute “best k among all non-deep unused”
        # (wildcard path), and for fixed-color paths we do it per-step.
        def path_bound(node: VesselTree, depth_from_here: int) -> int:
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
                next_nodes: list[VesselTree] = []
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

        def remaining_depth(node: VesselTree) -> int:
            """
            Longest path to a leaf from this node
            (small int; your vessels are length 6).
            """
            if not node.next:
                return 0
            return 1 + max(
                remaining_depth(child) for child in node.next.values()
            )

        # cache remaining depths per node so we do not recompute
        _remaining_depth_cache: dict[VesselTree, int] = {}

        def depth_cached(node: VesselTree) -> int:
            d = _remaining_depth_cache.get(node)
            if d is not None:
                return d
            d = remaining_depth(node)
            _remaining_depth_cache[node] = d
            return d

        def depth_first_search(node: VesselTree) -> None:
            # If this node names a completed urn, consider the current
            # partial build too.
            if node.name:
                current_build = Build(
                    vessel_name=node.name,
                    active_effects=scorer.active_effects,
                    score=scorer.current_score,
                    relic_indexes=tuple(chosen_indices),
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
                if progress_bar is not None:
                    progress_bar.update(1)
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

                    scorer.push_relic(self.scored_relics[index].relic)

                    # prune again with updated partial score
                    rem_depth_child = depth_cached(child)
                    optimistic_future_child = path_bound(
                        child, rem_depth_child
                    )
                    if scorer.current_score + optimistic_future_child >= bar:
                        depth_first_search(child)

                    # backtrack
                    chosen_indices.pop()
                    used[index] = False
                    scorer.pop_context(token)

                # allow “empty” slot if nothing was usable
                if not used_any:
                    chosen_indices.append(None)
                    depth_first_search(child)
                    chosen_indices.pop()

        depth_first_search(vessel_tree)
        return top.results_desc()
