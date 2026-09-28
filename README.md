# Enterprise Agentic RAG

Local-development Retrieval-Augmented Generation application for technical documents. The project uses FastAPI, LangGraph, Qdrant local storage, NeMo Guardrails, Streamlit, and Portkey for LLM requests.

This repository is currently configured for **local execution**, not production deployment.

## Current Local Architecture

| Component | Local setup |
|---|---|
| API | FastAPI at `http://localhost:8000` |
| Workflow | LangGraph planner -> retriever -> responder |
| Vector database | Qdrant file storage in `local_qdrant_db/`, collection `enterprise_rag` |
| Embeddings | Jina API when configured, otherwise local SentenceTransformer fallback |
| Reranking | Jina API when configured, otherwise original retrieval order |
| Conversation memory | LangGraph `MemorySaver` when `NEON_DB_URL` is empty |
| Rate limiting | In-memory when Upstash Redis variables are empty |
| LLM | Portkey gateway; a valid Portkey API key and saved config are required |
| UI | Streamlit at `http://localhost:8501` |
| Observability | Disabled when `LOGFIRE_TOKEN` is empty |

The local fallbacks are intended for development. Conversation memory is lost when the API process restarts.
Local Qdrant is file-based and supports one active Qdrant client at a time; do not start a second API process against the same `local_qdrant_db/` directory.

## Deploy to Render

The root `render.yaml` defines separate free web services for the FastAPI backend and Streamlit UI. Create a Blueprint from this repository and the `deployment` branch in the Render dashboard. During setup, enter the same `RAG_API_KEY` for both services, along with `PORTKEY_API_KEY`, `PORTKEY_PRIMARY_CONFIG_ID`, and your Qdrant Cloud URL and API key for the backend. Jina, Neon, Upstash, Logfire, and LangSmith credentials are optional.

The UI connects to the backend over Render's private network. Free services can spin down while idle. The local Qdrant database is excluded from Git, so configure Qdrant Cloud to use the indexed knowledge base in production.

## Project Structure

```text
Enterprise_RAG/
├── app/
│   ├── main.py                  # FastAPI app and /query route
│   ├── config.py                # Environment settings
│   ├── health.py                # /health and /ready endpoints
│   ├── agents/                  # LangGraph state and nodes
│   ├── gateway/                 # Portkey OpenAI-compatible clients
│   ├── guardrails/              # NeMo Guardrails configuration
│   ├── ingestion/               # Document parsing, chunking, and indexing
│   └── services/                # Embeddings, Qdrant, ranking, and health checks
├── DATA/                        # Source documents to ingest
├── local_qdrant_db/             # Generated local Qdrant data
├── processed_data/              # Generated processed document JSON
├── ui/app.py                    # Streamlit chat interface
├── FLOW_GRAPHS.md               # Workflow diagrams
├── pyproject.toml               # Project metadata and tooling config
└── requirements.txt             # Runtime dependency list
```

## Requirements

- Windows PowerShell, macOS, or Linux
- Python 3.11 or newer
- A Portkey account, API key, and saved config ID
- Internet access for Portkey and, optionally, Jina embeddings/reranking

The application can use a local embedding fallback, but the first fallback run downloads a SentenceTransformer model and requires additional disk space.

## Setup

### 1. Create and activate the virtual environment

PowerShell:

```powershell
python -m venv tenvv
.\tenvv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install development tools if you want to run local checks:

```powershell
python -m pip install pytest ruff
```

### 2. Configure `.env`

Create `.env` in the repository root. The following values are required for local execution:

```env
PORTKEY_API_KEY=pk-your-key
PORTKEY_PRIMARY_CONFIG_ID=pc-your-saved-config-id
PORTKEY_PRIMARY_SLUG=marathon-api

# Optional Jina embeddings and reranking. Leave empty to use local fallbacks.
JINA_API_KEY=

# Local development fallbacks. Leave these empty to use local Qdrant,
# MemorySaver, and in-memory rate limiting.
QDRANT_URL=
QDRANT_API_KEY=
NEON_DB_URL=
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# Local API settings
RAG_API_KEY=
RATE_LIMIT_PER_MINUTE=20
STRICT_STARTUP=false

