# One Line Studio

One Line Studio combines two independently usable tools:

- **Solver:** screenshot → detected board → validated Hamiltonian route → annotated image.
- **Generator:** dimensions/settings/seed → connected puzzle with exactly one fixed-start Hamiltonian path → SVG, PNG, matrix, and JSON outputs.

The original screenshot solver remains in `solve_one_line.py`. Generator code is isolated in `generator/`, its HTTP job layer is in `web/`, and the mobile UI calls backend APIs rather than containing puzzle logic.

## Quick start

Python 3.10+ is supported; Python 3.12 is used by the Docker image.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`. The production command is:

```bash
uvicorn app:app --host 0.0.0.0 --port 10000 --workers 1
```

The single worker is deliberate: the in-process job queue and bounded cache are designed for a modest server. The included Dockerfile and `render.yaml` use this configuration.

## Generate from Python or the command line

The engine can be imported without the website:

```python
from generator import (
    DifficultyTier, GenerationSettings, OutputOptions,
    ShapeMode, generate_level,
)

settings = GenerationSettings(
    width=12,
    height=9,
    difficulty=DifficultyTier.HARD,
    shape_mode=ShapeMode.NORMAL,
    seed="839271420",
    outputs=OutputOptions(True, True, True),
)
level = generate_level(settings)
assert level.validated and level.unique
```

Or create a complete output folder:

```bash
python generate_one_line.py \
  --width 12 --height 9 \
  --difficulty hard --shape-mode normal \
  --seed 839271420 \
  --output generated_level
```

This writes `level.json`, `matrix.txt`, functional SVG grids, and separate `unsolved_level.png` / `solved_level.png` files. Coordinates are consistently zero-based `(row, column)`.

## Solve a screenshot

```bash
python solve_one_line.py screenshot.png
```

The unchanged solver writes `solution.png`, `debug_tiles.png`, `debug_grid.png`, `detected_board.txt`, `route.txt`, and `result.json`. It never emits a solved route until `validate_solution` confirms the start, length, non-repetition, board membership, and every orthogonal move.

Manual matrix fallback remains available:

```text
S 1 1
1 1 1
0 1 1
```

```bash
python solve_one_line.py --matrix board.txt
python solve_one_line.py --matrix board.txt --start 2,4
```

See `solve_one_line.py --help` and `VERIFICATION.md` for detector/solver details.

## Why generated levels are unique

Generation does not place random cells and hope a solver succeeds. It starts with a chord-free induced grid path and applies certified **ear expansions**. An ear replaces route edge `u–v` with `u–a–b–v`, while `u–v` remains a tempting graph chord. The two new cells have no cross-edges when inserted, and the two original degree-one endpoints remain endpoints.

The uniqueness argument is inductive: in any Hamiltonian path between the forced degree-one endpoints, degree-two cells `a` and `b` must occur as `u–a–b–v`; contracting that segment yields the unique path from the previous construction step. This is scalable even at hundreds of cells.

The generator does **not** trust that argument alone:

1. `verify_construction_certificate` independently replays every insertion and checks its invariant.
2. The existing solver, which knows nothing about generation, finds a route from the fixed start.
3. `HamiltonianUniquenessVerifier` performs an exact stop-at-two search and explicitly looks for any second route.
4. Final validation rechecks cells, dimensions, connectivity, path, endpoints, matrix, image manifest, requested score tier, and deterministic replay.

The exact verifier uses bitsets, checkerboard parity, residual degree/end-point tests, flood connectivity, Tarjan separator pruning, fail-state caching, and known-route ordering. The known route changes ordering only; it cannot hide another route. Its pruning was cross-checked against naive counting on every connected board/start within 3×3 (1,081 cases).

## Human difficulty, not CPU difficulty

Scores are `0–600` and map to:

| Tier | Accepted score |
|---|---:|
| Easy | 0–119 |
| Medium | 120–239 |
| Hard | 240–359 |
| Expert | 360–479 |
| Evil | 480–600 |

The score uses measured route features: forced-move ratio, decision density, plausible wrong moves, average/maximum wrong-branch survival, local temptation, decision timing, human-like local-agent backtracking, turns, unused graph adjacencies, bottlenecks, cavities, pseudo-symmetry, and local ambiguity. Raw computer search time is recorded but has zero weight in the score.

Topology presets steer candidate search; they do not assign the public tier. A candidate is served only when its measured score falls inside the requested range. The score was calibrated on 600 exact-unique candidates. See `DIFFICULTY_CALIBRATION.md` and the raw CSV in `benchmark_results/calibration/`.

## Normal and Extreme

- **Normal** scores density, bounding-box use, compactness, chunkiness, centered balance, cavities, corridor fraction, and spikes. Weak silhouettes are rejected.
- **Extreme** permits sparse paths, asymmetry, deep corridors, cavities, and bottlenecks, but still applies a quality floor and every correctness check.

Base shapes include oriented coarse scaffolds and many-trial randomized induced walks. Starts are natural solution endpoints and vary with the seed; they are not forced to corners or a particular edge.

Each candidate records an exact topology hash, a rotation/reflection-canonical shape hash, degree distribution, normalized start position, solution-turn signature, density, symmetry, and geometric metrics. Repeated canonical candidates are skipped within a request. In the 600-candidate corpus, 592 exact and 555 canonical hashes were distinct; filtering does not consult mutable global history, so seed replay remains deterministic.

## Seed reproducibility

Topology randomness uses Python's local `random.Random`, seeded with a BLAKE2b integer derived from this canonical payload:

```text
generator_version + seed + width + height + difficulty + shape_mode + attempt
```

Output choices do not affect topology. The accepted attempt is replayed before serving. Reproducibility is versioned: version `1.0` promises identical output for identical topology settings and seed. A future incompatible algorithm will use a new generator version rather than silently changing the meaning of old seeds.

## Generator HTTP API

```text
POST /api/generate
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
GET  /api/jobs/{job_id}/level.png
GET  /api/jobs/{job_id}/solution.png
GET  /api/jobs/{job_id}/solution
GET  /api/jobs/{job_id}/level.json
```

`POST /api/generate` accepts:

```json
{
  "width": 8,
  "height": 7,
  "difficulty": "hard",
  "shape_mode": "normal",
  "seed": "839271420",
  "outputs": {
    "visual_grid": true,
    "polished_png": true,
    "binary_matrix": true
  }
}
```

Jobs move through `queued`, `generating`, `checking_solution`, `checking_uniqueness`, `scoring`, `rendering`, then `complete`, `failed`, or `cancelled`. Percentages are monotonic phase markers; attempt count, exact phase, best score, target, elapsed time, uniqueness status, and verifier nodes are live backend measurements. The UI does not invent an ETA.

Only fully validated unique results enter the bounded deterministic cache.

## Resource and input controls

- dimensions hard-capped to `3…20` on both axes;
- difficulty/mode enums and seed regex checked server-side;
- at least one output required;
- one generation worker by default, a four-job queue, and per-client submission limits;
- size/tier-dependent attempt and wall-time budgets, with a hard server cap;
- cancellation event checked in construction boundaries, exact search, and human scoring;
- bounded job history, TTL cleanup, and bounded verified-result cache;
- screenshot upload limit, subprocess timeout, safe suffixes, and temporary-directory cleanup;
- no filesystem paths or user data accepted by generator endpoints;
- structured request logs contain job/settings/performance data, not personal content.

Environment variables are documented in `.env.example`.

## Repository layout

```text
generator/
  engine.py          deterministic orchestration and final validation
  topology.py        induced-path construction, mutations, aesthetics, hashes
  uniqueness.py      certificate replay and independent exact stop-at-two search
  difficulty.py      measured human-difficulty features and scoring
  render.py          SVG, PNG, matrix, and route outputs
  models.py          settings, results, metrics, and versioned export schema
