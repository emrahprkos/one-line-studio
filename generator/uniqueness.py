from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

from solve_one_line import Graph, build_graph, validate_solution

from .models import Cell, ConstructionCertificate, TopologyCandidate, UniquenessStats
from .topology import graph_degree, neighbors, path_is_induced


class VerificationCancelled(RuntimeError):
    pass


class VerificationTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class CertificateResult:
    valid: bool
    reason: str
    reconstructed_path: tuple[Cell, ...] | None


@dataclass(frozen=True)
class UniquenessResult:
    unique: bool
    solutions: tuple[tuple[Cell, ...], ...]
    stats: UniquenessStats
    first_solution_seconds: float


def _orthogonally_adjacent(first: Cell, second: Cell) -> bool:
    return abs(first[0] - second[0]) + abs(first[1] - second[1]) == 1


def verify_construction_certificate(candidate: TopologyCandidate) -> CertificateResult:
    """Independently replay and prove the ear-expansion construction invariant."""
    certificate = candidate.certificate
    if certificate.method != "induced_path_ear_expansion_v1":
        return CertificateResult(False, "unsupported certificate method", None)
    path = list(certificate.base_path)
    if len(path) < 2 or not path_is_induced(path):
        return CertificateResult(False, "base path is not an induced path", None)
    cells = set(path)

    for step_number, step in enumerate(certificate.ears, 1):
        u, v = step.edge
        first, second = step.added
        if first in cells or second in cells or first == second:
            return CertificateResult(False, f"ear {step_number} reuses a cell", None)
        try:
            edge_index = next(
                index
                for index in range(len(path) - 1)
                if path[index] == u and path[index + 1] == v
            )
        except StopIteration:
            return CertificateResult(
                False, f"ear {step_number} does not replace a current path edge", None
            )
        if not (
            _orthogonally_adjacent(u, v)
            and _orthogonally_adjacent(u, first)
            and _orthogonally_adjacent(first, second)
            and _orthogonally_adjacent(second, v)
        ):
            return CertificateResult(False, f"ear {step_number} has invalid geometry", None)
        if {cell for cell in neighbors(first) if cell in cells} != {u}:
            return CertificateResult(False, f"ear {step_number} first cell has a cross-edge", None)
        if {cell for cell in neighbors(second) if cell in cells} != {v}:
            return CertificateResult(False, f"ear {step_number} second cell has a cross-edge", None)
        path[edge_index + 1 : edge_index + 1] = [first, second]
        cells.update((first, second))

    if cells != set(candidate.cells):
        return CertificateResult(False, "certificate cell set differs from candidate", None)
    known = list(candidate.solution)
    if path != known and list(reversed(path)) != known:
        return CertificateResult(False, "certificate path differs from known solution", None)
    if graph_degree(path[0], cells) != 1 or graph_degree(path[-1], cells) != 1:
        return CertificateResult(False, "both construction endpoints must remain degree one", None)
    return CertificateResult(True, "ear-expansion uniqueness invariant verified", tuple(path))


def _iter_bits(mask: int) -> Iterator[int]:
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


