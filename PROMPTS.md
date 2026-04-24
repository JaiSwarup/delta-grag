# D-GRAG: 30-Task Copilot/Cursor/Aider-Ready Implementation Roadmap

---

## PHASE 1: TOOLS & DISCOVERY (Tasks 1–5)

---

```
TASK 1/30: Static Analysis Parser Benchmark
PHASE: Tools
GOAL: Benchmarked comparison report of Tree-sitter vs. libcst vs. parso for Python AST/call extraction
INPUTS: 3 sample repos (fastapi, django, requests) cloned locally
OUTPUTS: reports/parser_benchmark.csv [columns: parser, repo, parse_time_ms, call_edges_found, accuracy_%], reports/parser_choice.md
SPECS/CASES:
  • Edge case: files with syntax errors must not crash pipeline — graceful skip + log
  • Perf constraint: parse 10k LOC repo in <30s on Colab T4
  • Validation: manually verify 20 call edges in fastapi; precision = TP/(TP+FP) ≥ 0.90
COPILOT PROMPT: "In VS Code, write a Python 3.12 benchmarking module `tools/parser_benchmark.py` using poetry. Import tree_sitter, libcst, parso. For each parser, parse all .py files in a given repo path, extract function defs and call expressions, record wall-clock time via timeit, count call edges found. Use dataclasses for BenchmarkResult(parser, repo, time_ms, edges, precision). Handle SyntaxError gracefully with try/except + logging.warning. Print a rich.table comparing all three. Save CSV to reports/parser_benchmark.csv. Test with tests/test_parser_benchmark.py asserting edges > 0 and time < 30000ms for fastapi repo. Add ruff linting."
```

---

```
TASK 2/30: Graph Library Performance Benchmark
PHASE: Tools
GOAL: Benchmarked comparison of NetworkX vs. python-igraph for call-graph ops (BFS, subgraph extraction, serialization)
INPUTS: parser_benchmark.csv (Task 1), synthetic graph fixtures (1k, 10k, 50k nodes)
OUTPUTS: reports/graph_lib_benchmark.csv [columns: library, nodes, edges, bfs_ms, subgraph_ms, serialize_ms, memory_mb], reports/graph_choice.md
SPECS/CASES:
  • Edge case: disconnected graphs, self-loops, multigraph edges must not break BFS
  • Perf constraint: BFS on 50k-node graph < 2s; subgraph extraction < 500ms
  • Validation: BFS result sets must be identical between libs for same fixture (set equality assert)
COPILOT PROMPT: "In VS Code, write `tools/graph_benchmark.py` using Python 3.12, poetry, networkx, igraph, tracemalloc. Generate synthetic directed graphs at sizes [1000, 10000, 50000] nodes with random edges (density=0.001). For each lib: run BFS from 5 random source nodes (depth=3), extract induced subgraph of BFS result, serialize to JSON. Time each op with timeit(number=5). Use Pydantic BaseModel for GraphBenchResult. Handle self-loops and isolated nodes. Print rich.table. Save to reports/graph_lib_benchmark.csv. Write pytest tests asserting BFS node sets match between networkx and igraph on same seed. Include memory profiling via tracemalloc. Save as tools/graph_benchmark.py."
```

---

```
TASK 3/30: Code Embedding Model Retrieval Recall Benchmark
PHASE: Tools
GOAL: Ranked comparison of CodeBERT vs. GraphCodeBERT vs. UniXCoder on structural retrieval recall for PR-diff queries
INPUTS: 10 real PRs from fastapi/diffusers (manually labeled ground-truth impacted functions), HuggingFace model hub access
OUTPUTS: reports/embedding_benchmark.csv [model, pr_id, recall@5, recall@10, mrr, latency_ms], reports/embedding_choice.md
SPECS/CASES:
  • Edge case: functions with no docstrings or <5 tokens must still embed without NaN/zero vectors
  • Perf constraint: encode 500 functions in <60s on CPU (Colab T4 GPU < 10s)
  • Validation: recall@10 ≥ 0.60 for chosen model on labeled PR set
COPILOT PROMPT: "In VS Code, write `tools/embedding_benchmark.py` using Python 3.12, transformers, torch, faiss-cpu, pydantic. Load microsoft/codebert-base, microsoft/graphcodebert-base, microsoft/unixcoder-base. For each of 10 sample PRs (stored as JSON in data/sample_prs/), encode all repo functions into FAISS index, query with diff hunk embedding, retrieve top-10, compare to ground-truth impacted function list (data/ground_truth.json). Compute recall@5, recall@10, MRR. Handle empty/short functions with padding. Time encoding with timeit. Use EmbeddingResult dataclass. Save CSV to reports/embedding_benchmark.csv. Write pytest asserting recall@10 > 0.4 for at least one model. Save as tools/embedding_benchmark.py."
```

---

```
TASK 4/30: LLM Proxy & Multi-Model Routing Benchmark
PHASE: Tools
GOAL: Evaluated comparison of LiteLLM vs. direct OpenAI SDK for multi-model PR review routing (GPT-4o, CodeLlama, Mistral)
INPUTS: 3 sample diff prompts (data/sample_prompts.json), .env with API keys
OUTPUTS: reports/llm_proxy_benchmark.csv [proxy, model, latency_ms, tokens_used, cost_usd, review_quality_score], reports/llm_choice.md
SPECS/CASES:
  • Edge case: API timeout (>30s) must trigger retry with exponential backoff (max 3 retries)
  • Perf constraint: first-token latency < 5s for GPT-4o
  • Validation: response must be valid JSON with keys {issues: [], severity: str, suggestions: []}
COPILOT PROMPT: "In VS Code, write `tools/llm_proxy_benchmark.py` using Python 3.12, litellm, openai, tenacity, pydantic. Define ReviewResponse(BaseModel) with fields issues: list[str], severity: Literal['low','medium','high'], suggestions: list[str]. For each of 3 sample prompts in data/sample_prompts.json, call GPT-4o and codellama/codellama-34b via LiteLLM with retry(stop=stop_after_attempt(3), wait=wait_exponential()). Record latency, token usage, cost estimate. Force JSON output via response_format. Handle timeout with asyncio timeout(30). Compare vs direct openai SDK. Print rich.table. Save CSV. Write pytest checking response parses to ReviewResponse. Save as tools/llm_proxy_benchmark.py."
```

---

