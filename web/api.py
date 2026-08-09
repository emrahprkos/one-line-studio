from __future__ import annotations

import json
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from generator.models import (
    GENERATOR_VERSION,
    DifficultyTier,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
)
from generator.render import binary_matrix_text, compact_route, render_svg_grid

from .jobs import ClientRateLimited, JOB_MANAGER, JobQueueFull


router = APIRouter(prefix="/api", tags=["generator"])


class OutputRequest(BaseModel):
    visual_grid: bool = True
    polished_png: bool = True
    binary_matrix: bool = True

    @model_validator(mode="after")
    def at_least_one(self) -> "OutputRequest":
        if not (self.visual_grid or self.polished_png or self.binary_matrix):
            raise ValueError("At least one output must be enabled.")
        return self


class GenerateRequest(BaseModel):
    width: int = Field(ge=3, le=20)
    height: int = Field(ge=3, le=20)
    difficulty: Literal["easy", "medium", "hard", "expert", "evil"]
    shape_mode: Literal["normal", "extreme"]
    seed: str | None = Field(default=None, max_length=64)
    outputs: OutputRequest = Field(default_factory=OutputRequest)


def _budgets(width: int, height: int, difficulty: DifficultyTier) -> tuple[int, float]:
    tier_index = list(DifficultyTier).index(difficulty)
    attempts = min(240, 80 + tier_index * 30)
    calculated = 8.0 + (width * height) / 20.0 + tier_index * 4.0
    server_cap = max(2.0, min(120.0, float(os.getenv("GENERATION_MAX_SECONDS", "50"))))
    return attempts, min(server_cap, calculated)


def _client_key(request: Request) -> str:
    # This value is used only for an in-memory resource limit and is never logged.
    return request.client.host if request.client else "unknown"


def _result_payload(job_id: str) -> dict[str, object]:
    job = JOB_MANAGER.get(job_id)
    if job is None:
        raise KeyError(job_id)
    payload = job.public_status()
    level = job.result
    if job.state != "complete" or level is None:
        return payload

    outputs = level.settings.outputs
    result: dict[str, object] = {
        "seed": level.settings.seed,
        "generator_version": GENERATOR_VERSION,
        "width": level.settings.width,
        "height": level.settings.height,
        "tile_count": level.tile_count,
        "density": round(level.density, 4),
        "start": list(level.start),
        "end": list(level.end),
        "difficulty": level.difficulty.as_dict(),
        "requested_difficulty": level.settings.difficulty.value,
        "shape_mode": level.settings.shape_mode.value,
        "unique": level.unique,
        "validated": level.validated,
        "details": {
            "turn_count": level.difficulty.turn_count,
            "forced_move_count": level.difficulty.forced_move_count,
            "forced_move_ratio": level.difficulty.forced_move_ratio,
            "branch_points": level.difficulty.branch_points,
            "wrong_branch_statistics": {
                "average_survival": level.difficulty.average_wrong_branch_survival,
                "maximum_survival": level.difficulty.maximum_wrong_branch_survival,
            },
            "solver_nodes_explored": level.diagnostics.solver_nodes_explored,
            "uniqueness_nodes_explored": level.diagnostics.uniqueness_nodes_explored,
            "generation_attempts": level.diagnostics.attempts,
            "generation_time": level.diagnostics.total_seconds,
            "uniqueness_check_time": level.diagnostics.uniqueness_check_seconds,
            "topology_hash": level.topology_hash,
            "canonical_shape_hash": level.canonical_shape_hash,
            "rejection_reasons": level.diagnostics.rejection_reasons,
        },
        "downloads": {
            "json": f"/api/jobs/{job_id}/level.json",
        },
        "solution_available": True,
    }
    downloads = result["downloads"]
    assert isinstance(downloads, dict)
    if outputs.visual_grid:
        result["visual_grid_svg"] = render_svg_grid(
            level.cells,
            level.start,
            level.settings.width,
            level.settings.height,
        )
    if outputs.binary_matrix:
        result["matrix"] = [list(row) for row in level.matrix]
        result["matrix_text"] = binary_matrix_text(
            level.cells, level.settings.width, level.settings.height
        )
    if outputs.polished_png:
        downloads["unsolved_png"] = f"/api/jobs/{job_id}/level.png"
        downloads["solved_png"] = f"/api/jobs/{job_id}/solution.png"
    payload["result"] = result
    return payload


