# Legal Knowledge Graph — Setup & Development Log

> Everything done to get this project from broken install → running frontend.

---

## 1. Fix: `pip install -e ".[gemini]"` Failing

### Problem

Running `pip install -e ".[gemini]"` (or `.[dev]`) threw:

```
BackendUnavailable: Cannot import 'setuptools.backends.legacy'
```

### Root Cause

`pyproject.toml` was using a build backend introduced only in certain builds of setuptools:

```toml
# ❌ Before
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.backends.legacy:build"
```

The `setuptools.backends.legacy` entry point **does not exist** in the setuptools wheel that Homebrew ships for Python 3.14, even though the version number was high enough. Confirmed by:

```bash
venv/bin/python -c "import setuptools.backends.legacy"
# ModuleNotFoundError: No module named 'setuptools.backends'
```

### Fix Applied

Switched to the universally-supported classic backend:

```toml
# ✅ After — in pyproject.toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"
```

`setuptools.build_meta` is functionally identical to the `legacy` alias and works on all Python versions and setuptools ≥ 61.

Also upgraded setuptools itself inside the venv:

```bash
venv/bin/pip install --upgrade setuptools wheel pip
```

After the fix, install succeeded:

```bash
venv/bin/pip install -e ".[gemini]"
# Successfully installed ... google-generativeai-0.8.6 ... legal-kg-1.0.0 ...
```

---

## 2. What the Repository Does

**Legal Knowledge Graph** is an AI-powered system for querying Indian legal documents (Acts, Sections, Amendments, Rules) using **natural language**.

### Architecture

```
Your Question (English)
        ↓
  LLM generates Cypher query
        ↓
  Neo4j graph is traversed
        ↓
  LLM explains results in plain English  ← grounded, no hallucination
```

### Tech Stack

| Layer | Technology |
|---|---|
| Graph DB | Neo4j |
| API | FastAPI + Uvicorn |
| AI / LLM | OpenAI / Anthropic / Gemini (pluggable) |
| Config | Pydantic Settings + `.env` |
| Frontend | Vanilla HTML/CSS/JS |

### Graph Schema

**Node Types:** `Act`, `Section`, `Subsection`, `Clause`, `Amendment`, `Rule`

**Key Relationships:**
- `(Act)-[:HAS_SECTION]→(Section)`
- `(Section)-[:AMENDED_BY]→(Amendment)`
- `(Amendment)-[:SUBSTITUTES|INSERTS|DELETES]→(Section)`
- `(Section)-[:DERIVED_RULE]→(Rule)`
- `(Section)-[:REFERS_TO]→(Section)`

---

## 3. Seeding the Database

```bash
python scripts/seed.py
```

Loads `data/sample_act.json` — a subset of the **Indian Penal Code, 1860 (IPC)** — into Neo4j:

| Entity | Count |
|---|---|
| Act | 1 (IPC_1860) |
| Sections | 6 |
| Amendments | 3 |
| Rules | 3 |
| Cross-references | 2 |

### Sections in the seed data

| Section | Title |
|---|---|
| §1 | Title and extent of operation of the Code |
| §2 | Punishment of offences committed within India |
| §300 | Murder |
| §302 | Punishment for murder |
| §354 | Assault or criminal force to woman with intent to outrage her modesty |
| §375 | Rape |

### Rules in the seed data (via `DERIVED_RULE`)

| Rule | Linked to |
|---|---|
| Code of Criminal Procedure — Arrest without warrant | §300 |
| Indian Evidence Act — Burden of proof in sexual offences | §375 |
| Sentencing Guidelines — Murder | §302 |

### Amendments in the seed data

| Amendment | Year | Sections Affected |
|---|---|---|
| Criminal Law (Amendment) Act | 2013 | §375, §354 |
| Criminal Law (Amendment) Act | 2018 | §302 |
| Jammu and Kashmir Reorganisation Act | 2019 | §1 |

---

## 4. Running the API

