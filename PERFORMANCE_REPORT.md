# Performance report — generator version 1.0

Measured locally on 2026-08-09 using the committed benchmark scripts. These are observed samples, not latency guarantees.

## Full cross-size benchmark

Command:

```bash
python benchmarks/run_benchmarks.py --samples 2 --time-budget 30
```

This ran every combination of six sizes, two modes, five tiers, and two seeds: 120 complete requests. PNG rendering was excluded so generation/verification timings are easier to compare; matrix/SVG validation remained enabled.

| Size | Requests | Success | Median successful time | Maximum request time |
|---|---:|---:|---:|---:|
| 5×5 | 20 | 16 | 0.008 s | 2.469 s |
| 7×7 | 20 | 20 | 0.033 s | 0.532 s |
| 9×9 | 20 | 20 | 0.049 s | 0.106 s |
| 12×12 | 20 | 20 | 0.074 s | 0.583 s |
| 15×15 | 20 | 20 | 0.143 s | 0.464 s |
| 20×20 | 20 | 20 | 0.203 s | 3.931 s |

Across 116 successful requests:

- median total: **0.067 s**
- p95 total: **0.583 s**
- maximum total: **3.931 s**
- maximum accumulated uniqueness time: **0.590 s**
- maximum accumulated uniqueness nodes: **2,216**
- peak process RSS: **78.50 MiB**
- total suite wall time: **29.95 s**

The four failures were precisely the two Normal and two Extreme 5×5 Evil requests. Every candidate remained below the required 480 score through 180 attempts, so the engine returned the documented budget failure rather than silently lowering difficulty.

## 20×20 detail

All 20 full 20×20 requests succeeded.

| Mode | Tier | Observed times (two seeds) | Scores |
|---|---|---:|---:|
| Normal | Easy | 0.060, 0.070 s | 22, 23 |
| Normal | Medium | 0.117, 0.235 s | 198, 190 |
| Normal | Hard | 0.200, 0.207 s | 329, 349 |
| Normal | Expert | 0.366, 0.430 s | 433, 463 |
| Normal | Evil | 0.671, 1.399 s | 545, 539 |
| Extreme | Easy | 0.100, 0.077 s | 37, 23 |
| Extreme | Medium | 0.169, 0.112 s | 226, 216 |
| Extreme | Hard | 0.121, 0.191 s | 340, 335 |
| Extreme | Expert | 1.304, 3.931 s | 475, 427 |
| Extreme | Evil | 0.631, 0.670 s | 552, 554 |

Expert can be slower than Evil: it may reject candidates that overshoot 479, while an Evil candidate can land above 480 immediately. This is target-search behavior, not a claim that Expert is intrinsically harder for the verifier.

## Additional large stress test

Command:

```bash
python benchmarks/stress_large.py --samples 3
```

This ran 90 more complete requests across 12×12, 15×15, and 20×20, every mode/tier, and three seeds:

- **90/90 succeeded**
- **30/30 20×20 succeeded**
- every success was exact-unique and independently validated
- median: **0.156 s**
- p95: **2.684 s**
- maximum overall and 20×20: **10.686 s**
- suite wall time: **55.08 s**

Raw per-request data is committed under `benchmark_results/performance/` and `benchmark_results/large_stress/`.

## Phase interpretation

The reports separately record candidate construction, first-solution verification, exact uniqueness, human scoring, rendering, solver nodes, uniqueness nodes, attempts, and rejection causes. Human scoring often costs more than exact uniqueness on this certified family because it deliberately simulates plausible local mistakes. Computer search duration is diagnostic only and does not enter the difficulty formula.

## Scaling architecture

The generated family avoids unrestricted 400-vertex uniqueness proofs. Ear-expansion certificates make uniqueness structurally constrained; the independent exact checker still searches for a second route and normally follows the known route while false choices prune quickly. General arbitrary 20×20 Hamiltonian boards remain potentially intractable, so all jobs retain hard attempt, node, and wall-time bounds.