web/
  api.py             validated generator HTTP endpoints
  jobs.py            bounded queue, progress, cancellation, cache, logging
solve_one_line.py    preserved screenshot detector/solver/validator/renderer
app.py               FastAPI entrypoint and preserved /api/solve endpoint
templates/, static/  responsive Solver | Generator PWA
benchmarks/           repeatable calibration, performance, and stress suites
tests/                solver, generator, property, stress, and HTTP tests
examples/generated/  every tier, both modes, and five 20×20 examples
```

## Tests and benchmarks

```bash
python -m pytest -q
RUN_STRESS_TESTS=1 python -m pytest -q tests/test_generator_stress.py
python benchmarks/calibrate_difficulty.py --samples 10
python benchmarks/run_benchmarks.py --samples 2
python benchmarks/stress_large.py --samples 3
python tools/generate_examples.py
```

The committed measured runs contain 600 calibration candidates, 120 full cross-size requests, and 90 additional large requests. All 90 large requests succeeded and were exact-unique/validated, including 30/30 at 20×20. See `PERFORMANCE_REPORT.md`.

## Honest limitation

Hamiltonian uniqueness remains NP-complete in the general case. The certified construction makes the supplied family scalable, but an exact second-solution check is still mandatory and bounded. Some requested seeds can exhaust their budget. In the measured matrix, 5×5 Evil could not reach a genuine `480+` human score and failed clearly rather than being mislabeled; 5×5 Easy through Expert and every tested tier at 7×7+ succeeded. Large Expert/Evil jobs can take several seconds, with a measured 20×20 stress maximum of 10.69 s.

## Deployment

See [START_HERE.md](START_HERE.md) for an iPhone-friendly GitHub → Render walkthrough and [DEPLOY_RENDER.md](DEPLOY_RENDER.md) for operator details.

## License

MIT; see [LICENSE](LICENSE).
