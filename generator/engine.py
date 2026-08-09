from __future__ import annotations

import random
import time
from collections import Counter
from typing import Any, Callable

from solve_one_line import OneLineError, SolveTimeout, solve_hamiltonian_path, validate_solution

from .difficulty import score_human_difficulty
from .models import (
    GeneratedLevel,
    GenerationDiagnostics,
    GenerationSettings,
    TopologyCandidate,
)
from .render import render_level_png, verify_render_manifest, verify_unsolved_png_pixels
from .topology import (
    canonical_shape_hash,
    cells_connected,
    generate_topology,
    matrix_from_cells,
)
from .uniqueness import (
    VerificationCancelled,
    VerificationTimeout,
    prove_unique,
    verify_construction_certificate,
)


ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class GenerationCancelled(RuntimeError):
    """Raised when a caller cancels generation."""


class GenerationFailure(RuntimeError):
    """Raised when no candidate reaches the requested quality inside the budget."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 0,
        rejection_reasons: dict[str, int] | None = None,
        best_score: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.rejection_reasons = rejection_reasons or {}
        self.best_score = best_score


class _ProgressReporter:
    def __init__(self, callback: ProgressCallback | None, started: float) -> None:
        self.callback = callback
        self.started = started
        self.maximum_percent = 0

    def send(
        self,
        percent: int,
        state: str,
        phase: str,
        message: str,
        *,
        attempt: int = 0,
        best_score: int | None = None,
        target_range: tuple[int, int] | None = None,
        uniqueness_status: str | None = None,
        verifier_nodes: int | None = None,
    ) -> None:
        if self.callback is None:
            return
        self.maximum_percent = max(self.maximum_percent, max(0, min(100, percent)))
        payload: dict[str, Any] = {
            "percent": self.maximum_percent,
            "state": state,
            "phase": phase,
            "message": message,
            "attempt": attempt,
            "elapsed_seconds": round(time.perf_counter() - self.started, 3),
        }
        if best_score is not None:
            payload["best_score"] = best_score
        if target_range is not None:
            payload["target_range"] = list(target_range)
        if uniqueness_status is not None:
            payload["uniqueness_status"] = uniqueness_status
        if verifier_nodes is not None:
            payload["verifier_nodes"] = verifier_nodes
        self.callback(payload)


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise GenerationCancelled("Generation was cancelled.")


def _aesthetic_threshold(settings: GenerationSettings) -> float:
    # Normal deliberately rejects weak silhouettes. Extreme still has a low
    # quality floor so "unusual" does not become arbitrary noise.
    if settings.shape_mode.value == "normal":
        return 0.32 if settings.difficulty.value == "easy" else 0.36
    return 0.22


def _remaining_seconds(started: float, settings: GenerationSettings) -> float:
    return settings.time_budget_seconds - (time.perf_counter() - started)


def _independent_candidate_validation(candidate: TopologyCandidate) -> None:
    if not candidate.cells:
        raise ValueError("candidate board is empty")
    if any(
        not (0 <= row < candidate.height and 0 <= column < candidate.width)
        for row, column in candidate.cells
    ):
        raise ValueError("candidate has out-of-range cells")
    if not cells_connected(candidate.cells):
        raise ValueError("candidate board is disconnected")
    if candidate.start not in candidate.cells or candidate.end not in candidate.cells:
        raise ValueError("candidate endpoint is not on the board")
    validate_solution(candidate.solution, candidate.cells, candidate.start)
    if candidate.solution[-1] != candidate.end:
        raise ValueError("known solution end differs from candidate end")


def _replay_determinism(
    settings: GenerationSettings,
    attempt: int,
    accepted: TopologyCandidate,
) -> None:
    replay_rng = random.Random(settings.attempt_seed(attempt))
    replay = generate_topology(settings, replay_rng, attempt)
    if (
        replay.topology_hash != accepted.topology_hash
        or replay.cells != accepted.cells
        or replay.start != accepted.start
        or replay.solution != accepted.solution
    ):
        raise RuntimeError("deterministic seed replay did not reproduce the candidate")


def _difficulty_distance(score: int, target_range: tuple[int, int]) -> int:
    low, high = target_range
    if score < low:
        return low - score
    if score > high:
        return score - high
    return 0


def generate_level(
    settings: GenerationSettings,
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> GeneratedLevel:
    """Generate, independently verify, score, validate, and render one level.

    All randomness is derived from ``GenerationSettings.attempt_seed``.  Output
    selections never affect topology, so the same version/settings/seed always
    reproduce the same board and start cell.
    """

    settings.validate()
    started = time.perf_counter()
    reporter = _ProgressReporter(progress, started)
    reporter.send(
        3,
        "generating",
        "initialize",
        "Initializing deterministic generator",
        target_range=settings.target_range,
    )

    rejections: Counter[str] = Counter()
    candidate_seconds = 0.0
    first_solution_seconds = 0.0
    uniqueness_seconds = 0.0
    difficulty_seconds = 0.0
    total_solver_nodes = 0
    total_uniqueness_nodes = 0
    best_score: int | None = None
    best_distance: int | None = None
    seen_candidate_shapes: set[str] = set()
    final: tuple[TopologyCandidate, Any, Any, int] | None = None
    attempts_run = 0

    for attempt in range(settings.max_attempts):
        attempts_run = attempt + 1
        _check_cancel(cancel_check)
        if _remaining_seconds(started, settings) <= 0:
            rejections["generation_timeout"] += 1
            break

        search_percent = 8 + int(34 * attempts_run / settings.max_attempts)
        reporter.send(
            search_percent,
            "generating",
            "constructing_topology",
            "Constructing and mutating a certified topology",
            attempt=attempts_run,
            best_score=best_score,
            target_range=settings.target_range,
        )
        phase_started = time.perf_counter()
        try:
            candidate = generate_topology(
                settings,
                random.Random(settings.attempt_seed(attempt)),
                attempt,
            )
            _independent_candidate_validation(candidate)
        except (RuntimeError, ValueError, OneLineError):
            candidate_seconds += time.perf_counter() - phase_started
            rejections["invalid_topology"] += 1
            continue
        candidate_seconds += time.perf_counter() - phase_started

        candidate_shape_hash = canonical_shape_hash(candidate.cells, candidate.start)
        if candidate_shape_hash in seen_candidate_shapes:
            rejections["duplicate_candidate"] += 1
            continue
        seen_candidate_shapes.add(candidate_shape_hash)

        if candidate.aesthetic_score < _aesthetic_threshold(settings):
            rejections["aesthetic_score_failed"] += 1
            continue

        certificate = verify_construction_certificate(candidate)
        if not certificate.valid:
            rejections["invalid_uniqueness_certificate"] += 1
            continue

        _check_cancel(cancel_check)
        reporter.send(
            50,
            "checking_solution",
            "checking_solution",
            "Independently finding a Hamiltonian solution",
            attempt=attempts_run,
            best_score=best_score,
            target_range=settings.target_range,
        )
        solve_started = time.perf_counter()
        try:
            remaining = max(0.05, _remaining_seconds(started, settings))
            solved_path, solver_stats = solve_hamiltonian_path(
                candidate.cells,
                candidate.start,
                timeout_seconds=min(5.0, remaining),
                max_states=2_000_000,
                cache_limit=250_000,
            )
        except SolveTimeout:
            first_solution_seconds += time.perf_counter() - solve_started
            rejections["solution_verification_timeout"] += 1
            continue
        first_solution_seconds += time.perf_counter() - solve_started
        total_solver_nodes += solver_stats.states
        if solved_path is None:
            rejections["not_solvable"] += 1
            continue
        try:
            validate_solution(solved_path, candidate.cells, candidate.start)
        except OneLineError:
            rejections["invalid_solver_route"] += 1
            continue

        _check_cancel(cancel_check)
        reporter.send(
            68,
            "checking_uniqueness",
            "checking_uniqueness",
            "Searching independently for a second solution",
            attempt=attempts_run,
            best_score=best_score,
            target_range=settings.target_range,
            uniqueness_status="checking",
        )
        unique_started = time.perf_counter()
        last_node_update = 0

        def uniqueness_progress(nodes: int) -> None:
            nonlocal last_node_update
            if nodes - last_node_update < 16_384:
                return
            last_node_update = nodes
            reporter.send(
                74,
                "checking_uniqueness",
                "checking_uniqueness",
                "Searching independently for a second solution",
                attempt=attempts_run,
                best_score=best_score,
                target_range=settings.target_range,
                uniqueness_status="checking",
                verifier_nodes=nodes,
            )

        try:
            remaining = max(0.05, _remaining_seconds(started, settings))
            uniqueness = prove_unique(
                candidate.cells,
                candidate.start,
                candidate.solution,
                timeout_seconds=min(8.0, remaining),
                max_nodes=5_000_000,
                cancel_check=cancel_check,
                progress=uniqueness_progress,
            )
        except VerificationCancelled as exc:
            raise GenerationCancelled("Generation was cancelled.") from exc
        except VerificationTimeout:
            uniqueness_seconds += time.perf_counter() - unique_started
            rejections["uniqueness_timeout"] += 1
            continue
        uniqueness_seconds += time.perf_counter() - unique_started
        total_uniqueness_nodes += uniqueness.stats.nodes_explored
        if not uniqueness.unique or uniqueness.stats.solutions_found != 1:
            rejections["multiple_solutions" if uniqueness.stats.solutions_found > 1 else "not_solvable"] += 1
            continue
        if tuple(solved_path) != uniqueness.solutions[0]:
            # A unique fixed-start solution must be identical regardless of the
            # independent solving implementation that found it.
            rejections["independent_solution_mismatch"] += 1
            continue

        _check_cancel(cancel_check)
        reporter.send(
            86,
            "scoring",
            "scoring_human_difficulty",
            "Measuring choices, traps, bottlenecks, and human-like backtracking",
            attempt=attempts_run,
            best_score=best_score,
            target_range=settings.target_range,
            uniqueness_status="unique",
        )
        score_started = time.perf_counter()
        try:
            difficulty = score_human_difficulty(
                candidate.cells,
                candidate.solution,
                candidate.width,
                candidate.height,
                cancel_check=cancel_check,
            )
        except RuntimeError as exc:
            if cancel_check is not None and cancel_check():
                raise GenerationCancelled("Generation was cancelled.") from exc
            difficulty_seconds += time.perf_counter() - score_started
            rejections["difficulty_evaluation_failed"] += 1
            continue
        difficulty_seconds += time.perf_counter() - score_started

        distance = _difficulty_distance(difficulty.score, settings.target_range)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_score = difficulty.score
        if distance:
            low, _ = settings.target_range
            rejections["difficulty_too_low" if difficulty.score < low else "difficulty_too_high"] += 1
            continue

        if difficulty.tier is not settings.difficulty:
            rejections["difficulty_classification_mismatch"] += 1
            continue
        final = (candidate, uniqueness, difficulty, attempt)
        break

    if final is None:
        reporter.send(
            100,
            "failed",
            "budget_exhausted",
            "No candidate met every requested constraint within the generation budget",
            attempt=attempts_run,
            best_score=best_score,
            target_range=settings.target_range,
        )
        reason = (
            "Could not generate a puzzle meeting the requested difficulty within "
            "the generation budget. Try another seed or relax the settings."
        )
        raise GenerationFailure(
            reason,
            attempts=attempts_run,
            rejection_reasons=dict(rejections),
            best_score=best_score,
        )

    candidate, uniqueness, difficulty, accepted_attempt = final
    _check_cancel(cancel_check)
    reporter.send(
        94,
        "rendering",
        "validating_outputs",
        "Replaying the seed and validating matrix and image outputs",
        attempt=attempts_run,
        best_score=difficulty.score,
        target_range=settings.target_range,
        uniqueness_status="unique",
    )

    # Final independent validation is intentionally repeated after all search
    # and scoring work, immediately before anything is served.
    _independent_candidate_validation(candidate)
    certificate = verify_construction_certificate(candidate)
    if not certificate.valid:
        raise RuntimeError(f"Final uniqueness certificate failed: {certificate.reason}")
    _replay_determinism(settings, accepted_attempt, candidate)
    matrix = matrix_from_cells(candidate.cells, settings.width, settings.height)
    matrix_cells = {
        (row, column)
        for row, values in enumerate(matrix)
        for column, value in enumerate(values)
        if value == 1
    }
    if matrix_cells != set(candidate.cells):
        raise RuntimeError("Binary matrix does not match the generated board.")

    render_started = time.perf_counter()
    unsolved_png: bytes | None = None
    solved_png: bytes | None = None
    if settings.outputs.polished_png:
        unsolved_png, unsolved_manifest = render_level_png(
            candidate.cells, candidate.start, settings, difficulty
        )
        verify_render_manifest(
            candidate.cells,
            candidate.start,
            unsolved_manifest,
            settings.width,
            settings.height,
        )
        verify_unsolved_png_pixels(
            unsolved_png,
            candidate.cells,
            candidate.start,
            unsolved_manifest,
            settings.width,
            settings.height,
        )
        solved_png, solved_manifest = render_level_png(
            candidate.cells,
            candidate.start,
            settings,
            difficulty,
            solution=candidate.solution,
        )
        verify_render_manifest(
            candidate.cells,
            candidate.start,
            solved_manifest,
            settings.width,
            settings.height,
        )
    rendering_seconds = time.perf_counter() - render_started
    total_seconds = time.perf_counter() - started
    diagnostics = GenerationDiagnostics(
        attempts=attempts_run,
        candidates_rejected=sum(rejections.values()),
        rejection_reasons=dict(sorted(rejections.items())),
        candidate_generation_seconds=candidate_seconds,
        first_solution_seconds=first_solution_seconds,
        uniqueness_check_seconds=uniqueness_seconds,
        difficulty_seconds=difficulty_seconds,
        rendering_seconds=rendering_seconds,
        total_seconds=total_seconds,
        peak_memory_mb=None,
        solver_nodes_explored=total_solver_nodes,
        uniqueness_nodes_explored=total_uniqueness_nodes,
    )
    level = GeneratedLevel(
        settings=settings,
        cells=candidate.cells,
        start=candidate.start,
        end=candidate.end,
        solution=candidate.solution,
        difficulty=difficulty,
        unique=True,
        validated=True,
        topology_hash=candidate.topology_hash,
        canonical_shape_hash=canonical_shape_hash(candidate.cells, candidate.start),
        certificate=candidate.certificate,
        uniqueness_stats=uniqueness.stats,
        diagnostics=diagnostics,
        matrix=matrix,
        unsolved_png=unsolved_png,
        solved_png=solved_png,
    )
    reporter.send(
        100,
        "complete",
        "complete",
        "Validated unique puzzle ready",
        attempt=attempts_run,
        best_score=difficulty.score,
        target_range=settings.target_range,
        uniqueness_status="unique",
    )
    return level
