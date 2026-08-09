from __future__ import annotations

import hashlib
import json
import math
import random
from collections import deque
from dataclasses import dataclass, replace
from typing import Iterable

from .models import (
    Cell,
    ConstructionCertificate,
    DifficultyTier,
    EarStep,
    GenerationSettings,
    ShapeMode,
    TopologyCandidate,
)


ORTHOGONAL = ((-1, 0), (1, 0), (0, -1), (0, 1))


def neighbors(cell: Cell) -> tuple[Cell, Cell, Cell, Cell]:
    r, c = cell
    return ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))


def cells_connected(cells: Iterable[Cell]) -> bool:
    board = set(cells)
    if not board:
        return False
    reached = {next(iter(board))}
    stack = list(reached)
    while stack:
        cell = stack.pop()
        for nxt in neighbors(cell):
            if nxt in board and nxt not in reached:
                reached.add(nxt)
                stack.append(nxt)
    return len(reached) == len(board)


def graph_degree(cell: Cell, cells: set[Cell] | frozenset[Cell]) -> int:
    return sum(nxt in cells for nxt in neighbors(cell))


def path_is_induced(path: Iterable[Cell]) -> bool:
    ordered = list(path)
    if len(set(ordered)) != len(ordered):
        return False
    board = set(ordered)
    for i, cell in enumerate(ordered):
        expected: set[Cell] = set()
        if i:
            expected.add(ordered[i - 1])
        if i + 1 < len(ordered):
            expected.add(ordered[i + 1])
        actual = {nxt for nxt in neighbors(cell) if nxt in board}
        if actual != expected:
            return False
    return True


def _full_horizontal_scaffold(width: int, rows: list[int]) -> list[Cell]:
    path: list[Cell] = []
    direction = 1
    current_x = 0
    for index, row in enumerate(rows):
        if index == 0:
            run = list(range(width))
            path.extend((row, c) for c in run)
            current_x = width - 1
            direction = -1
            continue
        previous_row = rows[index - 1]
        for connector_row in range(previous_row + 1, row + 1):
            path.append((connector_row, current_x))
        if direction < 0:
            run = range(current_x - 1, -1, -1)
            current_x = 0
        else:
            run = range(current_x + 1, width)
            current_x = width - 1
        path.extend((row, c) for c in run)
        direction *= -1
    return path


