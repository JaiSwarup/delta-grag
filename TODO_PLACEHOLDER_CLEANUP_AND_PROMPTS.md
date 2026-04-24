# BTP Placeholder Cleanup + PROMPTS Remaining Work

## 1) Placeholder/Dummy Inventory (and removal todo)

### Production-path placeholders and dummies

- [ ] Replace mock-first LLM defaults in [main.py](main.py#L209), [main.py](main.py#L210), [src/pipeline/review_pipeline.py](src/pipeline/review_pipeline.py#L74), [src/pipeline/review_pipeline.py](src/pipeline/review_pipeline.py#L75).
Action:
Set default backend/model to a real provider config path (or require explicit backend), and fail fast when no real backend is configured.

- [ ] Remove or gate deterministic mock response controls in runtime config from [main.py](main.py#L214), [src/pipeline/review_pipeline.py](src/pipeline/review_pipeline.py#L80), [src/llm/transformers_client.py](src/llm/transformers_client.py#L80), [src/llm/transformers_client.py](src/llm/transformers_client.py#L81).
Action:
Keep only for tests/dev profile; disable in production profile.

- [ ] Demote/relocate mock backend implementation in [src/llm/transformers_client.py](src/llm/transformers_client.py#L85).
Action:
Move mock backend into test utilities or a dev-only adapter, and keep production client focused on real providers.

- [ ] Remove sample smoke-check pathway from graph builder CLI in [src/graph/graph_builder.py](src/graph/graph_builder.py#L128), [src/graph/graph_builder.py](src/graph/graph_builder.py#L161), [src/graph/graph_builder.py](src/graph/graph_builder.py#L181).
Action:
Either delete sample-check code or keep it under tests only.

- [ ] Remove stale/duplicate documentation snapshot [README.old.md](README.old.md).
Action:
Delete if no longer needed, or clearly mark as archival and exclude from active docs references.

- [ ] Clean mock-generated artifacts used as real-looking outputs in [artifacts/runs/click_pr2944/review.md](artifacts/runs/click_pr2944/review.md#L22), [artifacts/runs/click_pr3084/review.md](artifacts/runs/click_pr3084/review.md#L22).
Action:
Regenerate with real backend or label these runs as mock-only examples.

### Test-only placeholders (keep, but isolate)

- [ ] Keep stubs/mocks in tests but isolate naming and folder conventions in [tests/test_llm_and_postprocess.py](tests/test_llm_and_postprocess.py#L27), [tests/test_llm_and_postprocess.py](tests/test_llm_and_postprocess.py#L113), [tests/test_review_pipeline.py](tests/test_review_pipeline.py#L279).
Action:
No production removal needed; ensure these are clearly test fixtures.

## 2) PROMPTS.md Remaining Tasks (completion todo)

Source roadmap: [PROMPTS.md](PROMPTS.md)

Legend:
- Status = Remaining means not implemented or only partially implemented.
- Status = Partial means there is related code but it does not match PROMPTS task acceptance criteria.

## Phase 1: Tools & Discovery (Tasks 1-5)

- [ ] Task 1 Remaining: Static Analysis Parser Benchmark module and reports.
- [ ] Task 2 Remaining: Graph library benchmark (NetworkX vs igraph).
- [ ] Task 3 Remaining: Embedding retrieval benchmark (CodeBERT/GraphCodeBERT/UniXCoder).
- [ ] Task 4 Remaining: LLM proxy/routing benchmark (LiteLLM vs direct SDK).
- [ ] Task 5 Remaining: Dataset acquisition + ground-truth labeling pipeline.

## Phase 2: Core Engine (Tasks 6-20)

- [ ] Task 6 Partial: Repo snapshot manager requested in PROMPTS vs current loader in [src/ingestion/repo_loader.py](src/ingestion/repo_loader.py).
Gap:
No clone-at-SHA cache manager as specified.

- [x] Task 7 Mostly done in [src/ingestion/diff_parser.py](src/ingestion/diff_parser.py).

- [ ] Task 8 Partial: File indexer metadata pipeline requested vs current loader in [src/ingestion/repo_loader.py](src/ingestion/repo_loader.py).
Gap:
No dedicated FileIndex model with encoding/LOC metadata and configured extension filtering contract from PROMPTS.

- [ ] Task 9 Partial: AST function extraction exists inside [src/graph/call_extractor.py](src/graph/call_extractor.py).
Gap:
No standalone module/API matching PROMPTS contract with explicit FunctionNode schema and dedicated tests by that boundary.

- [ ] Task 10 Partial: Call extraction exists in [src/graph/call_extractor.py](src/graph/call_extractor.py).
Gap:
Needs explicit CallEdge API contract and resolution-method reporting aligned to PROMPTS.

- [ ] Task 11 Partial: Graph builder exists in [src/graph/graph_builder.py](src/graph/graph_builder.py).
Gap:
PROMPTS asks GraphML + JSON serializers and wrapper APIs beyond current pickle path.

- [x] Task 12 Mostly done in [src/ingestion/anchor_resolver.py](src/ingestion/anchor_resolver.py).

- [ ] Task 13 Partial: Import resolution logic exists in [src/graph/call_extractor.py](src/graph/call_extractor.py).
Gap:
No standalone import boundary mapper matching PROMPTS interface and metrics.

- [x] Task 14 Mostly done in [src/graph/impact_subgraph.py](src/graph/impact_subgraph.py).

- [ ] Task 15 Partial: Induced subgraph extraction exists in [src/graph/impact_subgraph.py](src/graph/impact_subgraph.py).
Gap:
Missing explicit ImpactSubgraph/SubgraphStats datamodel and role enrichment contract.

- [ ] Task 16 Partial: Budget control exists as character budget in [src/linearization/bfs_linearizer.py](src/linearization/bfs_linearizer.py).
Gap:
PROMPTS requires token budget manager using tokenizer-aware pruning and anchor retention guarantees.

- [x] Task 17 Mostly done in [src/linearization/bfs_linearizer.py](src/linearization/bfs_linearizer.py).

- [x] Task 18 Mostly done in [src/llm/prompt_builder.py](src/llm/prompt_builder.py).

- [ ] Task 19 Partial: LLM invocation exists in [src/llm/transformers_client.py](src/llm/transformers_client.py).
Gap:
PROMPTS expects async LiteLLM caller with retry/backoff, strict schema parse, and provider-grade telemetry.

- [ ] Task 20 Remaining: Incremental graph updater module not present.

## Phase 3: Baselines + Pipeline (Tasks 21-25)

- [ ] Task 21 Remaining: Semantic RAG baseline (FAISS) module.
- [ ] Task 22 Remaining: Diff-only baseline reviewer module.
- [ ] Task 23 Remaining: File-context baseline reviewer module.
- [ ] Task 24 Partial: Pipeline exists in [src/pipeline/review_pipeline.py](src/pipeline/review_pipeline.py).
Gap:
PROMPTS expects PR-URL orchestrator with clone/base-head handling, caching, and full end-to-end timings.

- [ ] Task 25 Remaining: Typer CLI + FastAPI webhook integration not present as specified.

## Phase 4: Evaluation & Polish (Tasks 26-30)

- [ ] Task 26 Remaining: Evaluation metrics engine not present as specified.
- [ ] Task 27 Remaining: Ablation runner and heatmap outputs not present.
- [ ] Task 28 Remaining: Docker/containerization assets not present.
- [ ] Task 29 Partial: Tests exist in [tests](tests), but no explicit 80% coverage gate/CI workflow matching PROMPTS acceptance criteria.
- [ ] Task 30 Partial: README exists in [README.md](README.md), but notebook demo + arXiv LaTeX export pipeline not present.

## 3) Execution Order Todo (recommended)

- [ ] Milestone A: Remove production mock/dummy defaults and stale artifacts (Section 1).
- [ ] Milestone B: Finish core-engine partials (Tasks 6, 8-11, 13, 15-16, 19-20).
- [ ] Milestone C: Implement baselines and PR-url orchestrator (Tasks 21-25).
- [ ] Milestone D: Build evaluation/ablation and reproducibility layer (Tasks 26-30).

## 4) Definition of Done for placeholder cleanup

- [ ] No mock backend as runtime default for production CLI path.
- [ ] No stale mock review artifacts presented as real evaluation outputs.
- [ ] No duplicate old README confusion in top-level docs.
- [ ] Sample/demo code paths moved out of production runtime modules or clearly dev-only.
