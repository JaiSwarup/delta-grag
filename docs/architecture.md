# D-GRAG Architecture

This document describes the **implemented repository architecture** for D-GRAG, not just the idealized roadmap version.

D-GRAG (**Delta-Graph Retrieval-Augmented Generation**) is a PR-aware review system that combines:

- static repository analysis
- diff-to-anchor resolution
- bounded impact-subgraph retrieval
- deterministic context linearization
- prompt / model integration
- structured review post-processing
- evaluation and ablation tooling
- operational interfaces such as CLI, webhook, Docker, and CI

---

## 1. System goals

The current architecture is built around a few practical goals:

1. **PR-scoped retrieval**
   - retrieve only code structurally related to changed regions

2. **Deterministic context shaping**
   - use explicit traversal bounds to avoid prompt explosion

3. **Traceable review outputs**
   - preserve file paths, line spans, node identifiers, and graph metadata

4. **Modular execution**
   - keep ingestion, graphing, traversal, prompting, orchestration, and evaluation loosely coupled

5. **Operational usability**
   - support local CLI use, webhook-driven flows, Dockerized deployment, and CI validation

---

## 2. Implemented high-level architecture

At a system level, the repository currently works like this:

```text
                  +----------------------+
                  | Pull Request / Diff  |
                  +----------+-----------+
                             |
                             v
                  +----------------------+
                  | Ingestion            |
                  | - diff parsing       |
                  | - repo loading       |
                  | - anchor resolution  |
                  +----------+-----------+
                             |
                             v
+------------------+  +----------------------+  +-----------------------------+
| Repository Files |->| Static Graph Build   |->| Impact Retrieval            |
| / Snapshots      |  | / Update             |  | - anchors                   |
|                  |  | - AST extraction     |  | - bounded traversal         |
|                  |  | - call extraction    |  | - impact subgraph           |
|                  |  | - import resolution  |  +-------------+---------------+
+------------------+  +----------------------+                |
                                                             v
                                                +-----------------------------+
                                                | Linearization               |
                                                | - BFS ordering              |
                                                | - context shaping           |
                                                | - token budgeting           |
                                                +-------------+---------------+
                                                              |
                               +------------------------------+------------------------------+
                               |                                                             |
                               v                                                             v
                    +-------------------------+                                  +--------------------------+
                    | Retrieval-only pipeline |                                  | Full review pipeline     |
                    | / PR orchestrator       |                                  | - prompt builder         |
                    |                         |                                  | - model invocation       |
                    |                         |                                  | - normalization          |
                    +-------------+-----------+                                  | - dedupe / scoring       |
                                  |                                              +-------------+------------+
                                  |                                                            |
                                  v                                                            v
                    +-------------------------+                                  +--------------------------+
                    | CLI / Webhook / Caches  |                                  | Markdown / JSON /        |
                    | / Eval / Docker         |                                  | PR-comment-ready output  |
                    +-------------------------+                                  +--------------------------+
```

---

## 3. Repository layout

The architecture maps directly onto the current repository structure.

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
│   └── generate_latex_table.py
├── src/
│   ├── __init__.py
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
│   ├── baselines/
│   ├── eval/
│   ├── graph/
│   ├── grammar_libs/
│   ├── ingestion/
│   ├── linearization/
│   ├── llm/
│   ├── pipeline/
│   └── postprocess/
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

## 4. Architectural layers

### 4.1 Static analysis and graph construction

This layer converts source code into graph-structured repository knowledge.

#### Main modules

- `src/ast_extractor.py`
- `src/call_extractor.py`
- `src/import_resolver.py`
- `src/call_graph_builder.py`
- `src/graph_updater.py`
- `src/graph/call_extractor.py`
- `src/graph/graph_builder.py`

#### Responsibilities

- parse Python source into function-level nodes
- extract function, method, nested function, and lambda-assigned symbols
- resolve direct, import-based, and some class/self call patterns
- build a directed static call graph
- incrementally update graph state for changed files
- expose graph-builder CLI-style helpers for persistence and summaries

#### Core graph concepts

- **node**
  - function/method/symbol identifier
  - qualified name
  - source location
  - code snippet metadata

- **edge**
  - caller -> callee relation
  - optional call-site metadata such as line number

#### Two graph-related paths currently exist

There are two related graph-building surfaces in the repo:

1. **root-level graph path**
   - `src/ast_extractor.py`
   - `src/call_extractor.py`
   - `src/call_graph_builder.py`
   - `src/graph_updater.py`

2. **package-scoped graph path**
   - `src/graph/call_extractor.py`
   - `src/graph/graph_builder.py`
   - `src/graph/impact_subgraph.py`

This reflects iterative development. Both are tested and used for different slices of the system.

---

### 4.2 Repository and file metadata

This layer manages repository snapshots and file metadata needed by graphing and retrieval.

#### Main modules

- `src/repo_manager.py`
- `src/file_indexer.py`
- `src/ingestion/repo_loader.py`

#### Responsibilities

- clone repositories at specific SHAs
- cache local snapshots
- provide repository-relative file listings
- extract file-level metadata such as size and LOC
- filter parseable source files
- support downstream ingestion and file-context baselines

#### Design note

This layer intentionally separates:

- **snapshot acquisition** from
- **snapshot consumption**

so retrieval and analysis can operate on already materialized local trees.

---

### 4.3 PR ingestion and anchor resolution

This layer turns raw PR diffs into graph anchors.

#### Main modules

- `src/ingestion/diff_parser.py`
- `src/ingestion/anchor_resolver.py`

#### Responsibilities

- parse unified diff text
- recover changed files and changed line ranges
- map changed hunks to graph nodes
- track resolved and unresolved anchors
- preserve PR metadata for downstream retrieval and prompting

#### Core output

The key architectural artifact here is the **anchor set**:

- resolved anchor node IDs
- unresolved hunks
- hunk-to-anchor mappings
- PR metadata

This anchor set is the bridge between:
- textual code diffs
- structural graph retrieval

---

### 4.4 Impact retrieval and subgraph shaping

This layer determines the graph neighborhood worth sending to the reviewer.

#### Main modules

- `src/impact_subgraph.py`
- `src/graph/impact_subgraph.py`

#### Responsibilities

- perform bounded upstream and downstream expansion from anchors
- enforce hard limits:
  - `k_up`
  - `k_down`
  - `max_nodes`
  - `max_edges`
  - `max_per_anchor`
  - optional time budget
- classify node roles:
  - anchor
  - caller
  - callee
  - shared
- produce subgraph stats and cutoff reasons

#### Output characteristics

The impact retrieval layer returns:

- induced subgraph
- deterministic node order
- enriched node-role model
- traversal stats for observability and evaluation

This is the architecture’s core anti-context-explosion mechanism.

---

### 4.5 Linearization and context budgeting

This layer converts graph structure into promptable text.

#### Main modules

- `src/linearization/bfs_linearizer.py`
- `src/token_budget.py`

#### Responsibilities

- serialize impact subgraphs in deterministic BFS order
- separate context into structural sections
- optionally include code blocks
- optionally include diff sections
- enforce character/token budgets
- emit truncation markers when limits are reached

#### Design principle

The linearizer is intentionally **deterministic** and **budget-aware**.  
That matters because evaluation, debugging, and CI all depend on stable prompt inputs.

---

### 4.6 Prompting and model integration

This layer prepares reviewer prompts and interacts with model backends.

#### Main modules

- `src/llm/prompt_builder.py`
- `src/llm/review_generator.py`
- `src/llm/transformers_client.py`
- `src/llm/dev_mock_backend.py`
- `src/llm_caller.py`

#### Responsibilities

- construct structured review prompts
- support strict JSON-oriented prompting
- call model backends
- support deterministic mock / dev paths
- normalize provider responses
- capture telemetry such as attempts and latency

#### Two related LLM integration paths

There are two complementary integration styles in the repo:

1. **pipeline-oriented path**
   - prompt building
   - transformers client
   - post-processing integration

2. **async JSON-caller path**
   - `src/llm_caller.py`
   - schema-centric retries and telemetry
   - useful for baseline reviewer flows

This split exists because the project supports both:
- integrated review-pipeline flows
- lighter-weight async baseline review calls

---

### 4.7 Review post-processing

This layer turns model output into structured, rankable findings.

#### Main modules

- `src/postprocess/review_types.py`
- `src/postprocess/finding_deduper.py`
- `src/postprocess/scoring.py`
- `src/postprocess/formatter.py`

#### Responsibilities

- normalize raw JSON / JSON-like output into typed findings
- coerce severities, confidences, and evidence
- deduplicate findings
- compute risk / confidence scoring
- format final output as Markdown or JSON