# Optional observability
LOGFIRE_TOKEN=
LOGFIRE_BASE_URL=
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=enterprise_rag

# Streamlit backend
BACKEND_URL=http://localhost:8000
```

`PORTKEY_PRIMARY_CONFIG_ID` must be a real `pc-...` ID from the Portkey dashboard. A placeholder value only allows imports to be tested; it cannot serve real requests.

Do not commit `.env`. It is ignored by `.gitignore`.

### 3. Validate the Python source

```powershell
python -m compileall -q app ui
python -m ruff check app ui
```

### 4. Ingest the documents

Run this from the repository root:

```powershell
python -m app.ingestion.processor DATA --wipe
```

The command parses supported PDF, HTML, TXT, DOCX, and PPTX files, creates chunks, generates embeddings, and stores vectors in `local_qdrant_db/`. It also writes audit JSON files under `processed_data/`.

If `JINA_API_KEY` is empty, the local embedding model is downloaded on first use. Ingestion can take time for a large document collection.

### 5. Start the API

In terminal 1:

```powershell
.\tenvv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check that the process is alive:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

### 6. Query the API

In another PowerShell terminal:

```powershell
$body = @{
    q = "How does Kubernetes autoscaling work?"
    thread_id = "local-test"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri http://127.0.0.1:8000/query `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

The response contains the generated answer, workflow steps, status, and retrieved source chunks. Use the same `thread_id` for conversational context during the current API process.

### 7. Start the Streamlit UI

In a third terminal:

```powershell
.\tenvv\Scripts\Activate.ps1
python -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. Keep the API and Streamlit processes running in separate terminals.
If the API is already running, do not start another copy; local Qdrant will report that its storage folder is already in use.

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/` | Basic API status message |
| `GET` | `/health` | Liveness check |
| `GET` | `/ready` | Dependency readiness check |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/graph` | Render the compiled graph as a PNG |
| `POST` | `/query` | Run a guarded RAG query |

If `RAG_API_KEY` is set, send it as a bearer token. The local `.env` leaves this empty, so authentication is disabled for development:

```powershell
-Headers @{ Authorization = "Bearer $env:RAG_API_KEY" }
```

## Query Flow

1. The API validates the optional bearer token and applies local rate limiting.
2. NeMo Guardrails classifies greetings, farewells, off-topic requests, and jailbreak attempts.
3. The Planner classifies the request as conversational or technical.
4. Technical requests retrieve and rerank document chunks from local Qdrant.
5. The Responder sends the conversation and context to Portkey.
6. The API returns the answer, plan, status, and sources.

See [FLOW_GRAPHS.md](FLOW_GRAPHS.md) for diagrams of the workflow and fallbacks.

## Local Checks

The current workspace does not contain a `tests/` or `evals/` directory, so automated behavioral tests and evaluation commands are not currently available. The checks below validate the available source only:

```powershell
python -m compileall -q app ui
python -m ruff check app ui
```

## Generated Files and Git

The following directories are generated locally and may become large:

- `local_qdrant_db/`
- `processed_data/`

Review `.gitignore` and the staged file list before pushing. Do not commit `.env`, API keys, database URLs, or generated local service state.

## Current Limitations

- A real Portkey configuration is required for planner, guardrail, and responder LLM calls.
- Local conversation memory disappears when the API restarts.
- Local Qdrant is single-process development storage.
- No automated tests or evaluation suite are included in the current workspace.
- Production deployment, cloud persistence, distributed rate limiting, and observability are not part of the current local setup.

## Application Source Reference

This section documents the checked-in application source under `app/` and `ui/`. It is organized by runtime responsibility and records every function or method currently defined in those Python files.

### Repository Hierarchy

```text
Enterprise_RAG/
├── app/
│   ├── __init__.py                  # Package marker; no executable code
│   ├── config.py                    # Pydantic settings and environment setup
│   ├── health.py                    # FastAPI liveness and readiness routes
│   ├── logging.py                   # Async-safe request correlation IDs
│   ├── main.py                      # FastAPI application, auth, metrics, and /query
│   ├── agents/
│   │   ├── graph.py                 # LangGraph construction and checkpointer selection
│   │   ├── state.py                 # Shared AgentState TypedDict contract
│   │   └── nodes/
│   │       ├── planner.py           # Conversational vs technical intent routing
│   │       ├── retriever.py         # Qdrant search and semantic reranking node
│   │       └── responder.py         # Context-aware Portkey answer generation
│   ├── gateway/
│   │   ├── __init__.py              # Re-exports gateway helpers and client
│   │   └── client.py                # Portkey OpenAI and LangChain clients
│   ├── guardrails/
│   │   ├── __init__.py              # Re-exports guardrail entry points
│   │   ├── colang_rules.py          # In-memory Colang flows and rail indicators
│   │   └── rails.py                 # NeMo Guardrails singleton and gate function
│   ├── ingestion/
│   │   ├── processor.py             # Parse, chunk, embed, and index documents
│   │   ├── chunking/splitter.py     # Paragraph-preserving text chunking
│   │   └── loaders/
│   │       ├── __init__.py          # Loader package marker
│   │       ├── html.py              # HTML cleanup and text extraction
│   │       ├── office.py            # DOCX/PPTX extraction through Unstructured
│   │       ├── pdf.py               # PDF extraction with pdfplumber fallback
│   │       └── text.py              # Plain-text file loading
│   └── services/
│       ├── __init__.py              # Service package marker
│       ├── health/
│       │   ├── __init__.py          # Health utility package marker
│       │   └── connection_checker.py# External dependency health checks
│       └── retrieval/
│           ├── embedding.py         # Jina embeddings and local fallback
│           ├── qdrant_service.py    # Shared Qdrant client and vector search
│           └── ranking_service.py   # Jina reranking and order-preserving fallback
├── ui/
│   ├── app.py                       # Local Streamlit chat UI with polling compatibility
│   └── st_cloud_ui.py               # Simpler Streamlit/cloud chat UI
├── DATA/                            # Input documents for ingestion
├── processed_data/                  # Generated per-source processed chunk JSON
├── local_qdrant_db/                 # Generated local Qdrant collection storage
├── FLOW_GRAPHS.md                   # Workflow diagrams and fallback behavior
├── pyproject.toml                   # Pinned project metadata, tooling, and extras
└── requirements.txt                 # Runtime dependency groups and purposes
```

`app/gateway/__init__.py` and `app/guardrails/__init__.py` re-export functions from their implementation modules. The other initializer files contain no functions. `colang_rules.py` contains configuration constants rather than functions. The two UI files are Streamlit scripts whose behavior runs at import/script execution time and therefore define no Python functions.

### Function and Method Reference

#### `app/config.py` - settings and environment

`Settings` loads `.env` and environment variables through Pydantic settings, validates required Portkey configuration, and exposes derived connection values.

- `Settings._blank_qdrant_key_to_none(v)`: field validator that converts an empty Qdrant API key to `None`, preventing a blank authentication header; returns the normalized value.
- `Settings.judge_api_key()`: property returning `JUDGE_OPENAI_API_KEY`, or the primary OpenAI key when the dedicated evaluation key is absent.
- `Settings.postgres_uri()`: property appending TCP keepalive parameters to `NEON_DB_URL`; returns the connection string used by LangGraph Postgres persistence.
- `Settings.redis_url()`: property converting Upstash REST credentials into a TLS `rediss://` URL for Redis clients; returns the derived URL.
- `apply_langchain_env()`: copies enabled LangSmith tracing, API key, project, and endpoint settings into `os.environ` so LangChain discovers them automatically; has no return value.

#### `app/gateway/client.py` - Portkey gateway

- `_build_portkey_headers(feature="rag")`: validates the saved Portkey config ID and builds authentication, routing, and metadata headers; returns a header dictionary or raises `ValueError` when configuration is missing.
- `get_langchain_llm(feature="rag")`: creates and returns a `ChatOpenAI` wrapper pointed at the Portkey gateway with the selected feature metadata.
- `extract_cache_status(response)`: probes common OpenAI SDK response attributes for `x-portkey-cache-status`; returns an uppercase cache status or `"MISS"`.

