from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Iterable, Sequence

import cv2
import numpy as np

from solve_one_line import _rounded_rectangle, draw_solution, route_directions

from .models import Cell, DifficultyMetrics, GenerationSettings
from .topology import matrix_from_cells


@dataclass(frozen=True)
class RenderManifest:
    width_px: int
    height_px: int
    centers: dict[Cell, tuple[float, float]]
    tile_size: tuple[float, float]
    board_rect: tuple[int, int, int, int]


def binary_matrix_text(cells: Iterable[Cell], width: int, height: int) -> str:
    matrix = matrix_from_cells(cells, width, height)
    return "\n".join(" ".join(str(value) for value in row) for row in matrix) + "\n"


def _layout(width: int, height: int) -> tuple[int, int, int, int, int, int]:
    max_board = 1120
    min_tile = 34
    gap_ratio = 0.13
    tile = int(
        min(
            116,
            max_board / max(1, width + gap_ratio * (width - 1)),
            max_board / max(1, height + gap_ratio * (height - 1)),
        )
    )
    tile = max(min_tile, tile)
    gap = max(5, int(round(tile * gap_ratio)))
    board_w = width * tile + (width - 1) * gap
    board_h = height * tile + (height - 1) * gap
    margin_x = max(48, int(tile * 0.70))
    header = max(126, int(tile * 1.45))
    footer = max(76, int(tile * 0.80))
    canvas_w = board_w + 2 * margin_x
    canvas_h = header + board_h + footer
    return tile, gap, board_w, board_h, canvas_w, canvas_h


def render_level_png(
    cells: Iterable[Cell],
    start: Cell,
    settings: GenerationSettings,
    difficulty: DifficultyMetrics,
    solution: Sequence[Cell] | None = None,
) -> tuple[bytes, RenderManifest]:
    board = set(cells)
    tile, gap, board_w, board_h, canvas_w, canvas_h = _layout(
        settings.width, settings.height
    )
    image = np.full((canvas_h, canvas_w, 3), (28, 23, 21), dtype=np.uint8)
    # Subtle top glow, kept cheap and deterministic.
    overlay = image.copy()
    cv2.circle(
        overlay,
        (canvas_w // 2, 0),
        max(canvas_w // 2, 240),
        (47, 33, 69),
        -1,
        cv2.LINE_AA,
    )
    image = cv2.addWeighted(overlay, 0.20, image, 0.80, 0)

    origin_x = (canvas_w - board_w) // 2
    header = canvas_h - board_h - max(76, int(tile * 0.80))
    origin_y = header
    centers: dict[Cell, tuple[float, float]] = {}
    normal_color = (119, 105, 101)
    start_color = (75, 108, 245)
    marker_color = (125, 175, 255)

    for r, c in sorted(board):
        x = origin_x + c * (tile + gap)
        y = origin_y + r * (tile + gap)
        color = start_color if (r, c) == start else normal_color
        _rounded_rectangle(
            image,
            (x, y),
            (x + tile, y + tile),
            color,
            max(6, int(round(tile * 0.17))),
        )
        center = (x + tile / 2.0, y + tile / 2.0)
        centers[(r, c)] = center
        if (r, c) == start:
            cv2.circle(
                image,
                (int(center[0]), int(center[1])),
                max(6, int(tile * 0.24)),
                marker_color,
                -1,
                cv2.LINE_AA,
            )

    title_scale = max(0.75, min(1.35, canvas_w / 900.0))
    title_thickness = max(2, int(round(title_scale * 2.2)))
    cv2.putText(
        image,
        "ONE LINE",
        (origin_x, max(44, int(header * 0.38))),
        cv2.FONT_HERSHEY_SIMPLEX,
        title_scale,
        (248, 243, 239),
        title_thickness,
        cv2.LINE_AA,
    )
    subtitle = (
        f"{difficulty.tier.value.upper()}  {difficulty.score}/600   "
        f"{settings.width}x{settings.height}   SEED {settings.seed[:18]}"
    )
    cv2.putText(
        image,
        subtitle,
        (origin_x, max(76, int(header * 0.70))),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.43, title_scale * 0.45),
        (184, 169, 160),
        max(1, title_thickness - 1),
        cv2.LINE_AA,
    )

    if solution is not None:
        image = draw_solution(
            image,
            solution,
            centers,
            (float(tile), float(tile)),
        )

    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 7])
    if not ok:
        raise RuntimeError("OpenCV failed to encode the generated PNG.")
    manifest = RenderManifest(
        width_px=canvas_w,
        height_px=canvas_h,
        centers=centers,
        tile_size=(float(tile), float(tile)),
        board_rect=(origin_x, origin_y, board_w, board_h),
    )
    return encoded.tobytes(), manifest


