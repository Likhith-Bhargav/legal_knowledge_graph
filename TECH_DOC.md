# Legal Knowledge Graph (Legal-KG) — Deep Tech Documentation

This document provides a comprehensive technical breakdown of the `legal-kg` codebase. It is designed to onboard developers, scale the architecture, and document core system internals.

---

## 1. High-Level Architecture

The Legal-KG project is an **end-to-end RAG (Retrieval-Augmented Generation) and Knowledge Graph system**. It ingests unstructured/semi-structured legal Acts (like the Companies Act) and transforms them into an intensely structured property graph spanning Sections, Rules, and Amendments. 

This semantic network is then queried using Natural Language to Cypher translation powered by LLMs.

### Core Stack
* **Language:** Python 3.12+ (Type-hinted, modular)
* **API Layer:** FastAPI / Uvicorn
* **Database:** Neo4j (Bolt protocol)
* **Intelligence Layer:** Google Gemini, Local Ollama, OpenAI, Anthropic (via HTTP/REST APIs)
* **Ingestion Parsers:** `pdfplumber` (for raw text), Pydantic (Data modeling), Regex engines
* **Frontend:** Vanilla HTML/JS + CSS (Tailwind UI styles)

---

## 2. Codebase Hierarchy mapping (`/src/`)

```
src/
├── api/          # FastAPI Routes and Middleware
├── core/         # Settings (.env variables), Custom Exceptions, Logging
├── graph/        # Neo4j Driver Connection Pooling & Cypher Execution
├── ingestion/    # Text/PDF extraction and Graph Writers
├── intelligence/ # LLM orchestration, Prompt Injection, Cypher Translation
├── models/       # Pydantic Domain Entities (Act, Section, etc.)
├── services/     # Core application logical workflows
└── utils/        # Generic helpers
```

---

## 3. Deep Dive into System Modules

### 3.1 Domain Modeling (`src/models/domain.py`)
Relational structures are modeled primarily via **Pydantic Data Classes**.
Data inside the graph relies heavily on hierarchy mapping.
* `Act` ➔ Contains `Section` ➔ Contains `Subsection` ➔ Contains `Clause`.
* `Rule` ➔ Independent entity, but possesses a `RELATES_TO` relationship mapping back to a parent `Section`.
* `Amendment` ➔ Modifies specific sections, leaving audit trails of `MODIFIED_BY`.

### 3.2 System Ingestion Pipeline (`src/ingestion/`)
The heavy lifting of creating the graph happens here. It is abstracted via the Open-Closed pattern:

1. **`DocumentParser` Interface (`parsers.py`)**: Defines standard methods (`can_parse`, `parse`) that return a generic `ParsedDocument`.
2. **`JSONLegalParser`**: Used by `seed.py`. Fast loading of strictly structured JSON trees.
3. **`PDFLegalParser`**: The LLM-powered parser. It loads raw text pages, chunks them, and uses aggressive system prompting and exponential backoff to force an LLM (like Gemini) to output perfect JSON structures matching the domains.
4. **`RegexLegalParser`**: The ultra-fast, local heuristic fallback. Bypasses LLMs and identifies `Section` breakpoints directly using Legal-text semantic regex markers (like `"^\s*(\d+[a-zA-Z]?)\.\s+(.+)$"`).

**`GraphIngestionService` (`graph_ingestion.py`)**:
Takes any generic `ParsedDocument` and translates it into atomic Neo4j Cypher write transactions. It is designed to be fully idempotent (uses `MERGE` instead of `CREATE`) so running ingestion twice doesn't duplicate the graph.

### 3.3 Intelligence & Query Engine (`src/intelligence/`)
> [!IMPORTANT]  
> The `LegalQueryEngine` doesn't just query the DB. It uses **Text-to-Cypher** intelligence.

Incoming Natural Language queries pass through several steps:
1. **Provider Abstraction**: It routes to an `LLMProvider` (extending `GeminiProvider`, `OllamaProvider`, etc.). This protects the engine from underlying SDK deprecations.
2. **Schema Injection**: It pulls the current graph schema dynamically out of Neo4j to build the system prompt context. 
3. **Few-Shot Prompting**: It injects pre-validated Cypher examples (like `§5` translation) to guide the LLM. 
4. **Execution & Feedback**: If the LLM generates a bad cypher query (Syntax Error), the exact Neo4j error is caught and sent back to the LLM in a self-healing feedback loop asking it to retry and fix its mistake.

### 3.4 API Layer (`src/api/main.py`)
Exposes `POST /api/v1/query`. Expects `{"query": "natural language string"}`.
Spins up a lightweight Uvicorn server handling Cross-Origin (CORS) perfectly for the frontend.

---

## 4. Workflows

### 4.1 Ingestion Flow
1. Developer calls script (`scripts/ingest_regex.py`) with a target PDF.
2. `RegexLegalParser` fires up `pdfplumber`, loads raw strings, and creates `ParsedDocument` clusters.
3. `GraphIngestionService` binds `Neo4jDriver` constraints (assuring IDs are unique) and dynamically streams massive `UNWIND` statements to build nodes rapidly.

### 4.2 Query Response Flow
1. User types query on `frontend/index.html`. Request matches `/api/v1/query`.
2. API Layer delegates to `LegalQueryEngine.process_query()`.
3. LLM compiles Cypher -> Neo4j executes Cypher.
4. `Neo4jDriver` returns native neo4j Nodes/Edges dict arrays.
5. API translates driver objects into standard JSON arrays to stream back to the UI.

---

## 5. Security & Environment Configurations

* All sensitive routing lives in `config/.env`.
* **Neo4j Access**: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
* **LLM Selectors**: `LLM_PROVIDER` cleanly pivots the logic (can be set to `gemini` for global API, or `ollama` for strictly walled-off fast local processing).
* Rate limits and timeout delays are enforced specifically inside `pdf_parser.py` backoff configurations, shielding cloud accounts from "Quota Exceeded" blocks.
