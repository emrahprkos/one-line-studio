from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from web.api import router as generator_router


BASE_DIR = Path(__file__).resolve().parent
SOLVER = BASE_DIR / "solve_one_line.py"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
SOLVER_TIMEOUT_SECONDS = float(os.environ.get("SOLVER_TIMEOUT_SECONDS", "60"))
PROCESS_TIMEOUT_SECONDS = SOLVER_TIMEOUT_SECONDS + 15
MAX_CONCURRENT_SOLVES = max(1, int(os.environ.get("MAX_CONCURRENT_SOLVES", "1")))
SOLVE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SOLVES)

app = FastAPI(title="One Line Studio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.include_router(generator_router)


def _data_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in ALLOWED_EXTENSIONS else ""


def _human_solver_error(stderr: str) -> str:
    text = (stderr or "").strip()
    if "Detection confidence" in text:
        return (
            "I found a board, but the screenshot detection wasn't confident enough to trust. "
            "Try a clean screenshot with the full board visible."
        )
    if "No Hamiltonian path" in text:
        return (
            "I couldn't find a valid one-line route for the detected board. "
            "The board may have been detected incorrectly, or this screenshot may need manual correction."
        )
    if "Could not load image" in text:
        return "I couldn't read that image. Please use a PNG or JPEG screenshot."
    if text:
        last = text.splitlines()[-1]
        if last.startswith("ERROR:"):
            last = last.removeprefix("ERROR:").strip()
        return last[:500]
    return "The solver stopped before producing a validated solution."


def _run_solver(
    screenshot_path: Path | None,
    output_dir: Path,
    matrix_text: str | None = None,
    start_text: str | None = None,
) -> tuple[dict[str, Any] | None, subprocess.CompletedProcess[str]]:
    cmd = [sys.executable, str(SOLVER)]
    if screenshot_path is not None:
        cmd.append(str(screenshot_path))

    if matrix_text:
        matrix_path = output_dir.parent / "manual_board.txt"
        matrix_path.write_text(matrix_text.strip() + "\n", encoding="utf-8")
        cmd.extend(["--matrix", str(matrix_path)])
        if start_text:
            cmd.extend(["--start", start_text.strip()])

    cmd.extend(
        [
            "-o",
            str(output_dir),
            "--timeout",
            str(SOLVER_TIMEOUT_SECONDS),
        ]
    )

    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )

    result_path = output_dir / "result.json"
    if completed.returncode == 0 and result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8")), completed
    return None, completed


async def _save_upload(upload: UploadFile, destination: Path) -> int:
    total = 0
    with destination.open("wb") as handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise ValueError("upload_too_large")
            handle.write(chunk)
    return total


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"max_upload_mb": MAX_UPLOAD_MB},
    )


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/solve")
async def solve_api(
    screenshot: UploadFile | None = File(default=None),
    matrix: str = Form(default=""),
    start: str = Form(default=""),
):
    matrix_text = matrix.strip()
    start_text = start.strip()

    if screenshot is None and not matrix_text:
        return JSONResponse({"ok": False, "error": "Choose a screenshot first."}, status_code=400)

    suffix = ""
    if screenshot is not None:
        suffix = _safe_suffix(screenshot.filename)
        if not suffix:
            return JSONResponse(
                {"ok": False, "error": "Please upload a PNG or JPEG screenshot."},
                status_code=400,
            )

    if start_text:
        try:
            row_text, col_text = start_text.split(",", 1)
            int(row_text.strip())
            int(col_text.strip())
        except Exception:
            return JSONResponse(
                {"ok": False, "error": "Manual start must look like 2,4 (row,column)."},
                status_code=400,
            )

    try:
        with tempfile.TemporaryDirectory(prefix="one-line-") as tmp:
            tmp_dir = Path(tmp)
            screenshot_path: Path | None = None
            if screenshot is not None:
                screenshot_path = tmp_dir / f"screenshot{suffix}"
                try:
                    size = await _save_upload(screenshot, screenshot_path)
                finally:
                    await screenshot.close()
                if size == 0:
                    return JSONResponse({"ok": False, "error": "That image file is empty."}, status_code=400)

            output_dir = tmp_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            async with SOLVE_SEMAPHORE:
                result, completed = await asyncio.to_thread(
                    _run_solver,
                    screenshot_path,
                    output_dir,
                    matrix_text or None,
                    start_text or None,
                )

            if result is None:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": _human_solver_error(completed.stderr),
                        "debug_grid": _data_url(output_dir / "debug_grid.png"),
                        "debug_tiles": _data_url(output_dir / "debug_tiles.png"),
                    },
                    status_code=422,
                )

            detection = result.get("detection") or {}
            stats = result.get("solver_stats") or {}
            solution = _data_url(output_dir / "solution.png")
            if not solution:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The solver validated a route but the solution image was missing.",
                    },
                    status_code=500,
                )

            return {
                "ok": True,
                "solution": solution,
                "tile_count": result.get("tile_count"),
                "start": result.get("start"),
                "end": result.get("end"),
                "directions": result.get("directions", []),
                "directions_ascii": result.get("directions_ascii", []),
                "path": result.get("path", []),
                "validated": result.get("validated", False),
                "mode": result.get("mode"),
                "confidence": detection.get("confidence"),
                "grid_shape": detection.get("grid_shape") or result.get("grid_shape"),
                "solve_seconds": stats.get("elapsed_seconds"),
                "states": stats.get("states"),
                "backtracks": stats.get("backtracks"),
                "warnings": detection.get("warnings", []),
                "debug_grid": _data_url(output_dir / "debug_grid.png"),
            }
    except ValueError as exc:
        if str(exc) == "upload_too_large":
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"That image is too large. Maximum upload size is {MAX_UPLOAD_MB} MB.",
                },
                status_code=413,
            )
        raise
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {
                "ok": False,
                "error": "This board took too long to solve. Try again, or use the manual fallback if detection looks wrong.",
            },
            status_code=504,
        )
    except Exception as exc:
        # Keep server-side detail in logs without exposing temp paths or internals to the browser.
        print(f"Unexpected solve error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return JSONResponse(
            {"ok": False, "error": f"Unexpected server error: {type(exc).__name__}"},
            status_code=500,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "5000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