#### `app/agents/graph.py` and `app/agents/state.py` - workflow orchestration

`AgentState` defines the LangGraph state: accumulating `messages`, the planner's `current_query`, retrieved `documents`, user-visible `plan`, `status`, and `final_answer`.

- `create_checkpointer()`: selects Postgres-backed conversation persistence when `NEON_DB_URL` is configured, runs migrations, and returns `PostgresSaver`; otherwise or on failure returns in-memory `MemorySaver`.
- `build_graph(checkpointer=None)`: registers Planner, Retriever, and Responder nodes, routes conversational turns directly to the Responder, compiles the graph, and returns the runnable graph.
- `route_after_planner(state)`: nested graph router returning `"responder"` for the `CONVERSATIONAL` sentinel and `"retriever"` for technical queries.

#### `app/agents/nodes/planner.py` - intent planning

- `planner_node(state)`: formats conversation history and the latest message, asks the Portkey LLM for either `CONVERSATIONAL` or a focused search query, and returns updated `current_query`, `status`, and `plan` fields.

#### `app/agents/nodes/retriever.py` - retrieval node

- `retrieve_node(state)`: searches Qdrant for up to 15 candidates, reranks their text with the ranking service, formats the top five chunks, and returns updated `documents`, `status`, and `plan` fields.

#### `app/agents/nodes/responder.py` - answer synthesis

- `generate_node(state)`: builds a conversation-only or retrieved-context prompt, calls the retrying LLM helper, records Portkey cache hits in the plan, and returns `final_answer`, assistant `messages`, `status`, and `plan`.
- `_call_llm(prompt)`: sends a completion request through the raw Portkey OpenAI client using the configured virtual slug; retries transient failures up to three times with exponential backoff and returns the SDK response.

#### `app/guardrails/rails.py` and `app/guardrails/colang_rules.py` - input gate

`colang_rules.py` supplies `COLANG_CONTENT`, `YAML_CONTENT`, and `RAIL_INDICATORS` for off-topic, jailbreak, greeting, and farewell flows.

- `initialize_rails()`: builds the in-memory NeMo `RailsConfig`, creates the guardrail LLM through the gateway, and stores a module-level `LLMRails` singleton; has no return value.
- `guard(message)`: sends a message through the initialized rails and searches the response for configured indicator phrases; returns `(True, response)` when a rail fires or `(False, None)` when the request passes.

#### `app/health.py` - API probes

- `health()`: liveness route returning `{"status": "ok"}` while the process is running.
- `ready(request)`: runs all dependency checks, maps results to status strings, and returns HTTP 200 when all are healthy or HTTP 503 with the failing checks.

#### `app/logging.py` - request context

- `set_request_id(request_id)`: binds a request correlation ID to the current async-safe `ContextVar`; has no return value.
- `get_request_id()`: returns the correlation ID currently bound to the async context, or `None`.

#### `app/main.py` - FastAPI application

`QueryRequest` is the Pydantic request model containing `q` and an optional `thread_id`. `_DeferredLimiter` delays rate-limiter resolution until startup has configured `app.state`.

- `_DeferredLimiter.limit(rule_fn)`: stores a fixed or callable rate rule and returns a decorator for routes.
- `_DeferredLimiter.limit(...).decorator(func)`: nested decorator that wraps a route function and preserves its metadata.
- `_DeferredLimiter.limit(...).decorator(...).wrapper(*args, **kwargs)`: nested wrapper that applies the live limiter when available, otherwise calls the route directly; returns the route result.
- `_init_rate_limiter()`: attempts an Upstash Redis-backed SlowAPI limiter and falls back to in-memory limiting when Redis is unavailable; registers the rate-limit exception handler and has no return value.
- `verify_api_key(credentials)`: enforces the configured bearer token when `RAG_API_KEY` exists; returns the token on success, `None` when auth is disabled locally, or raises HTTP 401.
- `startup_event()`: initializes guardrails, compiles the graph, configures rate limiting, checks external services, and optionally aborts when `STRICT_STARTUP` is enabled; has no return value.
- `home()`: root route returning the API liveness message.
- `get_graph_image(_api_key)`: renders the compiled LangGraph as a Mermaid PNG and returns it, or returns an error object when rendering fails.
- `query(request, body, _api_key)`: main guarded RAG route; sets request context, blocks rail matches, invokes the graph with the conversation thread ID, records Prometheus metrics, and returns the answer, plan, status, and sources or HTTP 500 on pipeline failure.

