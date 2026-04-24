# Pseudocode for D-GRAG Core Algorithm

Below is a full Python-like pseudocode design that maps directly to your spec:

- Static call graph from AST
- Anchor set from PR diff
- Bidirectional bounded BFS
- Subgraph union across anchors
- Token-budget-aware pruning

---

## 1) Data Structures

```/dev/null/dgrag_types.py#L1-61
from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple, Optional

NodeId = str
Edge = Tuple[NodeId, NodeId]   # (caller, callee)

@dataclass
class FunctionNode:
    id: NodeId
    file_path: str
    qualname: str
    start_line: int
    end_line: int
    signature: str
    body_text: str

@dataclass
class CallGraph:
    nodes: Dict[NodeId, FunctionNode]
    succ: Dict[NodeId, Set[NodeId]]   # adjacency: caller -> callees
    pred: Dict[NodeId, Set[NodeId]]   # reverse adjacency: callee -> callers

@dataclass
class PRHunk:
    file_path: str
    start_line: int
    end_line: int

@dataclass
class TraversalConfig:
    k_up: int
    k_down: int
    max_nodes: int
    max_edges: int
    max_nodes_per_anchor: int
    token_budget: int
    reserve_tokens_for_prompt: int = 1500

@dataclass
class SubgraphResult:
    nodes: Set[NodeId]
    edges: Set[Edge]
    anchor_nodes: Set[NodeId]
    dist_up: Dict[NodeId, int]      # min dist from any anchor via upstream traversal
    dist_down: Dict[NodeId, int]    # min dist from any anchor via downstream traversal
    cutoff_events: List[str]
```

---

## 2) Build Static Call Graph from AST (Tree-sitter Pipeline)

```/dev/null/build_graph.py#L1-97
def build_static_call_graph(repo_files, parser_by_lang) -> CallGraph:
    """
    1) Parse files with Tree-sitter.
    2) Extract function definitions -> node table.
    3) Resolve call expressions to function symbols.
    4) Build directed call edges caller -> callee.
    """
    nodes = {}
    succ = defaultdict(set)
    pred = defaultdict(set)

    # symbol index: (file/module, name) -> candidate node ids
    symbol_index = defaultdict(list)

    # pass 1: collect function nodes
    for file_path in repo_files:
        lang = detect_language(file_path)
        parser = parser_by_lang[lang]
        tree = parser.parse_file(file_path)

        for fn_ast in extract_function_nodes(tree, lang):
            fn = FunctionNode(
                id=stable_node_id(file_path, fn_ast),
                file_path=file_path,
                qualname=qualified_name(fn_ast),
                start_line=fn_ast.start_line,
                end_line=fn_ast.end_line,
                signature=extract_signature(fn_ast),
                body_text=extract_body_text(fn_ast),
            )
            nodes[fn.id] = fn
            symbol_index[symbol_key(file_path, fn.qualname)].append(fn.id)

    # pass 2: collect call edges
    for node_id, fn in nodes.items():
        lang = detect_language(fn.file_path)
        parser = parser_by_lang[lang]
        fn_tree = parser.parse_span(fn.file_path, fn.start_line, fn.end_line)

        for call_site in extract_call_sites(fn_tree, lang):
            target_symbol = resolve_call_symbol(call_site, fn.file_path, lang)
            callee_candidates = symbol_index_lookup(symbol_index, target_symbol, fn.file_path)

            # conservative static analysis: add all plausible targets
            for callee_id in callee_candidates:
                if callee_id == node_id:
                    continue
                succ[node_id].add(callee_id)
                pred[callee_id].add(node_id)

    return CallGraph(nodes=nodes, succ=dict(succ), pred=dict(pred))
```

---

## 3) Anchor Set from PR Diff

```/dev/null/anchors_from_diff.py#L1-63
def extract_anchors_from_pr_diff(pr_hunks: List[PRHunk], graph: CallGraph) -> Set[NodeId]:
    """
    Map changed line ranges to enclosing function nodes.
    If multiple match, choose smallest enclosing function range.
    """
    anchors = set()

    # file -> sorted function intervals for fast overlap lookup
    by_file = build_interval_index(graph.nodes.values())

    for h in pr_hunks:
        candidates = interval_query(by_file[h.file_path], h.start_line, h.end_line)

        if not candidates:
            # fallback: nearest function by line distance in same file
            nearest = nearest_function(by_file[h.file_path], h.start_line, h.end_line)
            if nearest is not None:
                anchors.add(nearest.id)
            continue

        chosen = min(
            candidates,
            key=lambda fn: (fn.end_line - fn.start_line, abs(fn.start_line - h.start_line))
        )
        anchors.add(chosen.id)

    return anchors
```

---

## 4) Bidirectional BFS + Union + Token-Budget Bounding

