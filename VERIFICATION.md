# Verification report

Run date: 2026-08-09

The three supplied 1320×2868 screenshots were processed from original pixels by
the same automatic code path. The detected grids were visually compared with
the screenshots, the debug overlays were inspected, and every returned path was
then checked by `validate_solution` for the required start, exact length,
uniqueness, orthogonal adjacency, and membership in the detected board.

| Screenshot | Grid extent | Tiles | Start | End | CV confidence | Search states | Search time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Level 21 | 8×7 | 44 | (0,4) | (7,0) | 0.992 | 315 | 0.008 s |
| Level 22 | 9×7 | 47 | (2,4) | (8,0) | 0.994 | 13,171 | 0.396 s |
| Level 23 | 9×8 | 57 | (3,7) | (0,0) | 0.983 | 10,645 | 0.335 s |

Times are from the included development run and will vary by computer. All
three outputs have `"validated": true` in their `result.json` files.

## Detected matrices

`S` is the automatically identified colored tile.

### Level 21

```text
0 0 0 1 S 1 1
0 0 1 1 1 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 1
0 1 0 1 1 1 1
0 1 1 1 1 1 1
1 1 1 1 1 1 0
```

### Level 22

```text
0 0 0 1 1 1 1
0 0 1 1 1 1 1
0 1 1 1 S 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 1
0 1 1 1 1 1 0
1 1 1 0 0 0 0
```

### Level 23

```text
1 1 1 1 0 0 0 0
0 1 1 1 1 0 0 0
1 1 1 1 1 1 0 0
1 0 1 1 1 1 1 S
1 1 1 1 1 1 1 1
1 1 1 1 1 1 1 1
1 1 1 1 1 1 1 1
1 1 0 1 1 1 1 1
1 1 1 1 1 0 0 0
```

## Additional checks

- The optimized solver was compared with an independent naive DFS on every
  connected subset of a 3×3 grid and every possible starting tile; existence
  results matched in all cases.
- The same detector recovered the exact tile counts and starting coordinates
  from all three supplied screenshots after each of these transformations:
  50% downscaling, brightness reduction, and JPEG recompression at quality 55.
- An 80-tile 8×10 board was solved and validated from four different starts in
  80 visited search states per case during performance smoke testing.
- The matrix-only fallback, synthetic screenshot detector, debug renderers, and
  annotation output were exercised by the included test suite.

Hamiltonian path search is NP-complete, so pathological boards can still require
more time than these examples. Configured limits produce an explicit failure;
they never cause an unvalidated partial route to be labeled as solved.