#### Output contract

This layer is where the architecture transitions from:
- model output text
to
- stable review artifacts suitable for:
  - CLI output
  - saved review files
  - webhook/PR comments
  - evaluation

---

## 5. Pipeline architecture

There are two main orchestration surfaces in the current codebase.

### 5.1 Core review pipeline

#### Main module

- `src/pipeline/review_pipeline.py`

#### Flow

1. parse PR diff
2. resolve anchors
3. extract bounded impact subgraph
4. linearize context
5. optionally build prompt and invoke model
6. normalize, dedupe, score, and format findings
7. emit metadata summary

#### Key characteristics

- supports retrieval-only mode
- supports full-review mode
- supports configurable output format
- provides metadata for observability and evaluation

---

### 5.2 PR URL orchestrator

#### Main module

- `src/pipeline/pr_orchestrator.py`

#### Flow

1. validate GitHub PR URL
2. fetch PR metadata and diff through provider interface
3. clone base and head snapshots
4. parse diff
5. build call graph on head snapshot
6. resolve anchors
7. extract impact subgraph
8. cache serialized retrieval result
9. return timing breakdown and context-token estimate

#### Design purpose

This module is the bridge from:
- an external PR URL
to
- a fully materialized retrieval result

It is intentionally provider-based so tests can remain network-free.

---

## 6. Baseline systems

The repo includes non-D-GRAG baselines for comparison.

### Main modules

- `src/baselines/diff_only_reviewer.py`
- `src/baselines/file_context_reviewer.py`
- `src/baselines/semantic_rag.py`

### Baselines provided

#### Diff-only reviewer
Uses only the diff text, optionally truncated to a token budget.

#### File-context reviewer
Uses changed-file context and modified-function slices instead of graph context.

#### Semantic RAG
Uses a lightweight vector-like token-frequency retrieval baseline rather than graph traversal.

### Why these matter

Architecturally, baselines are isolated from the core graph pipeline so they can be:
- compared fairly
- evaluated with the same metrics tooling
- evolved independently

---

## 7. Evaluation architecture

This layer measures system quality and supports comparison.

### Main modules

- `src/eval/metrics.py`
- `src/eval/ablation.py`

### 7.1 Metrics engine

The metrics engine is intentionally **corpus-agnostic**.

It works over explicit evaluation cases and computes:

- structural recall
- token reduction percentage
- cross-file detection rate
- hallucination rate
- BLEU
- ROUGE-L

It also provides adapters from implemented runtime result types.

### 7.2 Ablation runner

The ablation runner is **corpus-driven** and currently built around explicit JSON inputs.

It supports:

- `(k, m)` sweeps
- parser labels
- aggregate CSV export
- heatmap generation
- concurrency limits for sweep execution

### Architectural note

The repo does not bundle fake “real benchmark” outputs.  
`results/` is expected to be populated by actual runs of the metrics/ablation tooling.

---

## 8. External integration architecture

### 8.1 GitHub integration

#### Main module

- `src/github_api.py`

#### Responsibilities

- read GitHub auth config from environment
- fetch PR metadata
- fetch unified diffs
- post review comments back to PRs
- render PR-comment-ready summaries

This is used by both:
- the CLI
- the webhook service

---

### 8.2 CLI

#### Main module

- `src/cli.py`
- entry module: `dgrag.py`

#### Responsibilities

- expose `review` command for GitHub PR review
- expose `benchmark` command for evaluation-oriented runs
- show rich terminal output
- write JSON output when requested
- validate invalid PR URL input cleanly

The CLI is one of the main user-facing architecture boundaries.

---

### 8.3 Webhook service

#### Main module

- `src/webhook.py`

#### Responsibilities

- run FastAPI application
- validate GitHub webhook signatures
- handle supported PR events
- call review orchestration
- post review summaries back to GitHub
- expose health endpoint
- support environment-driven runtime configuration

This is the architecture boundary for automated PR-event-driven review.

---

## 9. Container and deployment architecture

### Main files

- `Dockerfile`
- `docker-compose.yml`
- `scripts/entrypoint.sh`
- `.env.example`
- `docs/docker_setup.md`

### Responsibilities

- multi-stage build for reproducible environments
- containerized webhook / CLI runtime
- environment validation at entrypoint
- Docker health checks
- optional Redis sidecar in compose
- documented local deployment flow

