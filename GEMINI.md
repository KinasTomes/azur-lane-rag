# AzurLaneRAG Project Overview

A High-Performance Hybrid RAG (Retrieval-Augmented Generation) system for Azur Lane, combining SQL (Facts), Graph (Relationships), and Vector (Semantics) with a multi-tier agentic query engine.

## 🤖 3-Tier Agentic Architecture

The system operates on a **Research -> Strategy -> Execution** lifecycle:

1.  **Tier 1: Dispatcher (The Navigator)**
    *   **Model:** `@cf/zai-org/glm-4.7-flash` (Cloudflare).
    *   **Role:** Analyzes user intent, estimates complexity, and drafts a high-level execution plan.
    *   **Output:** Strict JSON containing intent, complexity, and tool selection.

2.  **Tier 2: Thinker (The Brain)**
    *   **Models:** `deepseek_v3.2` (685B), `minimax_m2.7` (230B), `qwq_32b`.
    *   **Role:** Converts the high-level plan into precise execution commands (SQL, Graph-SQL, Vector Search).
    *   **Context:** Uses a full 17-table schema and Graph metadata to ensure query precision.

3.  **Tier 3: Synthesizer (The Voice)**
    *   **Models:** `nemotron_super`, `mimo_v2_5_pro`, `qwen3_30b_fp8`.
    *   **Role:** Merges raw data findings into a professional, immersive, and accurate response.

## 🛠️ Key Components

-   **`azur-lane-router/`**: Cloudflare Worker acting as a multi-role AI Proxy Gateway.
-   **`src/utils/ai_gateway.py`**: Smart Router với provider abstraction (NVIDIA NIM, Xiaomi, CF) và automatic fallback.
-   **`src/core/thinker_executor.py`**: Local execution engine for SQL (SQLite), Graph (SQLite-based Triplestore), and Vector (ChromaDB).
-   **`src/core/main_orchestrator.py`**: The "Commander" that coordinates the 3-tier flow.

## 📊 Data Triad

-   **SQL (`azur_lane.db`)**: 17 tables containing hard stats, rarity, nations, and skills.
-   **Graph (`azur_lane_graph.db`)**: Nodes and Edges representing deep relationships and AI-generated Community Summaries.
-   **Vector (`chroma_db`)**: Semantic embeddings for 42,000+ voice lines and complex skill descriptions using `BAAI/bge-m3`.

## 🚀 Key Commands

### Run the RAG Query Engine
```powershell
$env:PYTHONPATH = "."; python src/core/main_orchestrator.py
```

### Deploy/Develop AI Router
```bash
cd azur-lane-router
npm run dev    # Local development
npm run deploy # Deploy to Cloudflare
```

## 📝 Development Conventions

-   **No Cypher**: Always use standard SQL for both relational and graph databases (Graph is SQLite-based).
-   **Strict Schema**: Always JOIN `ships` with `nations` and `rarities` for human-readable output.
-   **Model Selection**:
    *   Hard/Logic -> `deepseek_v4_pro` or `minimax_m2.7`.
    *   Reasoning/SQL -> `qwq_32b` or `mimo_v2_5_pro`.
    *   Synthesize/Voice -> `nemotron_super`.

## 🗺️ Project Roadmap

1.  [x] Stage 1: Local Vectorization & Graph Indexing.
2.  [x] Stage 2: 3-Tier Agentic Query Engine (Hybrid RAG).
3.  [ ] Stage 3: UI/CLI Integration (Textual TUI / Streamlit Web).
4.  [ ] Stage 4: Multi-turn Conversation & Memory Integration.