@router.post("/generate", status_code=202)
def create_generation_job(body: GenerateRequest, request: Request):
    seed = body.seed.strip() if body.seed else str(secrets.randbelow(10**12))
    difficulty = DifficultyTier(body.difficulty)
    max_attempts, time_budget = _budgets(body.width, body.height, difficulty)
    settings = GenerationSettings(
        width=body.width,
        height=body.height,
        difficulty=difficulty,
        shape_mode=ShapeMode(body.shape_mode),
        seed=seed,
        outputs=OutputOptions(**body.outputs.model_dump()),
        max_attempts=max_attempts,
        time_budget_seconds=time_budget,
    )
    try:
        settings.validate()
        job = JOB_MANAGER.submit(settings, _client_key(request))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except ClientRateLimited as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=429)
    except JobQueueFull as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return {
        "ok": True,
        "job_id": job.job_id,
        "seed": settings.seed,
        "status_url": f"/api/jobs/{job.job_id}",
        "cancel_url": f"/api/jobs/{job.job_id}/cancel",
        "state": job.state,
    }


@router.get("/jobs/{job_id}")
def generation_job_status(job_id: str):
    try:
        return {"ok": True, **_result_payload(job_id)}
    except KeyError:
        return JSONResponse({"ok": False, "error": "Generation job not found."}, status_code=404)


@router.post("/jobs/{job_id}/cancel")
def cancel_generation_job(job_id: str):
    job = JOB_MANAGER.cancel(job_id)
    if job is None:
        return JSONResponse({"ok": False, "error": "Generation job not found."}, status_code=404)
    return {"ok": True, **job.public_status()}


def _completed_level(job_id: str):
    job = JOB_MANAGER.get(job_id)
    if job is None:
        return None, JSONResponse({"ok": False, "error": "Generation job not found."}, status_code=404)
    if job.state != "complete" or job.result is None:
        return None, JSONResponse({"ok": False, "error": "Generation is not complete."}, status_code=409)
    return job.result, None


@router.get("/jobs/{job_id}/level.png")
def download_level_png(job_id: str):
    level, error = _completed_level(job_id)
    if error:
        return error
    if level.unsolved_png is None:
        return JSONResponse({"ok": False, "error": "PNG output was not requested."}, status_code=404)
    return Response(
        level.unsolved_png,
        media_type="image/png",
        headers={"Content-Disposition": 'attachment; filename="unsolved_level.png"'},
    )


@router.get("/jobs/{job_id}/solution.png")
def download_solution_png(job_id: str):
    level, error = _completed_level(job_id)
    if error:
        return error
    if level.solved_png is None:
        return JSONResponse({"ok": False, "error": "PNG output was not requested."}, status_code=404)
    return Response(
        level.solved_png,
        media_type="image/png",
        headers={"Content-Disposition": 'attachment; filename="solved_level.png"'},
    )


@router.get("/jobs/{job_id}/level.json")
def download_level_json(job_id: str):
    level, error = _completed_level(job_id)
    if error:
        return error
    content = json.dumps(level.export_dict(reveal_solution=True), indent=2, sort_keys=True)
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="one_line_level.json"'},
    )


@router.get("/jobs/{job_id}/solution")
def reveal_solution(job_id: str):
    level, error = _completed_level(job_id)
    if error:
        return error
    return {
        "ok": True,
        "route": compact_route(level.solution),
        "solution_svg": render_svg_grid(
            level.cells,
            level.start,
            level.settings.width,
            level.settings.height,
            solution=level.solution,
        ),
        "solved_png": (
            f"/api/jobs/{job_id}/solution.png" if level.solved_png is not None else None
        ),
    }
