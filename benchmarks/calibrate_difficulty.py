from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from generator.difficulty import score_human_difficulty
from generator.models import DifficultyTier, GenerationSettings, ShapeMode
from generator.topology import canonical_shape_hash, generate_topology
from generator.uniqueness import prove_unique, verify_construction_certificate
from solve_one_line import validate_solution


SIZES = [(5, 5), (7, 7), (9, 9), (12, 12), (15, 15), (20, 20)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a verified difficulty-feature corpus.")
    parser.add_argument("--samples", type=int, default=10, help="samples per size/mode/tier")
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/calibration"))
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    started = time.perf_counter()
    for width, height in SIZES:
        for mode in ShapeMode:
            for requested in DifficultyTier:
                for sample in range(args.samples):
                    seed = f"cal-v1-{width}-{height}-{mode.value}-{requested.value}-{sample}"
                    settings = GenerationSettings(width, height, requested, mode, seed)
                    attempt = sample % 7
                    candidate_started = time.perf_counter()
                    candidate = generate_topology(
                        settings,
                        random.Random(settings.attempt_seed(attempt)),
                        attempt,
                    )
                    candidate_seconds = time.perf_counter() - candidate_started
                    certificate = verify_construction_certificate(candidate)
                    if not certificate.valid:
                        raise RuntimeError(certificate.reason)
                    validate_solution(candidate.solution, candidate.cells, candidate.start)
                    unique = prove_unique(
                        candidate.cells,
                        candidate.start,
                        candidate.solution,
                        timeout_seconds=5,
                        max_nodes=5_000_000,
                    )
                    if not unique.unique:
                        raise RuntimeError("Corpus candidate was not exactly unique")
                    score_started = time.perf_counter()
                    metrics = score_human_difficulty(
                        candidate.cells,
                        candidate.solution,
                        width,
                        height,
                    )
                    score_seconds = time.perf_counter() - score_started
                    rows.append(
                        {
                            "width": width,
                            "height": height,
                            "mode": mode.value,
                            "topology_preset": requested.value,
                            "seed": seed,
                            "tile_count": len(candidate.cells),
                            "density": len(candidate.cells) / (width * height),
                            "ear_count": len(candidate.certificate.ears),
                            "topology_hash": candidate.topology_hash,
                            "canonical_shape_hash": canonical_shape_hash(
                                candidate.cells, candidate.start
                            ),
                            "aesthetic_score": candidate.aesthetic_score,
                            "difficulty_score": metrics.score,
                            "actual_tier": metrics.tier.value,
                            "forced_move_ratio": metrics.forced_move_ratio,
                            "branch_points": metrics.branch_points,
                            "branch_ratio": metrics.branch_ratio,
                            "average_wrong_branch_survival": metrics.average_wrong_branch_survival,
                            "maximum_wrong_branch_survival": metrics.maximum_wrong_branch_survival,
                            "temptation_score": metrics.temptation_score,
                            "agent_backtrack_score": metrics.agent_backtrack_score,
                            "turn_ratio": metrics.turn_ratio,
                            "loop_ratio": metrics.loop_ratio,
                            "bottleneck_ratio": metrics.bottleneck_ratio,
                            "cavity_count": metrics.cavity_count,
                            "pseudo_symmetry": metrics.pseudo_symmetry,
                            "candidate_seconds": candidate_seconds,
                            "uniqueness_seconds": unique.stats.elapsed_seconds,
                            "uniqueness_nodes": unique.stats.nodes_explored,
                            "difficulty_seconds": score_seconds,
                            "validated": True,
                            "unique": True,
                        }
                    )
        print(f"calibrated {width}x{height}: {len(rows)} rows", flush=True)

    csv_path = args.output / "difficulty_corpus.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        groups[(str(row["mode"]), str(row["topology_preset"]))].append(
            int(row["difficulty_score"])
        )
    summary = {
        "generator_version": "1.0",
        "samples_per_combination": args.samples,
        "candidate_count": len(rows),
        "all_unique_and_validated": all(row["unique"] and row["validated"] for row in rows),
        "distinct_topology_hashes": len({row["topology_hash"] for row in rows}),
        "distinct_canonical_shape_hashes": len(
            {row["canonical_shape_hash"] for row in rows}
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "score_distribution": {
            f"{mode}/{preset}": {
                "count": len(values),
                "minimum": min(values),
                "p10": percentile(values, 0.10),
                "median": statistics.median(values),
                "mean": statistics.mean(values),
                "p90": percentile(values, 0.90),
                "maximum": max(values),
            }
            for (mode, preset), values in sorted(groups.items())
        },
        "global": {
            "minimum": min(int(row["difficulty_score"]) for row in rows),
            "maximum": max(int(row["difficulty_score"]) for row in rows),
            "mean_uniqueness_nodes": statistics.mean(
                int(row["uniqueness_nodes"]) for row in rows
            ),
            "max_uniqueness_seconds": max(float(row["uniqueness_seconds"]) for row in rows),
            "max_difficulty_seconds": max(float(row["difficulty_seconds"]) for row in rows),
        },
    }
    (args.output / "calibration_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