def _horizontal_scaffold(
    width: int,
    height: int,
    rng: random.Random,
    mode: ShapeMode,
    difficulty: DifficultyTier,
) -> list[Cell]:
    if height <= 3:
        spacing = 2
    elif height <= 5:
        spacing = 2 if difficulty is DifficultyTier.EASY else 3
    else:
        spacing = 3 if mode is ShapeMode.NORMAL else rng.choice([3, 3, 4])
    offsets = list(range(min(spacing, height)))
    row_options = [[offset + k * spacing for k in range((height - 1 - offset) // spacing + 1)] for offset in offsets]
    max_count = max(len(rows) for rows in row_options)
    useful = [rows for rows in row_options if len(rows) == max_count]
    rows = list(rng.choice(useful))
    if len(rows) == 1 and height >= 3:
        rows = [0, height - 1]

    tier_index = list(DifficultyTier).index(difficulty)
    minimum_fraction = (
        min(0.88, 0.64 + 0.06 * tier_index)
        if mode is ShapeMode.NORMAL
        else min(0.76, 0.36 + 0.08 * tier_index)
    )
    minimum_run = max(2, min(width - 1, int(round(width * minimum_fraction))))
    edge_band = max(0, min(width // 4, 3))
    left = rng.randint(0, edge_band) if edge_band else 0
    right_low = max(left + minimum_run, width - 1 - edge_band)
    if right_low >= width:
        left, right_low = 0, width - 1
    right = rng.randint(right_low, width - 1)
    if right - left < 2:
        left, right = 0, width - 1

    forward = rng.random() < 0.5
    start_x, end_x = (left, right) if forward else (right, left)
    path: list[Cell] = [(rows[0], c) for c in (
        range(start_x, end_x + 1) if start_x <= end_x else range(start_x, end_x - 1, -1)
    )]
    current_x = end_x
    moving_right = not forward

    for index in range(1, len(rows)):
        previous_row, row = rows[index - 1], rows[index]
        for connector_row in range(previous_row + 1, row + 1):
            path.append((connector_row, current_x))

        if moving_right:
            far_low = max(current_x + minimum_run, width - 1 - edge_band)
            if far_low >= width:
                far = width - 1
            else:
                far = rng.randint(far_low, width - 1)
            run = range(current_x + 1, far + 1)
        else:
            far_high = min(current_x - minimum_run, edge_band)
            if far_high < 0:
                far = 0
            else:
                far = rng.randint(0, far_high)
            run = range(current_x - 1, far - 1, -1)
        path.extend((row, c) for c in run)
        current_x = far
        moving_right = not moving_right

    if len(path) < 3 or not path_is_induced(path):
        path = _full_horizontal_scaffold(width, rows)
    if len(path) < 3 or not path_is_induced(path):
        # The final fallback is deliberately simple and always induced.
        row = height // 2
        path = [(row, c) for c in range(width)]
    return path


def _random_induced_path(
    settings: GenerationSettings,
    rng: random.Random,
) -> list[Cell]:
    """Build a varied chord-free walk while retaining a cheap proof invariant."""

    width, height = settings.width, settings.height
    area = width * height
    trials = 30 if settings.shape_mode is ShapeMode.NORMAL else 22
    best_path: list[Cell] = []
    best_quality = -1.0

    for _ in range(trials):
        start = (rng.randrange(height), rng.randrange(width))
        path = [start]
        visited = {start}
        min_r = max_r = start[0]
        min_c = max_c = start[1]
        previous_direction: Cell | None = None

        while True:
            current = path[-1]
            options: list[tuple[float, Cell]] = []
            for nxt in neighbors(current):
                r, c = nxt
                if not (0 <= r < height and 0 <= c < width) or nxt in visited:
                    continue
                if {cell for cell in neighbors(nxt) if cell in visited} != {current}:
                    continue
                prospective = visited | {nxt}
                onward = 0
                for future in neighbors(nxt):
                    fr, fc = future
                    if not (0 <= fr < height and 0 <= fc < width) or future in prospective:
                        continue
                    if {cell for cell in neighbors(future) if cell in prospective} == {nxt}:
                        onward += 1
                new_min_r, new_max_r = min(min_r, r), max(max_r, r)
                new_min_c, new_max_c = min(min_c, c), max(max_c, c)
                old_box = (max_r - min_r + 1) * (max_c - min_c + 1)
                new_box = (new_max_r - new_min_r + 1) * (new_max_c - new_min_c + 1)
                expands = new_box - old_box
                direction = (r - current[0], c - current[1])
                turn = int(previous_direction is not None and direction != previous_direction)
                edge_distance = min(r, c, height - 1 - r, width - 1 - c)
                if settings.shape_mode is ShapeMode.NORMAL:
                    score = 2.1 * onward + 0.55 * expands + 0.18 * turn + 0.05 * edge_distance
                else:
                    score = 1.55 * onward + 0.85 * expands + 0.55 * turn - 0.08 * edge_distance
                score += rng.uniform(-0.9, 0.9)
                options.append((score, nxt))
            if not options:
                break
            options.sort(reverse=True)
            shortlist = options[: min(3, len(options))]
            # Rank-biased choice creates reproducible variety without frequently
            # sacrificing the walk to an immediate dead end.
            ranks = [1.0 / (index + 1) for index in range(len(shortlist))]
            nxt = rng.choices([item[1] for item in shortlist], weights=ranks, k=1)[0]
            direction = (nxt[0] - current[0], nxt[1] - current[1])
            path.append(nxt)
            visited.add(nxt)
            min_r, max_r = min(min_r, nxt[0]), max(max_r, nxt[0])
            min_c, max_c = min(min_c, nxt[1]), max(max_c, nxt[1])
            previous_direction = direction

        if len(path) < 3:
            continue
        bbox_use = ((max_r - min_r + 1) * (max_c - min_c + 1)) / area
        turn_ratio = sum(
            _direction(path[index - 1], path[index])
            != _direction(path[index], path[index + 1])
            for index in range(1, len(path) - 1)
        ) / max(1, len(path) - 2)
        length_fraction = len(path) / area
        if settings.shape_mode is ShapeMode.NORMAL:
            quality = 0.60 * length_fraction + 0.31 * bbox_use + 0.09 * turn_ratio
        else:
            center_r = sum(cell[0] for cell in path) / len(path)
            center_c = sum(cell[1] for cell in path) / len(path)
            asymmetry = min(
                1.0,
                math.hypot(
                    (center_r - (height - 1) / 2) / max(1, height),
                    (center_c - (width - 1) / 2) / max(1, width),
                )
                * 3,
            )
            quality = 0.43 * length_fraction + 0.29 * bbox_use + 0.16 * turn_ratio + 0.12 * asymmetry
        if quality > best_quality:
            best_quality = quality
            best_path = path

    if len(best_path) >= max(3, int(round(area * 0.18))) and path_is_induced(best_path):
        return best_path
    # This caller always has a deterministic scaffold fallback.
    return []


def _direction(first: Cell, second: Cell) -> Cell:
    return second[0] - first[0], second[1] - first[1]


def _make_base_path(settings: GenerationSettings, rng: random.Random) -> list[Cell]:
    random_walk_probability = 0.28 if settings.shape_mode is ShapeMode.NORMAL else 0.62
    if rng.random() < random_walk_probability:
        varied = _random_induced_path(settings, rng)
        if varied:
            return varied

    horizontal_bias = settings.width >= settings.height
    if settings.width == settings.height:
        horizontal_bias = rng.random() < 0.5
    elif rng.random() < (0.18 if settings.shape_mode is ShapeMode.NORMAL else 0.35):
        horizontal_bias = not horizontal_bias

    if horizontal_bias:
        path = _horizontal_scaffold(
            settings.width,
            settings.height,
            rng,
            settings.shape_mode,
            settings.difficulty,
        )
    else:
        transposed = _horizontal_scaffold(
            settings.height,
            settings.width,
            rng,
            settings.shape_mode,
            settings.difficulty,
        )
        path = [(c, r) for r, c in transposed]
    if not path_is_induced(path):
        raise RuntimeError("Internal scaffold construction produced a non-induced path.")
    return path


@dataclass(frozen=True)
class _EarOption:
    index: int
    first: Cell
    second: Cell
    depth: int


def _ear_options(
    path: list[Cell],
    cells: set[Cell],
    width: int,
    height: int,
    depths: dict[Cell, int],
) -> list[_EarOption]:
    options: list[_EarOption] = []
    # Keep both endpoint graph degrees at one so the unspecified end is forced.
    for index in range(1, len(path) - 2):
        u, v = path[index], path[index + 1]
        dr, dc = v[0] - u[0], v[1] - u[1]
        perpendiculars = ((-dc, dr), (dc, -dr))
        for pr, pc in perpendiculars:
            first = (u[0] + pr, u[1] + pc)
            second = (v[0] + pr, v[1] + pc)
            if not (
                0 <= first[0] < height
                and 0 <= first[1] < width
                and 0 <= second[0] < height
                and 0 <= second[1] < width
            ):
                continue
            if first in cells or second in cells:
                continue
            first_existing = {nxt for nxt in neighbors(first) if nxt in cells}
            second_existing = {nxt for nxt in neighbors(second) if nxt in cells}
            if first_existing != {u} or second_existing != {v}:
                continue
            depth = max(depths.get(u, 0), depths.get(v, 0)) + 1
            options.append(_EarOption(index, first, second, depth))
    return options


EAR_TARGET_FRACTION: dict[DifficultyTier, float] = {
    DifficultyTier.EASY: 0.000,
    DifficultyTier.MEDIUM: 0.018,
    DifficultyTier.HARD: 0.045,
    DifficultyTier.EXPERT: 0.100,
    DifficultyTier.EVIL: 0.160,
}


def _weighted_choice(
    options: list[_EarOption],
    path_length: int,
    tier: DifficultyTier,
    rng: random.Random,
) -> _EarOption:
    tier_index = list(DifficultyTier).index(tier)
    weights: list[float] = []
    for option in options:
        position = option.index / max(1, path_length - 1)
        early_weight = 1.0 + tier_index * (1.0 - position) * 0.9
        nested_weight = 1.0 + tier_index * option.depth * 0.38
        late_easy = 1.0 + (1.0 - tier_index / 4.0) * position * 0.55
        weights.append(early_weight * nested_weight * late_easy)
    return rng.choices(options, weights=weights, k=1)[0]


def _add_ears(
    base_path: list[Cell],
    settings: GenerationSettings,
    rng: random.Random,
    attempt: int,
) -> tuple[list[Cell], list[EarStep]]:
    path = list(base_path)
    cells = set(path)
    depths = {cell: 0 for cell in path}
    area = settings.width * settings.height
    base_target = area * EAR_TARGET_FRACTION[settings.difficulty]
    if settings.shape_mode is ShapeMode.EXTREME:
        extreme_multipliers = {
            DifficultyTier.EASY: 1.0,
            DifficultyTier.MEDIUM: 1.0,
            DifficultyTier.HARD: 0.84,
            DifficultyTier.EXPERT: 0.84,
            DifficultyTier.EVIL: 1.05,
        }
        base_target *= extreme_multipliers[settings.difficulty]
    jitter_span = 0.0 if settings.difficulty is DifficultyTier.EASY else max(0.8, base_target * 0.24)
    attempt_wave = ((attempt % 7) - 3) / 3.0
    target = int(round(base_target + attempt_wave * jitter_span + rng.uniform(-jitter_span, jitter_span)))
    minimums = {
        DifficultyTier.EASY: 0,
        DifficultyTier.MEDIUM: 1,
        DifficultyTier.HARD: 2,
        DifficultyTier.EXPERT: 3,
        DifficultyTier.EVIL: 4,
    }
    minimum = minimums[settings.difficulty]
    if settings.difficulty is DifficultyTier.HARD and area >= 36:
        minimum = 3
    target = max(minimum, target)
    target = min(target, max(0, (area - len(path)) // 2))

    steps: list[EarStep] = []
    for _ in range(target):
        options = _ear_options(path, cells, settings.width, settings.height, depths)
        if not options:
            break
        option = _weighted_choice(options, len(path), settings.difficulty, rng)
        u, v = path[option.index], path[option.index + 1]
        first, second = option.first, option.second
        path[option.index + 1 : option.index + 1] = [first, second]
        cells.update((first, second))
        depths[first] = depths[second] = option.depth
        steps.append(EarStep(edge=(u, v), added=(first, second)))
    return path, steps


def _transform_cell(cell: Cell, width: int, height: int, flip_h: bool, flip_v: bool) -> Cell:
    r, c = cell
    if flip_h:
        c = width - 1 - c
    if flip_v:
        r = height - 1 - r
    return r, c


def _transform_topology(
    base: list[Cell],
    solution: list[Cell],
    ears: list[EarStep],
    settings: GenerationSettings,
    rng: random.Random,
) -> tuple[list[Cell], list[Cell], list[EarStep]]:
    flip_h = rng.random() < 0.5
    flip_v = rng.random() < 0.5
    transform = lambda cell: _transform_cell(  # noqa: E731
        cell, settings.width, settings.height, flip_h, flip_v
    )
    base = [transform(cell) for cell in base]
    solution = [transform(cell) for cell in solution]
    ears = [
        EarStep(
            edge=(transform(step.edge[0]), transform(step.edge[1])),
            added=(transform(step.added[0]), transform(step.added[1])),
        )
        for step in ears
    ]
    if rng.random() < 0.5:
        solution.reverse()
    return base, solution, ears


def matrix_from_cells(cells: Iterable[Cell], width: int, height: int) -> tuple[tuple[int, ...], ...]:
    board = set(cells)
    return tuple(tuple(1 if (r, c) in board else 0 for c in range(width)) for r in range(height))


def exact_topology_hash(cells: Iterable[Cell], start: Cell, width: int, height: int) -> str:
    payload = {
        "width": width,
        "height": height,
        "start": start,
        "cells": sorted(cells),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_shape_hash(cells: Iterable[Cell], start: Cell) -> str:
    board = set(cells)
    min_r, max_r = min(r for r, _ in board), max(r for r, _ in board)
    min_c, max_c = min(c for _, c in board), max(c for _, c in board)
    normalized = {(r - min_r, c - min_c) for r, c in board}
    normalized_start = (start[0] - min_r, start[1] - min_c)

    def variants(points: set[Cell], marker: Cell) -> list[tuple[tuple[Cell, ...], Cell]]:
        result: list[tuple[tuple[Cell, ...], Cell]] = []
        current_points, current_marker = points, marker
        for _ in range(4):
            local_min_r = min(r for r, _ in current_points)
            local_min_c = min(c for _, c in current_points)
            shifted = {(r - local_min_r, c - local_min_c) for r, c in current_points}
            shifted_marker = (current_marker[0] - local_min_r, current_marker[1] - local_min_c)
            result.append((tuple(sorted(shifted)), shifted_marker))
            max_col = max(c for _, c in shifted)
            reflected = {(r, max_col - c) for r, c in shifted}
            reflected_marker = (shifted_marker[0], max_col - shifted_marker[1])
            result.append((tuple(sorted(reflected)), reflected_marker))
            max_row = max(r for r, _ in shifted)
            rotated = {(c, max_row - r) for r, c in shifted}
            current_points = rotated
            current_marker = (shifted_marker[1], max_row - shifted_marker[0])
        return result

    canonical = min(variants(normalized, normalized_start))
    raw = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cavity_count(cells: set[Cell], width: int, height: int) -> int:
    empty = {(r, c) for r in range(height) for c in range(width) if (r, c) not in cells}
    outside: set[Cell] = set()
    queue: deque[Cell] = deque()
    for cell in empty:
        if cell[0] in {0, height - 1} or cell[1] in {0, width - 1}:
            outside.add(cell)
            queue.append(cell)
    while queue:
        cell = queue.popleft()
        for nxt in neighbors(cell):
            if nxt in empty and nxt not in outside:
                outside.add(nxt)
                queue.append(nxt)
    internal = empty - outside
    cavities = 0
    while internal:
        cavities += 1
        seed = internal.pop()
        stack = [seed]
        while stack:
            cell = stack.pop()
            for nxt in neighbors(cell):
                if nxt in internal:
                    internal.remove(nxt)
                    stack.append(nxt)
    return cavities


def aesthetic_quality(
    cells: Iterable[Cell], width: int, height: int, mode: ShapeMode
) -> tuple[float, dict[str, float]]:
    board = set(cells)
    count = len(board)
    density = count / (width * height)
    min_r, max_r = min(r for r, _ in board), max(r for r, _ in board)
    min_c, max_c = min(c for _, c in board), max(c for _, c in board)
    bbox_use = ((max_r - min_r + 1) / height) * ((max_c - min_c + 1) / width)
    center_r = sum(r for r, _ in board) / count
    center_c = sum(c for _, c in board) / count
    center_offset = math.hypot(
        (center_r - (height - 1) / 2) / max(1, height),
        (center_c - (width - 1) / 2) / max(1, width),
    )
    in_square: set[Cell] = set()
    square_count = 0
    for r in range(height - 1):
        for c in range(width - 1):
            block = {(r, c), (r + 1, c), (r, c + 1), (r + 1, c + 1)}
            if block <= board:
                square_count += 1
                in_square |= block
    chunky_fraction = len(in_square) / count
    degrees = {cell: graph_degree(cell, board) for cell in board}
    corridor_fraction = sum(degrees[cell] == 2 and cell not in in_square for cell in board) / count
    spike_fraction = max(0, sum(degree == 1 for degree in degrees.values()) - 2) / count
    perimeter = sum(nxt not in board for cell in board for nxt in neighbors(cell))
    compactness = min(1.0, (4.0 * math.sqrt(max(1.0, count))) / max(1.0, perimeter))
    cavities = _cavity_count(board, width, height)
    cavity_bonus = min(1.0, cavities / 3.0)

    if mode is ShapeMode.NORMAL:
        density_fit = max(0.0, 1.0 - abs(density - 0.58) / 0.45)
        score = (
            0.24 * density_fit
            + 0.20 * bbox_use
            + 0.19 * chunky_fraction
            + 0.14 * (1.0 - min(1.0, corridor_fraction))
            + 0.10 * compactness
            + 0.07 * (1.0 - min(1.0, center_offset * 2.4))
            + 0.06 * cavity_bonus
            - 0.20 * spike_fraction
        )
    else:
        asymmetry = min(1.0, center_offset * 3.0)
        sparse_interest = max(0.0, 1.0 - abs(density - 0.40) / 0.40)
        score = (
            0.22 * bbox_use
            + 0.20 * sparse_interest
            + 0.17 * corridor_fraction
            + 0.15 * asymmetry
            + 0.12 * cavity_bonus
            + 0.14 * min(1.0, square_count / max(1.0, count * 0.08))
            - 0.20 * spike_fraction
        )
    metrics = {
        "density": density,
        "bounding_box_use": bbox_use,
        "chunky_fraction": chunky_fraction,
        "corridor_fraction": corridor_fraction,
        "spike_fraction": spike_fraction,
        "compactness": compactness,
        "center_offset": center_offset,
        "cavity_count": float(cavities),
        "two_by_two_blocks": float(square_count),
    }
    return max(0.0, min(1.0, score)), metrics


def generate_topology(
    settings: GenerationSettings,
    rng: random.Random,
    attempt: int,
) -> TopologyCandidate:
    trial_counts = {
        DifficultyTier.EASY: 1,
        DifficultyTier.MEDIUM: 2,
        DifficultyTier.HARD: 2,
        DifficultyTier.EXPERT: 4,
        DifficultyTier.EVIL: 6,
    }
    proposals: list[tuple[list[Cell], list[Cell], list[EarStep], float]] = []
    for trial_index in range(trial_counts[settings.difficulty]):
        local_rng = random.Random(rng.getrandbits(128))
        construction_settings = settings
        if (
            settings.shape_mode is ShapeMode.NORMAL
            and settings.difficulty in {DifficultyTier.EXPERT, DifficultyTier.EVIL}
            and trial_index % 2 == 1
        ):
            # High-tier Normal boards may borrow a maze-like base proposal, but
            # they are still evaluated and filtered by the stricter Normal
            # silhouette metric before they can be returned.
            construction_settings = replace(settings, shape_mode=ShapeMode.EXTREME)
        proposed_base = _make_base_path(construction_settings, local_rng)
        proposed_solution, proposed_ears = _add_ears(
            proposed_base, construction_settings, local_rng, attempt
        )
        proposed_aesthetic, _ = aesthetic_quality(
            proposed_solution,
            settings.width,
            settings.height,
            settings.shape_mode,
        )
        proposals.append(
            (proposed_base, proposed_solution, proposed_ears, proposed_aesthetic)
        )
    base_path, solution, ears, _ = max(
        proposals,
        key=lambda proposal: (
            len(proposal[2]),
            proposal[3],
            len(proposal[1]),
        ),
    )
    base_path, solution, ears = _transform_topology(
        base_path, solution, ears, settings, rng
    )
    cells = frozenset(solution)
    if not cells_connected(cells):
        raise RuntimeError("Generated topology is disconnected.")
    start, end = solution[0], solution[-1]
    score, aesthetic_metrics = aesthetic_quality(
        cells, settings.width, settings.height, settings.shape_mode
    )
    certificate = ConstructionCertificate(
        method="induced_path_ear_expansion_v1",
        base_path=tuple(base_path),
        ears=tuple(ears),
    )
    return TopologyCandidate(
        width=settings.width,
        height=settings.height,
        cells=cells,
        solution=tuple(solution),
        start=start,
        end=end,
        certificate=certificate,
        topology_hash=exact_topology_hash(
            cells, start, settings.width, settings.height
        ),
        aesthetic_score=score,
        aesthetic_metrics=aesthetic_metrics,
    )
