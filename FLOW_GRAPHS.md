# Enterprise Agentic RAG — Process Flow Graphs

This document contains Mermaid flowcharts detailing the exact actions, processes, and state transitions for each major component of the Enterprise RAG application.

---

## 1. Full Query Flow (HTTP `/query` Route)
**File:** `app/main.py`
This maps the entire lifecycle of a request from the moment it hits the FastAPI server until the response is returned.

```mermaid
flowchart TD
    A[Client HTTP POST /query] --> B{Bearer Token\nvalidation}
    B -- Invalid --> B1[401 Unauthorized]
    B -- Valid / No key set --> C{Rate limiter\ncheck}
    C -- Exceeded --> C1[429 Too Many Requests]
    C -- OK --> D[guard: NeMo Guardrails]
    D -- Rail fired --> E[Return blocked response\nno graph invoked]
    D -- Passed --> F[Build initial AgentState\nmessages, current_query, documents, plan, status]
    F --> G[rag_agent.invoke with thread_id config]
    G --> H[LangGraph: Planner Node]
    H --> I{route_after_planner\ncurrent_query == CONVERSATIONAL?}
    I -- Yes --> K[LangGraph: Responder Node\nconversational prompt]
    I -- No --> J[LangGraph: Retriever Node]
    J --> K
    K --> L[LangGraph: END\nCheckpoint saved to Postgres]
    L --> M[Return answer, plan, status, sources]
    M --> N[Update Prometheus counters\nRAG_REQUESTS_TOTAL, RAG_REQUEST_DURATION]
```

---

## 2. LangGraph State Machine — Agent Nodes
**Files:** `app/agents/graph.py`, `app/agents/nodes/*.py`, `app/agents/state.py`
This shows how the `AgentState` is mutated as it passes through the Planner, Retriever, and Responder nodes.

```mermaid
flowchart LR
    START([Initial State\nmessages, current_query=raw_q\ndocuments=empty, plan=Start]) --> PL

    subgraph PL ["Planner Node (planner.py)"]
        PL1["LLM classifies intent\nusing messages history"] --> PL2{"Decision"}
        PL2 -- CONVERSATIONAL --> PL3["writes: current_query=CONVERSATIONAL\nstatus=Responding from memory\nplan=Intent: Conversational"]
        PL2 -- search query --> PL4["writes: current_query=refined_query\nstatus=Technical query identified\nplan=Intent: Technical + Search Term"]
    end

    PL3 --> EDGE
    PL4 --> EDGE

    subgraph EDGE ["Conditional Router"]
        E1{"current_query\n== CONVERSATIONAL?"}
    end

    EDGE -- Yes --> RS
    EDGE -- No --> RT

    subgraph RT ["Retriever Node (retriever.py)"]
        RT1["embed current_query\nQdrant cosine search top-15"] --> RT2["Jina Reranker\ntop-5 cross-encoder"]
        RT2 --> RT3["writes: documents=top-5 chunks\nstatus=Context retrieved\nplan += Context Retrieved"]
    end

    RT --> RS

    subgraph RS ["Responder Node (responder.py)"]
        RS1{"current_query\n== CONVERSATIONAL?"} -- Yes --> RS2["Build conversational prompt\nhistory + latest message"]
        RS1 -- No --> RS3["Build RAG prompt\ndocuments + history + question"]
        RS2 --> RS4["portkey_client LLM call\nwith retry up to 3x"]
        RS3 --> RS4
        RS4 --> RS5["Check x-portkey-cache-status header"]
        RS5 -- HIT --> RS6["writes: final_answer, status=Cached\nplan += Cache: Hit, messages += assistant"]
        RS5 -- MISS --> RS7["writes: final_answer, status=Generated\nplan unchanged, messages += assistant"]
    end

    RS --> CHECKPOINT["Checkpoint persisted to Postgres\nfull AgentState serialized under thread_id"]
    CHECKPOINT --> END_NODE([END])
```

---

## 3. Document Ingestion Pipeline
**File:** `app/ingestion/processor.py`
The offline process for parsing, chunking, embedding, and uploading data to the vector database.

```mermaid
flowchart TD
    A[python -m app.ingestion.processor DATA --wipe] --> B{--wipe flag?}
    B -- Yes --> C[qdrant_client.delete_collection]
    B -- No --> D
    C --> D{Collection exists?}
    D -- No --> E["qdrant_client.create_collection\nsize=1024, distance=Cosine"]
    D -- Yes --> F
    E --> F[Scan base_dir for sub-folders]
    F --> G{"Sub-folders\nfound?"}
    G -- No --> H["_get_source_type base_dir_name\ntrue / noisy / general"]
    G -- Yes --> I["For each sub-folder:\n_get_source_type sub_name"]
    H --> J[process_directory]
    I --> J

    J --> K[For each file in directory]
    K --> L{File extension}
    L -- pdf --> M[parse_pdf: pypdf + pdfplumber fallback]
    L -- html/htm --> N[parse_html: BeautifulSoup]
    L -- txt --> O[parse_text: plain read]
    L -- docx/pptx --> P[parse_office: python-docx / pptx]

    M --> Q[chunk_text\n≤1500 chars per chunk\nparagraph-aligned]
    N --> Q
    O --> Q
    P --> Q

    Q --> R["save_processed_chunk\nJSON to processed_data/source_type/"]
    R --> S["embed_texts chunks\nJina API or local mxbai"]
    S --> T["qdrant_client.upsert\nPointStruct per chunk\npayload: text, source, source_type"]
```

