# Start here: put One Line Studio online

You do not need Python on your iPhone. The one-time setup is: upload the extracted project to GitHub, connect that repository to a host, then open the resulting URL.

## 1. Upload the project to GitHub

1. Download and extract the supplied `one-line-studio.zip`.
2. Sign in to [GitHub](https://github.com/) in Safari and create a new repository, for example `one-line-studio`.
3. Upload the **contents of the extracted folder**. `app.py`, `Dockerfile`, `render.yaml`, `generator/`, `web/`, `templates/`, and `static/` must be at the repository root.
4. Commit the upload.

Do not upload only the ZIP as one file; the host needs the extracted project.

## 2. Deploy with Render

1. Sign in to [Render](https://render.com/) and connect GitHub.
2. Choose **New → Blueprint**.
3. Select the repository you just uploaded.
4. Render reads `render.yaml`, builds the Dockerfile, and creates the web service.
5. Wait for the health check to turn green, then open the provided `onrender.com` URL.

No database, API key, paid add-on, or separate worker is required. A free/low-resource instance can sleep between visits, so its first request may be slower.

If Render asks for settings manually:

- runtime: **Docker**
- health check: `/health`
- Dockerfile: `Dockerfile` at the repository root
- start command: already included in the Dockerfile

## 3. Add it to your iPhone Home Screen

1. Open the deployed URL in Safari.
2. Tap **Share**.
3. Choose **Add to Home Screen**.
4. Name it `One Line`.

The existing screenshot workflow is under **Solver**. Puzzle creation is under **Generator**. A generation continues if Safari briefly backgrounds; return to the page to resume the same job status. Use **Cancel generation** for a job you no longer want.

## 4. Safe defaults

The deployed defaults allow one CPU-heavy generation at a time and queue a few more requests. They are appropriate for a small service. Optional tuning is documented in `.env.example` and `DEPLOY_RENDER.md`.

## Updating later

Commit replacement files to the same GitHub repository. Render normally rebuilds automatically. Generator exports contain `generator_version`; version `1.0` seeds reproduce version `1.0` topology.

