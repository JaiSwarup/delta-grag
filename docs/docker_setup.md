# Docker Setup Guide

This guide explains how to build and run D-GRAG in Docker using the repository's container assets.

## What is included

The Docker setup currently includes:

- A multi-stage `Dockerfile`
- A `docker-compose.yml` with:
  - `dgrag` application service
  - optional `redis` sidecar
- A container entrypoint script at `scripts/entrypoint.sh`
- An example environment file at `.env.example`
- A default health check in the image

## Prerequisites

Before you start, make sure you have:

- Docker installed
- Docker Compose available via `docker compose`
- A GitHub token if you plan to use GitHub-backed review flows
- A webhook secret if you plan to run the webhook service
- An LLM API key only if you plan to run full-review / LLM-backed flows

## Quick start

### 1. Copy the environment template

Create a local `.env` file from the example:

```/dev/null/.env.example#L1-3
cp .env.example .env
```

On Windows PowerShell:

```/dev/null/.env.example#L1-3
Copy-Item .env.example .env
```

### 2. Fill in required values

At minimum, update these values in `.env`:

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET` for webhook mode
- `OPENAI_API_KEY` only for `llm` or `full-review` mode

Recommended defaults:

```/dev/null/example.env#L1-8
DGRAG_MODE=webhook
DGRAG_HOST=0.0.0.0
DGRAG_PORT=8000
DGRAG_CACHE_DIR=/app/.cache/dgrag
DGRAG_DEPTH_K=2
DGRAG_DEPTH_M=3
REDIS_ENABLED=false
REDIS_URL=redis://redis:6379/0
```

### 3. Build the image

From the project root:

```/dev/null/build.sh#L1-1
docker build -t btp-dgrag:latest .
```

### 4. Run with Docker Compose

```/dev/null/compose.sh#L1-1
docker compose up --build
```

This starts the `dgrag` service and the optional `redis` service declared in `docker-compose.yml`.

## Running the webhook service

The default container mode is webhook-oriented.

### Compose

```/dev/null/compose-webhook.sh#L1-1
docker compose up --build
```

### Direct Docker run

```/dev/null/run-webhook.sh#L1-1
docker run --rm -p 8000:8000 --env-file .env btp-dgrag:latest
```

The container entrypoint validates required environment variables before launching the service. If required values are missing, the container exits with a clear error message instead of failing silently.

## Running the CLI inside Docker

You can also use the image as a CLI container.

### Review help

```/dev/null/run-help.sh#L1-1
docker run --rm --env-file .env btp-dgrag:latest python -m dgrag --help
```

### Review command example

```/dev/null/run-review.sh#L1-1
docker run --rm --env-file .env btp-dgrag:latest python -m dgrag review --pr-url https://github.com/owner/repo/pull/123
```

### Force CLI mode

If you want environment validation to follow CLI rules:

```/dev/null/run-cli.sh#L1-1
docker run --rm --env-file .env -e DGRAG_MODE=cli btp-dgrag:latest python -m dgrag --help
```

## Using Redis

The compose file includes a Redis service as an optional sidecar.

Current notes:

- `redis` is available for future cache integration
- the application currently still uses filesystem-oriented cache paths by default
- `REDIS_ENABLED` and `REDIS_URL` are included to support a cleaner transition to Redis-backed caching later

If you do not want Redis behavior enabled, keep:

```/dev/null/redis.env#L1-2
REDIS_ENABLED=false
REDIS_URL=redis://redis:6379/0
```

## Health checks

The image includes a Docker `HEALTHCHECK` that verifies the D-GRAG module can be imported successfully.

This is intended as a lightweight runtime sanity check, not a full application readiness probe.

## Environment variables

### Core runtime

- `DGRAG_MODE`
  - `webhook`
  - `cli`
  - `llm`
  - `full-review`
- `DGRAG_HOST`
- `DGRAG_PORT`
- `DGRAG_CACHE_DIR`

### GitHub integration

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_TOKEN_ENV_VAR`

### Retrieval defaults

- `DGRAG_DEPTH_K`
- `DGRAG_DEPTH_M`

### Optional Redis config