```
TASK 5/30: Dataset Acquisition & Ground-Truth Labeling Pipeline
PHASE: Tools
GOAL: Curated dataset of 50 PRs (10 fastapi, 10 diffusers, 10 django, 20 synthetic bug-injected) with ground-truth impacted function labels
INPUTS: GitHub API token, PyDriller, bugsinpy or manual injection scripts
OUTPUTS: data/pr_corpus/ [pr_id/: diff.patch, repo_snapshot/, impacted_functions.json, metadata.json], data/corpus_stats.csv
SPECS/CASES:
  • Edge case: PRs with >500 changed lines must be flagged as "large_pr" and capped at 200 for pilot
  • Perf constraint: full dataset download + labeling < 20 min
  • Validation: each PR must have ≥ 1 ground-truth impacted function; schema validated via pydantic
COPILOT PROMPT: "In VS Code, write `tools/dataset_builder.py` using Python 3.12, PyGithub, gitpython, pydriller, pydantic. Define PRRecord(BaseModel) with pr_id, repo, diff_patch: str, impacted_functions: list[str], pr_size: Literal['small','medium','large'], metadata: dict. Fetch 10 PRs each from fastapi/fastapi and huggingface/diffusers using GitHub API (filter: merged, Python-only, 10-200 changed lines). Clone repo at base SHA, compute impacted functions via call-site grep + AST scan. For synthetic PRs, inject bugs via ast.NodeTransformer (swap operators, remove null checks). Save each PR as data/pr_corpus/PR_ID/. Validate all with pydantic. Print corpus stats. Write pytest checking 50 records exist and schema is valid. Save as tools/dataset_builder.py."
```

---

## PHASE 2: CORE ENGINE (Tasks 6–20)

---

```
TASK 6/30: Repository Cloner & Snapshot Manager
PHASE: Graph
GOAL: Module that clones any GitHub repo at a specific commit SHA and manages local snapshots with dedup caching
INPUTS: repo_url: str, commit_sha: str, cache_dir: Path
OUTPUTS: src/repo_manager.py, RepoSnapshot dataclass, tests/test_repo_manager.py
SPECS/CASES:
  • Edge case: re-clone of same SHA must return cached path (content-addressed by SHA)
  • Perf constraint: clone + checkout < 2 min for repos ≤ 50MB
  • Validation: snapshot path must exist, contain .git/, and git rev-parse HEAD must equal requested SHA
COPILOT PROMPT: "In VS Code, write `src/repo_manager.py` using Python 3.12, gitpython, pydantic, pathlib. Define RepoSnapshot(BaseModel) with repo_url, commit_sha, local_path: Path, cloned_at: datetime, size_mb: float. Implement clone_at_sha(repo_url, sha, cache_dir) that checks cache_dir/sha[:8]/ exists first (return cached), else git.Repo.clone_from() then repo.git.checkout(sha). Handle GitCommandError, InvalidGitRepositoryError with custom RepoError. Add get_file_list() returning all .py paths. Write teardown fixtures for pytest. Test: assert snapshot.local_path exists, HEAD SHA matches, cached call returns same path. Use ruff + typing throughout. Save as src/repo_manager.py."
```

---

```
TASK 7/30: PR Diff Parser & Hunk Extractor
PHASE: Graph
GOAL: Module that parses unified diff patches into structured DiffHunk objects with file, line ranges, and change type
INPUTS: diff.patch string or file path, PRRecord from Task 5
OUTPUTS: src/diff_parser.py, DiffHunk dataclass [file_path, added_lines: list[int], removed_lines: list[int], change_type], tests/test_diff_parser.py
SPECS/CASES:
  • Edge case: binary file diffs, renamed files (--- a/old.py → +++ b/new.py), empty diffs must not crash
  • Perf constraint: parse 500-hunk diff in < 1s
  • Validation: sum of added_lines counts must equal git diff --stat added line count
COPILOT PROMPT: "In VS Code, write `src/diff_parser.py` using Python 3.12, unidiff, pydantic, pathlib. Define DiffHunk(BaseModel) with file_path: Path, added_lines: list[int], removed_lines: list[int], change_type: Literal['modify','add','delete','rename'], old_path: Optional[Path]. Implement parse_diff(patch: str) -> list[DiffHunk] using unidiff.PatchSet. Handle binary files (skip with log), renames (capture both paths), empty patches (return []). Implement get_modified_lines(diff_hunks) -> dict[Path, set[int]] for anchor extraction. Test with 5 fixtures: normal diff, rename, binary, empty, large (500 hunks). Assert added_lines non-empty for modify type. Save as src/diff_parser.py."
```

---

```
TASK 8/30: Multi-Language Repo File Indexer
PHASE: Graph
GOAL: Module that indexes all parseable source files in a repo snapshot, extracting file metadata and filtering non-Python files
INPUTS: RepoSnapshot (Task 6), configurable include_extensions list
OUTPUTS: src/file_indexer.py, FileIndex dataclass [files: dict[Path, FileMetadata]], FileMetadata [path, size_bytes, loc, encoding], tests/test_file_indexer.py
SPECS/CASES:
  • Edge case: files with encoding errors (latin-1, binary) must be skipped with warning, not crash
  • Perf constraint: index 1000-file repo in < 5s
  • Validation: FileIndex.files must contain only files matching include_extensions; LOC counts verified on 3 known files
COPILOT PROMPT: "In VS Code, write `src/file_indexer.py` using Python 3.12, pathlib, chardet, pydantic, tqdm. Define FileMetadata(BaseModel) with path: Path, size_bytes: int, loc: int, encoding: str, is_parseable: bool. Define FileIndex with files: dict[str, FileMetadata] and method get_python_files(). Implement build_index(snapshot_path: Path, extensions=['.py']) scanning recursively, detecting encoding via chardet, counting LOC (non-blank lines), skipping files >1MB or with decode errors (log warning). Use ThreadPoolExecutor for parallel scan. Test: assert all returned files end in .py, loc > 0 for non-empty files, encoding is not None. Include tqdm progress bar. Save as src/file_indexer.py."
```

---

