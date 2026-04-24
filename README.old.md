# Delta-GRAG (D-GRAG)

Deterministic, PR-aware code review context generation pipeline.

This project builds a static call graph, resolves PR diff anchors to function nodes, extracts a bounded impact subgraph with deterministic BFS, linearizes context under budget, and optionally runs an end-to-end LLM review + postprocessing flow.

---

## Features

- Static Python call graph construction.
- PR unified diff parsing and hunk extraction.
- Anchor resolution from changed lines to function-level nodes.
- Bounded bidirectional impact subgraph extraction.
- Deterministic BFS-based context linearization.
- Optional full-review mode:
  - Prompt construction
  - LLM invocation (`mock` or `hf_pipeline`)
  - Finding normalization, deduplication, scoring, formatting

---

## Requirements

- Python `>=3.14`
- Dependencies in `pyproject.toml` (notably `networkx`, `tree-sitter`, `pytest`)
- Optional for Tree-sitter Python grammar path:
  - `tree_sitter_python` package
- Optional for HF backend:
  - `transformers`

---

## CLI

The project exposes a single CLI entrypoint via:

```/dev/null/cmd.txt#L1-1
python main.py
```

### Top-level help

```/dev/null/cmd.txt#L1-1
python main.py -h
```

You should see two commands:

- `build-graph`
- `review`

---

## Command: `build-graph`

Build and persist a call graph from a repository.

### Usage

```/dev/null/cmd.txt#L1-1
python main.py build-graph --repo <path-to-repo> --output <graph.pkl>
```

### Arguments

- `--repo` (required): repository root to scan
- `--output` (required): output path for serialized graph (`.pkl`)

### Example

```/dev/null/cmd.txt#L1-1
python main.py build-graph --repo . --output artifacts/call_graph.pkl
```

### Output

The command prints a summary including:

- repo path
- output path
- node count
- edge count

---

## Command: `review`

Run retrieval-only context generation or full end-to-end review from an existing graph + PR diff.

### Usage

```/dev/null/cmd.txt#L1-1
python main.py review --graph <graph.pkl> --diff <pr.diff> [options]
```

### Required arguments

- `--graph`: input graph pickle file
- `--diff`: unified diff file path

### Optional arguments

- `--pr-metadata <metadata.json>`: JSON object with PR metadata
- `--output <path>`: write output to file (otherwise prints to stdout)
- `--summary-output <path>`: write JSON summary artifact

#### Retrieval controls

- `--k-up <int>` (default: `2`)
- `--k-down <int>` (default: `3`)
- `--max-nodes <int>` (default: `180`)
- `--max-chars <int>` (default: `12000`)
- `--no-code` (exclude code snippets from context)

#### Full-review / LLM controls

- `--full-review` (enable prompt + model + postprocessing)
- `--output-format {markdown,json}` (default: `markdown`)
- `--llm-backend <mock|hf_pipeline>` (default: `mock`)
- `--llm-model <name>` (default: `mock-model`)
- `--llm-temperature <float>` (default: `0.1`)
- `--llm-max-new-tokens <int>` (default: `2048`)
- `--llm-mock-response <text>` (optional deterministic mock override)
- `--non-strict-json` (relax strict JSON instruction in prompt)

#### Postprocess controls

- `--no-dedupe` (disable finding deduplication)
- `--dedupe-include-fix` (include `suggested_fix` in dedupe key)

---

## Review Modes

### 1) Retrieval-only mode (default)

If `--full-review` is **not** provided, the command outputs:

- linearized impact context text

This is useful for debugging/inspection or external model orchestration.

Example:

```/dev/null/cmd.txt#L1-1
python main.py review --graph artifacts/call_graph.pkl --diff samples/pr.diff --output artifacts/context.md
```

### 2) Full-review mode

If `--full-review` is provided, the command outputs:

- formatted review artifact (`markdown` or `json`)
- optional summary JSON if `--summary-output` is provided

Example (mock backend):

```/dev/null/cmd.txt#L1-1
python main.py review --graph artifacts/call_graph.pkl --diff samples/pr.diff --full-review --llm-backend mock --output-format markdown --output artifacts/review.md --summary-output artifacts/review_summary.json
```

Example (HF backend):

```/dev/null/cmd.txt#L1-1
python main.py review --graph artifacts/call_graph.pkl --diff samples/pr.diff --full-review --llm-backend hf_pipeline --llm-model mistralai/Mistral-7B-Instruct-v0.2 --output-format json --output artifacts/review.json
```

> Note: `hf_pipeline` requires `transformers` and compatible local runtime setup.

---

## Metadata file format (`--pr-metadata`)

Provide a JSON object, for example:

```/dev/null/example.json#L1-6
{
  "pr_id": 123,
  "title": "Refactor cache invalidation",
  "author": "alice",
  "base_branch": "main"
}
```

---

## Quick start

1. Build graph:

```/dev/null/cmd.txt#L1-1
python main.py build-graph --repo . --output artifacts/call_graph.pkl
```

2. Run retrieval-only review context:

```/dev/null/cmd.txt#L1-1
python main.py review --graph artifacts/call_graph.pkl --diff samples/pr.diff --output artifacts/context.md
```

3. Run full review with mock backend:

```/dev/null/cmd.txt#L1-1
python main.py review --graph artifacts/call_graph.pkl --diff samples/pr.diff --full-review --llm-backend mock --output artifacts/review.md --summary-output artifacts/summary.json
```

---

## Testing

Run tests with:

```/dev/null/cmd.txt#L1-1
pytest -q
```

---

## Notes

- The pipeline is deterministic by design (ordering, BFS traversal, serialization).
- Token budget is currently approximated by character budget for context/prompt truncation.
- When Python Tree-sitter grammar bindings are unavailable, extraction falls back to AST-based parsing for robustness.