from __future__ import annotations

import math
from collections import deque
from typing import Callable, Iterable, Sequence

from solve_one_line import build_graph

from .models import Cell, DifficultyMetrics, tier_for_score
from .topology import neighbors


def _direction(first: Cell, second: Cell) -> Cell:
    return second[0] - first[0], second[1] - first[1]


def _articulation_points(cells: set[Cell]) -> set[Cell]:
    if len(cells) < 3:
        return set()
    discovery: dict[Cell, int] = {}
    low: dict[Cell, int] = {}
    parent: dict[Cell, Cell | None] = {}
    cuts: set[Cell] = set()
    timer = 0

    def visit(cell: Cell) -> None:
        nonlocal timer
        discovery[cell] = low[cell] = timer
        timer += 1
        children = 0
        for nxt in neighbors(cell):
            if nxt not in cells:
                continue
            if nxt not in discovery:
                parent[nxt] = cell
                children += 1
                visit(nxt)
                low[cell] = min(low[cell], low[nxt])
                if parent.get(cell) is None and children > 1:
                    cuts.add(cell)
                if parent.get(cell) is not None and low[nxt] >= discovery[cell]:
                    cuts.add(cell)
            elif nxt != parent.get(cell):
                low[cell] = min(low[cell], discovery[nxt])

    root = next(iter(cells))
    parent[root] = None
    visit(root)
    return cuts


def _cavity_count(cells: set[Cell], width: int, height: int) -> int:
    empty = {(r, c) for r in range(height) for c in range(width) if (r, c) not in cells}
    exterior: set[Cell] = set()
    queue: deque[Cell] = deque()
    for cell in empty:
        if cell[0] in {0, height - 1} or cell[1] in {0, width - 1}:
            exterior.add(cell)
            queue.append(cell)
    while queue:
        cell = queue.popleft()
        for nxt in neighbors(cell):
            if nxt in empty and nxt not in exterior:
                exterior.add(nxt)
                queue.append(nxt)
    remaining = empty - exterior
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            cell = stack.pop()
            for nxt in neighbors(cell):
                if nxt in remaining:
                    remaining.remove(nxt)
                    stack.append(nxt)
    return count


def _pseudo_symmetry(cells: set[Cell], width: int, height: int) -> float:
    variants = [
        {(r, width - 1 - c) for r, c in cells},
        {(height - 1 - r, c) for r, c in cells},
        {(height - 1 - r, width - 1 - c) for r, c in cells},
    ]
    scores = [len(cells & variant) / max(1, len(cells | variant)) for variant in variants]
    return max(scores, default=0.0)


def _choice_key(
    current: Cell,
    previous: Cell | None,
    candidate: Cell,
    cells: set[Cell],
    visited: set[Cell],
    style: int,
) -> tuple[float, float, Cell]:
    onward = sum(nxt in cells and nxt not in visited for nxt in neighbors(candidate))
    straight = 0
    if previous is not None:
        straight = int(_direction(previous, current) == _direction(current, candidate))
    if style == 0:
        return float(onward), float(-straight), candidate
    if style == 1:
        return float(-onward), float(-straight), candidate
    if style == 2:
        return float(-straight), float(onward), candidate
    return float(straight), float(onward), candidate


def _wrong_branch_survival(
    cells: set[Cell],
    prefix: Sequence[Cell],
    wrong: Cell,
    cancel_check: Callable[[], bool] | None,
) -> int:
    best = 1
    for style in range(4):
        visited = set(prefix)
        previous = prefix[-1]
        current = wrong
        visited.add(current)
        depth = 1
        while depth < len(cells) - len(prefix):
            if cancel_check is not None and (depth & 63) == 0 and cancel_check():
                raise RuntimeError("difficulty scoring cancelled")
            choices = [nxt for nxt in neighbors(current) if nxt in cells and nxt not in visited]
            if not choices:
                break
            choices.sort(
                key=lambda cell: _choice_key(
                    current, previous, cell, cells, visited, style
                )
            )
            previous, current = current, choices[0]
            visited.add(current)
            depth += 1
        best = max(best, depth)
    return best


