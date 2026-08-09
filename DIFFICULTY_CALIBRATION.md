# Difficulty calibration — generator version 1.0

## Corpus

Command:

```bash
python benchmarks/calibrate_difficulty.py --samples 10
```

The corpus contains **600 candidates**: six sizes (5, 7, 9, 12, 15, and 20 square), two modes, five topology presets, and ten deterministic seeds. Every entry passed certificate replay, route validation, and an exact stop-at-two uniqueness search.

- all unique and validated: **yes**
- distinct exact topology/start hashes: **592 / 600 (98.7%)**
- distinct rotation/reflection-canonical hashes: **555 / 600 (92.5%)**
- observed score range: **21–579**
- mean exact-uniqueness nodes: **78.76**
- maximum exact-uniqueness time: **0.116 s**
- maximum human-difficulty evaluation time: **0.373 s**
- corpus wall time: **46.08 s**

The raw feature rows are in `benchmark_results/calibration/difficulty_corpus.csv`.

Within each 60-candidate mode/preset group, exact diversity was 59–60 and canonical diversity was 53–60. Global canonical collisions include shapes reached by different topology presets; the engine also rejects a repeated canonical candidate within one request. Exact seeds remain deterministic—diversity filtering never depends on prior users or mutable server history.

## Distribution by topology preset

Presets guide how many and where deceptive ears are attempted. They are not labels: the engine accepts a final candidate only if its measured score is inside the requested tier.

| Mode / preset | P10 | Median | Mean | P90 | Range |
|---|---:|---:|---:|---:|---:|
| Normal / Easy | 23 | 24 | 28.0 | 40 | 21–45 |
| Normal / Medium | 105 | 189 | 174.9 | 245 | 83–315 |
| Normal / Hard | 244 | 312 | 320.0 | 413 | 169–466 |
| Normal / Expert | 316 | 471 | 442.4 | 547 | 272–566 |
| Normal / Evil | 308 | 523 | 466.2 | 564 | 190–579 |
| Extreme / Easy | 22 | 32 | 31.1 | 41 | 21–44 |
| Extreme / Medium | 99 | 164 | 167.7 | 245 | 40–312 |
| Extreme / Hard | 172 | 309 | 296.0 | 412 | 36–461 |
| Extreme / Expert | 264 | 428 | 400.7 | 522 | 42–571 |
| Extreme / Evil | 290 | 518 | 472.2 | 572 | 252–578 |

Overlap is intentional and expected. Shape randomness changes how long a wrong branch remains plausible. The rejection loop is the calibration boundary: e.g. a topology produced with the Hard preset that measures 412 is rejected for a Hard request.

## Score construction

The model uses normalized measured features:

- decision exposure: branch density plus a capped absolute branch count;
- forced-move ratio;
- average and maximum wrong-branch survival, gated by decision exposure;
- how locally tempting a wrong move looks compared with the true move;
- position of the first meaningful decision;
- two human-like local DFS agents and their backtracking;
- extra graph adjacencies / near-loops;
- turn ratio;
- articulation-style bottlenecks and open regions;
- enclosed cavities;
- pseudo-symmetry;
- locally interchangeable choices;
- a deliberately tiny, logarithmically normalized planning-size term.

A critical calibration change was gating deceptive-depth metrics by decision exposure. Without it, one wrong turn on a long 20×20 board could score as Evil solely because a naive walk survived many cells. Easy 20×20 corpus samples remain around the 20s–40s, proving tile count alone does not force a high tier.

Solver states, exact-verifier nodes, wall time, CPU model, and memory use have **zero score weight**. They are logged only for diagnostics.

## Human-readable explanation

Each level explanation is generated from the same measured features. It reports actual branch count, forced ratio, longest wrong branch, bottleneck/cavity counts, first-decision location, and high local temptation when present. It does not select generic prose from the requested tier.

## Limits and future calibration

This is a synthetic human model, not a replacement for player telemetry. Version 1.0 is calibrated for clear monotonic separation and size normalization using verified corpus features. A future version can fit weights to anonymized player solve/error data. Such a model change should increment `generator_version` if it changes accepted topology for a seed.
