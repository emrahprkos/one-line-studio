# Web application notes

The mobile website now contains both **Solver** and **Generator** without coupling either engine to presentation code.

- `app.py` preserves `/api/solve` and includes the generator router.
- `web/api.py` validates requests and serves job/artifact endpoints.
- `web/jobs.py` owns the bounded worker queue, progress, cancellation, cache, TTL cleanup, rate limits, and structured logs.
- `templates/index.html`, `static/app.js`, and `static/generator.js` provide the responsive PWA interface.

Run locally with `python app.py`; see `README.md` for the API and architecture, `WEB_QA_REPORT.md` for browser/HTTP verification, and `START_HERE.md` for deployment.

