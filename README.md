# Delta-GRAG (D-GRAG)

[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-blue)](#continuous-integration)
[![Coverage](https://img.shields.io/badge/Coverage-80%25%2B-green)](#testing-and-coverage)
[![Python](https://img.shields.io/badge/Python-3.12+-informational)](#quickstart)

**Delta-Graph Retrieval-Augmented Generation** for pull-request-aware code review.

D-GRAG builds a static call graph over a repository, maps changed PR lines to function-level anchors, extracts a bounded impact subgraph, linearizes that context, and feeds it into review generation and post-processing pipelines. The project also includes retrieval-only orchestration, baseline reviewers, Docker assets, and CI/coverage enforcement.

---

## Why D-GRAG

Traditional diff-only review loses architectural context. Traditional semantic retrieval can pull in broad but weakly related code. D-GRAG aims for a middle ground:

- **PR-scoped retrieval** around changed functions
- **Graph-aware context expansion** through callers and callees
- **Deterministic bounded traversal** to avoid context explosion
- **Traceable outputs** with node/file/line provenance
- **Modular architecture** so ingestion, traversal, and prompting can evolve independently

---

## What is implemented

Current repository capabilities include:

- **Static graph construction**
  - Python symbol extraction
  - intra-repo call edge resolution
  - graph persistence and incremental graph updates
- **PR ingestion**
  - unified diff parsing
  - anchor resolution from changed lines to graph nodes
- **Impact retrieval**
  - bounded upstream/downstream traversal
  - explicit impact subgraph model and stats
  - BFS linearization for promptable context
- **Review pipeline**
  - retrieval-only orchestration
  - prompt building
  - model call abstraction
  - normalized review output, dedupe, scoring, and formatting
- **Baselines**
  - diff-only reviewer
  - file-context reviewer
  - lightweight semantic RAG baseline
- **Operational surfaces**
  - Typer CLI
  - FastAPI GitHub webhook
  - Docker / docker-compose setup
- **Quality gates**
  - pytest suite
  - 80%+ coverage gate
  - CI workflow
  - Ruff linting

---

## Repository status note

This repository contains a functioning implementation of the core D-GRAG pipeline and surrounding tooling, but some runtime paths still depend on **real deployment secrets** or **runtime integrations** that are not bundled into the repo by default.

Examples:

- GitHub-backed review flows require a valid `GITHUB_TOKEN`
- webhook mode requires a `GITHUB_WEBHOOK_SECRET`
- full-review / external-LLM modes require the appropriate provider credentials

---

## System architecture

At a high level, the system flows like this:

```text
                 +----------------------+
                 |   Pull Request Diff  |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |   Diff Parser        |
                 |   Anchor Resolver    |
                 +----------+-----------+
                            |
                            v
+------------------+   +----------------------+   +----------------------+
| Repository Files |-->| Static Call Graph    |-->| Impact Subgraph      |
| (Tree-sitter)    |   | Builder / Updater    |   | Extraction (k_up/m)  |
+------------------+   +----------------------+   +----------+-----------+
                                                              |
                                                              v
                                                   +----------------------+
                                                   | BFS Linearization    |
                                                   | Context Packing      |
                                                   +----------+-----------+
                                                              |
                               +------------------------------+-----------------------------+
                               |                                                            |
                               v                                                            v
                    +----------------------+                                   +----------------------+
                    | Retrieval-only        |                                   | Full Review Pipeline |
                    | Orchestrator          |                                   | Prompt + LLM + Post  |
                    +----------+-----------+                                   +----------+-----------+
                               |                                                            |
                               v                                                            v
                    +----------------------+                                   +----------------------+
                    | CLI / Webhook /      |                                   | Findings / Markdown  |
                    | Evaluation / Caches  |                                   | JSON / PR Comments   |
                    +----------------------+                                   +----------------------+
```

---

## Project structure

```text
btp/
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── architecture.md
│   ├── docker_setup.md
│   ├── methodology.md
│   └── pseudocode.md
├── notebooks/
│   └── demo.ipynb
├── results/
├── scripts/
│   ├── entrypoint.sh
│   └── entrypoint.sh
├── src/
│   ├── baselines/
│   ├── graph/
│   ├── ingestion/
│   ├── linearization/
│   ├── llm/
│   ├── pipeline/
│   ├── postprocess/
│   ├── grammar_libs/
│   ├── ast_extractor.py
│   ├── call_extractor.py
│   ├── call_graph_builder.py
│   ├── cli.py
│   ├── file_indexer.py
│   ├── github_api.py
│   ├── graph_updater.py
│   ├── impact_subgraph.py
│   ├── import_resolver.py
│   ├── llm_caller.py
│   ├── repo_manager.py
│   ├── token_budget.py
│   └── webhook.py
├── tests/
│   ├── integration/
│   ├── conftest.py
│   └── test_*.py
├── Dockerfile
├── docker-compose.yml
├── dgrag.py
├── pyproject.toml
└── README.md
```

---

## Quickstart

### 1. Install dependencies

This project uses `uv` in the current workflow.

```bash
uv sync --all-groups
```

### 2. Run the test suite

```bash
uv run pytest
```

### 3. Run lint checks

```bash
uv run ruff check .
```

### 4. Show CLI help

```bash
uv run python -m dgrag --help
```

### 5. Run a PR review from a GitHub PR URL

```bash
uv run python -m dgrag review \
  --pr-url https://github.com/owner/repo/pull/123 \
  --depth-k 2 \
  --depth-m 3 \
  --model retrieval-only \
  --output-json review.json
```

> This command requires a valid `GITHUB_TOKEN` in your environment.

---

## CLI usage

The CLI currently exposes one main command:

### `review`

Runs end-to-end PR review preparation for a GitHub PR.

Example:

```bash
uv run python -m dgrag review \
  --pr-url https://github.com/owner/repo/pull/123 \
  --depth-k 2 \
  --depth-m 3 \
  --model retrieval-only \
  --cache-dir .cache/dgrag \
  --output-json artifacts/review.json
```

## Webhook usage

The project also provides a FastAPI GitHub webhook service.

Expected webhook endpoint:

- `POST /webhook/github`

Current behavior includes:

- HMAC validation using `X-Hub-Signature-256`
- support for PR events such as `opened`, `reopened`, and `synchronize`
- review orchestration through the GitHub-backed PR service
- PR comment posting for generated review summaries
- configurable timeout (`DGRAG_REVIEW_TIMEOUT_SECONDS`) for safer webhook execution

To run the webhook locally with Docker, see the Docker section below.

---

## Docker quickstart

### 1. Copy the environment template

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

### 2. Fill in required values

At minimum:

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET` for webhook mode
- `OPENAI_API_KEY` only if you run full-review / LLM-backed flows

### 3. Build and start

```bash
docker compose up --build
```

### 4. Docker CLI smoke test

```bash
docker run --rm --env-file .env -e DGRAG_MODE=cli btp-dgrag:latest python -m dgrag --help
```

For more details, see:

- `docs/docker_setup.md`

---

## Configuration and environment

### Common environment variables

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `DGRAG_MODE`
- `DGRAG_DEPTH_K`
- `DGRAG_DEPTH_M`
- `DGRAG_CACHE_DIR`

### Optional variables

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `REDIS_ENABLED`
- `REDIS_URL`
- `DGRAG_REVIEW_TIMEOUT_SECONDS`

### Mode expectations

- **webhook**
  - requires `GITHUB_TOKEN`
  - requires `GITHUB_WEBHOOK_SECRET`
- **cli**
  - requires `GITHUB_TOKEN`
- **llm` / `full-review`**
  - requires `GITHUB_TOKEN`
  - requires `OPENAI_API_KEY`

See `.env.example` for the current template.

---

## Demo notebook

The repository includes:

- `notebooks/demo.ipynb`

The notebook is intended to demonstrate:

1. installing or verifying dependencies
2. building a small graph over a tiny example repo
3. running the pipeline with a mocked or deterministic review flow
4. visualizing the impact subgraph
5. displaying results in a notebook-friendly format

The demo should be runnable without requiring a real API key for the demonstration path.

---

## Deeper documentation

If you want the design-level view rather than the landing page:

- `docs/architecture.md`
- `docs/methodology.md`
- `docs/pseudocode.md`
- `docs/docker_setup.md`

---

## Testing and coverage

The repository has an enforced quality bar:

- `uv run ruff check .`
- `uv run pytest --cov=src --cov-report=term-missing --cov-report=html --cov-fail-under=80`
- `uv run python -c "from src.eval.runner import run_benchmarks; run_benchmarks(fixture_path='tests/fixtures/eval_cases.json', output_dir='artifacts/eval')"`

Current CI is defined in:

- `.github/workflows/ci.yml`

The HTML coverage report is generated into:

- `htmlcov/`

---

## Continuous integration

The GitHub Actions workflow currently performs:

- checkout
- Python setup
- dependency sync with `uv`
- Ruff lint
- pytest with coverage
- coverage artifact upload

This keeps the repository aligned with the 80%+ coverage acceptance criteria.

---

## Limitations and honest caveats

A few important realities to keep in mind:

- The system is strongest on **static**, **intra-repo**, **Python-oriented** analysis paths currently implemented in the repo.
- Dynamic dispatch, reflection, framework magic, and runtime-only call resolution remain limited by design.
- The GitHub-backed orchestration path needs valid credentials and reachable repositories.
- Tree-sitter grammar binaries are not faked in-repo; the current implementation relies primarily on package-based runtime support.

---

## Roadmap snapshot

By this point, the repository includes major implementation slices across:

- graph construction
- anchor resolution
- impact traversal
- review pipeline
- baselines
- CLI
- webhook
- Docker
- CI / coverage

The remaining polish typically centers on:
- richer docs and demo experience
- stronger deployment hardening
- broader language/runtime support

---

## Development workflow

A typical contributor loop looks like:

```bash
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run python -m dgrag --help
```

For Docker validation:

```bash
docker build -t btp-dgrag:latest .
docker run --rm --env-file .env -e DGRAG_MODE=cli btp-dgrag:latest python -m dgrag --help
```

---

## License / usage

No license metadata is currently declared in this README. If you intend to publish or distribute the project broadly, add an explicit license file and update this section.

---

## Summary

D-GRAG is a modular PR-aware review system that uses **static call graphs + bounded graph retrieval** to provide more structurally relevant review context than diff-only or purely semantic baselines.

If you want to get started quickly:

1. install dependencies with `uv`
2. run the test suite
3. inspect CLI help
4. configure `.env` for GitHub-backed flows
5. use Docker or the CLI depending on your workflow

For design details and deployment notes, continue into the `docs/` directory.
