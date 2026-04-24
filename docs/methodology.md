# D-GRAG Description

**Delta-Graph Retrieval-Augmented Generation (D-GRAG)** is a structurally grounded framework designed for automated code review. Unlike traditional Al systems that rely on textual or semantic similarity (embedding-based RAG), D-GRAG constructs **Dynamic Differential Subgraphs** tailored specifically to each Pull Request (PR). It prioritizes **architectural reachability** within a static call graph to provide Large Language Models (LLMs) with a targeted, dependency-aware reasoning window.

---

## Methodology

The D-GRAG framework follows a specific technical pipeline to generate reviews:

### 1. Graph Construction

* **Parsing:** The system uses **Tree-sitter** to parse repository files and extract function-level AST nodes.

* Static Call Graph: It builds a directed graph $G=(V,E)$ where $V$ represents functions and $E$ represents call dependencies.

### 2. Anchor Identification & Traversal

* **Anchor Set (A):** The system identifies functions directly modified in the PR.

* **Bounded Bidirectional Traversal:** It performs a Breadth-First Search (BFS) from these anchors to a specified depth: **k** (upstream/callers) and **m** (downstream/callees).

### 3. Subgraph Extraction & Linearization

* **Impact Subgraph:** A differential subgraph is created, containing only the anchor nodes and their structurally reachable neighbors.

* **Linearization:** To suit LLM processing, the graph is serialized using BFS order to preserve structural locality.

* **Prompt Construction:** The LLM receives a structured prompt containing the modified logic, upstream impact, and downstream propagation regions
Design a complete modular architecture for Delta-GRAG (D-GRAG).