```
TASK 9/30: AST Function Extractor (Tree-sitter Core)
PHASE: Graph
GOAL: Module that uses Tree-sitter to extract all function definitions from a Python file with precise line spans and fully-qualified names
INPUTS: FileIndex (Task 8), tree_sitter Python grammar
OUTPUTS: src/ast_extractor.py, FunctionNode dataclass [fqn: str, file_path, start_line, end_line, source_code, params: list[str]], tests/test_ast_extractor.py
SPECS/CASES:
  • Edge case: nested functions, lambda expressions, class methods must all be captured with correct FQN (Class.method)
  • Perf constraint: extract all functions from 10k LOC file in < 2s
  • Validation: FQN uniqueness per file; start_line < end_line for all; cross-check count with ast.parse on same file
COPILOT PROMPT: "In VS Code, write `src/ast_extractor.py` using Python 3.12, tree_sitter, tree_sitter_languages, pydantic. Define FunctionNode(BaseModel) with fqn: str, file_path: Path, start_line: int, end_line: int, source_code: str, params: list[str], is_method: bool, class_name: Optional[str]. Implement extract_functions(file_path: Path) -> list[FunctionNode] using tree-sitter Python grammar. Walk AST for function_definition and class method nodes, build FQN as module.Class.method using parent stack. Handle nested functions (prefix with parent FQN). Extract params from parameters node. Test with fixtures: top-level func, class method, nested func, lambda (skip). Assert FQN uniqueness, start<end, source non-empty. Save as src/ast_extractor.py."
```

---

```
TASK 10/30: Call Edge Extractor (Intra-Repo Resolution)
PHASE: Graph
GOAL: Module that extracts all function call edges from a file and resolves callee FQNs to intra-repo functions
INPUTS: FunctionNode list (Task 9), FileIndex (Task 8), import resolution logic
OUTPUTS: src/call_extractor.py, CallEdge dataclass [caller_fqn, callee_fqn, call_site_line, is_resolved: bool], tests/test_call_extractor.py
SPECS/CASES:
  • Edge case: method calls on self (self.foo()), chained calls (a.b.c()), dynamic calls (getattr) — resolve where possible, mark unresolved
  • Perf constraint: process 500-function repo in < 10s
  • Validation: ≥ 80% call resolution rate on fastapi repo (verified against manual sample of 20)
COPILOT PROMPT: "In VS Code, write `src/call_extractor.py` using Python 3.12, tree_sitter, tree_sitter_languages, pydantic. Define CallEdge(BaseModel) with caller_fqn: str, callee_fqn: str, call_site_line: int, is_resolved: bool, resolution_method: Literal['direct','import','self','unresolved']. Implement extract_calls(func_node: FunctionNode, all_functions: dict[str, FunctionNode], import_map: dict) -> list[CallEdge]. Use tree-sitter call_expression nodes. Resolve: (1) direct name match in same file, (2) import alias lookup, (3) self.method lookup in same class. Mark dynamic/getattr calls as unresolved. Test: assert caller_fqn in known functions, resolved edges > 0 for test file with explicit calls. Save as src/call_extractor.py."
```

---

```
TASK 11/30: Static Call Graph Builder & Serializer
PHASE: Graph
GOAL: Full-repo call graph construction using Tasks 9–10, stored as NetworkX DiGraph with node/edge attributes and serialized to GraphML + JSON
INPUTS: RepoSnapshot (Task 6), FileIndex (Task 8), FunctionNode list (Task 9), CallEdge list (Task 10)
OUTPUTS: src/call_graph_builder.py, CallGraph wrapper class, artifacts/call_graph.graphml, artifacts/call_graph.json, tests/test_call_graph_builder.py
SPECS/CASES:
  • Edge case: circular call chains (A→B→A) must be stored without infinite loops; self-loops allowed
  • Perf constraint: build full graph for 10k-function repo in < 3 min
  • Validation: |V| matches total extracted FunctionNodes; |E| ≥ 90% of resolved CallEdges; GraphML round-trips correctly
COPILOT PROMPT: "In VS Code, write `src/call_graph_builder.py` using Python 3.12, networkx, pydantic, json, tqdm. Define CallGraph class wrapping nx.DiGraph with methods: add_function(FunctionNode), add_call(CallEdge), get_callers(fqn, depth), get_callees(fqn, depth), save_graphml(path), save_json(path), load_json(path). Node attrs: fqn, file_path, start_line, end_line. Edge attrs: call_site_line, is_resolved. Implement build_call_graph(snapshot: RepoSnapshot) -> CallGraph orchestrating Tasks 9-10 across all files with tqdm. Handle circular edges naturally (DiGraph supports cycles). Test: build on small fixture repo, assert node count matches function count, nx.is_directed(G), graphml round-trip preserves node attrs. Save as src/call_graph_builder.py."
```

---

```
TASK 12/30: Line-to-Function Anchor Mapper
PHASE: Traversal
GOAL: Module that maps modified line numbers from a diff to their containing FunctionNodes, producing the Anchor Set A
INPUTS: DiffHunk list (Task 7), CallGraph (Task 11), FunctionNode index by file+line
OUTPUTS: src/anchor_mapper.py, AnchorSet dataclass [anchors: list[FunctionNode], pr_id, unmapped_lines: list[int]], tests/test_anchor_mapper.py
SPECS/CASES:
  • Edge case: line falls between functions (module-level code) → create synthetic MODULE_LEVEL node
  • Edge case: deleted-only hunks (removed_lines only) → still produce anchor from base snapshot
  • Validation: for 10 labeled PRs, anchor_recall = |A ∩ ground_truth_anchors| / |ground_truth_anchors| ≥ 0.95
COPILOT PROMPT: "In VS Code, write `src/anchor_mapper.py` using Python 3.12, pydantic, intervaltree. Define AnchorSet(BaseModel) with anchors: list[FunctionNode], pr_id: str, unmapped_lines: list[tuple[Path,int]], coverage_ratio: float. Implement build_anchor_set(diff_hunks: list[DiffHunk], call_graph: CallGraph) -> AnchorSet. Use intervaltree.IntervalTree per file (intervals = [start_line, end_line] per FunctionNode) for O(log n) line lookup. For each modified line, query tree; if no hit, create MODULE_LEVEL FunctionNode with fqn=file.module_level. Handle added/removed lines separately (use head snapshot for added, base for removed). Compute coverage_ratio. Test with 5 labeled PRs, assert coverage_ratio ≥ 0.90. Save as src/anchor_mapper.py."
```

---