### Important current note

The repository includes a reserved `src/grammar_libs/` location for future offline grammar assets, but it does **not** currently fake or bundle custom grammar shared libraries as if they were real production artifacts.

---

## 10. Testing and CI architecture

### Main files

- `tests/conftest.py`
- `tests/integration/test_full_pipeline.py`
- many module-specific test files under `tests/`
- `.github/workflows/ci.yml`

### Responsibilities

- unit coverage of implemented modules
- integration coverage of end-to-end pipeline flow
- coverage gating
- lint enforcement
- artifact generation for HTML coverage

### Quality gates currently enforced

- `ruff check .`
- pytest suite
- coverage threshold `>= 80%`

This is an explicit architectural layer, not just project hygiene:  
the repo now assumes changes should remain compatible with automated validation.

---

## 11. Data and control flow details

### 11.1 Retrieval-only path

A retrieval-only path typically looks like:

1. PR URL arrives
2. metadata provider returns PR info
3. repo snapshots are cloned / reused
4. diff is parsed
5. graph is built
6. anchors are resolved
7. impact subgraph is extracted
8. node code is aggregated for context sizing
9. result is cached and returned

### 11.2 Full review path

A full-review path typically looks like:

1. diff is parsed
2. anchors are resolved
3. impact subgraph is extracted
4. context is linearized
5. prompt is built
6. model is called
7. output is normalized
8. findings are deduped and scored
9. final markdown/json is emitted

---

## 12. Architectural strengths

The implemented architecture already has several strong properties:

### Deterministic retrieval
Traversal and linearization are bounded and stable.

### Strong observability
Many modules emit metadata, timing, or summary structures.

### Testability
Provider protocols, stub-friendly APIs, and corpus-driven evaluation make it easy to test without real network calls.

### Operational separation
Core logic, webhook behavior, CLI output, and deployment concerns are separated.

### Evaluation-ready design
Baselines and D-GRAG outputs can be pushed through the same metrics stack.

---

## 13. Current architectural constraints

This architecture also has known limitations.

### Static-only limitations
The call-graph logic remains conservative and cannot fully model dynamic runtime dispatch.

### Multiple historical code paths
There are parallel root-level and package-level graph/review utilities in the repository due to iterative development.

### Evaluation inputs are external
Real benchmark corpora and PR datasets are not bundled by default.

### GitHub / LLM credentials are external
Operational flows still depend on environment-provided secrets and external services.

### Uneven module maturity
Some modules are production-facing and heavily tested; others are supporting utilities or roadmap carryovers with lighter runtime integration.

---

## 14. Recommended way to think about the codebase

The cleanest mental model for the current repository is:

### Core engine
- ingestion
- graph construction
- impact retrieval
- linearization
- prompt / review pipeline
- post-processing

### Operational shell
- CLI
- webhook
- GitHub integration
- Docker
- CI

### Research / comparison layer
- baselines
- metrics
- ablation
- notebook / LaTeX export

This separation explains most of the directory structure and module boundaries.

---

## 15. Architecture by package

### `src/ingestion/`
Diffs, repositories, and anchor resolution.

### `src/graph/`
Static symbol extraction, graph build helpers, and graph-scoped impact traversal.

### `src/linearization/`
Context ordering and formatting.

### `src/llm/`
Prompt and model-facing logic.

### `src/pipeline/`
End-to-end orchestration.

### `src/postprocess/`
Normalization, dedupe, scoring, and formatting.

### `src/baselines/`
Comparison systems.

### `src/eval/`
Metrics and ablation infrastructure.

### top-level `src/*.py`
Cross-cutting utilities and earlier/root-level architecture surfaces that still participate in the working implementation.

---

## 16. Summary

The implemented D-GRAG repository is not just a prototype script collection. It now has a recognizable architecture with:

- graph-construction and update paths
- PR-aware ingestion and anchor resolution
- bounded impact retrieval
- deterministic context linearization
- multiple review-generation paths
- structured output normalization
- baseline comparators
- evaluation and ablation tooling
- operational interfaces
- Docker deployment assets
- CI and coverage enforcement

The key architectural idea remains the same throughout the repo:

> convert changed PR regions into structural graph anchors, expand only the relevant impact neighborhood, and present that bounded context to a reviewer pipeline in a deterministic, auditable way.