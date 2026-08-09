#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from generator import (
    DifficultyTier,
    GenerationFailure,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
    generate_level,
)
from generator.render import binary_matrix_text, render_svg_grid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one independently verified, uniquely solvable One Line puzzle."
    )
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument(
        "--difficulty",
        choices=[tier.value for tier in DifficultyTier],
        default="medium",
    )
    parser.add_argument(
        "--shape-mode",
        choices=[mode.value for mode in ShapeMode],
        default="normal",
    )
    parser.add_argument("--seed", help="1–64 letters, numbers, underscores, or hyphens")
    parser.add_argument("-o", "--output", type=Path, default=Path("generated_level"))
    parser.add_argument("--max-attempts", type=int, default=180)
    parser.add_argument("--time-budget", type=float, default=50.0)
    parser.add_argument("--no-png", action="store_true", help="Skip PNG files")
    parser.add_argument("--quiet", action="store_true", help="Suppress live phase updates")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seed = args.seed or str(secrets.randbelow(10**12))
    settings = GenerationSettings(
        width=args.width,
        height=args.height,
        difficulty=DifficultyTier(args.difficulty),
        shape_mode=ShapeMode(args.shape_mode),
        seed=seed,
        outputs=OutputOptions(True, not args.no_png, True),
        max_attempts=args.max_attempts,
        time_budget_seconds=args.time_budget,
    )
    last_line = ""

    def progress(update: dict[str, object]) -> None:
        nonlocal last_line
        if args.quiet:
            return
        score = update.get("best_score")
        score_text = "—" if score is None else f"{score}/600"
        line = (
            f"{update.get('percent', 0):>3}%  {update.get('phase', ''):<26} "
            f"attempt {update.get('attempt', 0):>3}  best {score_text}"
        )
        if line != last_line:
            print(line, file=sys.stderr)
            last_line = line

    try:
        level = generate_level(settings, progress=progress)
    except (ValueError, GenerationFailure) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "level.json").write_text(
        json.dumps(level.export_dict(reveal_solution=True), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "matrix.txt").write_text(
        binary_matrix_text(level.cells, settings.width, settings.height)
        + f"start = {level.start}\n",
        encoding="utf-8",
    )
    (args.output / "visual_grid.svg").write_text(
        render_svg_grid(level.cells, level.start, settings.width, settings.height),
        encoding="utf-8",
    )
    (args.output / "solution_grid.svg").write_text(
        render_svg_grid(
            level.cells,
            level.start,
            settings.width,
            settings.height,
            solution=level.solution,
        ),
        encoding="utf-8",
    )
    if level.unsolved_png is not None:
        (args.output / "unsolved_level.png").write_bytes(level.unsolved_png)
    if level.solved_png is not None:
        (args.output / "solved_level.png").write_bytes(level.solved_png)

    print(
        json.dumps(
            {
                "ok": True,
                "seed": level.settings.seed,
                "dimensions": [settings.width, settings.height],
                "tile_count": level.tile_count,
                "start": level.start,
                "end": level.end,
                "difficulty": level.difficulty.tier.value,
                "score": level.difficulty.score,
                "unique": level.unique,
                "validated": level.validated,
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