```
TASK 13/30: Import Resolution & Module Boundary Mapper
PHASE: Graph
GOAL: Module that resolves Python import statements to intra-repo file paths, enabling cross-file call edge resolution
INPUTS: FileIndex (Task 8), RepoSnapshot path, AST import nodes
OUTPUTS: src/import_resolver.py, ImportMap dataclass [alias_to_fqn: dict, file_to_module: dict], tests/test_import_resolver.py
SPECS/CASES:
  • Edge case: relative imports (from . import foo), __init__.py re-exports, conditional imports (try/except ImportError) must all be handled
  • Perf constraint: resolve imports for 1000-file repo in < 30s
  • Validation: resolution rate ≥ 85% for standard library-free intra-repo imports in fastapi
COPILOT PROMPT: "In VS Code, write `src/import_resolver.py` using Python 3.12, tree_sitter, tree_sitter_languages, pathlib, pydantic. Define ImportMap(BaseModel) with file_to_module: dict[str,str] (path → dotted module), alias_to_fqn: dict[str, dict[str,str]] (per-file alias → resolved FQN). Implement build_import_map(snapshot_path: Path, file_index: FileIndex) -> ImportMap. Convert file paths to dotted module names relative to repo root. Parse import_statement and import_from_statement nodes. Resolve relative imports using parent package. Handle __init__.py by mapping package imports to __init__ contents. Skip stdlib/third-party (check against sys.stdlib_module_names). Test: assert 'fastapi.routing' resolves, relative import resolves to correct module. Save as src/import_resolver.py."
```

---

```
TASK 14/30: Caller/Callee BFS Traversal Engine
PHASE: Traversal
GOAL: Core BFS traversal that computes Callers(a,k) and Callees(a,m) sets from the call graph with configurable depth bounds
INPUTS: CallGraph (Task 11), AnchorSet (Task 12), params k: int, m: int, max_nodes: int = 150
OUTPUTS: src/bfs_traversal.py, TraversalResult dataclass [callers: set[str], callees: set[str], depth_map: dict[str, int], truncated: bool], tests/test_bfs_traversal.py
SPECS/CASES:
  • Edge case: anchor node is isolated (no edges) → return only anchor in V_impact
  • Edge case: circular call chains must terminate at depth bound, not loop infinitely
  • Perf constraint: BFS on 50k-node graph with k=3, m=3 in < 500ms
COPILOT PROMPT: "In VS Code, write `src/bfs_traversal.py` using Python 3.12, networkx, collections.deque, pydantic. Define TraversalResult(BaseModel) with callers: set[str], callees: set[str], depth_map: dict[str,int], truncated: bool, total_nodes: int. Implement bfs_callers(graph: nx.DiGraph, anchor: str, k: int, max_nodes: int) -> set[str] traversing reversed graph (predecessors) up to depth k using deque-based BFS with visited set. Implement bfs_callees similarly on forward graph. Implement get_impact_set(graph, anchor_set, k, m, max_nodes) -> TraversalResult aggregating across all anchors. Set truncated=True if max_nodes hit. Guarantee termination via visited set. Test: circular graph A→B→A, isolated node, star graph. Assert no duplicates in result, depth_map values ≤ k or m. Save as src/bfs_traversal.py."
```

---

```
TASK 15/30: Impact Subgraph Constructor
PHASE: Traversal
GOAL: Module that extracts the induced subgraph G_sub from V_impact, preserving all edges between impact nodes
INPUTS: TraversalResult (Task 14), CallGraph (Task 11), FunctionNode registry
OUTPUTS: src/subgraph_constructor.py, ImpactSubgraph dataclass [graph: nx.DiGraph, anchor_nodes: list[str], caller_nodes: list[str], callee_nodes: list[str], stats: SubgraphStats], tests/test_subgraph_constructor.py
SPECS/CASES:
  • Edge case: V_impact with only 1 node → single-node subgraph with no edges (valid)
  • Edge case: edges between two callee nodes that are both in V_impact must be preserved
  • Validation: G_sub must be a valid induced subgraph (all edges have both endpoints in V_impact)
COPILOT PROMPT: "In VS Code, write `src/subgraph_constructor.py` using Python 3.12, networkx, pydantic. Define SubgraphStats(BaseModel) with n_nodes, n_edges, n_anchors, n_callers, n_callees, density: float, is_connected: bool. Define ImpactSubgraph(BaseModel with arbitrary_types_allowed) with graph: nx.DiGraph, anchor_nodes, caller_nodes, callee_nodes, stats: SubgraphStats. Implement build_impact_subgraph(call_graph: CallGraph, traversal_result: TraversalResult, anchor_set: AnchorSet) -> ImpactSubgraph using nx.induced_subgraph(). Enrich node attrs with role: Literal['anchor','caller','callee','both']. Compute stats including nx.density and nx.is_weakly_connected. Test: assert all subgraph edges have both endpoints in V_impact, anchor nodes all present, stats.n_nodes <= 150. Save as src/subgraph_constructor.py."
```

---

```
TASK 16/30: Token Budget Manager & Subgraph Pruner
PHASE: Traversal
GOAL: Module that enforces LLM token budget by pruning the impact subgraph using priority-scored BFS frontier halting
INPUTS: ImpactSubgraph (Task 15), token_budget: int = 32000, tokenizer (tiktoken)
OUTPUTS: src/token_budget.py, PrunedSubgraph dataclass [retained_nodes: list[str], pruned_nodes: list[str], estimated_tokens: int, pruning_ratio: float], tests/test_token_budget.py
SPECS/CASES:
  • Edge case: single function exceeding token budget → include truncated source with [TRUNCATED] marker
  • Edge case: anchor nodes must never be pruned (always retained regardless of budget)
  • Validation: estimated_tokens ≤ token_budget after pruning; anchor nodes always in retained_nodes
COPILOT PROMPT: "In VS Code, write `src/token_budget.py` using Python 3.12, tiktoken, pydantic, heapq. Define PrunedSubgraph(BaseModel) with retained_nodes: list[str], pruned_nodes: list[str], estimated_tokens: int, pruning_ratio: float. Implement prune_to_budget(subgraph: ImpactSubgraph, node_registry: dict[str, FunctionNode], budget: int, tokenizer_model='cl100k_base') -> PrunedSubgraph. Use tiktoken.encoding_for_model() to count tokens per function source. Priority queue: anchors=0 (highest), callers by depth (lower=higher priority), callees by depth. Greedily add nodes until budget hit. Truncate large functions at 200 tokens with [TRUNCATED] suffix. Test: assert anchors always retained, total tokens ≤ budget, pruning_ratio = pruned/(retained+pruned). Save as src/token_budget.py."
```