---

## 4. Embedding Provider Fallback Logic
**File:** `app/services/retrieval/embedding.py`
Shows how the system gracefully falls back to a local model if the Jina API is unreachable.

```mermaid
flowchart TD
    A["embed_query or embed_texts called\n_init check: _model_type is None?"] --> B{"_model_type\nalready set?"}
    B -- Yes --> G
    B -- No --> C["_probe_jina\nPOST single test vector to Jina API"]
    C --> D{HTTP 200\nand non-empty data?}
    D -- Yes --> E["_model_type = jina\nno local model loaded"]
    D -- No --> F["_model_type = fallback\nload mxbai SentenceTransformer locally\n~560MB download on first run"]
    E --> G{Active model type?}
    F --> G
    G -- jina --> H["_embed_via_jina\nBatch POST to api.jina.ai/v1/embeddings\nBATCH_SIZE=64\nwith @retry 3x backoff"]
    G -- fallback --> I["_embed_via_fallback\nmodel.encode in batches\nBATCH_SIZE=64"]
    H --> J{Jina call raised\nexception at runtime?}
    J -- Yes --> K["Switch to fallback:\nload mxbai model\n_model_type = fallback\nretry with local model"]
    J -- No --> L[Return embeddings list]
    I --> L
    K --> I
```

---

## 5. Checkpointer Initialization
**File:** `app/agents/graph.py`
Determines whether to use Neon Postgres for persistent memory or fallback to in-memory testing.

```mermaid
flowchart TD
    A[build_graph called at startup_event] --> B["create_checkpointer"]
    B --> C["ConnectionPool open\nconninfo=NEON_DB_URL\nmax_size=20, max_idle=240s"]
    C --> D{Pool.open\nsucceeds?}
    D -- No --> E["Exception caught\nreturn MemorySaver\nstate lost on restart"]
    D -- Yes --> F["Verify pool: getconn + putconn"]
    F --> G["Run migrations on autocommit connection\nPostgresSaver.from_conn_string.setup"]
    G --> H{Migrations\nsucceeded?}
    H -- No --> I["pool.close\nreturn MemorySaver"]
    H -- Yes --> J["PostgresSaver pool\nreturn as checkpointer"]
    J --> K["workflow.compile checkpointer=PostgresSaver\nstate persisted per thread_id across restarts"]
    E --> L["workflow.compile checkpointer=MemorySaver\nstate in-process RAM only"]
    I --> L
```

---

## 6. Rate Limiter Startup
**File:** `app/main.py`
Probes Upstash Redis on startup and configures the distributed or local rate limiter.

```mermaid
flowchart TD
    A[_init_rate_limiter called at startup] --> B["RedisStorage UPSTASH TLS URL\nrediss://default:token@host/0"]
    B --> C{storage.check\nand redis.ping succeed?}
    C -- Yes --> D["Limiter key_func=get_remote_address\nstorage_uri=redis_url\napp.state.rate_limiter_storage = redis"]
    C -- No / Exception --> E["Limiter key_func=get_remote_address\nno storage_uri = in-memory\napp.state.rate_limiter_storage = memory"]
    D --> F["app.state.limiter = Limiter instance"]
    E --> F
    F --> G["add_exception_handler RateLimitExceeded\n→ 429 response"]
    G --> H["Per request: _DeferredLimiter resolves\napp.state.limiter at request time\napplies RATE_LIMIT_PER_MINUTE/minute per IP"]
```

---

## 7. NeMo Guardrails Execution
**File:** `app/guardrails/rails.py`
Shows how user intents are classified against the Colang flows to block malicious or off-topic questions early.

```mermaid
flowchart TD
    A["guard(message) called"] --> B{"_rails initialized?"}
    B -- No --> C["Log warning<br/>return False, None<br/>pipeline proceeds"]
    B -- Yes --> D["_rails.generate<br/>messages: role=user, content=message<br/>NeMo runs intent matching against Colang flows"]
    D --> E{"Response content<br/>contains any RAIL_INDICATOR substring?"}
    E -- Yes --> F["Rail fired!<br/>log rail event<br/>return True, rail_response"]
    E -- No --> G["Guardrails passed<br/>return False, None"]
    F --> H["/query returns blocked response<br/>no LangGraph invoked<br/>GUARDRAILS_BLOCKS_TOTAL blocked=true"]
    G --> I["LangGraph pipeline starts<br/>GUARDRAILS_BLOCKS_TOTAL blocked=false"]

    subgraph INDICATORS ["RAIL_INDICATORS checked"]
        I1["can't help with that but ask me anything technical"]
        I2["I maintain consistent guidelines regardless of how I am prompted"]
        I3["Hello! I'm your Enterprise IT Assistant"]
        I4["Goodbye! Feel free to return whenever"]
    end
    E -.checks.-> INDICATORS
```
