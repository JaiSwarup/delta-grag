# Real-world evaluation (`eval_final`)

This folder holds a **standalone, offline** evaluation on real GitHub Python repos: we build a **commit-level dataset**, score Delta‑GRAG per case, then compare to simple baselines on the same labels and diffs.

---

## Quick start

From the `delta-grag` project root (with dependencies installed):

```bash
python eval_final/run_real_eval.py
python eval_final/compare_methods.py
```

Optional flags (see `run_real_eval.py --help`):

- `--repos` — comma-separated catalog names (default: `flask,httpx,fastapi,requests,click`).
- `--commits-per-repo` — how many top-scoring commits to keep per repo (default: `10`).
- `--search-limit` — max commits scanned from history per repo (default: `220`).
- `--output-root` — defaults to `eval_final` under the repo root.

Long runs: **call-graph construction** is expensive on large repos; set `PYTHONUNBUFFERED=1` so progress prints flush immediately.

---

## Folder layout

| Path | Purpose |
|------|---------|
| `run_real_eval.py` | Clones/updates repos, scans commits, builds one call graph per repo, writes the dataset and `summary.*`. |
| `compare_methods.py` | Reads the dataset + `repos_cache`, recomputes D‑GRAG vs baselines, writes `baseline_comparison_*`. |
| `repos_cache/<name>/` | Local git clones used for `git diff` and graph rebuilds. **Do not commit** these nested repos (see project `.gitignore`). |
| `results/real_eval_cases.json` | The **dataset**: one JSON object with a `cases` array. |
| `results/real_eval_cases.csv` | Same cases as flattened rows (list fields JSON-encoded in cells). |
| `results/summary.json` / `summary.md` / **`summary.csv`** | Aggregated metrics over all cases (`csv`: one `overall` row + `per_repo` rows). |
| `results/baseline_comparison_summary.{json,md}` / **`baseline_comparison_summary.csv`** | Macro averages per method + context reduction vs file-context. |
| `results/baseline_comparison_per_case.csv` | Per-case precision/recall/F1/tokens for each method. |

---

## Phase 1 — Did we build a dataset? (`run_real_eval.py`)

**Yes.** The dataset is **`results/real_eval_cases.json`**.

It is **not** a hand-annotated benchmark: each **case** is one **real commit** from an upstream repo, plus numbers and node IDs produced by this pipeline. It is **reproducible** (same script, same SHAs) and **automatic** (labels are rules, not human review).

### How each case is built (per catalog repo)

1. **Clone or update** the repo into `repos_cache/<name>`.
2. **Build one Python call graph** for the whole checkout (`build_call_graph`).
3. **Walk history**: `git log --first-parent --no-merges`, newest first, up to `--search-limit` commits.
4. For each commit, compare **`parent → head`** with `git diff … -- *.py`.
5. **Keep** commits that pass filters:
   - Non-empty Python diff with hunks.
   - Not **test-only** changes (paths under test trees are skipped).
   - Diff can be **anchored** to graph nodes (`resolve_anchors_from_parsed_diff`), or a **fallback** picks nodes touching changed files.
   - **Impact subgraph** extraction returns a non-empty ordering (`extract_impact_subgraph` with bounded `k_up`/`k_down` and caps).
6. For each surviving commit we record:
   - **`base_sha` / `head_sha`** (parent vs child commit).
   - **`changed_files`**, **`retrieved_nodes`** (top 20 from impact ordering = D‑GRAG retrieval).
   - **`manual_impacted_nodes`** — up to 5 nodes from **`_manual_labels`**: prefer anchors in the subgraph, then neighbors. Despite the name, this is **automatic / heuristic**, not human ground truth.
   - **Token estimates**: **`graph_tokens`** (linearized subgraph text) vs **`baseline_tokens`** (reading full changed `.py` files, non-test).
   - Per-case **recall**, **context_reduction**, **anchor_resolution_rate**, and a composite **`score`**.
7. **Selection**: while scanning, collect up to `2 × commits_per_repo` candidates, then **sort by `score` and keep the top `commits_per_repo`** per repo. So commits are **“best under this automatic score”**, not uniformly random.

Outputs: **`real_eval_cases.json`**, **`summary.json`**, **`summary.md`**.

---

## Phase 2 — Baseline comparison (`compare_methods.py`)

Reads **`real_eval_cases.json`** and rebuilds graphs from **`repos_cache`** (same repos as phase 1). For each case it compares:

| Method | Idea |
|--------|------|
| **dgrag** | Uses stored `retrieved_nodes` (top K = 20). |
| **diff_only** | Graph nodes resolved directly from the unified diff (anchors). |
| **file_context** | Graph nodes whose files appear in the diff (broad “everything in touched files”, capped at K). |
| **semantic_proxy** | Nodes whose text lexically overlaps the diff (cheap semantic stand-in). |

Each method gets **precision, recall, F1** against the **same** `manual_impacted_nodes` set, and a **token budget** definition appropriate to that baseline. **Context reduction vs file_context** is \((\text{file\_context tokens} - \text{method tokens}) / \text{file\_context tokens}\).

Outputs: **`baseline_comparison_summary.{json,md}`**, **`baseline_comparison_per_case.csv`**.

---

## Metrics cheat sheet

### In `summary.json` (D‑GRAG vs proxy labels, per pipeline)

- **Structural recall** — Fraction of `manual_impacted_nodes` that appear in the top-20 `retrieved_nodes`.
- **Context reduction** — \((\text{baseline\_tokens} - \text{graph\_tokens}) / \text{baseline\_tokens}\) where baseline is **full changed `.py` file** content (non-test).
- **Anchor resolution rate** — Implemented as **`len(anchor_ids) / total_hunks`**. Values **> 1** are possible (more anchors than hunks); **do not read this as a percentage**. Prefer fixing the definition for thesis text (e.g. resolved hunks / total hunks, capped at 1).

### In `baseline_comparison_summary.json`

- **Precision / recall / F1** — Standard overlap between the method’s top-K node set and `manual_impacted_nodes`.
- **Avg tokens** — Average estimated context size per method (definitions differ: diff text vs full files vs graph context).
- **context_reduction_vs_file_context** — Savings vs loading all changed Python files for that case.

---

## How to interpret results (thesis-friendly)

- **Strong internal consistency + compression:** High recall/precision against **`manual_impacted_nodes`** together with **large context reduction vs full files** shows the graph path is **coherent under the proxy labels** and **much smaller** than pasting whole files.

- **Perfect-looking recall** should be caveated: labels come from the **same impact machinery** as retrieval, so numbers can be **optimistically high**. For external validity, **spot-check or manually label** a subset of cases.

- **diff_only at 0 recall** in some runs usually reflects **mismatch between anchor IDs and the heuristic label set**, not that “diffs are useless” in general.

---

## Repo catalog

Defined in `run_real_eval.py` (`REPO_CATALOG`). Non-Python entries (e.g. express, gin) clone but produce **few or no** `*.py` diffs under this pipeline unless you change the diff filter.

---

## Licensing / provenance

Cases point at **public upstream repositories**. When you cite them, name the repo and commit SHAs from each case in `real_eval_cases.json`.