#### `app/ingestion/chunking/splitter.py` - chunking

- `chunk_text(text, chunk_size=1500)`: splits text at double-newline paragraph boundaries, accumulates paragraphs below the size limit, removes empty chunks, and returns the resulting list.

#### `app/ingestion/loaders/*.py` - document loaders

- `parse_html(file_path)`: reads HTML, removes scripts/styles/metadata/noscript elements, extracts visible text with BeautifulSoup, normalizes whitespace, and returns cleaned text.
- `parse_office(file_path)`: uses Unstructured auto-partitioning for DOCX/PPTX files, joins extracted elements, and returns plain text.
- `parse_pdf(file_path)`: extracts PDF pages with `pypdf`, retries blank pages with `pdfplumber`, and returns combined text.
- `parse_text(file_path)`: reads a UTF-8 text file with replacement-tolerant decoding and returns its contents.

#### `app/ingestion/processor.py` - ingestion pipeline

- `_get_source_type(name)`: maps names containing `true` or `noisy` to those labels and otherwise returns the original name.
- `save_processed_chunk(data, source_type, filename)`: creates `processed_data/<source_type>/`, writes chunk metadata as indented JSON, and returns the destination path.
- `process_file(file_path, filename, source_type)`: selects a loader by extension, chunks extracted text, saves audit JSON, embeds chunks, and upserts UUID-keyed points into Qdrant; skips unsupported or empty files and logs failures.
- `process_directory(dir_path, source_type)`: lists files directly inside a directory and calls `process_file` for each; has no return value.
- `run_ingestion(base_dir, explicit_source_type=None, wipe=False)`: optionally deletes the existing collection, creates it with the active embedding dimension, and processes either the base directory or each source subdirectory.

#### `app/services/retrieval/embedding.py` - embeddings

- `_load_fallback_model()`: loads the configured local SentenceTransformer model and returns it.
- `_probe_jina()`: sends a probe embedding request to Jina; returns `True` when the API responds with data and `False` when credentials or connectivity fail.
- `_init()`: lazily chooses Jina or the local model and records the active backend; has no return value.
- `get_embedding_dim()`: initializes the backend and returns the fixed 1024-dimensional vector size.
- `_embed_jina_batch(texts, task)`: posts one batch to Jina with retry and returns embeddings ordered by response index.
- `_embed_via_jina(texts, task)`: splits input into `BATCH_SIZE` groups, embeds each through Jina, and returns the combined vectors.
- `_embed_via_fallback(texts)`: batches input through the local model and returns its list-of-list vectors.
- `_embed(texts, task)`: routes to the active backend and permanently switches to the local model if a runtime Jina call fails; returns embeddings.
- `embed_query(query)`: embeds one retrieval query and returns its vector.
- `embed_texts(texts)`: embeds document passages for indexing and returns their vectors.

#### `app/services/retrieval/qdrant_service.py` - vector search

- `_search(query, limit)`: embeds a query, performs a Qdrant cosine search with payloads, and returns dictionaries containing content, source, and score; retries transient failures.
- `search_enterprise_knowledge(query, limit=8)`: public search wrapper returning `_search` results or an empty list when Qdrant remains unavailable.

#### `app/services/retrieval/ranking_service.py` - reranking

- `_JinaReranker.rerank(query, documents, top_n)`: posts candidates to the Jina cross-encoder, resolves returned text by value or index, and returns the top relevant strings.
- `_get_ranker()`: lazily creates and returns the shared `_JinaReranker` instance.
- `_rerank_with_retry(query, documents, top_n)`: invokes the shared reranker with exponential retry and returns its result.
- `rerank_documents(query, documents, top_n=5)`: reranks with Jina when configured, otherwise or on failure preserves Qdrant order and returns the first `top_n` documents.

