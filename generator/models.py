from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


Cell = tuple[int, int]
GENERATOR_VERSION = "1.0"
SEED_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DifficultyTier(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"
    EVIL = "evil"


class ShapeMode(str, Enum):
    NORMAL = "normal"
    EXTREME = "extreme"


TIER_RANGES: dict[DifficultyTier, tuple[int, int]] = {
    DifficultyTier.EASY: (0, 119),
    DifficultyTier.MEDIUM: (120, 239),
    DifficultyTier.HARD: (240, 359),
    DifficultyTier.EXPERT: (360, 479),
    DifficultyTier.EVIL: (480, 600),
}


@dataclass(frozen=True)
class OutputOptions:
    visual_grid: bool = True
    polished_png: bool = True
    binary_matrix: bool = True

    def validate(self) -> None:
        if not (self.visual_grid or self.polished_png or self.binary_matrix):
            raise ValueError("At least one output must be enabled.")

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationSettings:
    width: int
    height: int
    difficulty: DifficultyTier
    shape_mode: ShapeMode
    seed: str
    outputs: OutputOptions = field(default_factory=OutputOptions)
    max_attempts: int = 80
    time_budget_seconds: float = 20.0

    def validate(self) -> None:
        if not 3 <= self.width <= 20:
            raise ValueError("Width must be between 3 and 20.")
        if not 3 <= self.height <= 20:
            raise ValueError("Height must be between 3 and 20.")
        if not isinstance(self.difficulty, DifficultyTier):
            raise ValueError("Unsupported difficulty tier.")
        if not isinstance(self.shape_mode, ShapeMode):
            raise ValueError("Unsupported shape mode.")
        if not SEED_PATTERN.fullmatch(self.seed):
            raise ValueError("Seed must be 1–64 letters, numbers, underscores, or hyphens.")
        if not 1 <= self.max_attempts <= 500:
            raise ValueError("max_attempts must be between 1 and 500.")
        if not 0.25 <= self.time_budget_seconds <= 120.0:
            raise ValueError("time_budget_seconds must be between 0.25 and 120.")
        self.outputs.validate()

    @property
    def target_range(self) -> tuple[int, int]:
        return TIER_RANGES[self.difficulty]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "generator_version": GENERATOR_VERSION,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "difficulty": self.difficulty.value,
            "shape_mode": self.shape_mode.value,
        }

    def cache_key(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def attempt_seed(self, attempt: int) -> int:
        payload = {**self.canonical_payload(), "attempt": attempt}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return int.from_bytes(hashlib.blake2b(raw, digest_size=16).digest(), "big")


@dataclass(frozen=True)
class EarStep:
    edge: tuple[Cell, Cell]
    added: tuple[Cell, Cell]

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge": [list(self.edge[0]), list(self.edge[1])],
            "added": [list(self.added[0]), list(self.added[1])],
        }


@dataclass(frozen=True)
class ConstructionCertificate:
    method: str
    base_path: tuple[Cell, ...]
    ears: tuple[EarStep, ...]

    def as_dict(self, include_steps: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "method": self.method,
            "base_path_length": len(self.base_path),
            "ear_count": len(self.ears),
        }
        if include_steps:
            result["base_path"] = [list(cell) for cell in self.base_path]
            result["ears"] = [ear.as_dict() for ear in self.ears]
        return result


@dataclass(frozen=True)
class TopologyCandidate:
    width: int
    height: int
    cells: frozenset[Cell]
    solution: tuple[Cell, ...]
    start: Cell
    end: Cell
    certificate: ConstructionCertificate
    topology_hash: str
    aesthetic_score: float
    aesthetic_metrics: dict[str, float]


@dataclass(frozen=True)
class DifficultyMetrics:
    score: int
    tier: DifficultyTier
    forced_move_count: int
    forced_move_ratio: float
    branch_points: int
    branch_ratio: float
    plausible_wrong_moves: int
    first_decision_fraction: float
    average_wrong_branch_survival: float
    maximum_wrong_branch_survival: int
    maximum_wrong_branch_fraction: float
    temptation_score: float
    turn_count: int
    turn_ratio: float
    extra_adjacencies: int
    loop_ratio: float
    articulation_points: int
    bottleneck_ratio: float
    cavity_count: int
    pseudo_symmetry: float
    local_ambiguity: float
    agent_backtrack_score: float
    explanation: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["tier"] = self.tier.value
        result["explanation"] = list(self.explanation)
        return result