---

```
TASK 17/30: Subgraph Linearizer (BFS-Order Prompt Serializer)
PHASE: Traversal
GOAL: Module that serializes the pruned impact subgraph into the structured prompt template in BFS traversal order
INPUTS: PrunedSubgraph (Task 16), FunctionNode registry, DiffHunk list (Task 7), ImpactSubgraph roles
OUTPUTS: src/linearizer.py, LinearizedContext dataclass [prompt_text: str, sections: dict, token_count: int, node_order: list[str]], tests/test_linearizer.py
SPECS/CASES:
  • Edge case: function with no source code (external/unresolved) → placeholder [EXTERNAL FUNCTION - SOURCE UNAVAILABLE]
  • Edge case: same function appearing as both caller and callee → place in [ANCHOR] section, note dual role
  • Validation: output must match prompt template schema; BFS order verified by depth_map
COPILOT PROMPT: "In VS Code, write `src/linearizer.py` using Python 3.12, pydantic, tiktoken, textwrap. Define LinearizedContext(BaseModel) with prompt_text: str, sections: dict[str,list[str]], token_count: int, node_order: list[str]. Implement linearize_subgraph(pruned: PrunedSubgraph, node_registry, diff_hunks, depth_map) -> LinearizedContext. Build prompt as: [DIFF SUMMARY] → [MODIFIED FUNCTIONS] (anchors with +/- line annotations) → [CALLERS depth=1..k] (each with file, signature, body) → [CALLEES depth=1..m] (same). Sort within each section by BFS depth then alphabetically. Handle missing source with placeholder. Use textwrap.indent for code blocks. Count final tokens via tiktoken. Test: assert all anchor FQNs in [MODIFIED] section, BFS order consistent with depth_map, token_count ≤ 32000. Save as src/linearizer.py."
```

---

```
TASK 18/30: Structured Review Prompt Builder
PHASE: LLM
GOAL: Module that wraps the linearized context with system/user prompt scaffolding enforcing structured JSON review output
INPUTS: LinearizedContext (Task 17), PR metadata (title, description, author), ReviewConfig dataclass
OUTPUTS: src/prompt_builder.py, ReviewPrompt dataclass [system_prompt, user_prompt, expected_schema], data/prompt_templates/review_template.jinja2, tests/test_prompt_builder.py
SPECS/CASES:
  • Edge case: PR description is empty/None → infer intent from diff summary only
  • Edge case: prompt exceeds model max context (128k) → raise ContextOverflowError with token count
  • Validation: rendered prompt must parse to ReviewPrompt; Jinja2 template renders without UndefinedError
COPILOT PROMPT: "In VS Code, write `src/prompt_builder.py` using Python 3.12, jinja2, pydantic, tiktoken. Define ReviewConfig(BaseModel) with model: str, max_context_tokens: int, depth_k: int, depth_m: int, focus: list[Literal['security','performance','correctness','style']]. Define ReviewPrompt(BaseModel) with system_prompt, user_prompt, expected_schema: dict, total_tokens: int. Create Jinja2 template at data/prompt_templates/review_template.jinja2 with blocks for: task description, structural context, output schema (JSON with issues[{fqn,line,severity,description,type}], impact_summary, cross_file_risks). Implement build_prompt(context: LinearizedContext, pr_meta: dict, config: ReviewConfig) -> ReviewPrompt. Raise ContextOverflowError if tokens > max_context_tokens. Test: render with empty description, assert JSON schema present, token count accurate. Save as src/prompt_builder.py."
```

---

```
TASK 19/30: LLM Review Caller & Response Parser
PHASE: LLM
GOAL: Async module that sends review prompts to LLM via LiteLLM and parses structured JSON responses into ReviewOutput objects
INPUTS: ReviewPrompt (Task 18), LiteLLM config, .env API keys
OUTPUTS: src/llm_caller.py, ReviewOutput dataclass [issues: list[ReviewIssue], impact_summary, cross_file_risks, model_used, latency_ms, tokens_used], tests/test_llm_caller.py
SPECS/CASES:
  • Edge case: malformed JSON response → retry with explicit "respond ONLY with JSON" reinforcement prompt (max 2 retries)
  • Edge case: rate limit (429) → exponential backoff via tenacity
  • Validation: ReviewOutput.issues must all have severity in {low,medium,high,critical}; fqn must match a known node
COPILOT PROMPT: "In VS Code, write `src/llm_caller.py` using Python 3.12, litellm, tenacity, asyncio, pydantic, json. Define ReviewIssue(BaseModel) with fqn: str, line: Optional[int], severity: Literal['low','medium','high','critical'], description: str, issue_type: Literal['security','logic','performance','style','cross_file']. Define ReviewOutput(BaseModel) with issues, impact_summary: str, cross_file_risks: list[str], model_used, latency_ms, tokens_used. Implement async call_llm_reviewer(prompt: ReviewPrompt, config) -> ReviewOutput with @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1,max=10)). Parse JSON with json.loads, fallback regex extraction. Validate severities. Test with mocked litellm response. Save as src/llm_caller.py."
```

---

```
TASK 20/30: Graph Diff Versioning (Incremental Update for Successive PRs)
PHASE: Graph
GOAL: Module that updates the call graph incrementally when a new PR modifies only a subset of files, avoiding full rebuild
INPUTS: CallGraph (Task 11), new DiffHunk list (Task 7), updated file contents
OUTPUTS: src/graph_updater.py, GraphDelta dataclass [added_nodes, removed_nodes, added_edges, removed_edges, unchanged_nodes], tests/test_graph_updater.py
SPECS/CASES:
  • Edge case: file rename in PR → remove all edges for old path, re-extract for new path
  • Edge case: function deletion → remove node and all incident edges from graph
  • Perf constraint: incremental update < 10% of full rebuild time for 1-file change
COPILOT PROMPT: "In VS Code, write `src/graph_updater.py` using Python 3.12, networkx, pydantic. Define GraphDelta(BaseModel) with added_nodes: list[str], removed_nodes: list[str], added_edges: list[tuple], removed_edges: list[tuple], unchanged_nodes: int, update_time_ms: float. Implement incremental_update(call_graph: CallGraph, diff_hunks: list[DiffHunk], snapshot: RepoSnapshot) -> tuple[CallGraph, GraphDelta]. For each modified file: (1) remove all nodes/edges associated with that file's functions from graph, (2) re-extract functions + calls via Tasks 9-10, (3) add new nodes/edges, (4) log delta. Handle renames by tracking both old_path and new_path from DiffHunk. Benchmark vs full rebuild with timeit. Test: modify 1 file, assert only that file's nodes updated, delta.unchanged_nodes > 0. Save as src/graph_updater.py."
```