- `REDIS_ENABLED`
- `REDIS_URL`

### Optional LLM config

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `DGRAG_MODEL_LABEL`

## Mode-specific required variables

### `webhook`

Required:

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET`

### `cli`

Required:

- `GITHUB_TOKEN`

### `llm` or `full-review`

Required:

- `GITHUB_TOKEN`
- `OPENAI_API_KEY`

## Tree-sitter note

The current codebase relies on Python package-based Tree-sitter support rather than repository-bundled precompiled grammar shared libraries.

That means:

- dependency installation happens during image build
- runtime does not depend on fetching grammars from the internet
- there are not currently custom bundled grammar `.so` files in the repository

If custom offline grammar artifacts are introduced later, they should be copied into the image in a dedicated grammar directory and documented alongside the build process.

## File overview

Relevant container files:

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`
- `scripts/entrypoint.sh`

## Troubleshooting

### Container exits immediately with missing variable errors

Cause:
- required environment variables are missing for the selected mode

What to do:
- check `.env`
- verify `DGRAG_MODE`
- ensure required variables for that mode are set

Example symptoms:

```/dev/null/log.txt#L1-4
[dgrag][error] Missing required environment variables: GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET
[dgrag][error] Set them in your container environment or .env file before starting D-GRAG.
```

### `docker run ... python -m dgrag --help` still complains about missing env vars

Cause:
- the container entrypoint validates environment variables before launching commands
- default mode may still be `webhook`

Fix:
- set `DGRAG_MODE=cli`

Example:

```/dev/null/run-cli-help.sh#L1-1
docker run --rm --env-file .env -e DGRAG_MODE=cli btp-dgrag:latest python -m dgrag --help
```

### Webhook requests return signature errors

Cause:
- `GITHUB_WEBHOOK_SECRET` in the container does not match the GitHub webhook configuration

What to do:
- update `.env`
- ensure GitHub uses the exact same secret
- restart the container after changes

### Port 8000 is already in use

Cause:
- another process or container is already bound to port `8000`

Fix options:
- stop the existing process
- remap the host port

Example:

```/dev/null/compose-alt-port.sh#L1-1
docker run --rm -p 8080:8000 --env-file .env btp-dgrag:latest
```

### Docker build fails due to dependency resolution

Cause:
- local Docker environment cannot resolve or install required Python packages

What to do:
- retry with a clean Docker cache
- verify outbound network access during image build
- confirm `pyproject.toml` and `uv.lock` are in sync

Useful commands:

```/dev/null/docker-rebuild.sh#L1-2
docker build --no-cache -t btp-dgrag:latest .
docker compose build --no-cache
```

### Webhook service starts but PR processing fails

Possible causes:
- invalid GitHub token
- insufficient GitHub permissions
- bad PR URL or inaccessible repository
- missing webhook secret
- unsupported event payload

What to check:
- token scopes and repository access
- container logs
- webhook payload and signature headers

### Redis starts but nothing seems to use it

This is expected right now unless you have added Redis-backed cache integration in application code.

The compose setup includes Redis as an optional deployment building block, but filesystem cache remains the default operational path.

## Verifying the setup

### Build test

```/dev/null/verify-build.sh#L1-1
docker build -t btp-dgrag:latest .
```

### CLI smoke test

```/dev/null/verify-help.sh#L1-1
docker run --rm --env-file .env -e DGRAG_MODE=cli btp-dgrag:latest python -m dgrag --help
```

### Compose smoke test

```/dev/null/verify-compose.sh#L1-1
docker compose up --build
```

## Recommended development workflow

1. Copy `.env.example` to `.env`
2. Fill in required secrets
3. Build the image
4. Run `python -m dgrag --help` through Docker
5. Start the webhook service with Compose
6. Inspect logs if startup validation fails

## Next improvement areas

If you continue hardening the container story later, useful follow-ups would be:

- dedicated readiness endpoint checks
- non-root runtime user
- explicit Redis-backed cache integration
- slimmer final image via more aggressive dependency pruning
- fully offline bundled Tree-sitter grammar artifacts
- CI-based Docker build and smoke validation