#### `app/services/health/connection_checker.py` - dependency health

`ConnectionResult` stores a service name, boolean health flag, and diagnostic message.

- `ConnectionResult.__init__(name, healthy, message="")`: initializes one service result; has no return value.
- `ConnectionResult.to_dict()`: converts the result into status, health, and message fields for API responses.
- `_check_neon_postgres()`: opens a short-lived pool, runs `SELECT 1`, and returns a healthy or unavailable Postgres result.
- `_check_upstash_redis()`: creates a Redis client, sends `PING`, and returns the connectivity result.
- `_check_qdrant()`: selects the configured remote or shared local client, calls `get_collections()`, and returns the Qdrant result.
- `_check_portkey_gateway()`: sends a minimal completion request and returns whether Portkey produced non-empty content.
- `_check_jina_embeddings()`: probes the Jina embeddings endpoint and returns whether embedding data was received.
- `_check_jina_reranker()`: probes the Jina reranker endpoint and returns whether a results payload was received.
- `_check_logfire()`: reports whether a Logfire token is configured; it does not make a network request.
- `_check_langsmith()`: requests the LangSmith `/ok` endpoint and returns the reachability result.
- `check_all_connections()`: runs the ordered checker list and returns a map keyed by service name.
- `log_connection_summary(results)`: logs each result and returns `True` only when every check is healthy.
- `_print_cli_report(results)`: prints a human-readable command-line report and returns process code `0` for all healthy or `1` for any failure.

#### `ui/app.py` and `ui/st_cloud_ui.py` - Streamlit scripts

These modules intentionally define no functions. Their top-level script execution configures the page, initializes session state and Logfire, renders chat history, posts user prompts to the FastAPI `/query` endpoint, displays plans and sources, streams the answer character by character, and handles errors. `ui/app.py` additionally loads the root `.env`, supports bearer authentication, supports the legacy polling response shape, and exposes a memory-reset button. `ui/st_cloud_ui.py` uses Streamlit secrets when available and provides the simpler synchronous cloud-facing flow.

### Runtime Cooperation

1. `app.main.startup_event()` initializes NeMo rails, compiles `app.agents.graph`, chooses conversation persistence, initializes rate limiting, and checks dependencies.
2. `POST /query` authenticates and rate-limits the request, then `app.guardrails.rails.guard()` can return a greeting, farewell, off-topic, or jailbreak response without invoking LangGraph.
3. Passing requests enter the graph with `AgentState`. `planner_node()` marks conversational turns or rewrites technical questions into retrieval queries.
4. Conversational turns go directly to `generate_node()`. Technical turns go through `retrieve_node()`, which combines Qdrant vector search, embedding selection, and optional Jina reranking before the responder.
5. `generate_node()` calls Portkey with conversation history and, for technical requests, capped retrieved context. The API returns the answer, visible plan, status, and source chunks to either Streamlit UI.
6. The ingestion command uses the same embedding and Qdrant services to transform `DATA/` documents into searchable vectors and audit JSON.
7. `/ready` and the standalone connection-check command use `connection_checker.py` to report external dependency availability.

### System Design Concepts Used

The project combines the following software, AI, and distributed-system design concepts:

