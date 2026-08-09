# Render deployment and operations

`render.yaml` defines one Docker web service. The Docker command runs one Uvicorn worker so the in-memory job queue, cancellation events, cache, and polling state remain coherent.

```text
uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1
```

Do not raise Uvicorn's process-worker count without moving jobs to a shared external queue. On a larger single machine, `MAX_CONCURRENT_GENERATIONS=2` enables the engine's capped second thread, but one is safer on small instances.

## Health and logs

- health check: `GET /health`
- generation completion logs: structured JSON with job ID, dimensions, tier, mode, seed, attempts, score, timings, and state
- solver errors and unexpected generator failures are logged server-side without exposing paths to clients

## Recommended environment

The checked-in defaults need no environment variables. See `.env.example` for every supported limit. Useful production overrides include:

```text
GENERATION_MAX_SECONDS=50
MAX_CONCURRENT_GENERATIONS=1
GENERATION_QUEUE_SIZE=4
GENERATION_CACHE_SIZE=8
GENERATION_JOB_HISTORY=24
GENERATION_JOB_TTL_SECONDS=1800
GENERATION_RATE_LIMIT_PER_MINUTE=6
MAX_UPLOAD_MB=15
SOLVER_TIMEOUT_SECONDS=60
MAX_CONCURRENT_SOLVES=1
```

## Statelessness

Jobs and the verified-result cache are intentionally bounded and in memory. A server restart clears them; deterministic seeds reproduce completed levels. The application writes solver uploads only inside automatically cleaned temporary directories and does not require persistent disk.

## Local Docker check

```bash
docker build -t one-line-studio .
docker run --rm -p 10000:10000 one-line-studio
curl http://localhost:10000/health
```

