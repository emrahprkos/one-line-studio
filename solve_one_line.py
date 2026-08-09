#!/usr/bin/env python3
"""Automatic screenshot-to-solution pipeline for the mobile game One Line.

The vision layer detects a repeated lattice of rounded square tiles.  The solver
layer only sees an irregular set of integer grid cells, so it can also be used
directly with a manually supplied binary matrix.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import cv2
import numpy as np


Cell = tuple[int, int]


class OneLineError(RuntimeError):
    """Base class for expected, user-facing failures."""


class DetectionError(OneLineError):
    """The screenshot could not be converted into a confident board."""


class NoSolutionError(OneLineError):
    """No Hamiltonian path was found for the supplied board and start."""


class SolveTimeout(OneLineError):
    """The configured solver limit was reached."""


@dataclass(frozen=True)
class Detection:
    component_id: int
    bbox: tuple[int, int, int, int]
    center: tuple[float, float]
    area: int
    fill_ratio: float
    mean_bgr: tuple[float, float, float]

    @property
    def width(self) -> int:
        return self.bbox[2]

    @property
    def height(self) -> int:
        return self.bbox[3]

    @property
    def side(self) -> float:
        return math.sqrt(self.width * self.height)


@dataclass
class DetectionReport:
    background_bgr: tuple[int, int, int]
    threshold: float
    raw_candidates: list[Detection]
    detections: list[Detection]
    size_cv: float
    lattice_fraction: float
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class GridModel:
    cells: set[Cell]
    centers: dict[Cell, tuple[float, float]]
    detection_for_cell: dict[Cell, Detection]
    row_centers: list[float]
    col_centers: list[float]
    row_pitch: float
    col_pitch: float
    tile_size: tuple[float, float]
    residual_fraction: float
    confidence: float
    warnings: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return max((r for r, _ in self.cells), default=-1) + 1

    @property
    def cols(self) -> int:
        return max((c for _, c in self.cells), default=-1) + 1


@dataclass
class StartDetection:
    cell: Cell
    color_distance: float
    second_distance: float
    confidence: float
    distances: dict[Cell, float]


@dataclass
class Graph:
    cells: list[Cell]
    index: dict[Cell, int]
    adjacency: list[int]
    color_masks: tuple[int, int]

    @property
    def all_mask(self) -> int:
        return (1 << len(self.cells)) - 1


@dataclass
class SolverStats:
    states: int = 0
    backtracks: int = 0
    cache_hits: int = 0
    connectivity_prunes: int = 0
    degree_prunes: int = 0
    parity_prunes: int = 0
    separator_prunes: int = 0
    elapsed_seconds: float = 0.0
    cache_entries: int = 0


def _iter_bits(mask: int) -> Iterator[int]:
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def load_image(path: str | Path) -> np.ndarray:
    path = Path(path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise OneLineError(f"Could not load image: {path}")
    return image


def _estimate_background(image: np.ndarray) -> np.ndarray:
    """Estimate the UI background from the modal quantized screen color."""
    h, w = image.shape[:2]
    scale = min(1.0, 520.0 / max(h, w))
    sample = cv2.resize(
        image,
        (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA,
    )
    quantized = (sample.reshape(-1, 3) // 8).astype(np.int32)
    keys = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
    modal_key = int(np.bincount(keys, minlength=32768).argmax())
    matching = sample.reshape(-1, 3)[keys == modal_key]
    if len(matching) == 0:
        return np.median(sample.reshape(-1, 3), axis=0).astype(np.uint8)
    return np.median(matching, axis=0).astype(np.uint8)


def _color_distance_from_background(
    image: np.ndarray, background_bgr: np.ndarray
) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    background_lab = cv2.cvtColor(
        background_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2LAB
    ).astype(np.float32)[0, 0]
    return np.linalg.norm(lab - background_lab, axis=2)


def _component_candidates(
    image: np.ndarray, distance: np.ndarray, threshold: float
) -> list[Detection]:
    h, w = image.shape[:2]
    mask = (distance > threshold).astype(np.uint8) * 255
    kernel_size = max(1, int(round(min(h, w) / 650)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    if kernel_size > 1:
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    min_side = max(10.0, min(h, w) * 0.012)
    max_side = min(h, w) * 0.38
    candidates: list[Detection] = []
    for component_id in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[component_id])
        side = math.sqrt(bw * bh)
        aspect = bw / max(1, bh)
        fill = area / max(1, bw * bh)
        if not (min_side <= side <= max_side):
            continue
        if not (0.55 <= aspect <= 1.82 and fill >= 0.34):
            continue

        inset_x = max(1, int(round(bw * 0.18)))
        inset_y = max(1, int(round(bh * 0.18)))
        x1, x2 = x + inset_x, x + bw - inset_x
        y1, y2 = y + inset_y, y + bh - inset_y
        if x2 <= x1 or y2 <= y1:
            patch = image[y : y + bh, x : x + bw]
        else:
            patch = image[y1:y2, x1:x2]
        mean_bgr = tuple(float(v) for v in np.mean(patch, axis=(0, 1)))
        candidates.append(
            Detection(
                component_id=component_id,
                bbox=(x, y, bw, bh),
                center=(x + (bw - 1) / 2.0, y + (bh - 1) / 2.0),
                area=area,
                fill_ratio=fill,
                mean_bgr=mean_bgr,
            )
        )
    return candidates


def _largest_lattice_component(
    detections: Sequence[Detection], tile_w: float, tile_h: float
) -> list[Detection]:
    if len(detections) <= 1:
        return list(detections)
    neighbors: list[list[int]] = [[] for _ in detections]
    for i, first in enumerate(detections):
        x1, y1 = first.center
        for j in range(i + 1, len(detections)):
            x2, y2 = detections[j].center
            dx, dy = abs(x1 - x2), abs(y1 - y2)
            horizontal = dy <= 0.32 * tile_h and 0.82 * tile_w <= dx <= 1.80 * tile_w
            vertical = dx <= 0.32 * tile_w and 0.82 * tile_h <= dy <= 1.80 * tile_h
            if horizontal or vertical:
                neighbors[i].append(j)
                neighbors[j].append(i)

    unseen = set(range(len(detections)))
    components: list[list[int]] = []
    while unseen:
        root = unseen.pop()
        stack = [root]
        component = [root]
        while stack:
            node = stack.pop()
            for nxt in neighbors[node]:
                if nxt in unseen:
                    unseen.remove(nxt)
                    stack.append(nxt)
                    component.append(nxt)
        components.append(component)
    best = max(components, key=lambda group: (len(group), -min(group)))
    return [detections[i] for i in sorted(best)]


def _select_tile_group(
    candidates: Sequence[Detection], min_tiles: int = 2
) -> tuple[list[Detection], float, float]:
    eligible = [
        d
        for d in candidates
        if 0.68 <= d.width / max(1, d.height) <= 1.47 and d.fill_ratio >= 0.55
    ]
    if not eligible:
        return [], float("inf"), 0.0

    best: list[Detection] = []
    best_cv = float("inf")
    best_fraction = 0.0
    best_score = -float("inf")
    for reference in eligible:
        group = [
            d
            for d in eligible
            if 0.84 <= d.side / reference.side <= 1.19
        ]
        if len(group) < min_tiles:
            continue
        median_w = float(np.median([d.width for d in group]))
        median_h = float(np.median([d.height for d in group]))
        group = [
            d
            for d in group
            if abs(d.width - median_w) <= 0.17 * median_w
            and abs(d.height - median_h) <= 0.17 * median_h
        ]
        if not group:
            continue
        lattice = _largest_lattice_component(group, median_w, median_h)
        sides = np.array([d.side for d in lattice], dtype=float)
        cv = float(np.std(sides) / max(1.0, np.mean(sides)))
        fraction = len(lattice) / len(group)
        mean_fill = float(np.mean([d.fill_ratio for d in lattice]))
        score = len(lattice) * 100.0 + fraction * 8.0 + mean_fill * 3.0 - cv * 80.0
        if score > best_score:
            best_score = score
            best = lattice
            best_cv = cv
            best_fraction = fraction
    return best, best_cv, best_fraction


def detect_board(image: np.ndarray) -> DetectionReport:
    """Locate the repeated rounded-square tile lattice in an arbitrary screenshot."""
    background = _estimate_background(image)
    distance = _color_distance_from_background(image, background)

    nearest_half = distance[distance <= np.percentile(distance, 50)]
    noise = float(np.median(nearest_half)) if len(nearest_half) else 0.0
    thresholds = sorted(
        {
            round(max(9.0, noise * 1.8 + offset), 1)
            for offset in (2.0, 8.0, 15.0, 24.0, 34.0)
        }
    )

    best_report: DetectionReport | None = None
    best_score = -float("inf")
    for threshold in thresholds:
        raw = _component_candidates(image, distance, threshold)
        selected, size_cv, fraction = _select_tile_group(raw)
        if not selected:
            continue
        score = len(selected) * 100.0 + fraction * 10.0 - size_cv * 100.0
        if score > best_score:
            size_score = max(0.0, 1.0 - size_cv / 0.10)
            count_score = min(1.0, len(selected) / 8.0)
            confidence = 0.48 * size_score + 0.32 * fraction + 0.20 * count_score
            warnings: list[str] = []
            if len(selected) < 4:
                warnings.append("Very few tiles were detected; inspect debug_tiles.png.")
            if size_cv > 0.08:
                warnings.append("Detected tile sizes vary more than expected.")
            if fraction < 0.80:
                warnings.append("Several same-sized square components were rejected as UI/outliers.")
            best_report = DetectionReport(
                background_bgr=tuple(int(v) for v in background),
                threshold=threshold,
                raw_candidates=raw,
                detections=selected,
                size_cv=size_cv,
                lattice_fraction=fraction,
                confidence=confidence,
                warnings=warnings,
            )
            best_score = score

    if best_report is None or len(best_report.detections) < 2:
        raise DetectionError(
            "Could not find a repeated, connected lattice of square tiles. "
            "Use --matrix (and optionally --grid-bbox) for manual fallback."
        )
    return best_report


def _cluster_axis(values: Sequence[float], tolerance: float) -> tuple[list[float], list[int]]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    clusters: list[list[int]] = []
    for index in order:
        if not clusters:
            clusters.append([index])
            continue
        current_mean = float(np.mean([values[i] for i in clusters[-1]]))
        if abs(values[index] - current_mean) <= tolerance:
            clusters[-1].append(index)
        else:
            clusters.append([index])
    centers = [float(np.mean([values[i] for i in group])) for group in clusters]
    assignments = [-1] * len(values)
    for cluster_index, group in enumerate(clusters):
        for original_index in group:
            assignments[original_index] = cluster_index
    return centers, assignments


def _infer_axis_indices(
    centers: Sequence[float], tile_size: float
) -> tuple[list[int], float, float, list[float]]:
    if len(centers) == 1:
        return [0], tile_size * 1.12, 0.0, [float(centers[0])]
    diffs = np.diff(np.array(centers, dtype=float))
    plausible = diffs[(diffs >= 0.80 * tile_size) & (diffs <= 1.70 * tile_size)]
    if len(plausible):
        pitch = float(np.median(plausible))
    else:
        pitch = float(np.min(diffs))
    if pitch <= 0:
        raise DetectionError("Could not infer a positive grid pitch.")

    indices = [0]
    for difference in diffs:
        indices.append(indices[-1] + max(1, int(round(float(difference) / pitch))))
    idx = np.array(indices, dtype=float)
    coords = np.array(centers, dtype=float)
    design = np.column_stack([np.ones_like(idx), idx])
    origin, refined_pitch = np.linalg.lstsq(design, coords, rcond=None)[0]
    if refined_pitch > 0:
        pitch = float(refined_pitch)
        indices = [int(round((value - origin) / pitch)) for value in centers]
        minimum = min(indices)
        indices = [value - minimum for value in indices]
        idx = np.array(indices, dtype=float)
        design = np.column_stack([np.ones_like(idx), idx])
        origin, pitch = (float(v) for v in np.linalg.lstsq(design, coords, rcond=None)[0])

    fitted = [origin + pitch * value for value in indices]
    residual = max(abs(a - b) for a, b in zip(centers, fitted)) / max(1.0, pitch)
    return indices, pitch, float(residual), fitted


def _cells_connected(cells: set[Cell]) -> bool:
    if not cells:
        return False
    reached = {next(iter(cells))}
    stack = list(reached)
    while stack:
        r, c = stack.pop()
        for neighbor in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if neighbor in cells and neighbor not in reached:
                reached.add(neighbor)
                stack.append(neighbor)
    return len(reached) == len(cells)


def reconstruct_grid(detections: Sequence[Detection]) -> GridModel:
    """Cluster pixel centers into integer grid rows/columns, preserving holes."""
    if not detections:
        raise DetectionError("No tile detections were supplied.")
    tile_w = float(np.median([d.width for d in detections]))
    tile_h = float(np.median([d.height for d in detections]))
    xs = [d.center[0] for d in detections]
    ys = [d.center[1] for d in detections]
    raw_cols, col_assignment = _cluster_axis(xs, tolerance=0.30 * tile_w)
    raw_rows, row_assignment = _cluster_axis(ys, tolerance=0.30 * tile_h)
    col_indices, col_pitch, col_residual, fitted_cols = _infer_axis_indices(raw_cols, tile_w)
    row_indices, row_pitch, row_residual, fitted_rows = _infer_axis_indices(raw_rows, tile_h)

    cells: set[Cell] = set()
    centers: dict[Cell, tuple[float, float]] = {}
    by_cell: dict[Cell, Detection] = {}
    for i, detection in enumerate(detections):
        cell = (row_indices[row_assignment[i]], col_indices[col_assignment[i]])
        if cell in cells:
            raise DetectionError(f"Two detected components mapped to grid cell {cell}.")
        cells.add(cell)
        centers[cell] = detection.center
        by_cell[cell] = detection

    if not _cells_connected(cells):
        raise DetectionError(
            "The reconstructed tile graph is disconnected. Inspect debug_tiles.png "
            "or use the manual --matrix fallback."
        )
    residual = max(row_residual, col_residual)
    residual_score = max(0.0, 1.0 - residual / 0.24)
    pitch_score = min(
        1.0,
        max(0.0, col_pitch / max(1.0, tile_w) - 0.75) / 0.25,
        max(0.0, row_pitch / max(1.0, tile_h) - 0.75) / 0.25,
    )
    confidence = 0.78 * residual_score + 0.22 * pitch_score
    warnings: list[str] = []
    if residual > 0.18:
        warnings.append("Tile centers have unusually large grid-fit residuals.")
    if col_pitch < 0.90 * tile_w or row_pitch < 0.90 * tile_h:
        warnings.append("Inferred tiles overlap; grid reconstruction may be wrong.")

    max_row = max(r for r, _ in cells)
    max_col = max(c for _, c in cells)
    row_axis = [float(fitted_rows[min(range(len(row_indices)), key=lambda i: abs(row_indices[i] - r))])
                if r in row_indices else float(fitted_rows[0] + r * row_pitch)
                for r in range(max_row + 1)]
    col_axis = [float(fitted_cols[min(range(len(col_indices)), key=lambda i: abs(col_indices[i] - c))])
                if c in col_indices else float(fitted_cols[0] + c * col_pitch)
                for c in range(max_col + 1)]
    return GridModel(
        cells=cells,
        centers=centers,
        detection_for_cell=by_cell,
        row_centers=row_axis,
        col_centers=col_axis,
        row_pitch=row_pitch,
        col_pitch=col_pitch,
        tile_size=(tile_w, tile_h),
        residual_fraction=residual,
        confidence=confidence,
        warnings=warnings,
    )


def detect_start(image: np.ndarray, grid: GridModel) -> StartDetection:
    """Find the one tile whose interior color differs from the normal tiles."""
    del image  # Detection patches already carry antialias-resistant mean colors.
    ordered_cells = sorted(grid.cells)
    bgr = np.array(
        [grid.detection_for_cell[cell].mean_bgr for cell in ordered_cells], dtype=np.uint8
    ).reshape(-1, 1, 3)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32).reshape(-1, 3)
    baseline = np.median(lab, axis=0)
    distances_array = np.linalg.norm(lab - baseline, axis=1)
    order = np.argsort(distances_array)[::-1]
    best_index = int(order[0])
    best = float(distances_array[best_index])
    second = float(distances_array[int(order[1])]) if len(order) > 1 else 0.0
    separation = max(0.0, (best - second) / max(1.0, best))
    absolute = min(1.0, max(0.0, (best - 8.0) / 30.0))
    confidence = 0.62 * absolute + 0.38 * min(1.0, separation / 0.55)
    distances = {cell: float(distances_array[i]) for i, cell in enumerate(ordered_cells)}
    return StartDetection(
        cell=ordered_cells[best_index],
        color_distance=best,
        second_distance=second,
        confidence=confidence,
        distances=distances,
    )


def build_graph(cells: Iterable[Cell]) -> Graph:
    ordered = sorted(set(cells))
    if not ordered:
        raise OneLineError("The board has no tiles.")
    index = {cell: i for i, cell in enumerate(ordered)}
    adjacency = [0] * len(ordered)
    color_masks = [0, 0]
    for i, (r, c) in enumerate(ordered):
        color_masks[(r + c) & 1] |= 1 << i
        for neighbor in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if neighbor in index:
                adjacency[i] |= 1 << index[neighbor]
    return Graph(
        cells=ordered,
        index=index,
        adjacency=adjacency,
        color_masks=(color_masks[0], color_masks[1]),
    )


class HamiltonianPathSolver:
    """Bit-mask DFS with graph-theoretic pruning and fail-state caching."""

    def __init__(
        self,
        graph: Graph,
        start: Cell,
        timeout_seconds: float = 60.0,
        max_states: int = 5_000_000,
        cache_limit: int = 1_000_000,
        use_separator_pruning: bool = True,
    ) -> None:
        if start not in graph.index:
            raise OneLineError(f"Start cell {start} is not an existing tile.")
        self.graph = graph
        self.start_index = graph.index[start]
        self.timeout_seconds = timeout_seconds
        self.max_states = max_states
        self.cache_limit = cache_limit
        self.use_separator_pruning = use_separator_pruning
        self.dead: set[tuple[int, int]] = set()
        self.path: list[int] = [self.start_index]
        self.stats = SolverStats()
        self.deadline = 0.0

    def solve(self) -> list[Cell] | None:
        started = time.perf_counter()
        self.deadline = started + self.timeout_seconds
        visited = 1 << self.start_index
        try:
            solved = self._dfs(self.start_index, visited)
        finally:
            self.stats.elapsed_seconds = time.perf_counter() - started
            self.stats.cache_entries = len(self.dead)
        if not solved:
            return None
        return [self.graph.cells[i] for i in self.path]

    def _check_limits(self) -> None:
        if self.stats.states > self.max_states:
            raise SolveTimeout(
                f"Solver exceeded --max-states={self.max_states:,}. "
                "Increase the limit after confirming the detected grid."
            )
        if time.perf_counter() > self.deadline:
            raise SolveTimeout(
                f"Solver exceeded --timeout={self.timeout_seconds:g} seconds. "
                "Increase the limit after confirming the detected grid."
            )

    def _flood(self, allowed: int) -> int:
        if not allowed:
            return 0
        reached = allowed & -allowed
        frontier = reached
        while frontier:
            expansion = 0
            for node in _iter_bits(frontier):
                expansion |= self.graph.adjacency[node]
            expansion &= allowed & ~reached
            if not expansion:
                break
            reached |= expansion
            frontier = expansion
        return reached

    def _parity_ok(self, current: int, unvisited: int) -> bool:
        residual = unvisited | (1 << current)
        first_count = (residual & self.graph.color_masks[0]).bit_count()
        second_count = residual.bit_count() - first_count
        if residual.bit_count() % 2 == 0:
            return first_count == second_count
        current_color = sum(self.graph.cells[current]) & 1
        if current_color == 0:
            return first_count == second_count + 1
        return second_count == first_count + 1

    def _has_excessive_separator(self, current: int, active: int) -> bool:
        """Tarjan test: removing a path vertex may create at most two regions."""
        n = len(self.graph.cells)
        discovery = [-1] * n
        low = [0] * n
        timer = 0
        excessive = False

        def visit(node: int, parent: int) -> None:
            nonlocal timer, excessive
            discovery[node] = low[node] = timer
            timer += 1
            children = 0
            cut_children = 0
            neighbors = self.graph.adjacency[node] & active
            for nxt in _iter_bits(neighbors):
                if discovery[nxt] == -1:
                    children += 1
                    visit(nxt, node)
                    low[node] = min(low[node], low[nxt])
                    if parent != -1 and low[nxt] >= discovery[node]:
                        cut_children += 1
                elif nxt != parent:
                    low[node] = min(low[node], discovery[nxt])
            components_after_removal = children if parent == -1 else cut_children + 1
            allowed_components = 1 if node == current else 2
            if components_after_removal > allowed_components:
                excessive = True

        visit(current, -1)
        return excessive

    def _residual_feasible(self, current: int, unvisited: int) -> bool:
        if not self._parity_ok(current, unvisited):
            self.stats.parity_prunes += 1
            return False

        current_neighbors = self.graph.adjacency[current] & unvisited
        if not current_neighbors:
            self.stats.degree_prunes += 1
            return False

        if self._flood(unvisited) != unvisited:
            self.stats.connectivity_prunes += 1
            return False

        active = unvisited | (1 << current)
        degree_one = 0
        for node in _iter_bits(unvisited):
            available = self.graph.adjacency[node] & active
            degree = available.bit_count()
            if degree == 0:
                self.stats.degree_prunes += 1
                return False
            if degree == 1:
                degree_one += 1
                if available == (1 << current) and unvisited.bit_count() > 1:
                    self.stats.degree_prunes += 1
                    return False
                if degree_one > 1:
                    self.stats.degree_prunes += 1
                    return False

        if self.use_separator_pruning and active.bit_count() >= 6:
            if self._has_excessive_separator(current, active):
                self.stats.separator_prunes += 1
                return False
        return True

    def _move_key(self, node: int, unvisited: int) -> tuple[int, int, int, int]:
        after = unvisited & ~(1 << node)
        onward = (self.graph.adjacency[node] & after).bit_count()
        pressure = 0
        minimum_neighbor_degree = 5
        for neighbor in _iter_bits(self.graph.adjacency[node] & after):
            degree = (self.graph.adjacency[neighbor] & (after | (1 << node))).bit_count()
            minimum_neighbor_degree = min(minimum_neighbor_degree, degree)
            if degree <= 2:
                pressure += 1
        return onward, minimum_neighbor_degree, -pressure, node

    def _remember_dead(self, key: tuple[int, int]) -> None:
        if len(self.dead) < self.cache_limit:
            self.dead.add(key)

    def _dfs(self, current: int, visited: int) -> bool:
        self.stats.states += 1
        if (self.stats.states & 2047) == 0:
            self._check_limits()
        if visited == self.graph.all_mask:
            return True

        key = (current, visited)
        if key in self.dead:
            self.stats.cache_hits += 1
            return False

        unvisited = self.graph.all_mask ^ visited
        if not self._residual_feasible(current, unvisited):
            self._remember_dead(key)
            return False

        candidates = list(_iter_bits(self.graph.adjacency[current] & unvisited))
        candidates.sort(key=lambda node: self._move_key(node, unvisited))
        for nxt in candidates:
            self.path.append(nxt)
            if self._dfs(nxt, visited | (1 << nxt)):
                return True
            self.path.pop()
        self.stats.backtracks += 1
        self._remember_dead(key)
        return False


def solve_hamiltonian_path(
    cells: Iterable[Cell],
    start: Cell,
    timeout_seconds: float = 60.0,
    max_states: int = 5_000_000,
    cache_limit: int = 1_000_000,
    use_separator_pruning: bool = True,
) -> tuple[list[Cell] | None, SolverStats]:
    graph = build_graph(cells)
    solver = HamiltonianPathSolver(
        graph,
        start,
        timeout_seconds=timeout_seconds,
        max_states=max_states,
        cache_limit=cache_limit,
        use_separator_pruning=use_separator_pruning,
    )
    return solver.solve(), solver.stats


def validate_solution(path: Sequence[Cell], cells: Iterable[Cell], start: Cell) -> None:
    board = set(cells)
    errors: list[str] = []
    if not path:
        errors.append("path is empty")
    else:
        if path[0] != start:
            errors.append(f"path starts at {path[0]}, expected {start}")
        if len(path) != len(board):
            errors.append(f"path has {len(path)} cells, board has {len(board)}")
        if len(set(path)) != len(path):
            errors.append("path revisits at least one tile")
        nonexistent = [cell for cell in path if cell not in board]
        if nonexistent:
            errors.append(f"path uses nonexistent cells: {nonexistent[:5]}")
        for first, second in zip(path, path[1:]):
            if abs(first[0] - second[0]) + abs(first[1] - second[1]) != 1:
                errors.append(f"non-orthogonal move: {first} -> {second}")
                break
    if errors:
        raise OneLineError("Internal solution validation failed: " + "; ".join(errors))


def route_directions(path: Sequence[Cell]) -> tuple[list[str], list[str]]:
    unicode_map = {(-1, 0): "↑", (1, 0): "↓", (0, -1): "←", (0, 1): "→"}
    ascii_map = {(-1, 0): "U", (1, 0): "D", (0, -1): "L", (0, 1): "R"}
    unicode: list[str] = []
    ascii: list[str] = []
    for first, second in zip(path, path[1:]):
        delta = (second[0] - first[0], second[1] - first[1])
        unicode.append(unicode_map[delta])
        ascii.append(ascii_map[delta])
    return unicode, ascii


def board_to_text(cells: Iterable[Cell], start: Cell | None = None) -> str:
    board = set(cells)
    rows = max((r for r, _ in board), default=-1) + 1
    cols = max((c for _, c in board), default=-1) + 1
    output: list[str] = []
    for r in range(rows):
        values: list[str] = []
        for c in range(cols):
            cell = (r, c)
            if cell == start:
                values.append("S")
            else:
                values.append("1" if cell in board else "0")
        output.append(" ".join(values))
    return "\n".join(output) + "\n"


def parse_matrix(path: str | Path) -> tuple[set[Cell], Cell | None, int, int]:
    rows: list[list[str]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split() if any(ch.isspace() for ch in line) else list(line)
        normalized = [token.upper() for token in tokens]
        invalid = [token for token in normalized if token not in {"0", "1", "S"}]
        if invalid:
            raise OneLineError(
                f"Invalid token(s) on matrix line {line_number}: {', '.join(invalid)}"
            )
        rows.append(normalized)
    if not rows:
        raise OneLineError("Matrix file is empty.")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise OneLineError("All matrix rows must have the same width.")
    cells: set[Cell] = set()
    embedded_starts: list[Cell] = []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value in {"1", "S"}:
                cells.add((r, c))
            if value == "S":
                embedded_starts.append((r, c))
    if len(embedded_starts) > 1:
        raise OneLineError("Matrix contains more than one S start marker.")
    return cells, embedded_starts[0] if embedded_starts else None, len(rows), width


def _rounded_rectangle(
    image: np.ndarray,
    top_left: tuple[int, int],
    bottom_right: tuple[int, int],
    color: tuple[int, int, int],
    radius: int,
) -> None:
    x1, y1 = top_left
    x2, y2 = bottom_right
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    cv2.rectangle(image, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(image, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    for center in (
        (x1 + radius, y1 + radius),
        (x2 - radius, y1 + radius),
        (x1 + radius, y2 - radius),
        (x2 - radius, y2 - radius),
    ):
        cv2.circle(image, center, radius, color, -1, cv2.LINE_AA)


def render_manual_board(
    cells: set[Cell], start: Cell, rows: int | None = None, cols: int | None = None
) -> tuple[np.ndarray, dict[Cell, tuple[float, float]], tuple[float, float]]:
    rows = rows or max(r for r, _ in cells) + 1
    cols = cols or max(c for _, c in cells) + 1
    tile, gap, margin = 92, 18, 74
    pitch = tile + gap
    image = np.full(
        (margin * 2 + rows * pitch - gap, margin * 2 + cols * pitch - gap, 3),
        (49, 44, 41),
        dtype=np.uint8,
    )
    centers: dict[Cell, tuple[float, float]] = {}
    for r, c in cells:
        x = margin + c * pitch
        y = margin + r * pitch
        color = (240, 150, 65) if (r, c) == start else (123, 105, 98)
        _rounded_rectangle(image, (x, y), (x + tile, y + tile), color, radius=16)
        centers[(r, c)] = (x + tile / 2.0, y + tile / 2.0)
    cv2.putText(
        image,
        "MANUAL MATRIX",
        (20, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )
    return image, centers, (float(tile), float(tile))


def _draw_text_label(
    image: np.ndarray,
    anchor: tuple[int, int],
    text: str,
    color: tuple[int, int, int],
    tile_size: float,
) -> None:
    h, w = image.shape[:2]
    scale = max(0.50, min(1.15, tile_size / 145.0 * 0.72))
    thickness = max(1, int(round(scale * 2.2)))
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    padding = max(5, int(round(tile_size * 0.045)))
    candidates = [
        (anchor[0] - tw // 2, anchor[1] - int(tile_size * 0.25) - th - 2 * padding),
        (anchor[0] - tw // 2, anchor[1] + int(tile_size * 0.25) + padding),
        (anchor[0] + int(tile_size * 0.22), anchor[1] - th // 2 - padding),
        (anchor[0] - int(tile_size * 0.22) - tw - 2 * padding, anchor[1] - th // 2 - padding),
    ]
    x, y = candidates[0]
    for candidate_x, candidate_y in candidates:
        if (
            padding <= candidate_x
            and candidate_x + tw + 2 * padding < w - padding
            and padding <= candidate_y
            and candidate_y + th + baseline + 2 * padding < h - padding
        ):
            x, y = candidate_x, candidate_y
            break
    x = max(2, min(w - tw - 2 * padding - 2, x))
    y = max(2, min(h - th - baseline - 2 * padding - 2, y))
    cv2.rectangle(
        image,
        (x, y),
        (x + tw + 2 * padding, y + th + baseline + 2 * padding),
        (12, 12, 14),
        -1,
    )
    cv2.rectangle(
        image,
        (x, y),
        (x + tw + 2 * padding, y + th + baseline + 2 * padding),
        color,
        max(1, thickness),
    )
    cv2.putText(
        image,
        text,
        (x + padding, y + padding + th),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def draw_solution(
    image: np.ndarray,
    path: Sequence[Cell],
    centers: dict[Cell, tuple[float, float]],
    tile_size: tuple[float, float],
) -> np.ndarray:
    """Draw a high-contrast directed path, START marker, and END marker."""
    output = image.copy()
    points = [tuple(int(round(v)) for v in centers[cell]) for cell in path]
    base_tile = min(tile_size)
    line_width = max(4, int(round(base_tile * 0.065)))
    outline_width = line_width + max(5, int(round(base_tile * 0.045)))
    outline = (12, 15, 18)
    path_color = (25, 245, 255)  # bright yellow in BGR

    for first, second in zip(points, points[1:]):
        cv2.arrowedLine(
            output,
            first,
            second,
            outline,
            outline_width,
            cv2.LINE_AA,
            tipLength=0.19,
        )
    for first, second in zip(points, points[1:]):
        cv2.arrowedLine(
            output,
            first,
            second,
            path_color,
            line_width,
            cv2.LINE_AA,
            tipLength=0.19,
        )

    radius = max(10, int(round(base_tile * 0.13)))
    start_color = (65, 225, 70)
    end_color = (70, 75, 250)
    for point, color in ((points[0], start_color), (points[-1], end_color)):
        cv2.circle(output, point, radius + 5, outline, -1, cv2.LINE_AA)
        cv2.circle(output, point, radius, color, -1, cv2.LINE_AA)
        cv2.circle(output, point, radius, (255, 255, 255), 2, cv2.LINE_AA)
    _draw_text_label(output, points[0], "START", start_color, base_tile)
    _draw_text_label(output, points[-1], "END", end_color, base_tile)
    return output


def draw_debug_tiles(
    image: np.ndarray,
    report: DetectionReport,
    start: Cell | None = None,
    grid: GridModel | None = None,
) -> np.ndarray:
    output = image.copy()
    selected_ids = {d.component_id for d in report.detections}
    for detection in report.raw_candidates:
        x, y, w, h = detection.bbox
        selected = detection.component_id in selected_ids
        color = (60, 225, 80) if selected else (0, 180, 255)
        cv2.rectangle(output, (x, y), (x + w, y + h), color, 3 if selected else 2)
    if start is not None and grid is not None:
        detection = grid.detection_for_cell[start]
        x, y, w, h = detection.bbox
        cv2.rectangle(output, (x - 4, y - 4), (x + w + 4, y + h + 4), (255, 0, 255), 6)
    text = (
        f"tiles={len(report.detections)} threshold={report.threshold:.1f} "
        f"confidence={report.confidence:.2f}"
    )
    scale = max(0.55, image.shape[1] / 1320.0 * 0.80)
    cv2.putText(output, text, (18, 42), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(output, text, (18, 42), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
    return output


def draw_debug_grid(
    image: np.ndarray,
    cells: set[Cell],
    centers: dict[Cell, tuple[float, float]],
    tile_size: tuple[float, float],
    start: Cell,
    confidence: float | None = None,
) -> np.ndarray:
    output = image.copy()
    tile = min(tile_size)
    for r, c in sorted(cells):
        point = tuple(int(round(v)) for v in centers[(r, c)])
        for neighbor in ((r + 1, c), (r, c + 1)):
            if neighbor in cells:
                other = tuple(int(round(v)) for v in centers[neighbor])
                cv2.line(output, point, other, (220, 210, 40), max(2, int(tile * 0.018)), cv2.LINE_AA)
    font_scale = max(0.34, min(0.72, tile / 170.0 * 0.58))
    thickness = max(1, int(round(font_scale * 2)))
    for cell in sorted(cells):
        x, y = (int(round(v)) for v in centers[cell])
        color = (255, 0, 255) if cell == start else (255, 255, 255)
        cv2.circle(output, (x, y), max(5, int(tile * 0.052)), (20, 20, 20), -1, cv2.LINE_AA)
        cv2.circle(output, (x, y), max(3, int(tile * 0.032)), color, -1, cv2.LINE_AA)
        label = f"{cell[0]},{cell[1]}"
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.putText(
            output,
            label,
            (x - tw // 2, y - max(10, int(tile * 0.11))),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            thickness + 3,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            label,
            (x - tw // 2, y - max(10, int(tile * 0.11))),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
            cv2.LINE_AA,
        )
    header = f"grid={max(r for r, _ in cells)+1}x{max(c for _, c in cells)+1} tiles={len(cells)} start={start}"
    if confidence is not None:
        header += f" confidence={confidence:.2f}"
    cv2.putText(output, header, (18, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(output, header, (18, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return output


def _parse_cell(value: str, option: str) -> Cell:
    try:
        first, second = value.split(",", 1)
        return int(first.strip()), int(second.strip())
    except (ValueError, AttributeError) as exc:
        raise OneLineError(f"{option} must be ROW,COLUMN using zero-based integers.") from exc


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise OneLineError("--grid-bbox must be LEFT,TOP,RIGHT,BOTTOM.") from exc
    if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
        raise OneLineError("--grid-bbox must be LEFT,TOP,RIGHT,BOTTOM with positive size.")
    return values  # type: ignore[return-value]


def _centers_from_bbox(
    cells: set[Cell], rows: int, cols: int, bbox: tuple[float, float, float, float]
) -> tuple[dict[Cell, tuple[float, float]], tuple[float, float]]:
    left, top, right, bottom = bbox
    pitch_x = (right - left) / cols
    pitch_y = (bottom - top) / rows
    centers = {
        (r, c): (left + (c + 0.5) * pitch_x, top + (r + 0.5) * pitch_y)
        for r, c in cells
    }
    return centers, (pitch_x * 0.88, pitch_y * 0.88)


def _safe_write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OneLineError(f"Could not write image: {path}")


def _write_results(
    output_dir: Path,
    original: np.ndarray,
    cells: set[Cell],
    start: Cell,
    centers: dict[Cell, tuple[float, float]],
    tile_size: tuple[float, float],
    path: list[Cell],
    stats: SolverStats,
    metadata: dict[str, object],
) -> None:
    validate_solution(path, cells, start)
    unicode_directions, ascii_directions = route_directions(path)
    solution = draw_solution(original, path, centers, tile_size)
    _safe_write_image(output_dir / "solution.png", solution)
    route_text = (
        f"Validated: yes\n"
        f"Tiles: {len(cells)}\n"
        f"Start: {start}\n"
        f"End: {path[-1]}\n"
        f"Directions: {' '.join(unicode_directions)}\n"
        f"ASCII: {''.join(ascii_directions)}\n"
        f"Coordinates: {path}\n"
    )
    (output_dir / "route.txt").write_text(route_text, encoding="utf-8")
    result = {
        "validated": True,
        "tile_count": len(cells),
        "start": list(start),
        "end": list(path[-1]),
        "directions": unicode_directions,
        "directions_ascii": ascii_directions,
        "path": [list(cell) for cell in path],
        "solver_stats": asdict(stats),
        **metadata,
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _run_automatic(args: argparse.Namespace) -> int:
    image = load_image(args.screenshot)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = detect_board(image)
    grid = reconstruct_grid(report.detections)
    start_report = detect_start(image, grid)
    start = _parse_cell(args.start, "--start") if args.start else start_report.cell
    if start not in grid.cells:
        raise OneLineError(f"Start override {start} is not a detected tile.")

    overall_confidence = min(report.confidence, grid.confidence)
    if not args.start:
        overall_confidence = min(overall_confidence, start_report.confidence)
    warnings = report.warnings + grid.warnings
    if start_report.color_distance < 12.0 and not args.start:
        warnings.append("The colored start tile is not strongly separated from normal tiles.")
    _safe_write_image(output_dir / "debug_tiles.png", draw_debug_tiles(image, report, start, grid))
    _safe_write_image(
        output_dir / "debug_grid.png",
        draw_debug_grid(
            image,
            grid.cells,
            grid.centers,
            grid.tile_size,
            start,
            confidence=overall_confidence,
        ),
    )
    (output_dir / "detected_board.txt").write_text(
        board_to_text(grid.cells, start), encoding="utf-8"
    )
    if overall_confidence < args.min_confidence and not args.accept_low_confidence:
        raise DetectionError(
            f"Detection confidence {overall_confidence:.2f} is below "
            f"--min-confidence={args.min_confidence:.2f}. Inspect the debug images, "
            "override --start, pass --accept-low-confidence, or use --matrix."
        )

    path, stats = solve_hamiltonian_path(
        grid.cells,
        start,
        timeout_seconds=args.timeout,
        max_states=args.max_states,
        cache_limit=args.cache_limit,
        use_separator_pruning=not args.no_separator_pruning,
    )
    if path is None:
        raise NoSolutionError(
            "No Hamiltonian path exists for the detected board/start. Inspect "
            "debug_grid.png and detected_board.txt before changing solver limits."
        )
    validate_solution(path, grid.cells, start)
    metadata: dict[str, object] = {
        "mode": "automatic",
        "source_image": str(Path(args.screenshot)),
        "detection": {
            "confidence": overall_confidence,
            "tile_confidence": report.confidence,
            "grid_confidence": grid.confidence,
            "start_confidence": start_report.confidence,
            "threshold": report.threshold,
            "background_bgr": list(report.background_bgr),
            "grid_shape": [grid.rows, grid.cols],
            "grid_residual_fraction": grid.residual_fraction,
            "start_color_distance": start_report.color_distance,
            "second_color_distance": start_report.second_distance,
            "warnings": warnings,
        },
    }
    _write_results(
        output_dir,
        image,
        grid.cells,
        start,
        grid.centers,
        grid.tile_size,
        path,
        stats,
        metadata,
    )
    _print_success(output_dir, path, stats, overall_confidence)
    return 0


def _run_manual(args: argparse.Namespace) -> int:
    cells, embedded_start, rows, cols = parse_matrix(args.matrix)
    start = _parse_cell(args.start, "--start") if args.start else embedded_start
    if start is None:
        raise OneLineError("Manual mode requires --start ROW,COL or one S in the matrix.")
    if start not in cells:
        raise OneLineError(f"Manual start {start} is not a 1/S cell.")
    if not _cells_connected(cells):
        raise OneLineError("Manual board is disconnected, so no Hamiltonian path can exist.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report: DetectionReport | None = None
    grid: GridModel | None = None
    if args.screenshot:
        image = load_image(args.screenshot)
        if args.grid_bbox:
            centers, tile_size = _centers_from_bbox(
                cells, rows, cols, _parse_bbox(args.grid_bbox)
            )
        else:
            try:
                report = detect_board(image)
                grid = reconstruct_grid(report.detections)
            except DetectionError:
                grid = None
            if grid is not None and grid.rows == rows and grid.cols == cols:
                centers = {
                    cell: (grid.col_centers[cell[1]], grid.row_centers[cell[0]])
                    for cell in cells
                }
                tile_size = grid.tile_size
            elif not args.grid_bbox:
                print(
                    "Warning: screenshot geometry did not match the manual matrix; "
                    "rendering a schematic solution. Supply --grid-bbox to overlay it.",
                    file=sys.stderr,
                )
                image, centers, tile_size = render_manual_board(cells, start, rows, cols)
    else:
        image, centers, tile_size = render_manual_board(cells, start, rows, cols)

    path, stats = solve_hamiltonian_path(
        cells,
        start,
        timeout_seconds=args.timeout,
        max_states=args.max_states,
        cache_limit=args.cache_limit,
        use_separator_pruning=not args.no_separator_pruning,
    )
    if path is None:
        raise NoSolutionError("No Hamiltonian path exists for the manual board/start.")
    validate_solution(path, cells, start)

    if report is not None and grid is not None:
        _safe_write_image(output_dir / "debug_tiles.png", draw_debug_tiles(image, report))
    else:
        _safe_write_image(output_dir / "debug_tiles.png", image)
    _safe_write_image(
        output_dir / "debug_grid.png",
        draw_debug_grid(image, cells, centers, tile_size, start),
    )
    (output_dir / "detected_board.txt").write_text(
        board_to_text(cells, start), encoding="utf-8"
    )
    metadata = {
        "mode": "manual",
        "matrix_file": str(Path(args.matrix)),
        "source_image": str(Path(args.screenshot)) if args.screenshot else None,
        "grid_shape": [rows, cols],
    }
    _write_results(
        output_dir,
        image,
        cells,
        start,
        centers,
        tile_size,
        path,
        stats,
        metadata,
    )
    _print_success(output_dir, path, stats, None)
    return 0


def _print_success(
    output_dir: Path,
    path: Sequence[Cell],
    stats: SolverStats,
    confidence: float | None,
) -> None:
    unicode_directions, ascii_directions = route_directions(path)
    print(f"VALIDATED SOLUTION: {len(path)} tiles, start {path[0]}, end {path[-1]}")
    if confidence is not None:
        print(f"Detection confidence: {confidence:.3f}")
    print(f"Directions: {' '.join(unicode_directions)}")
    print(f"ASCII: {''.join(ascii_directions)}")
    print(f"Coordinates: {list(path)}")
    print(
        f"Search: {stats.states:,} states, {stats.backtracks:,} backtracks, "
        f"{stats.elapsed_seconds:.3f}s"
    )
    print(f"Wrote: {output_dir / 'debug_tiles.png'}")
    print(f"Wrote: {output_dir / 'debug_grid.png'}")
    print(f"Wrote: {output_dir / 'solution.png'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect, solve, validate, and annotate One Line puzzle screenshots. "
            "Grid coordinates printed by the program are zero-based."
        )
    )
    parser.add_argument(
        "screenshot",
        nargs="?",
        help="PNG/JPEG screenshot. Optional in manual --matrix mode.",
    )
    parser.add_argument(
        "--matrix",
        help="Manual 0/1/S board matrix. S may mark the start tile.",
    )
    parser.add_argument(
        "--start",
        help="Override/define the zero-based start as ROW,COLUMN.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="one_line_output",
        help="Output directory (default: one_line_output).",
    )
    parser.add_argument(
        "--grid-bbox",
        help=(
            "Manual overlay box LEFT,TOP,RIGHT,BOTTOM around the full matrix grid. "
            "Only used with --matrix and a screenshot."
        ),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-states", type=int, default=5_000_000)
    parser.add_argument("--cache-limit", type=int, default=1_000_000)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--accept-low-confidence", action="store_true")
    parser.add_argument("--no-separator-pruning", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.screenshot and not args.matrix:
        parser.error("provide a screenshot or --matrix")
    if args.grid_bbox and (not args.matrix or not args.screenshot):
        parser.error("--grid-bbox requires both --matrix and a screenshot")
    try:
        return _run_manual(args) if args.matrix else _run_automatic(args)
    except (OneLineError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
