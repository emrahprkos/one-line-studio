from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

from generator import (
    DifficultyTier,
    GenerationFailure,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
    generate_level,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress full generation on large boards.")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/large_stress"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    started = time.perf_counter()
    for width, height in [(12, 12), (15, 15), (20, 20)]:
        for mode in ShapeMode:
            for tier in DifficultyTier:
                for sample in range(args.samples):
                    seed = f"stress-v1-{width}-{mode.value}-{tier.value}-{sample}"
                    settings = GenerationSettings(
                        width,
                        height,
                        tier,
                        mode,
                        seed,
                        OutputOptions(True, False, True),
                        max_attempts=180,
                        time_budget_seconds=30,
                    )
                    request_started = time.perf_counter()
                    try:
                        level = generate_level(settings)
                    except GenerationFailure as exc:
                        rows.append(
                            {
                                "dimensions": f"{width}x{height}",
                                "mode": mode.value,
                                "tier": tier.value,
                                "seed": seed,
                                "success": False,
                                "score": exc.best_score,
                                "tile_count": None,
                                "attempts": exc.attempts,
                                "seconds": time.perf_counter() - request_started,
                                "uniqueness_nodes": None,
                                "reason": json.dumps(exc.rejection_reasons, sort_keys=True),
                            }
                        )
                        continue
                    if not (level.unique and level.validated):
                        raise RuntimeError("stress run returned an unvalidated level")
                    rows.append(
                        {
                            "dimensions": f"{width}x{height}",
                            "mode": mode.value,
                            "tier": tier.value,
                            "seed": seed,
                            "success": True,
                            "score": level.difficulty.score,
                            "tile_count": level.tile_count,
                            "attempts": level.diagnostics.attempts,
                            "seconds": level.diagnostics.total_seconds,
                            "uniqueness_nodes": level.diagnostics.uniqueness_nodes_explored,
                            "reason": "",
                        }
                    )
        completed = [row for row in rows if row["dimensions"] == f"{width}x{height}"]
        print(
            f"stress {width}x{height}: {sum(bool(row['success']) for row in completed)}/{len(completed)}",
            flush=True,
        )

    with (args.output / "large_stress_runs.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    successful = [row for row in rows if row["success"]]
    summary = {
        "requests": len(rows),
        "successful": len(successful),
        "failed": len(rows) - len(successful),
        "all_successes_unique_and_validated": True,
        "elapsed_seconds": time.perf_counter() - started,
        "median_seconds": statistics.median(float(row["seconds"]) for row in successful),
        "p95_seconds": sorted(float(row["seconds"]) for row in successful)[
            round((len(successful) - 1) * 0.95)
        ],
        "maximum_seconds": max(float(row["seconds"]) for row in successful),
        "twenty_by_twenty": {
            "requests": sum(row["dimensions"] == "20x20" for row in rows),
            "successful": sum(
                row["dimensions"] == "20x20" and bool(row["success"]) for row in rows
            ),
            "maximum_seconds": max(
                float(row["seconds"])
                for row in successful
                if row["dimensions"] == "20x20"
            ),
        },
    }
    (args.output / "large_stress_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if len(successful) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

