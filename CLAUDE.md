# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend
```bash
# Start backend (from repo root)
python -m src.main
# or via start script
./start.sh

# Run tests
pytest tests/

# Run a single test file
pytest tests/test_group_store.py

# Run a single test by name
pytest tests/test_group_store.py::test_create_group
```

### Frontend
```bash
cd frontend

# Dev server (http://localhost:3000)
npm run dev

# Build for production (outputs to frontend/dist/)
npm run build

# Preview production build
npm run preview
```

### Document parsers (optional, install as needed)
```bash
pip install pymupdf          # PDF
pip install python-docx      # .docx
pip install openpyxl         # .xlsx
pip install python-pptx      # .pptx
pip install beautifulsoup4   # web pages
pip install chromadb sentence-transformers  # vector store
```

## Architecture

MemoX is a multi-agent RAG assistant. The backend is a FastAPI app; the frontend is a React/Ant Design SPA served from the same origin in production via `frontend/dist/`.

### Backend layers (`src/`)

```
src/
├── main.py               # Uvicorn entrypoint — reads config.yaml, starts server
├── config/               # Loads config.yaml into typed dataclasses
├── web/api.py            # All FastAPI routes + startup lifecycle
├── auth.py               # Bearer-token auth (in-memory, config-driven)
├── knowledge/
│   ├── document_parser.py    # Multi-format document → TextChunk[]
│   ├── vector_store.py       # ChromaDB wrapper + embedding adapters
│   ├── rag_engine.py         # RAGEngine: add/search/delete docs, sessions
│   └── group_store.py        # KnowledgeGroup persistence (data/groups.json)
├── agents/
│   ├── base_agent.py         # LLMProvider ABC + concrete providers (Anthropic, OpenAI, MiniMax, Kimi)
│   └── worker_pool.py        # WorkerPool, WorkerAgent, Task/SubTask state machine
└── coordinator/
    └── task_planner.py       # TaskPlanner: decomposes tasks, dispatches to workers, generates suggestions
```

**Request flow for `/api/chat/stream`:**
1. Auth middleware validates Bearer token
2. RAGEngine.search() queries ChromaDB with optional `group_ids` filter
3. Results injected into prompt; LLM called via configured coordinator provider
4. Response chunked and sent as SSE (`text/event-stream`)

**Request flow for `/api/tasks`:**
1. RAGEngine.search() retrieves context, injected into task `context["knowledge_context"]`
2. TaskPlanner.plan_task() calls LLM to classify complexity (simple/parallel/sequential/mixed) and decompose subtasks
3. TaskPlanner.execute_task() dispatches subtasks to WorkerPool
4. OptimizationSuggestions generated and returned alongside the result

### Document groups

Groups are stored in `data/groups.json` (via `GroupStore`). The special `"ungrouped"` group always exists and cannot be renamed or deleted. Document chunk metadata in ChromaDB carries `group_id`; search filters pass `{"group_id": {"$in": group_ids}}` to ChromaDB. On startup, `migrate_add_group_id()` backfills `group_id=ungrouped` for legacy chunks.

### Worker skills

Each worker can be assigned a list of skills via its template in `config.yaml`:

```yaml
worker_templates:
  reviewer:
    model: claude-sonnet-4-20250514
    provider: anthropic
    skills: ["code-review"]
```

Skills live in `data/skills/<name>/SKILL.md` in Claude Code skill format (YAML frontmatter with `name` + `description`, then markdown body). A worker's system prompt only lists `name + description`; the LLM calls the `load_skill` tool to fetch the full body on demand. This keeps the prompt small and enables hot reload — a newly installed skill is visible to the next task without restarting.

Install skills from GitHub:

```bash
python -m skills install github.com/anthropics/skills/code-review
python -m skills list
python -m skills remove code-review
python -m skills update code-review
```

Missing skills (listed in config but not on disk) produce a warning and are silently skipped from the system prompt — the worker still starts. Skill loading is whitelisted per-worker: even if `data/skills/` contains 20 skills, a worker can only `load_skill` the ones in its own `config.skills`.

### Frontend (`frontend/src/`)

React 18 + React Router v6 + Ant Design 5. Pages: Chat, Tasks, Documents. The Documents page has group filter tabs and per-document group assignment. The Chat and Tasks pages send `active_group_ids` to restrict RAG search scope.

### Configuration (`config.yaml`)

All runtime settings live here: server, providers (API keys resolved from env vars via `${VAR}` syntax), coordinator model, worker templates, embedding provider, auth users. The `knowledge_base.embedding_provider` key selects between `dashscope`, `openai`, or local `sentence-transformer`.

### SSL

If `ssl/cert.pem` and `ssl/key.pem` exist, the server starts in HTTPS mode automatically.

### State persistence

- ChromaDB at `data/chroma/` — vector embeddings + document chunk metadata (survives restarts)
- `data/groups.json` — group definitions
- `data/uploads/` — raw uploaded files (filename prefixed with UUID hex)
- Chat sessions and task history are **in-memory only** (lost on restart)
