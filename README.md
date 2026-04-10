# Legal Knowledge Graph System

A production-grade, AI-powered Knowledge Graph system for accurate legal document querying, built on **Neo4j**, **FastAPI**, and **LLMs**.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         API Layer (FastAPI)                         │
│         /query   /graph   /ingest   /sections   /amendments         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                     Intelligence Layer                              │
│   NL Query Parser → Cypher Generator → Response Grounding Engine   │
│                   (LLM + Few-Shot Prompting)                        │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                      Service Layer                                  │
│   GraphQueryService  │  DocumentIngestionService  │  LegalService   │
└────────┬─────────────┴──────────────┬──────────────┴───────────────┘
         │                            │
┌────────▼────────┐         ┌─────────▼──────────────────────────────┐
│  Neo4j Driver   │         │         Domain Models                  │
│  (Repository    │         │  Act, Section, Amendment, Rule, Clause  │
│   Pattern)      │         └────────────────────────────────────────┘
└─────────────────┘
```

---

## Graph Schema Design

### Node Types

| Label        | Key Properties                                              |
|--------------|-------------------------------------------------------------|
| `Act`        | `id`, `title`, `year`, `number`, `effective_date`          |
| `Section`    | `id`, `number`, `title`, `content`, `effective_content`    |
| `Subsection` | `id`, `number`, `content`                                  |
| `Clause`     | `id`, `identifier`, `content`                               |
| `Amendment`  | `id`, `number`, `year`, `title`, `effective_date`          |
| `Rule`       | `id`, `number`, `title`, `content`                         |
| `Provision`  | `id`, `text`, `type`                                       |

### Relationship Types

| Relationship     | From        | To          | Properties                        |
|------------------|-------------|-------------|-----------------------------------|
| `HAS_SECTION`    | Act         | Section     | `order`                           |
| `HAS_SUBSECTION` | Section     | Subsection  | `order`                           |
| `HAS_CLAUSE`     | Subsection  | Clause      | `order`                           |
| `AMENDED_BY`     | Section     | Amendment   | `type`, `effective_date`          |
| `SUBSTITUTES`    | Amendment   | Section     | `old_content`, `new_content`      |
| `INSERTS`        | Amendment   | Section     | `position`, `content`             |
| `DELETES`        | Amendment   | Section     | `deleted_content`                 |
| `DERIVED_RULE`   | Section     | Rule        | `authority`                       |
| `REFERS_TO`      | Section     | Section     | `context`                         |
| `UNDER_ACT`      | Rule        | Act         | —                                  |

---

## Key Modeling Decisions

1. **Effective State Tracking**: Each `Section` node stores both `original_content` and `effective_content`. When an amendment applies, `effective_content` is updated while history is preserved via `AMENDED_BY` relationships.

2. **Amendment Lineage**: Amendments are first-class nodes. Each amendment action (`SUBSTITUTES`, `INSERTS`, `DELETES`) is a typed relationship carrying change metadata — enabling full audit trails.

3. **Temporal Modeling**: All amendment relationships carry `effective_date`, allowing point-in-time queries ("what was Section X on date Y?").

4. **Extensibility via Repository Pattern**: All Neo4j interactions go through typed repository classes. Adding a new node type requires implementing one interface — no changes to service or API layers.

5. **AI Layer is Grounded**: The LLM generates Cypher queries from natural language, then the results are passed back to the LLM for explanation. This prevents hallucination — every answer is backed by a graph traversal.

6. **Idempotent, Cost-Free Ingestion**: Rather than using expensive LLMs to extract graph nodes, the ingestion engine (`GraphIngestionService`) uses deterministic, rule-based Regex parsers. The entire ingestion pipeline is built on Neo4j `MERGE` statements, making it safely idempotent (can be re-run safely without duplication) while dynamically calculating the `effective_content` string manipulations during the build phase.

---

## Project Structure

```
legal-kg/
├── src/
│   ├── core/               # Config, exceptions, base classes
│   ├── models/             # Pydantic domain models
│   ├── graph/              # Neo4j driver & repository pattern
│   ├── services/           # Business logic layer
│   ├── ingestion/          # Document parsers + graph builders
│   ├── intelligence/       # LLM integration (NL→Cypher→Answer)
│   ├── api/                # FastAPI routes & schemas
│   └── utils/              # Helpers, logging, etc.
├── tests/
│   ├── unit/
│   └── integration/
├── scripts/                # CLI tools (ingest, seed, query)
├── config/                 # Environment configs
├── data/                   # Sample legal documents
├── docker-compose.yml      # Neo4j + App stack
└── README.md
```

---

## Setup & Running

### Prerequisites
- Python 3.12+
- Docker & Docker Compose (for Neo4j)
- (Optional) Ollama for running local LLMs, or an OpenAI/Gemini API Key.

### Quick Start

```bash
# 1. Clone and set up
git clone <repo>
python3 -m venv venv
source venv/bin/activate

# 2. Start Neo4j
docker-compose up -d neo4j

# 3. Install dependencies
pip install -e .                # Core dependencies (OpenAI, Neo4j, FastAPI)
pip install -e ".[dev]"         # Development tools (pytest, ruff)
pip install -e ".[gemini]"      # Google Gemini support
pip install -e ".[anthropic]"   # Anthropic Claude support
pip install -e ".[all-providers]" # Both Gemini and Anthropic

# (Optional) Run tests to verify setup
pytest


# 4. Ingest the Data (Companies Act, Rules & Amendments)
# We use a blazing-fast Regex parser to load the graph locally without burning API credits:
chmod +x run_all_ingestions.sh
./run_all_ingestions.sh

# 5. Start the Intelligence API Backend
# Make sure your .env has LLM_PROVIDER=ollama and an LLM_MODEL set, or configure your API key.
uvicorn src.api.main:app --reload 
# API will run on http://localhost:8000

# 6. Start the Frontend UI Client
# In a new terminal window:
cd frontend
python3 -m http.server 3000
# Open http://localhost:3000 in your browser to interact with the Knowledge Graph!
```

### Running Queries

You can query the graph natively through the web UI frontend at `http://localhost:3000`.

Alternatively, via API:
```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Which rules apply under Section 12?"}'
```

---

## Example Queries

| Natural Language | Cypher Generated |
|---|---|
| "Current text of Section 5" | `MATCH (s:Section {number:'5'}) RETURN s.effective_content` |
| "Amendments to Section 3" | `MATCH (s:Section {number:'3'})-[:AMENDED_BY]->(a:Amendment) RETURN a` |
| "Rules under Section 7" | `MATCH (s:Section {number:'7'})-[:DERIVED_RULE]->(r:Rule) RETURN r` |