class HamiltonianUniquenessVerifier:
    """Exact stop-at-two Hamiltonian path counter for a fixed start.

    This verifier has no dependency on how a candidate was generated.  It uses
    the known route only for search ordering; all other routes remain eligible.
    """

    def __init__(
        self,
        cells: Sequence[Cell] | set[Cell] | frozenset[Cell],
        start: Cell,
        known_solution: Sequence[Cell] | None = None,
        timeout_seconds: float = 10.0,
        max_nodes: int = 5_000_000,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        self.graph: Graph = build_graph(cells)
        if start not in self.graph.index:
            raise ValueError("Start cell is not on the board.")
        self.start = self.graph.index[start]
        self.known_indices = (
            [self.graph.index[cell] for cell in known_solution]
            if known_solution is not None
            else None
        )
        self.timeout_seconds = timeout_seconds
        self.max_nodes = max_nodes
        self.cancel_check = cancel_check
        self.progress = progress
        self.dead: set[tuple[int, int]] = set()
        self.path: list[int] = [self.start]
        self.solutions: list[tuple[Cell, ...]] = []
        self.started = 0.0
        self.deadline = 0.0
        self.first_solution_seconds = 0.0
        self.nodes = 0
        self.backtracks = 0
        self.forced_moves = 0
        self.cache_hits = 0
        self.connectivity_checks = 0
        self.connectivity_prunes = 0
        self.degree_prunes = 0
        self.parity_prunes = 0
        self.separator_checks = 0
        self.separator_prunes = 0
        self.max_depth = 1

    def run(self, limit: int = 2) -> UniquenessResult:
        if limit < 1:
            raise ValueError("Solution limit must be positive.")
        self.started = time.perf_counter()
        self.deadline = self.started + self.timeout_seconds
        self._search(self.start, 1 << self.start, limit)
        elapsed = time.perf_counter() - self.started
        stats = UniquenessStats(
            solutions_found=len(self.solutions),
            nodes_explored=self.nodes,
            backtracks=self.backtracks,
            forced_moves=self.forced_moves,
            cache_hits=self.cache_hits,
            connectivity_checks=self.connectivity_checks,
            connectivity_prunes=self.connectivity_prunes,
            degree_prunes=self.degree_prunes,
            parity_prunes=self.parity_prunes,
            separator_checks=self.separator_checks,
            separator_prunes=self.separator_prunes,
            elapsed_seconds=elapsed,
            max_depth=self.max_depth,
        )
        return UniquenessResult(
            unique=len(self.solutions) == 1,
            solutions=tuple(self.solutions),
            stats=stats,
            first_solution_seconds=self.first_solution_seconds,
        )

    def _check_budget(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise VerificationCancelled("Uniqueness verification cancelled.")
        if self.nodes > self.max_nodes:
            raise VerificationTimeout(f"Uniqueness verifier exceeded {self.max_nodes:,} nodes.")
        if time.perf_counter() > self.deadline:
            raise VerificationTimeout("Uniqueness verifier exceeded its time budget.")
        if self.progress is not None:
            self.progress(self.nodes)

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
        first = (residual & self.graph.color_masks[0]).bit_count()
        second = residual.bit_count() - first
        if residual.bit_count() % 2 == 0:
            return first == second
        color = sum(self.graph.cells[current]) & 1
        return first == second + 1 if color == 0 else second == first + 1

    def _excessive_separator(self, current: int, active: int) -> bool:
        self.separator_checks += 1
        size = len(self.graph.cells)
        discovery = [-1] * size
        low = [0] * size
        timer = 0
        excessive = False

        def visit(node: int, parent: int) -> None:
            nonlocal timer, excessive
            discovery[node] = low[node] = timer
            timer += 1
            children = 0
            cut_children = 0
            for nxt in _iter_bits(self.graph.adjacency[node] & active):
                if discovery[nxt] == -1:
                    children += 1
                    visit(nxt, node)
                    low[node] = min(low[node], low[nxt])
                    if parent != -1 and low[nxt] >= discovery[node]:
                        cut_children += 1
                elif nxt != parent:
                    low[node] = min(low[node], discovery[nxt])
            components = children if parent == -1 else cut_children + 1
            if components > (1 if node == current else 2):
                excessive = True

        visit(current, -1)
        return excessive

    def _residual_feasible(self, current: int, unvisited: int) -> bool:
        if not self._parity_ok(current, unvisited):
            self.parity_prunes += 1
            return False
        current_neighbors = self.graph.adjacency[current] & unvisited
        if not current_neighbors:
            self.degree_prunes += 1
            return False

        self.connectivity_checks += 1
        if self._flood(unvisited) != unvisited:
            self.connectivity_prunes += 1
            return False

        active = unvisited | (1 << current)
        endpoint_count = 0
        unvisited_count = unvisited.bit_count()
        for node in _iter_bits(unvisited):
            available = self.graph.adjacency[node] & active
            degree = available.bit_count()
            if degree == 0:
                self.degree_prunes += 1
                return False
            if degree == 1:
                endpoint_count += 1
                if available == (1 << current) and unvisited_count > 1:
                    self.degree_prunes += 1
                    return False
                if endpoint_count > 1:
                    self.degree_prunes += 1
                    return False

        if active.bit_count() >= 7 and self._excessive_separator(current, active):
            self.separator_prunes += 1
            return False
        return True

    def _candidate_key(self, node: int, unvisited: int, depth: int) -> tuple[int, int, int]:
        known_penalty = 1
        if self.known_indices is not None and depth < len(self.known_indices):
            if node == self.known_indices[depth]:
                known_penalty = 0
        onward = (self.graph.adjacency[node] & (unvisited & ~(1 << node))).bit_count()
        return known_penalty, onward, node

    def _search(self, current: int, visited: int, limit: int) -> int:
        self.nodes += 1
        self.max_depth = max(self.max_depth, len(self.path))
        if (self.nodes & 255) == 0:
            self._check_budget()
        if visited == self.graph.all_mask:
            route = tuple(self.graph.cells[index] for index in self.path)
            self.solutions.append(route)
            if len(self.solutions) == 1:
                self.first_solution_seconds = time.perf_counter() - self.started
            return 1

        key = (current, visited)
        if key in self.dead:
            self.cache_hits += 1
            return 0
        unvisited = self.graph.all_mask ^ visited
        if not self._residual_feasible(current, unvisited):
            self.dead.add(key)
            return 0

        candidates = list(_iter_bits(self.graph.adjacency[current] & unvisited))
        if len(candidates) == 1:
            self.forced_moves += 1
        depth = len(self.path)
        candidates.sort(key=lambda node: self._candidate_key(node, unvisited, depth))
        found = 0
        for nxt in candidates:
            self.path.append(nxt)
            found += self._search(nxt, visited | (1 << nxt), limit - found)
            self.path.pop()
            if found >= limit or len(self.solutions) >= limit:
                break
        if found == 0:
            self.dead.add(key)
            self.backtracks += 1
        return found


def prove_unique(
    cells: Sequence[Cell] | set[Cell] | frozenset[Cell],
    start: Cell,
    known_solution: Sequence[Cell] | None,
    timeout_seconds: float,
    max_nodes: int,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
) -> UniquenessResult:
    if known_solution is not None:
        validate_solution(known_solution, cells, start)
    verifier = HamiltonianUniquenessVerifier(
        cells=cells,
        start=start,
        known_solution=known_solution,
        timeout_seconds=timeout_seconds,
        max_nodes=max_nodes,
        cancel_check=cancel_check,
        progress=progress,
    )
    return verifier.run(limit=2)