@dataclass(frozen=True)
class UniquenessStats:
    solutions_found: int
    nodes_explored: int
    backtracks: int
    forced_moves: int
    cache_hits: int
    connectivity_checks: int
    connectivity_prunes: int
    degree_prunes: int
    parity_prunes: int
    separator_checks: int
    separator_prunes: int
    elapsed_seconds: float
    max_depth: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationDiagnostics:
    attempts: int
    candidates_rejected: int
    rejection_reasons: dict[str, int]
    candidate_generation_seconds: float
    first_solution_seconds: float
    uniqueness_check_seconds: float
    difficulty_seconds: float
    rendering_seconds: float
    total_seconds: float
    peak_memory_mb: float | None
    solver_nodes_explored: int
    uniqueness_nodes_explored: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GeneratedLevel:
    settings: GenerationSettings
    cells: frozenset[Cell]
    start: Cell
    end: Cell
    solution: tuple[Cell, ...]
    difficulty: DifficultyMetrics
    unique: bool
    validated: bool
    topology_hash: str
    canonical_shape_hash: str
    certificate: ConstructionCertificate
    uniqueness_stats: UniquenessStats
    diagnostics: GenerationDiagnostics
    matrix: tuple[tuple[int, ...], ...]
    unsolved_png: bytes | None = None
    solved_png: bytes | None = None

    @property
    def tile_count(self) -> int:
        return len(self.cells)

    @property
    def density(self) -> float:
        return self.tile_count / (self.settings.width * self.settings.height)

    def export_dict(self, reveal_solution: bool = True) -> dict[str, Any]:
        degree_distribution: dict[str, int] = {}
        for row, column in self.cells:
            degree = sum(
                neighbor in self.cells
                for neighbor in (
                    (row - 1, column),
                    (row + 1, column),
                    (row, column - 1),
                    (row, column + 1),
                )
            )
            key = str(degree)
            degree_distribution[key] = degree_distribution.get(key, 0) + 1
        directions = [
            (second[0] - first[0], second[1] - first[1])
            for first, second in zip(self.solution, self.solution[1:])
        ]
        turns = "".join(
            "1" if directions[index] != directions[index - 1] else "0"
            for index in range(1, len(directions))
        )
        turn_signature = hashlib.sha256(
            min(turns, turns[::-1]).encode("ascii")
        ).hexdigest()
        result: dict[str, Any] = {
            "version": 1,
            "generator_version": GENERATOR_VERSION,
            "seed": self.settings.seed,
            "width": self.settings.width,
            "height": self.settings.height,
            "matrix": [list(row) for row in self.matrix],
            "start": list(self.start),
            "end": list(self.end),
            "tile_count": self.tile_count,
            "density": self.density,
            "difficulty": self.difficulty.as_dict(),
            "requested_difficulty": self.settings.difficulty.value,
            "shape_mode": self.settings.shape_mode.value,
            "unique": self.unique,
            "validated": self.validated,
            "topology_hash": self.topology_hash,
            "canonical_shape_hash": self.canonical_shape_hash,
            "topology_features": {
                "degree_distribution": degree_distribution,
                "solution_turn_signature": turn_signature,
                "start_relative": [
                    self.start[0] / max(1, self.settings.height - 1),
                    self.start[1] / max(1, self.settings.width - 1),
                ],
            },
            "certificate": self.certificate.as_dict(include_steps=False),
            "uniqueness_stats": self.uniqueness_stats.as_dict(),
            "generation": self.diagnostics.as_dict(),
        }
        if reveal_solution:
            result["solution"] = [list(cell) for cell in self.solution]
        return result


def tier_for_score(score: int) -> DifficultyTier:
    score = max(0, min(600, int(score)))
    for tier, (low, high) in TIER_RANGES.items():
        if low <= score <= high:
            return tier
    return DifficultyTier.EVIL