---

## PHASE 3: BASELINES + PIPELINE (Tasks 21–25)

---

```
TASK 21/30: FAISS Semantic RAG Baseline (Embedding Retrieval)
PHASE: Retrieval
GOAL: Baseline retrieval system using FAISS + chosen embedding model (Task 3 winner) that retrieves top-K semantically similar functions for a given diff
INPUTS: FunctionNode list (Task 9), chosen embedding model, DiffHunk (Task 7), FAISS index
OUTPUTS: src/baselines/semantic_rag.py, FAISSIndex serialized to artifacts/faiss_index.bin, SemanticRetrievalResult dataclass, tests/test_semantic_rag.py
SPECS/CASES:
  • Edge case: query function not in index → return top-K from full index (no crash)
  • Edge case: empty repo (0 functions) → return empty result with warning
  • Perf constraint: index 10k functions in < 2 min; query < 100ms
COPILOT PROMPT: "In VS Code, write `src/baselines/semantic_rag.py` using Python 3.12, faiss, transformers, torch, pydantic, numpy. Define SemanticRetrievalResult(BaseModel) with query_fqn: str, retrieved: list[tuple[str,float]] (fqn, similarity_score), top_k: int, query_tokens: int. Implement build_faiss_index(functions: list[FunctionNode], model_name: str, save_path: Path) -> faiss.Index: encode all function sources in batches of 32, L2-normalize, add to IndexFlatIP. Implement semantic_retrieve(diff_hunks, index, fqn_list, model, k=10) -> list[SemanticRetrievalResult]. Handle empty corpus with early return. Serialize index with faiss.write_index. Test: build index on 50 functions, retrieve top-5 for a known function, assert result fqns are strings. Save as src/baselines/semantic_rag.py."
```

---

```
TASK 22/30: Diff-Only GPT Baseline
PHASE: Retrieval
GOAL: Simplest baseline: sends raw unified diff (no graph context) to LLM and returns ReviewOutput for comparison
INPUTS: DiffHunk list (Task 7), PR metadata, LiteLLM config (Task 4)
OUTPUTS: src/baselines/diff_only_reviewer.py, DiffOnlyReviewOutput (extends ReviewOutput), tests/test_diff_only_reviewer.py
SPECS/CASES:
  • Edge case: diff > 8k tokens → truncate to first 8k with [TRUNCATED — N lines omitted] suffix
  • Edge case: PR with only whitespace changes → LLM should return issues=[] (validate no hallucinated issues)
  • Validation: output parses to ReviewOutput schema; latency logged for comparison
COPILOT PROMPT: "In VS Code, write `src/baselines/diff_only_reviewer.py` using Python 3.12, litellm, tiktoken, pydantic, tenacity. Implement diff_only_review(diff_hunks: list[DiffHunk], pr_meta: dict, config: ReviewConfig) -> ReviewOutput. Build prompt: system='You are a code reviewer', user=f'Review this diff:\n{diff_text}\nRespond ONLY with JSON: {schema}'. Truncate diff_text to 8000 tokens via tiktoken, append truncation notice. Use same ReviewOutput pydantic model as Task 19 for apples-to-apples comparison. Apply same retry logic (3 attempts, exponential backoff). Log latency and token usage. Test with whitespace-only diff fixture, assert issues is list (may be empty), schema valid. Save as src/baselines/diff_only_reviewer.py."
```

---

```
TASK 23/30: Function-Context Baseline (File-Scoped RAG)
PHASE: Retrieval
GOAL: Intermediate baseline that includes full source of modified files (not graph-traversed) as context, simulating typical IDE copilot behavior
INPUTS: DiffHunk list (Task 7), FileIndex (Task 8), FunctionNode list (Task 9), token budget
OUTPUTS: src/baselines/file_context_reviewer.py, FileContextResult dataclass [included_files, total_tokens, ReviewOutput], tests/test_file_context_reviewer.py
SPECS/CASES:
  • Edge case: modified file > token budget alone → include only modified functions from that file, not full file
  • Edge case: PR modifying 20+ files → include only files with most change density (sort by changed_lines/total_lines)
  • Validation: total_tokens ≤ budget; all modified files represented in context
COPILOT PROMPT: "In VS Code, write `src/baselines/file_context_reviewer.py` using Python 3.12, tiktoken, pydantic, litellm. Define FileContextResult(BaseModel) with included_files: list[Path], total_tokens: int, truncated_files: list[Path], review: ReviewOutput. Implement file_context_review(diff_hunks, file_index, function_nodes, pr_meta, config) -> FileContextResult. Collect all modified files from diff_hunks. Sort by change_density = changed_lines/file_loc. Greedily add full file source to context until budget hit; for oversized files, include only modified functions. Assemble prompt same way as Task 18 but with [FILE CONTEXT] section instead of graph sections. Call LLM via litellm. Test: 3-file PR fixture, assert all modified files present or noted as truncated, token count ≤ budget. Save as src/baselines/file_context_reviewer.py."
```

---

