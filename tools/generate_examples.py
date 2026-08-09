from __future__ import annotations

import json
from pathlib import Path

from generator import (
    DifficultyTier,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
    generate_level,
)
from generator.render import binary_matrix_text, render_svg_grid


EXAMPLES = [
    ("easy_normal_8x8", 8, 8, DifficultyTier.EASY, ShapeMode.NORMAL),
    ("medium_normal_9x8", 9, 8, DifficultyTier.MEDIUM, ShapeMode.NORMAL),
    ("hard_extreme_9x9", 9, 9, DifficultyTier.HARD, ShapeMode.EXTREME),
    ("expert_normal_12x12", 12, 12, DifficultyTier.EXPERT, ShapeMode.NORMAL),
    ("evil_extreme_12x12", 12, 12, DifficultyTier.EVIL, ShapeMode.EXTREME),
    ("large_easy_normal_20x20", 20, 20, DifficultyTier.EASY, ShapeMode.NORMAL),
    ("large_hard_extreme_20x20", 20, 20, DifficultyTier.HARD, ShapeMode.EXTREME),
    ("large_expert_normal_20x20", 20, 20, DifficultyTier.EXPERT, ShapeMode.NORMAL),
    ("large_evil_normal_20x20", 20, 20, DifficultyTier.EVIL, ShapeMode.NORMAL),
    ("large_evil_extreme_20x20", 20, 20, DifficultyTier.EVIL, ShapeMode.EXTREME),
]


def main() -> int:
    root = Path("examples/generated")
    root.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, object]] = []
    for name, width, height, tier, mode in EXAMPLES:
        settings = GenerationSettings(
            width,
            height,
            tier,
            mode,
            f"example-v1-{name}",
            OutputOptions(True, True, True),
            max_attempts=220,
            time_budget_seconds=60,
        )
        level = generate_level(settings)
        destination = root / name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "level.json").write_text(
            json.dumps(level.export_dict(reveal_solution=True), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (destination / "matrix.txt").write_text(
            binary_matrix_text(level.cells, width, height) + f"start = {level.start}\n",
            encoding="utf-8",
        )
        (destination / "visual_grid.svg").write_text(
            render_svg_grid(level.cells, level.start, width, height), encoding="utf-8"
        )
        (destination / "solution_grid.svg").write_text(
            render_svg_grid(level.cells, level.start, width, height, level.solution),
            encoding="utf-8",
        )
        if level.unsolved_png is None or level.solved_png is None:
            raise RuntimeError("Example PNG rendering unexpectedly disabled")
        (destination / "unsolved_level.png").write_bytes(level.unsolved_png)
        (destination / "solved_level.png").write_bytes(level.solved_png)
        index.append(
            {
                "name": name,
                "seed": settings.seed,
                "dimensions": [width, height],
                "requested_tier": tier.value,
                "actual_tier": level.difficulty.tier.value,
                "score": level.difficulty.score,
                "mode": mode.value,
                "tile_count": level.tile_count,
                "start": level.start,
                "end": level.end,
                "unique": level.unique,
                "validated": level.validated,
                "generation_seconds": level.diagnostics.total_seconds,
            }
        )
        print(f"{name}: {level.difficulty.score}/600, {level.tile_count} tiles")
    (root / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

