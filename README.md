# ai-video-workflow

End-to-end AI video automation: script → shot generation → human review → YouTube publish, with a durable task queue & dashboard.

## Business Context

- **Category:** content automation product
- **Audience:** creators and small teams that want repeatable publishing, research, or video-production workflows.
- **Repository status:** Public repository. Keep examples, docs, and issues free of credentials, private data, and machine-specific paths.
- **Topics:** ai-video, automation, content-creation, fastapi, orchestration, pixverse, python, video-generation, workflow, youtube

## What This Project Is For

- End-to-end AI video automation: script → shot generation → human review → YouTube publish, with a durable task queue & dashboard.
- Move content work from ad hoc drafting to an inspectable production pipeline.
- Preserve human review while automating mechanical research, drafting, or publishing steps.

## Where It Fits

This repository turns content work into a repeatable workflow: inputs, processing steps, review points, and outputs are visible enough to audit and improve.

## Technical Overview

- **Primary language:** Python
- **Detected stack:** Python, Node.js, Python project metadata, Python dependencies, Docker, Docker Compose, Next.js, React, Tailwind CSS, TypeScript
- **Default branch:** `main`
- **Visibility:** `PUBLIC`
- **License:** Apache License 2.0

## Repository Map

- `api`
- `docs`
- `tests`
- `.devcontainer`
- `.dockerignore`
- `.env.example`
- `Dockerfile`
- `LICENSE`
- `NOTICE`
- `ORCHESTRATOR_README.md`
- `README.md`
- `SECURITY.md`

## Quick Start

### Python services (API & Orchestrator Worker)

The Python services need the `ffmpeg` binary on `PATH`, and so do the API route tests (they start the video service). The Dockerfile and CI install it with apt.

```bash
# Install ffmpeg first:
#   macOS:          brew install ffmpeg
#   Debian/Ubuntu:  sudo apt-get install -y ffmpeg
uv sync --locked --extra dev         # Python dependencies + pytest, pytest-httpx, ruff
uv run python api/app.py             # API server (binds 127.0.0.1:8000 by default)
uv run python -m orchestrator.worker # background shot/asset worker
uv run ruff check .                  # lint (CI runs the same check)
uv run pytest                        # tests
```

### Dashboard (`dashboard/`)

Run Next.js dashboard commands inside the `dashboard/` directory:

```bash
cd dashboard
npm install
npm run dev
npm start
npm run build
npm run lint
```

Docs: `pip install -r requirements-docs.txt` and then `mkdocs build --strict`.

## Scope & Security Boundaries

- **Caller authentication (`PIXELLE_API_TOKEN`)**: Mutating orchestrator endpoints (`POST /api/orchestrator/tasks*`, shot/video review, and `/publish`) require `Authorization: Bearer $PIXELLE_API_TOKEN` whenever `PIXELLE_API_TOKEN` or `PIXELLE_REQUIRE_AUTH=1` is set, and otherwise only allow loopback clients (`127.0.0.1`, `::1`, `localhost`).
- **Loopback default binding**: `api/app.py` defaults to `--host 127.0.0.1`. Container deployments (`Dockerfile`) explicitly pass `--host 0.0.0.0` so port exposure remains explicit.
- **Two-stage review & idempotent publish**: Publishing requires both video-level approval (`POST /review/approve`) and at least one approved shot with a clip asset, and persists an `uploading`/`published` lock in `publish_records` before uploading to YouTube to prevent duplicate uploads on retries.
- **Single-operator model**: Multi-tenant per-user/workspace isolation on task listings is outside the current single-operator boundary; protect shared or remote deployments behind authenticated reverse proxies or `PIXELLE_API_TOKEN`.

## Operating Notes

- Keep real credentials out of the repository. Use local environment files, GitHub repository secrets, or the deployment platform secret manager.
- If a `.env.example` file exists, treat it as documentation only; never commit filled-in `.env` files.
- Before publishing screenshots, demos, or client examples, remove private names, internal paths, account IDs, and API endpoints.
- The `Repository Hygiene` workflow is a lightweight guardrail, not a replacement for product-specific tests.

## Delivery Checklist

- [ ] README describes the user, business outcome, and operating boundary.
- [ ] Setup or preview commands are current and do not rely on private machine state.
- [ ] No real secrets, private user data, or machine-local state are tracked.
- [ ] Screenshots, demos, or sample outputs are safe to share publicly when the repository is public.
- [ ] Product-specific tests or smoke checks are documented before production use.

## Roadmap

- Tighten the fastest path from clone to useful demo.
- Add project-specific screenshots, sample outputs, or a short walkthrough where useful.
- Promote repeated manual steps into scripts, tests, or documented workflows.
- Keep security, privacy, and licensing boundaries explicit as the project evolves.

## Maintainer Notes

Maintained by [Tony Sheng](https://github.com/shengdabai). This README is written as a business-facing handoff: it should help a future collaborator, client, or reviewer understand why the repository exists, how to inspect it, and what must be true before it is reused or shipped.
