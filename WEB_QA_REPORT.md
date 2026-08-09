# Deployable web-app QA

## Automated HTTP coverage

`tests/test_web.py` starts the real FastAPI application through its ASGI interface and verifies:

- combined page contains both preserved Solver and new Generator panels;
- static PWA assets load;
- invalid dimensions, enums, and empty output selection return 422;
- generation receives a job ID and reaches a terminal state through polling;
- completed result is unique/validated and its matrix has exact requested dimensions;
- SVG, unsolved PNG, solved PNG, JSON, and solution-reveal endpoints work;
- repeated identical settings/seed use only a fully validated cached result and preserve topology hash;
- cancellation reaches `cancelled`;
- the existing manual `/api/solve` endpoint still returns a validated annotated image.

## Real browser mobile run

The production-style Uvicorn app was launched locally and driven through headless Chromium at an iPhone portrait viewport (`390×844`, device scale 2, touch enabled).

Actions performed:

1. opened the Generator tab;
2. set 9×8 with touch controls;
3. selected Hard / Extreme;
4. entered seed `mobile-ui-test`;
5. generated through the real background API;
6. loaded Visual Grid, Polished PNG, and 8-row Binary Matrix;
7. revealed the solution;
8. switched back to Solver.

Observed result:

```json
{
  "tier": "Hard — 246 / 600",
  "seed": "mobile-ui-test",
  "matrixRows": 8,
  "svgCount": 1,
  "pngLoaded": true,
  "solutionMoves": 33,
  "initialHorizontalOverflowPx": 0,
  "resultHorizontalOverflowPx": 0,
  "solverVisibleAfterSwitch": true,
  "browserErrors": []
}
```

The full-page capture is `examples/debug/mobile-generator.png`.

## PWA behavior

The service worker caches only shell assets, bypasses `/api/` so job polling is never stale, deletes the old shell cache on activation, and preserves standalone/home-screen metadata. A brief page background does not cancel a job; the current job ID is retained in local storage and polling resumes after reload.