```
TASK 24/30: D-GRAG Full Review Pipeline (Orchestrator v1)
PHASE: LLM
GOAL: End-to-end orchestrator combining Tasks 6–19 into a single review_pr() function that takes a GitHub PR URL and returns a ReviewOutput
INPUTS: pr_url: str, config: ReviewConfig, cache_dir: Path, .env
OUTPUTS: src/pipeline.py, PipelineResult dataclass [pr_id, review: ReviewOutput, subgraph_stats, timing_breakdown: dict, context_tokens: int], tests/test_pipeline.py
SPECS/CASES:
  • Edge case: PR already reviewed (cached) → return cached PipelineResult from JSON without LLM call
  • Edge case: repo clone fails (private repo, network error) → raise PipelineError with actionable message
  • Perf constraint: full pipeline (excluding clone) < 5 min for 10k LOC repo
COPILOT PROMPT: "In VS Code, write `src/pipeline.py` using Python 3.12, asyncio, pydantic, json, time. Define PipelineResult(BaseModel) with pr_id, pr_url, review: ReviewOutput, subgraph_stats: SubgraphStats, timing_breakdown: dict[str,float], context_tokens: int, cache_hit: bool. Implement async review_pr(pr_url: str, config: ReviewConfig, cache_dir: Path) -> PipelineResult orchestrating: (1)clone repo at base+head SHAs, (2)build/load call graph with incremental update, (3)parse diff→anchors, (4)BFS traversal, (5)subgraph+pruning, (6)linearize+prompt build, (7)LLM call. Cache results to cache_dir/pr_id.json. Time each stage with time.perf_counter. Raise PipelineError(stage, message) on failures. Test with one real fastapi PR. Save as src/pipeline.py."
```

---

```
TASK 25/30: CLI Interface & GitHub Webhook Integration
PHASE: LLM
GOAL: Typer-based CLI and FastAPI webhook endpoint enabling D-GRAG to be invoked manually or automatically on PR events
INPUTS: pipeline.py (Task 24), GitHub webhook secret, ReviewConfig
OUTPUTS: src/cli.py, src/webhook.py, docker-compose.yml stub, tests/test_cli.py
SPECS/CASES:
  • Edge case: CLI called with invalid PR URL → validate URL format, print helpful error (not stack trace)
  • Edge case: webhook payload signature mismatch → return 403, log attempt
  • Validation: `python -m dgrag review --pr-url <url>` runs end-to-end; webhook POST returns 200 + review JSON
COPILOT PROMPT: "In VS Code, write `src/cli.py` using typer, rich, asyncio and `src/webhook.py` using fastapi, uvicorn, hmac, pydantic. CLI commands: `review` (--pr-url, --depth-k INT, --depth-m INT, --model STR, --output-json PATH), `benchmark` (runs eval suite). Use rich.progress for live stage updates. Print ReviewOutput as rich.table of issues. In webhook.py: POST /webhook/github validates X-Hub-Signature-256 via hmac.compare_digest, parses PR opened/synchronize events, calls review_pr() async, posts results back as PR comment via GitHub API (PyGithub). Write Dockerfile stub (FROM python:3.12-slim, COPY, RUN poetry install). Test CLI with --help and mock PR URL validation. Save as src/cli.py and src/webhook.py."
```

---

## PHASE 4: EVALUATION & POLISH (Tasks 26–30)

---

```
TASK 26/30: Evaluation Metrics Engine
PHASE: Eval
GOAL: Metrics module computing structural_recall, context_token_reduction, cross_file_detection_rate, hallucination_rate, and BLEU/ROUGE across all three systems (D-GRAG, semantic RAG, diff-only)
INPUTS: PipelineResult list (Task 24), SemanticRetrievalResult list (Task 21), ground-truth corpus (Task 5), ReviewOutput list
OUTPUTS: src/eval/metrics.py, results/metrics_table.csv [system, pr_id, structural_recall, token_reduction_%, cross_file_rate, hallucination_rate, bleu, rouge_l], tests/test_metrics.py
SPECS/CASES:
  • Edge case: PR with 0 cross-file impacts → cross_file_rate defined as N/A (not 0 or 1)
  • Edge case: hallucination_rate computed as % of issue.fqn not in known function registry
  • Validation: metrics are reproducible (same inputs → same outputs); structural_recall ∈ [0,1]
COPILOT PROMPT: "In VS Code, write `src/eval/metrics.py` using Python 3.12, sacrebleu, rouge_score, pydantic, pandas. Define EvalResult(BaseModel) with system, pr_id, structural_recall: float, token_reduction_pct: float, cross_file_detection_rate: Optional[float], hallucination_rate: float, bleu: float, rouge_l: float. Implement: compute_structural_recall(retrieved, ground_truth) = |R∩I|/|I|; compute_token_reduction(C_embed, C_graph); compute_hallucination_rate(issues, known_fqns) = fqns_not_in_registry/total_issues; compute_bleu/rouge using sacrebleu/rouge_score vs reference reviews. Run across all 50 PRs for all 3 systems. Save DataFrame to results/metrics_table.csv. Test: assert recall ∈ [0,1], hallucination_rate ∈ [0,1], CSV has 150 rows (50 PRs × 3 systems). Save as src/eval/metrics.py."
```

---

```
TASK 27/30: Ablation Study Runner (Depth & Parser Sensitivity)
PHASE: Eval
GOAL: Automated ablation runner that sweeps BFS depth parameters (k∈{1,2,3}, m∈{1,2,3}) and parser choices, logging all metrics to compare configurations
INPUTS: pipeline.py (Task 24), metrics.py (Task 26), 20-PR ablation subset from corpus (Task 5)
OUTPUTS: src/eval/ablation.py, results/ablation_results.csv [k, m, parser, structural_recall, token_reduction, hallucination_rate], results/ablation_heatmap.png, tests/test_ablation.py
SPECS/CASES:
  • Edge case: depth k=0, m=0 → only anchor nodes retrieved; must not crash, recall will be low
  • Perf constraint: full 9-configuration sweep (k×m ∈ {1,2,3}²) on 20 PRs < 2 hours on T4
  • Validation: results/ablation_results.csv has 9 rows; heatmap renders without matplotlib errors
COPILOT PROMPT: "In VS Code, write `src/eval/ablation.py` using Python 3.12, itertools, pandas, matplotlib, seaborn, asyncio. Implement run_ablation_sweep(pr_corpus_path, base_config: ReviewConfig, k_values=[1,2,3], m_values=[1,2,3]) -> pd.DataFrame. For each (k,m) combination: update config.depth_k/m, run pipeline on 20-PR subset, compute metrics via Task 26, append row to results. Use asyncio.gather for parallel runs (max 3 concurrent to respect API limits). Plot heatmap of structural_recall vs (k,m) using seaborn.heatmap, save to results/ablation_heatmap.png. Save CSV to results/ablation_results.csv. Test: assert DataFrame has 9 rows, all metric columns non-null, heatmap file exists. Save as src/eval/ablation.py."
```

---