```/dev/null/dgrag_core.py#L1-180
def run_dgrag_core(
    graph: CallGraph,
    anchors: Set[NodeId],
    cfg: TraversalConfig,
    tokenizer
) -> SubgraphResult:
    """
    G' = union over anchors a in A:
         (Upstream_k(a) union Downstream_m(a)) union {a}
    then token-budget trim while preserving anchors.
    """
    selected_nodes: Set[NodeId] = set(anchors)
    selected_edges: Set[Edge] = set()
    dist_up = {}
    dist_down = {}
    cutoff_events = []

    # global and per-anchor accounting
    per_anchor_nodes = defaultdict(int)

    # initialize distances for anchors
    for a in anchors:
        dist_up[a] = 0
        dist_down[a] = 0

    for a in anchors:
        # -------- Upstream BFS: traverse predecessors (callers) --------
        q_up = deque([(a, 0)])
        seen_up = set([(a, 0)])  # (node, depth) allows same node at better depth checks

        while q_up:
            node, d = q_up.popleft()
            if d >= cfg.k_up:
                continue

            for parent in graph.pred.get(node, set()):
                nd = d + 1
                state = (parent, nd)
                if state in seen_up:
                    continue
                seen_up.add(state)

                # Bound checks before adding
                if _hit_structural_limits(selected_nodes, selected_edges, cfg, per_anchor_nodes[a]):
                    cutoff_events.append(f"UP cutoff at anchor={a}, depth={nd}")
                    q_up.clear()
                    break

                # Add node/edge
                selected_nodes.add(parent)
                selected_edges.add((parent, node))
                per_anchor_nodes[a] += 1

                # Distance merge (min over anchors)
                prev = dist_up.get(parent, 10**9)
                dist_up[parent] = min(prev, nd)

                q_up.append((parent, nd))

        # -------- Downstream BFS: traverse successors (callees) --------
        q_down = deque([(a, 0)])
        seen_down = set([(a, 0)])

        while q_down:
            node, d = q_down.popleft()
            if d >= cfg.k_down:
                continue

            for child in graph.succ.get(node, set()):
                nd = d + 1
                state = (child, nd)
                if state in seen_down:
                    continue
                seen_down.add(state)

                if _hit_structural_limits(selected_nodes, selected_edges, cfg, per_anchor_nodes[a]):
                    cutoff_events.append(f"DOWN cutoff at anchor={a}, depth={nd}")
                    q_down.clear()
                    break

                selected_nodes.add(child)
                selected_edges.add((node, child))
                per_anchor_nodes[a] += 1

                prev = dist_down.get(child, 10**9)
                dist_down[child] = min(prev, nd)

                q_down.append((child, nd))

    # -------- Token-budget enforcement --------
    token_limit = max(0, cfg.token_budget - cfg.reserve_tokens_for_prompt)
    selected_nodes, selected_edges, trim_events = enforce_token_budget(
        graph=graph,
        nodes=selected_nodes,
        edges=selected_edges,
        anchors=anchors,
        dist_up=dist_up,
        dist_down=dist_down,
        token_limit=token_limit,
        tokenizer=tokenizer
    )
    cutoff_events.extend(trim_events)

    return SubgraphResult(
        nodes=selected_nodes,
        edges=selected_edges,
        anchor_nodes=anchors,
        dist_up=dist_up,
        dist_down=dist_down,
        cutoff_events=cutoff_events
    )


def _hit_structural_limits(nodes, edges, cfg, per_anchor_count) -> bool:
    if len(nodes) >= cfg.max_nodes:
        return True
    if len(edges) >= cfg.max_edges:
        return True
    if per_anchor_count >= cfg.max_nodes_per_anchor:
        return True
    return False


def estimate_node_tokens(fn: FunctionNode, tokenizer) -> int:
    # include signature + compact body/snippet
    text = fn.signature + "\n" + summarize_body(fn.body_text, max_lines=40)
    return len(tokenizer.encode(text))


def enforce_token_budget(graph, nodes, edges, anchors, dist_up, dist_down, token_limit, tokenizer):
    """
    Keep anchors always.
    Rank non-anchor nodes by priority:
      1) smaller graph distance to any anchor
      2) bidirectional relevance (present in both up/down maps)
      3) deterministic tie-break: file_path, start_line
    Drop lowest-priority nodes until total estimated tokens <= token_limit.
    """
    events = []
    node_tokens = {}
    for n in nodes:
        node_tokens[n] = estimate_node_tokens(graph.nodes[n], tokenizer)

    total = sum(node_tokens.values())
    if total <= token_limit:
        return nodes, edges, events

    def priority(n):
        du = dist_up.get(n, 10**6)
        dd = dist_down.get(n, 10**6)
        dmin = min(du, dd)
        bi = 0 if (n in dist_up and n in dist_down) else 1  # prefer bidirectional nodes
        fn = graph.nodes[n]
        return (dmin, bi, fn.file_path, fn.start_line)

    removable = [n for n in nodes if n not in anchors]
    removable.sort(key=priority, reverse=True)  # drop farthest/least relevant first

    kept = set(nodes)
    for n in removable:
        if total <= token_limit:
            break
        kept.remove(n)
        total -= node_tokens[n]
        events.append(f"TOKEN trim dropped node={n}")

    # rebuild induced edges
    kept_edges = set((u, v) for (u, v) in edges if u in kept and v in kept)
    return kept, kept_edges, events
```