def _human_like_backtracking(
    cells: set[Cell],
    start: Cell,
    node_budget: int,
    style: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[int, int, bool]:
    visited = {start}
    path = [start]
    nodes = 0
    backtracks = 0

    def search(current: Cell, previous: Cell | None) -> bool:
        nonlocal nodes, backtracks
        nodes += 1
        if nodes >= node_budget:
            return False
        if cancel_check is not None and (nodes & 255) == 0 and cancel_check():
            raise RuntimeError("difficulty scoring cancelled")
        if len(visited) == len(cells):
            return True
        choices = [nxt for nxt in neighbors(current) if nxt in cells and nxt not in visited]
        choices.sort(
            key=lambda cell: _choice_key(current, previous, cell, cells, visited, style)
        )
        for nxt in choices:
            visited.add(nxt)
            path.append(nxt)
            if search(nxt, current):
                return True
            path.pop()
            visited.remove(nxt)
            backtracks += 1
        return False

    solved = search(start, None)
    return nodes, backtracks, solved


def _temptation(
    previous: Cell | None,
    current: Cell,
    true_next: Cell,
    wrong: Cell,
    cells: set[Cell],
    visited: set[Cell],
) -> float:
    true_degree = sum(nxt in cells and nxt not in visited for nxt in neighbors(true_next))
    wrong_degree = sum(nxt in cells and nxt not in visited for nxt in neighbors(wrong))
    degree_similarity = 1.0 - min(1.0, abs(true_degree - wrong_degree) / 3.0)
    straight_bonus = 0.0
    if previous is not None:
        incoming = _direction(previous, current)
        true_straight = incoming == _direction(current, true_next)
        wrong_straight = incoming == _direction(current, wrong)
        if wrong_straight and not true_straight:
            straight_bonus = 1.0
        elif wrong_straight == true_straight:
            straight_bonus = 0.45
    return min(1.0, 0.62 * degree_similarity + 0.38 * straight_bonus)


def score_human_difficulty(
    cells: Iterable[Cell],
    solution: Sequence[Cell],
    width: int,
    height: int,
    cancel_check: Callable[[], bool] | None = None,
) -> DifficultyMetrics:
    """Estimate human planning difficulty using route-local and deceptive features."""
    board = set(cells)
    route = list(solution)
    count = len(route)
    graph = build_graph(board)
    edge_count = sum(mask.bit_count() for mask in graph.adjacency) // 2
    extra_adjacencies = max(0, edge_count - (count - 1))

    forced_moves = 0
    branch_points = 0
    plausible_wrong_moves = 0
    wrong_survivals: list[int] = []
    temptation_values: list[float] = []
    ambiguous_choices = 0
    first_decision_index: int | None = None
    visited = {route[0]}

    for index, current in enumerate(route[:-1]):
        if cancel_check is not None and (index & 31) == 0 and cancel_check():
            raise RuntimeError("difficulty scoring cancelled")
        true_next = route[index + 1]
        choices = [nxt for nxt in neighbors(current) if nxt in board and nxt not in visited]
        wrong_choices = [cell for cell in choices if cell != true_next]
        if not wrong_choices:
            forced_moves += 1
        else:
            branch_points += 1
            plausible_wrong_moves += len(wrong_choices)
            if first_decision_index is None:
                first_decision_index = index
            previous = route[index - 1] if index else None
            true_degree = sum(nxt in board and nxt not in visited for nxt in neighbors(true_next))
            for wrong in wrong_choices:
                wrong_degree = sum(nxt in board and nxt not in visited for nxt in neighbors(wrong))
                if wrong_degree == true_degree:
                    ambiguous_choices += 1
                temptation_values.append(
                    _temptation(previous, current, true_next, wrong, board, visited)
                )
                wrong_survivals.append(
                    _wrong_branch_survival(board, route[: index + 1], wrong, cancel_check)
                )
        visited.add(true_next)

    turn_count = sum(
        _direction(route[i - 1], route[i]) != _direction(route[i], route[i + 1])
        for i in range(1, count - 1)
    )
    articulations = _articulation_points(board)
    cavities = _cavity_count(board, width, height)
    symmetry = _pseudo_symmetry(board, width, height)

    agent_budget = min(25_000, max(2_000, count * 80))
    agent_runs = [
        _human_like_backtracking(board, route[0], agent_budget, style, cancel_check)
        for style in (0, 2)
    ]
    agent_backtracks = sum(run[1] for run in agent_runs) / len(agent_runs)
    agent_backtrack_score = min(1.0, math.log1p(agent_backtracks) / math.log1p(agent_budget))

    move_count = max(1, count - 1)
    forced_ratio = forced_moves / move_count
    branch_ratio = branch_points / move_count
    first_fraction = (
        first_decision_index / move_count if first_decision_index is not None else 1.0
    )
    average_survival = (
        sum(wrong_survivals) / len(wrong_survivals) if wrong_survivals else 0.0
    )
    max_survival = max(wrong_survivals, default=0)
    max_survival_fraction = max_survival / move_count
    average_survival_fraction = average_survival / move_count
    temptation_score = (
        sum(temptation_values) / len(temptation_values) if temptation_values else 0.0
    )
    turn_ratio = turn_count / max(1, count - 2)
    loop_ratio = extra_adjacencies / move_count
    bottleneck_ratio = len(articulations) / count
    local_ambiguity = ambiguous_choices / max(1, plausible_wrong_moves)

    ambiguity_component = min(1.0, branch_ratio / 0.24)
    forced_component = min(1.0, (1.0 - forced_ratio) / 0.24)
    average_deception = min(1.0, average_survival_fraction / 0.28)
    maximum_deception = min(1.0, max_survival_fraction / 0.70)
    early_decision = 1.0 - first_fraction
    loop_component = min(1.0, loop_ratio / 0.24)
    turn_component = min(1.0, turn_ratio / 0.62)
    bottleneck_component = min(1.0, bottleneck_ratio / 0.26)
    cavity_component = min(1.0, cavities / 3.0)
    size_planning = min(1.0, math.log2(max(2, count)) / math.log2(400))

    # A single false choice on a large board should not become "Evil" merely
    # because a naive walk can survive for many cells.  Deception features are
    # therefore gated by measured choice exposure: both choice density and an
    # absolute (size-capped) decision count.  This also lets a compact board be
    # genuinely difficult when ambiguity is dense.
    choice_exposure = min(
        1.0,
        0.58 * min(1.0, branch_ratio / 0.22)
        + 0.42 * min(1.0, branch_points / 10.0),
    )
    compact_ambiguity = (
        min(1.0, branch_ratio / 0.15)
        * min(1.0, average_survival_fraction / 0.35)
        * temptation_score
    )
    open_region_component = 1.0 - min(1.0, bottleneck_component)
    raw = (
        0.018
        + 0.205 * choice_exposure
        + 0.065 * ambiguity_component
        + 0.040 * forced_component
        + 0.145 * average_deception * choice_exposure
        + 0.080 * maximum_deception * choice_exposure
        + 0.075 * temptation_score * choice_exposure
        + 0.050 * early_decision * choice_exposure
        + 0.075 * agent_backtrack_score * choice_exposure
        + 0.050 * loop_component
        + 0.025 * turn_component
        + 0.030 * open_region_component * choice_exposure
        + 0.020 * cavity_component
        + 0.015 * symmetry
        + 0.030 * local_ambiguity * choice_exposure
        + 0.200 * compact_ambiguity
        + 0.007 * size_planning
    )
    score = max(0, min(600, int(round(raw * 600))))
    tier = tier_for_score(score)

    explanation: list[str] = []
    if branch_points:
        explanation.append(f"{branch_points} significant branch point{'s' if branch_points != 1 else ''}")
    else:
        explanation.append("no meaningful branch points")
    explanation.append(f"{forced_ratio:.0%} forced moves")
    if wrong_survivals:
        explanation.append(f"longest deceptive wrong branch: {max_survival} moves")
    if articulations:
        explanation.append(f"{len(articulations)} structural bottleneck{'s' if len(articulations) != 1 else ''}")
    if cavities:
        explanation.append(f"{cavities} enclosed cavit{'ies' if cavities != 1 else 'y'}")
    if first_decision_index is not None:
        location = "early" if first_fraction < 0.33 else ("mid-route" if first_fraction < 0.67 else "late")
        explanation.append(f"first critical decision occurs {location}")
    if temptation_score >= 0.65:
        explanation.append("wrong moves look locally convincing")

    return DifficultyMetrics(
        score=score,
        tier=tier,
        forced_move_count=forced_moves,
        forced_move_ratio=forced_ratio,
        branch_points=branch_points,
        branch_ratio=branch_ratio,
        plausible_wrong_moves=plausible_wrong_moves,
        first_decision_fraction=first_fraction,
        average_wrong_branch_survival=average_survival,
        maximum_wrong_branch_survival=max_survival,
        maximum_wrong_branch_fraction=max_survival_fraction,
        temptation_score=temptation_score,
        turn_count=turn_count,
        turn_ratio=turn_ratio,
        extra_adjacencies=extra_adjacencies,
        loop_ratio=loop_ratio,
        articulation_points=len(articulations),
        bottleneck_ratio=bottleneck_ratio,
        cavity_count=cavities,
        pseudo_symmetry=symmetry,
        local_ambiguity=local_ambiguity,
        agent_backtrack_score=agent_backtrack_score,
        explanation=tuple(explanation),
    )
