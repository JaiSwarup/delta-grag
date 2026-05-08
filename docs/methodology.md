# D-GRAG Methodology

Delta-Graph Retrieval-Augmented Generation (D-GRAG) is a structurally bounded PR review pipeline.  
The implementation focuses on deterministic, PR-scoped retrieval rather than global semantic similarity search.

## Retrieval Formulation

- Repository is modeled as a directed call graph `G=(V,E)` where `u -> v` means `u` calls `v`.
- Given PR delta lines `Δ`, anchors are functions whose spans intersect `Δ`.
- Retrieved context is an impact subgraph around anchors using bounded bidirectional BFS:
  - `k_up`: caller hops
  - `k_down`: callee hops
  - hard caps: `max_nodes`, `max_edges`, optional per-anchor and time budgets

## Implemented Pipeline

1. Parse unified diff and collect changed lines/files.
2. Resolve anchors from changed hunks against function spans in the static call graph.
3. Extract bounded impact subgraph with deterministic node discovery order.
4. Linearize context with explicit sections for modified nodes, callers, and callees.
5. Optionally run full review generation, then normalize, dedupe, and score findings.

## Robustness Features

- Deterministic traversal and serialization for reproducible outputs.
- Explicit cutoff/truncation metadata to avoid silent context collapse.
- Retry/backoff behavior on transient GitHub and clone/network failures.
- Timeout-controlled webhook execution.

## Offline Evaluation (MVP)

The repository includes a standalone offline evaluation harness in `src/eval` with deterministic fixtures:

- `impact_accuracy`: precision/recall/F1 on impacted-function retrieval.
- `token_efficiency`: context reduction relative to baseline token size.

Run example:

```bash
uv run python -c "from src.eval.runner import run_benchmarks; run_benchmarks(fixture_path='tests/fixtures/eval_cases.json', output_dir='artifacts/eval')"
```