---

## 5) Linearization (BFS-ordered for LLM Prompt)

```/dev/null/linearize_subgraph.py#L1-87
def linearize_subgraph_for_prompt(result: SubgraphResult, graph: CallGraph) -> str:
    """
    Section order:
      1) Anchors
      2) Upstream by distance
      3) Downstream by distance
    Deterministic ordering for reproducibility.
    """
    anchors = sorted(
        result.anchor_nodes,
        key=lambda n: (graph.nodes[n].file_path, graph.nodes[n].start_line, graph.nodes[n].qualname)
    )

    upstream_nodes = [n for n in result.nodes if n not in result.anchor_nodes and n in result.dist_up]
    downstream_nodes = [n for n in result.nodes if n not in result.anchor_nodes and n in result.dist_down]

    upstream_nodes.sort(
        key=lambda n: (result.dist_up.get(n, 10**6), graph.nodes[n].file_path, graph.nodes[n].start_line)
    )
    downstream_nodes.sort(
        key=lambda n: (result.dist_down.get(n, 10**6), graph.nodes[n].file_path, graph.nodes[n].start_line)
    )

    lines = []
    lines.append("## Anchors")
    for n in anchors:
        fn = graph.nodes[n]
        lines.append(f"- {fn.qualname} ({fn.file_path}:{fn.start_line}-{fn.end_line})")

    lines.append("\n## Upstream Impact (Callers)")
    for n in upstream_nodes:
        fn = graph.nodes[n]
        lines.append(f"- d_up={result.dist_up[n]} :: {fn.qualname} ({fn.file_path}:{fn.start_line})")

    lines.append("\n## Downstream Propagation (Callees)")
    for n in downstream_nodes:
        fn = graph.nodes[n]
        lines.append(f"- d_down={result.dist_down[n]} :: {fn.qualname} ({fn.file_path}:{fn.start_line})")

    if result.cutoff_events:
        lines.append("\n## Cutoff Notes")
        for ev in result.cutoff_events:
            lines.append(f"- {ev}")

    return "\n".join(lines)
```

---

## 6) Complexity Notes

Let:

- `N = |V|` functions
- `E = |E|` call edges
- `A = |anchors|`
- `b` = average branching factor in call graph
- `k = k_up`, `m = k_down`

### Graph construction
- AST parse + symbol extraction: approximately `O(total_source_size)`
- Call edge resolution:
  - with indexed symbol lookup, near `O(number_of_callsites * lookup_cost)`
  - practical total often approximated as `O(N + E)` after indexing

### Anchor extraction from PR hunks
- With interval index per file: `O(H log F_file + matches)` where `H` = number of hunks.

### Bidirectional traversal
- Per anchor worst-case bounded by:
  - upstream nodes visited: `O(sum_{i=0..k} b^i)`
  - downstream nodes visited: `O(sum_{i=0..m} b^i)`
- Global worst-case (without caps): `O(A * (b^k + b^m))`
- With hard caps (`max_nodes`, `max_edges`, per-anchor cap), traversal is effectively:
  - `O(min(max_edges, explored_edges))`, bounded in practice.

### Token-budget trimming
- Token estimation: `O(|V'| * avg_snippet_tokens)`
- Sorting removable nodes: `O(|V'| log |V'|)`
- Edge re-induction: `O(|E'|)`

So end-to-end retrieval stage is intentionally controlled by structural caps + token caps to prevent explosion.

---

## 7) Sample Traversal Visualization

Assume anchor `A`, `k_up=2`, `k_down=2`.

```/dev/null/sample_graph.txt#L1-16
Edges (caller -> callee):
U2 -> U1 -> A -> D1 -> D2
X  -> A
U1 -> H
A  -> J
J  -> K
```

From anchor `A`:

- Upstream depth 1: `{U1, X}`
- Upstream depth 2: `{U2}` (via U1)
- Downstream depth 1: `{D1, J}`
- Downstream depth 2: `{D2, K}`

Thus:

- `V' = {A, U1, X, U2, D1, J, D2, K}`
- `E' = {(U1,A), (X,A), (U2,U1), (A,D1), (A,J), (D1,D2), (J,K)}`

Layered view:

```/dev/null/sample_layers.txt#L1-12
Upstream L2:   U2
Upstream L1:   U1, X
Anchor L0:     A
Downstream L1: D1, J
Downstream L2: D2, K
```