def verify_render_manifest(
    cells: Iterable[Cell],
    start: Cell,
    manifest: RenderManifest,
    width: int,
    height: int,
) -> None:
    board = set(cells)
    if set(manifest.centers) != board:
        raise ValueError("PNG render manifest does not match the board cell set.")
    if start not in manifest.centers:
        raise ValueError("PNG render manifest omits the start tile.")
    for r, c in board:
        if not (0 <= r < height and 0 <= c < width):
            raise ValueError("PNG render manifest contains an out-of-range tile.")
    if manifest.width_px <= 0 or manifest.height_px <= 0:
        raise ValueError("PNG dimensions must be positive.")


def verify_unsolved_png_pixels(
    png: bytes,
    cells: Iterable[Cell],
    start: Cell,
    manifest: RenderManifest,
    width: int,
    height: int,
) -> None:
    """Decode the output and independently sample every grid position."""

    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Rendered PNG cannot be decoded.")
    if image.shape[:2] != (manifest.height_px, manifest.width_px):
        raise ValueError("Rendered PNG dimensions differ from its manifest.")
    board = set(cells)
    tile_width, tile_height = manifest.tile_size
    origin_x, origin_y, board_width, board_height = manifest.board_rect
    gap_x = (board_width - width * tile_width) / max(1, width - 1)
    gap_y = (board_height - height * tile_height) / max(1, height - 1)
    normal = np.array((119, 105, 101), dtype=np.float32)
    marker = np.array((125, 175, 255), dtype=np.float32)

    for row in range(height):
        for column in range(width):
            x = int(round(origin_x + column * (tile_width + gap_x) + tile_width / 2))
            y = int(round(origin_y + row * (tile_height + gap_y) + tile_height / 2))
            pixel = image[y, x].astype(np.float32)
            cell = (row, column)
            if cell == start:
                if np.linalg.norm(pixel - marker) > 8:
                    raise ValueError("PNG start marker color does not match generated start.")
            elif cell in board:
                if np.linalg.norm(pixel - normal) > 8:
                    raise ValueError("PNG tile pixels do not match generated board cells.")
            elif min(np.linalg.norm(pixel - normal), np.linalg.norm(pixel - marker)) < 22:
                raise ValueError("PNG draws a tile at a missing matrix position.")


def render_svg_grid(
    cells: Iterable[Cell],
    start: Cell,
    width: int,
    height: int,
    solution: Sequence[Cell] | None = None,
) -> str:
    board = set(cells)
    tile = 42
    gap = 6
    pad = 12
    total_w = width * tile + (width - 1) * gap + pad * 2
    total_h = height * tile + (height - 1) * gap + pad * 2
    parts = [
        f'<svg class="generated-grid-svg" viewBox="0 0 {total_w} {total_h}" '
        'role="img" aria-label="Generated One Line puzzle">',
        f'<rect width="{total_w}" height="{total_h}" rx="18" fill="#120f0d"/>',
    ]
    centers: dict[Cell, tuple[float, float]] = {}
    for r, c in sorted(board):
        x = pad + c * (tile + gap)
        y = pad + r * (tile + gap)
        centers[(r, c)] = (x + tile / 2, y + tile / 2)
        fill = "#f56c52" if (r, c) == start else "#776a66"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{tile}" height="{tile}" rx="9" fill="{fill}"/>'
        )
        if (r, c) == start:
            parts.append(
                f'<circle cx="{x + tile / 2}" cy="{y + tile / 2}" r="10" fill="#ffb28a"/>'
            )
    if solution is not None:
        points = " ".join(
            f"{centers[cell][0]:.1f},{centers[cell][1]:.1f}" for cell in solution
        )
        parts.append(
            f'<polyline points="{escape(points)}" fill="none" stroke="#fff322" '
            'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        start_center = centers[solution[0]]
        end_center = centers[solution[-1]]
        parts.append(
            f'<circle cx="{start_center[0]}" cy="{start_center[1]}" r="7" fill="#54df73"/>'
        )
        parts.append(
            f'<circle cx="{end_center[0]}" cy="{end_center[1]}" r="7" fill="#ff5252"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def compact_route(solution: Sequence[Cell]) -> dict[str, object]:
    arrows, ascii_directions = route_directions(solution)
    return {
        "arrows": arrows,
        "ascii": ascii_directions,
        "coordinates": [list(cell) for cell in solution],
    }
