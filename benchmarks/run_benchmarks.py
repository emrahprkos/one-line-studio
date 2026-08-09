from __future__ import annotations

import argparse
import csv
import json
import resource
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from generator import (
    DifficultyTier,
    GenerationFailure,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
    generate_level,
)


SIZES = [(5, 5), (7, 7), (9, 9), (12, 12), (15, 15), (20, 20)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark full validated generation requests.")
    parser.add_argument("--samples", type=int, default=3, help="seeds per combination")
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/performance"))
    parser.add_argument("--time-budget", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    suite_started = time.perf_counter()
    for width, height in SIZES:
        for mode in ShapeMode:
            for tier in DifficultyTier:
                for sample in range(args.samples):
                    seed = f"bench-v1-{width}-{height}-{mode.value}-{tier.value}-{sample}"
                    settings = GenerationSettings(
                        width,
                        height,
                        tier,
                        mode,
                        seed,
                        OutputOptions(True, False, True),
                        max_attempts=180,
                        time_budget_seconds=args.time_budget,
                    )
                    started = time.perf_counter()
                    try:
                        level = generate_level(settings)
                    except GenerationFailure as exc:
                        rows.append(
                            {
                                "width": width,
                                "height": height,
                                "mode": mode.value,
                                "requested_tier": tier.value,
                                "seed": seed,
                                "success": False,
                                "score": exc.best_score,
                                "tile_count": None,
                                "attempts": exc.attempts,
                                "candidate_seconds": None,
                                "first_solution_seconds": None,
                                "uniqueness_seconds": None,
                                "difficulty_seconds": None,
                                "rendering_seconds": None,
                                "total_seconds": time.perf_counter() - started,
                                "solver_nodes": None,
                                "uniqueness_nodes": None,
                                "rejection_reasons": json.dumps(exc.rejection_reasons, sort_keys=True),
                            }
                        )
                        continue
                    diagnostics = level.diagnostics
                    rows.append(
                        {
                            "width": width,
                            "height": height,
                            "mode": mode.value,
                            "requested_tier": tier.value,
                            "seed": seed,
                            "success": True,
                            "score": level.difficulty.score,
                            "tile_count": level.tile_count,
                            "attempts": diagnostics.attempts,
                            "candidate_seconds": diagnostics.candidate_generation_seconds,
                            "first_solution_seconds": diagnostics.first_solution_seconds,
                            "uniqueness_seconds": diagnostics.uniqueness_check_seconds,
                            "difficulty_seconds": diagnostics.difficulty_seconds,
                            "rendering_seconds": diagnostics.rendering_seconds,
                            "total_seconds": diagnostics.total_seconds,
                            "solver_nodes": diagnostics.solver_nodes_explored,
                            "uniqueness_nodes": diagnostics.uniqueness_nodes_explored,
                            "rejection_reasons": json.dumps(
                                diagnostics.rejection_reasons, sort_keys=True
                            ),
                        }
                    )
        successes = sum(bool(row["success"]) for row in rows if row["width"] == width)
        total = sum(1 for row in rows if row["width"] == width)
        print(f"benchmarked {width}x{height}: {successes}/{total} succeeded", flush=True)

    csv_path = args.output / "performance_runs.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    successful = [row for row in rows if row["success"]]
    by_size: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_size[f"{row['width']}x{row['height']}"] .append(row)
    rejection_totals: Counter[str] = Counter()
    for row in rows:
        for reason, count in json.loads(str(row["rejection_reasons"])).items():
            rejection_totals[reason] += count
    summary = {
        "generator_version": "1.0",
        "samples_per_combination": args.samples,
        "requests": len(rows),
        "successful": len(successful),
        "failed": len(rows) - len(successful),
        "suite_seconds": time.perf_counter() - suite_started,
        "peak_process_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "by_size": {
            size: {
                "requests": len(values),
                "successful": sum(bool(row["success"]) for row in values),
                "median_seconds_success": (
                    statistics.median(float(row["total_seconds"]) for row in values if row["success"])
                    if any(row["success"] for row in values)
                    else None
                ),
                "maximum_seconds": max(float(row["total_seconds"]) for row in values),
            }
            for size, values in by_size.items()
        },
        "successful_runs": {
            "median_total_seconds": statistics.median(
                float(row["total_seconds"]) for row in successful
            ),
            "p95_total_seconds": sorted(float(row["total_seconds"]) for row in successful)[
                round((len(successful) - 1) * 0.95)
            ],
            "maximum_total_seconds": max(float(row["total_seconds"]) for row in successful),
            "maximum_uniqueness_seconds": max(
                float(row["uniqueness_seconds"]) for row in successful
            ),
            "maximum_uniqueness_nodes": max(
                int(row["uniqueness_nodes"]) for row in successful
            ),
        },
        "rejection_totals": dict(sorted(rejection_totals.items())),
    }
    (args.output / "performance_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

