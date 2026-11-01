# Enterprise Agentic RAG

A RAG app for asking questions about technical documents. Put your files in `DATA/`, index them, then chat with them through a Streamlit UI or the API. It's built on FastAPI, LangGraph, Qdrant, NeMo Guardrails and Portkey.

Right now it's set up to run on your own machine. There's a Render config for putting it online, covered near the bottom.

## How a question gets answered

1. NeMo Guardrails looks at the message first and deals with greetings, goodbyes, off-topic questions and jailbreak attempts on its own.
2. The planner decides whether it's small talk or a real technical question. Technical ones get rewritten into a search query.
3. The retriever pulls 15 chunks from Qdrant and reranks them down to the best 5.
4. The responder sends the chat history and those chunks to the LLM through Portkey, and you get back the answer plus the sources.

Diagrams are in [FLOW_GRAPHS.md](FLOW_GRAPHS.md).

Most of the cloud services are optional. Leave a setting empty and the app uses a local stand-in:

| If this is empty | What happens |
|---|---|
| `JINA_API_KEY` | Local SentenceTransformer embeddings, and no reranking (Qdrant's order is kept) |
| `QDRANT_URL` | Vectors are stored in `local_qdrant_db/` |
| `NEON_DB_URL` | Chat memory lives in RAM and is lost on restart |
| `UPSTASH_REDIS_*` | Rate limiting happens in memory |
| `LOGFIRE_TOKEN` | Logfire is off |

Portkey is the one thing you can't skip.

## What you need

- Python 3.11 or newer
- A Portkey account with an API key and a saved config ID (`pc-...`)
- Internet access for Portkey, and for Jina if you use it

Without Jina, the first run downloads a SentenceTransformer model, so expect a wait and some disk use.

## Setup

**1. Install**

```powershell
python -m venv tenvv
.\tenvv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate with `source tenvv/bin/activate` instead.

**2. Create a `.env` in the repo root**

```env
PORTKEY_API_KEY=pk-your-key
PORTKEY_PRIMARY_CONFIG_ID=pc-your-saved-config-id
PORTKEY_PRIMARY_SLUG=marathon-api
BACKEND_URL=http://localhost:8000
```

The config ID has to be a real one from the Portkey dashboard. A placeholder lets the code import, but no request will work.

Everything else is optional and can stay blank: `JINA_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, `NEON_DB_URL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `RAG_API_KEY`, `RATE_LIMIT_PER_MINUTE` (20), `STRICT_STARTUP` (false), `LOGFIRE_TOKEN`, `LOGFIRE_BASE_URL`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT`.

`.env` is already in `.gitignore`. Keep it out of commits.

**3. Index your documents**

```powershell
python -m app.ingestion.processor DATA --wipe
```

This reads PDF, HTML, TXT, DOCX and PPTX files, splits them into chunks, embeds them and stores them in `local_qdrant_db/`. A JSON copy of every chunk is saved under `processed_data/` so you can see what got indexed. `--wipe` clears the existing collection first.

**4. Start the API**

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check it's up with `Invoke-RestMethod http://127.0.0.1:8000/health`. You should get `{"status":"ok"}`.

**5. Ask something**

```powershell
$body = @{
    q = "How does Kubernetes autoscaling work?"
    thread_id = "local-test"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post `
    -ContentType "application/json" -Body $body
```

You get back the answer, the steps it took, a status and the source chunks. Reuse the same `thread_id` for follow-ups. The history lasts until the API restarts.

**6. Start the UI** (in a separate terminal, with the venv activated)

```powershell
python -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Then open http://127.0.0.1:8501. Leave the API running in its own terminal.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/` | Basic status message |
| `GET` | `/health` | Is the process alive |
| `GET` | `/ready` | Checks the external services, returns 503 if any are down |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/graph` | The workflow graph as a PNG |
| `POST` | `/query` | Ask a question |

If `RAG_API_KEY` is set, send it as a bearer token (`Authorization: Bearer <key>`). It's empty by default, so auth is off locally.

## Where things live

```text
app/
  main.py          FastAPI app and the /query route
  config.py        settings from .env
  health.py        /health and /ready
  agents/          LangGraph state, graph, planner, retriever, responder
  gateway/         Portkey clients
  guardrails/      NeMo Guardrails setup
  ingestion/       loaders, chunking, indexing
  services/        embeddings, Qdrant, reranking, health checks
ui/
  app.py           Streamlit chat UI (local)
  st_cloud_ui.py   simpler version for cloud hosting
DATA/              your source documents
local_qdrant_db/   generated vector store
processed_data/    generated chunk JSON
```

## Deploying to Render

`render.yaml` sets up two free web services, one for the backend and one for the Streamlit UI. In Render, create a Blueprint from this repo using the `deployment` branch. When it asks for values:

- Use the same `RAG_API_KEY` for both services.
- Give the backend your `PORTKEY_API_KEY`, `PORTKEY_PRIMARY_CONFIG_ID`, and your Qdrant Cloud URL and API key.
- Jina, Neon, Upstash, Logfire and LangSmith are optional.

The UI talks to the backend over Render's private network. Free services go to sleep when idle, so the first request after a break is slow. `local_qdrant_db/` isn't in Git, so you'll need to index your documents into Qdrant Cloud for the deployed version to have anything to search.

## Things to know

- Local Qdrant only allows one process at a time. If you start a second API against the same `local_qdrant_db/`, it will complain that the folder is in use.
- Chat memory resets when the API restarts (unless `NEON_DB_URL` is set).
- Portkey is needed for every LLM call: the planner, the guardrails and the responder.
- There are no tests or evals yet. The only checks are `python -m compileall -q app ui` and `python -m ruff check app ui`.
- `local_qdrant_db/` and `processed_data/` can get big. Check `git status` before you push.