- **Retrieval-Augmented Generation (RAG):** The application retrieves relevant document chunks from Qdrant and supplies them to an LLM so technical answers are grounded in the indexed enterprise material.
- **Agentic workflow orchestration:** LangGraph models the request lifecycle as cooperating Planner, Retriever, and Responder nodes instead of one monolithic handler.
- **Explicit state-machine design:** `AgentState` provides a typed shared contract for messages, query intent, documents, plan, status, and final answer; graph edges control the allowed transitions.
- **Conditional routing:** The Planner emits a `CONVERSATIONAL` sentinel for turns that do not need retrieval, allowing the graph to skip unnecessary vector search.
- **Conversation memory and checkpointing:** Thread IDs connect requests to LangGraph checkpoints. Postgres persistence is used when configured, while `MemorySaver` provides a local-development fallback.
- **Guardrail-first request processing:** NeMo Guardrails checks greetings, farewells, off-topic requests, and jailbreak attempts before the request reaches the main agent workflow.
- **Defense in depth:** API bearer authentication, guardrail classification, rate limiting, input-domain restrictions, and controlled LLM prompts provide separate protection layers.
- **Embedding-based semantic search:** Documents and queries are converted into vectors, then compared in Qdrant using cosine similarity rather than exact keyword matching.
- **Two-stage retrieval:** Qdrant provides a broad candidate set and the Jina cross-encoder reranker narrows it to the most relevant context before generation.
- **Provider abstraction through an LLM gateway:** Portkey presents an OpenAI-compatible interface while centralizing provider routing, saved configuration, retries, fallback policy, and semantic-cache behavior outside the application nodes.
- **Configuration-driven behavior:** Pydantic settings load environment variables and derive connection URLs, model routing values, feature flags, and optional integrations from one shared `Settings` object.
- **Graceful degradation:** Missing optional services select local alternatives: SentenceTransformer for embeddings, original Qdrant order when reranking is unavailable, local Qdrant storage, in-memory conversation memory, and in-memory rate limiting.
- **Retry with exponential backoff:** Tenacity retries transient LLM, embedding, vector-search, and reranking failures before the surrounding service applies its fallback or error policy.
- **Lazy initialization and singleton reuse:** Embedding backends, the reranker, guardrails, clients, and shared Qdrant storage are initialized once and reused to avoid repeated setup cost.
- **Batch processing:** Embedding requests are split into fixed-size batches, and ingestion processes documents directory by directory to control request size and memory use.
- **Document ingestion pipeline:** Loader, chunker, embedder, and vector-index stages form a repeatable path from PDF, HTML, text, DOCX, or PPTX input to searchable Qdrant points and audit JSON.
- **Paragraph-aware chunking:** Ingestion preserves double-newline paragraph boundaries while enforcing a target character limit, improving the semantic coherence of retrieved context.
- **Separation of concerns:** API routing, workflow nodes, gateway clients, guardrails, ingestion, retrieval, health checks, and UI rendering live in separate modules with narrow responsibilities.
- **Dependency injection at the graph boundary:** `build_graph()` accepts an optional checkpointer, allowing production persistence to be replaced with a test or local implementation without changing node logic.
- **API contract modeling:** Pydantic `QueryRequest` validates the request body, while consistent response fields expose the answer, plan, status, and sources to both clients.
- **Fail-soft service boundaries:** Search and reranking convert dependency failures into empty or original-order results where possible; the API returns a controlled error response when the full pipeline cannot complete.
- **Readiness versus liveness:** `/health` only confirms that the process is alive, while `/ready` actively checks external dependencies and returns 503 when the service is not ready.
- **Observability and correlation:** Logfire spans, Prometheus counters/histograms, LangSmith configuration, request IDs, and thread IDs connect user requests to workflow stages and external calls.
- **Async-safe request context:** `ContextVar` stores correlation IDs per execution context so concurrent requests do not overwrite one another's logging metadata.
- **Distributed and local rate limiting:** SlowAPI uses Upstash Redis when available for shared limits across instances and falls back to process-local limiting for development.
- **Semantic caching awareness:** The responder inspects Portkey cache headers and records cache hits in the user-visible plan without coupling the graph to a particular provider implementation.
- **Operational command-line entry points:** Ingestion and dependency checks can run independently of the API process, supporting maintenance and diagnostics workflows.
- **Environment-based deployment portability:** The same modules support local file-backed Qdrant and memory fallbacks as well as remote Qdrant, Neon Postgres, Upstash Redis, Jina, Portkey, Logfire, and LangSmith integrations.

### Documentation Boundary

`DATA/` is input content. `processed_data/` contains generated chunk/audit JSON, and `local_qdrant_db/` contains generated local vector-store state; these directories are described here but not expanded into per-document or database-internal references. `tenvv/`, `.git/`, caches, compiled files, `.env`, secrets, and generated dependency/vector-store internals are excluded. `FLOW_GRAPHS.md`, `pyproject.toml`, and `requirements.txt` remain the authoritative references for diagrams, project tooling, and dependency declarations.