```bash
uvicorn src.api.main:app --reload
# API available at: http://localhost:8000
# Swagger docs at:  http://localhost:8000/docs
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health + AI status check |
| `POST` | `/api/v1/query` | **AI natural-language query** |
| `GET` | `/api/v1/acts` | List all acts |
| `GET` | `/api/v1/acts/{id}/sections` | List all sections for an act |
| `GET` | `/api/v1/acts/{id}/sections/{num}` | Section detail + amendments + rules |
| `GET` | `/api/v1/acts/{id}/amendments` | All amendments for an act |
| `GET` | `/api/v1/acts/{id}/analytics/impact` | Most-amended sections |
| `POST` | `/api/v1/ingest` | Ingest a new legal document |

### Example AI Query (curl)

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which rules apply under Section 302?"}'
```

---

## 5. Frontend

A single-page app at `frontend/index.html`, served via Python's built-in HTTP server.

### Running the Frontend

```bash
# From the legal-kg directory, with venv active:
venv/bin/python -m http.server 3000 --directory frontend
# Open: http://localhost:3000
```

### Panels

| Panel | What it shows |
|---|---|
| **AI Query** | Natural-language search → LLM generates Cypher → graph result → grounded answer |
| **Acts** | All loaded Acts with click-through to sections |
| **Sections** | All sections with preview text; click to open detail drawer with Amendments / Rules / Cross-refs tabs |
| **Amendments** | Full amendment timeline for selected Act |
| **Analytics** | Bar chart of most-amended sections with summary stats |

### Status Badge (top-right)

Shows live connection status polled every 15 seconds:
- 🟢 `Connected · AI enabled` — API + Neo4j + LLM all healthy
- 🔴 `degraded` — DB or LLM unavailable

---

## 6. Bug: "No rules found" for Every Question

### Problem

Every query was returning 0 results with the message *"No rules were found"*.

### Root Cause

The example chips in the UI were asking about sections that **don't exist** in the seed data:

```
❌ "What is Section 5 about?"      → §5 doesn't exist
❌ "Show amendments to Section 3"  → §3 doesn't exist
❌ "Which rules apply under Section 7?"  → §7 doesn't exist
```

The LLM generated **correct Cypher**, but the graph had no matching nodes — so 0 results were returned, and the LLM correctly reported "nothing found."

### Fix Applied

Updated the frontend chips to use real section numbers from the seeded data:

```
✅ "What is Section 300 about?"
✅ "Show amendments to Section 375"
✅ "Which rules apply under Section 302?"
✅ "What sections reference Section 302?"
✅ "List all sections of IPC 1860"
✅ "What is the current text of Section 1?"
✅ "Show all amendments to the Act"
```

Also added a hint bar below the search box:
> 💡 **Seeded sections:** §1, §2, §300, §302, §354, §375 (IPC 1860) · Rules on §300, §302, §375

---

## 7. Known Issues / Warnings

### `FutureWarning: google.generativeai` deprecated

```
FutureWarning: All support for the `google.generativeai` package has ended.
Please switch to the `google.genai` package as soon as possible.
```

The `GeminiProvider` in `src/intelligence/query_engine.py` currently uses the old `google-generativeai` SDK. The API still works, but you should migrate to `google-genai` when ready. This is a separate tracked task.

---

## 8. Quick Start (Full Sequence)

```bash
# 1. Activate venv
source venv/bin/activate

# 2. Install dependencies (already done)
pip install -e ".[gemini]"

# 3. Start Neo4j (if not running)
docker-compose up -d neo4j

# 4. Seed sample data
python scripts/seed.py

# 5. Start API
uvicorn src.api.main:app --reload

# 6. In a separate terminal — start frontend
venv/bin/python -m http.server 3000 --directory frontend

# 7. Open browser
open http://localhost:3000
```

### Working AI Queries to Try

| Question | What it returns |
|---|---|
| `Which rules apply under Section 302?` | Sentencing Guidelines for Murder |
| `Which rules apply under Section 300?` | CrPC — Arrest without warrant |
| `Show amendments to Section 375` | 2013 Criminal Law Amendment (age of consent) |
| `What is the current text of Section 1?` | Amended text (includes J&K after 2019) |
| `What sections reference Section 302?` | §300 via cross-reference |
| `Show all amendments to the Act` | All 3 amendments with affected sections |
| `List all sections of IPC 1860` | All 6 sections in order |