```
TASK 28/30: Docker Containerization & Reproducible Environment
PHASE: Eval
GOAL: Production-ready Docker image with multi-stage build, health check, and docker-compose setup for D-GRAG service + optional Redis cache
INPUTS: All src/ modules, pyproject.toml, .env.example
OUTPUTS: Dockerfile, docker-compose.yml, .dockerignore, scripts/entrypoint.sh, docs/docker_setup.md, tests/test_docker_build.sh
SPECS/CASES:
  • Edge case: missing .env → container must start and print actionable error listing missing vars (not silent crash)
  • Edge case: tree-sitter language grammar compilation inside container (no internet) → bundle pre-compiled .so files
  • Validation: `docker build .` succeeds in < 5 min; `docker run dgrag --help` prints CLI help
COPILOT PROMPT: "In VS Code, write a multi-stage Dockerfile using python:3.12-slim. Stage 1 (builder): install poetry, copy pyproject.toml, RUN poetry install --no-dev. Stage 2 (runtime): copy --from=builder venv, copy src/, copy pre-built tree-sitter .so grammars. Add HEALTHCHECK CMD python -c 'import dgrag; print(ok)'. Write docker-compose.yml with services: dgrag (build ., env_file .env, volumes for cache), redis (redis:7-alpine for result caching). Write scripts/entrypoint.sh that validates required env vars (GITHUB_TOKEN, OPENAI_API_KEY) with clear error messages before exec. Write .dockerignore excluding .git, __pycache__, .env. Write docs/docker_setup.md with quickstart. Test: bash script running docker build and docker run --help, assert exit code 0. Save all files."
```

---

```
TASK 29/30: Full pytest Suite with 80%+ Coverage
PHASE: Eval
GOAL: Comprehensive pytest test suite covering all modules with fixtures, parametrize, mocks, and coverage report ≥ 80%
INPUTS: All src/ modules (Tasks 6–25), data/sample_prs/, conftest.py with shared fixtures
OUTPUTS: tests/conftest.py, tests/unit/ (one file per module), tests/integration/test_full_pipeline.py, .github/workflows/ci.yml, htmlcov/ coverage report
SPECS/CASES:
  • Edge case: all external API calls (LiteLLM, GitHub API) must be mocked via pytest-mock/responses in unit tests
  • Edge case: integration test uses a real small repo (< 500 LOC) to validate end-to-end flow without LLM call (stub reviewer)
  • Validation: pytest --cov=src --cov-report=html achieves ≥ 80% line coverage
COPILOT PROMPT: "In VS Code, write `tests/conftest.py` using pytest, pytest-mock, responses, factory_boy. Define fixtures: sample_repo_snapshot (clones tiny fixture repo), sample_diff_hunks (loads from data/fixtures/sample.patch), sample_call_graph (builds from fixture repo), mock_llm_response (returns hardcoded ReviewOutput JSON). Write unit tests for every src/ module: test_parser, test_call_graph, test_anchor_mapper, test_bfs_traversal, test_linearizer, test_prompt_builder, test_llm_caller (mocked), test_metrics. Write integration test orchestrating full pipeline with stubbed LLM. Write .github/workflows/ci.yml: checkout, setup-python 3.12, poetry install, ruff check, pytest --cov=src --cov-fail-under=80. Assert coverage gate. Save all under tests/. Run pytest locally to verify."
```

---

```
TASK 30/30: README, Demo Notebook & arXiv-Ready Results Export
PHASE: Eval
GOAL: Publication-quality README, interactive Jupyter demo notebook, and arXiv-ready results CSV + LaTeX table generator
INPUTS: All results/ CSVs (Tasks 26–27), pipeline.py (Task 24), docs/, sample PR for live demo
OUTPUTS: README.md (badges, architecture diagram, quickstart), notebooks/demo.ipynb (end-to-end walkthrough), results/arxiv_table.tex (LaTeX results table), scripts/generate_latex_table.py, docs/architecture.md
SPECS/CASES:
  • Edge case: demo notebook must run top-to-bottom without errors using only public repos and mocked LLM (no real API key needed for demo)
  • Edge case: LaTeX table must handle missing metric values (N/A) gracefully with \textemdash
  • Validation: README renders correctly on GitHub; notebook runs clean via `jupyter nbconvert --execute`; LaTeX compiles without errors
COPILOT PROMPT: "In VS Code, write README.md with: badges (CI, coverage, Python 3.12), system architecture ASCII diagram, D-GRAG vs baselines results table (from results/metrics_table.csv), quickstart (docker-compose up + CLI example), project structure tree. Write notebooks/demo.ipynb: cells for (1)install deps, (2)clone fixture repo, (3)run D-GRAG pipeline with mocked LLM, (4)visualize impact subgraph with networkx+matplotlib, (5)display ReviewOutput as DataFrame. Write scripts/generate_latex_table.py using pandas, jinja2: load metrics_table.csv, pivot by system, render to LaTeX booktabs table (save results/arxiv_table.tex). Notebook must run with MOCK_LLM=true env var. Test via nbconvert --execute. Save all files to their respective paths."
```

---

## Quick Reference Architecture Map

```
data/              ← PR corpus, fixtures, ground truth
src/
  repo_manager.py       ← Task 6
  diff_parser.py        ← Task 7
  file_indexer.py       ← Task 8
  ast_extractor.py      ← Task 9
  call_extractor.py     ← Task 10
  call_graph_builder.py ← Task 11
  anchor_mapper.py      ← Task 12
  import_resolver.py    ← Task 13
  bfs_traversal.py      ← Task 14
  subgraph_constructor.py ← Task 15
  token_budget.py       ← Task 16
  linearizer.py         ← Task 17
  prompt_builder.py     ← Task 18
  llm_caller.py         ← Task 19
  graph_updater.py      ← Task 20
  baselines/
    semantic_rag.py     ← Task 21
    diff_only_reviewer.py ← Task 22
    file_context_reviewer.py ← Task 23
  pipeline.py           ← Task 24
  cli.py / webhook.py   ← Task 25
  eval/
    metrics.py          ← Task 26
    ablation.py         ← Task 27
tools/             ← Tasks 1–5 benchmarks
results/           ← CSVs, plots, LaTeX
tests/             ← Task 29
Dockerfile         ← Task 28
notebooks/demo.ipynb ← Task 30
```

Each task's `COPILOT PROMPT` is paste-ready into Cursor's cmd+L or Copilot Chat — sequential execution guarantees that every module's imports resolve from prior tasks with zero rework.
