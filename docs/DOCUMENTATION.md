# Medixa AI — End-to-End Documentation

## Overview

Medixa AI is a clinical drug-interaction analysis platform. A user enters two drug names; the system classifies interaction severity using a trained ML model and generates a streaming GPT-4o explanation grounded in real adverse-event data retrieved via RAG.

---

## Architecture at a Glance

| Layer | Technology | Port |
|---|---|---|
| Frontend | React + TypeScript + Vite (Nginx) | 5173 |
| API Gateway | FastAPI | 8000 |
| ML Service | FastAPI + BioBERT + XGBoost | 8001 |
| GenAI Service | FastAPI + ChromaDB + GPT-4o | 8002 |
| Data Pipeline (Producer) | Python + OpenFDA API | 8003 (metrics) |
| Data Pipeline (Consumer) | Python + BioBERT | 8004 (metrics) |
| Cache / Store | Redis 7 | 6379 |
| Message Broker | Apache Kafka (KRaft) | 9092 |
| Metrics | Prometheus | 9090 |
| Dashboards | Grafana | 3000 |

---

## Services

### 1. Frontend (`frontend/`)
- **Stack**: React 18, TypeScript, Vite, Nginx
- **Key features**:
  - Drug name autocomplete with debounced AI lookup (1500 ms)
  - Fetches drug profile via `GET /drug-info` (class, common uses)
  - Submits analysis via `POST /analyse` and reads Server-Sent Events (SSE)
  - Renders streaming tokens as Markdown in real time
  - Severity color-coded badge + confidence meter
  - Query history panel (last 10 queries, click to replay, PDF export)
  - Dark/light mode (persisted to `localStorage`)
- **Environment**: `VITE_API_URL` → API Gateway URL

### 2. API Gateway (`api-gateway/main.py`)
- **Stack**: FastAPI, httpx, redis-py, prometheus-fastapi-instrumentator
- **Responsibilities**:
  - Single entry point for the frontend
  - Redis cache lookup (1 h TTL, key = sorted drug pair)
  - Orchestrates ML Service → GenAI Service in a single SSE stream
  - Stores query history in Redis list (capped at 10)
  - Retry logic (3 attempts) for ML Service calls
  - Trace IDs (`X-Trace-ID` header) per request for log correlation
- **Endpoints**:

| Method | Path | Description |
|---|---|---|
| POST | `/analyse` | Main analysis — returns SSE stream |
| GET | `/drug-info?name=` | Proxies GenAI drug-info lookup |
| GET | `/history` | Last 10 queries from Redis |
| GET | `/health` | Redis + downstream URLs status |
| GET | `/metrics` | Prometheus metrics |

- **SSE event types**: `severity`, `sources`, `token`, `done`, `error`
- **Prometheus metrics**:
  - `api_request_latency_ms` (histogram, per path/method)
  - `api_error_count` (counter, 5xx)
  - `drug_pairs_analysed_total` (counter)
  - `severity_class_distribution` (counter, per severity label)

### 3. ML Service (`ml-service/`)
- **Stack**: FastAPI, SentenceTransformers (BioBERT), XGBoost, Redis, prometheus-fastapi-instrumentator
- **serve.py** — inference server:
  - Loads `dmis-lab/biobert-base-cased-v1.2` to embed drug name strings
  - Loads XGBoost classifier from `/app/model/severity_classifier.onnx`
  - Feature vector = `embedding_a (768d) + embedding_b (768d) + cyp450_flag (1) + pair_frequency (1)` → 1538 dims
  - Reads `cyp450_flag` and `pair_frequency` from Redis for enrichment
  - Returns `{ severity, confidence, drug_a, drug_b }`
- **train.py** — continuous trainer:
  - Consumes `processed_features` Kafka topic (up to 7000 records)
  - Builds feature matrix, fits XGBoost (100 trees, depth 6, lr 0.1)
  - 80/20 stratified train/test split, logs classification report
  - Saves model + label encoder JSON, then sleeps 1 h before retraining
  - Minimum 500 samples required before training starts
- **Severity classes**: `None`, `Mild`, `Moderate`, `Severe`, `Contraindicated`
- **Prometheus metrics**:
  - `ml_inference_latency_ms`
  - `ml_model_confidence`

### 4. GenAI Service (`genai-service/`)
- **Stack**: FastAPI, OpenAI (gpt-4o), ChromaDB, SentenceTransformers (BioBERT)
- **main.py** — endpoints:
  - `POST /explain` → SSE stream of clinical explanation tokens
  - `GET|POST /drug-info` → JSON drug profile from GPT-4o (name, drugClass, commonUses, is_drug)
  - On startup, calls `rag_service.populate()` to seed ChromaDB from Kafka
- **Prompt**: 4-section structured output — mechanism, clinical consequences, recommended action, confidence caveat
- **rag.py** — RAG service:
  - ChromaDB persistent store at `/app/chromadb`
  - Collection `drug_interactions` with cosine similarity (HNSW)
  - Populate: reads `raw_drug_events` Kafka topic, embeds documents in batches of 100
  - Retrieve: top-K=3 passages filtered by drug_a or drug_b metadata, fallback to unfiltered
- **Prometheus metrics**:
  - `llm_time_to_first_token_ms`
  - `llm_tokens_per_second`

### 5. Data Pipeline — Producer (`data-pipeline/producer.py`)
- Polls OpenFDA `/drug/event.json` (serious ADRs, 100 per page) every 60 s
- Extracts drug pairs (suspect→interacting preferred, suspect→concomitant fallback)
- Builds `raw_text` from reactions, indications, seriousness flags, and literature references
- Also fetches `/drug/label.json` for drug interactions/warnings/boxed warnings
- Publishes canonical events to Kafka topic `raw_drug_events`
- Resets pagination offset after 10,000 records
- Exposes Prometheus metrics on port 8003

### 6. Data Pipeline — Consumer (`data-pipeline/consumer.py`)
- Subscribes to `raw_drug_events` Kafka topic
- For each event:
  1. Embeds `drug_a` and `drug_b` with BioBERT
  2. Detects CYP450 enzyme involvement via keyword matching on `raw_text`
  3. Maps seriousness keywords to severity label (`None`/`Mild`/`Moderate`/`Severe`/`Contraindicated`)
  4. Increments Redis pair frequency counter (`pair:<sorted_key>`)
  5. Publishes enriched feature event to `processed_features` topic
- Tracks and exports consumer lag metric
- Exposes Prometheus metrics on port 8004

---

## Data Flow

```
OpenFDA API
    │  (100 events / 60 s)
    ▼
[Producer]──► Kafka: raw_drug_events ──►┬──► [Consumer] ──► Kafka: processed_features ──► [ML Trainer]
                                        │         │                                              │
                                        │         │ (Redis pair freq)                            │ (saves model)
                                        │         ▼                                              ▼
                                        └──► [RAG Populator] ──► ChromaDB            /app/model/*.onnx
                                                                        ▲
User ──► [Frontend] ──► [API Gateway]                                   │
              SSE ◄──── (Redis cache)                                   │
                          │                                             │
                          ├──► [ML Service] ──► BioBERT + XGBoost      │
                          │        ▲ (reads model)─────────────────────┘
                          │        └─ (reads Redis: cyp450, freq)
                          │
                          └──► [GenAI Service] ──► ChromaDB RAG ──► GPT-4o
                                    SSE stream of tokens ◄───────────────
```

### Request Lifecycle (Happy Path)

1. User types a drug name → debounced `GET /drug-info` → GPT-4o returns class/uses
2. User clicks **Analyse** → `POST /analyse` to API Gateway
3. API Gateway generates `trace_id`, checks Redis cache
4. **Cache hit**: replays cached severity + tokens, done
5. **Cache miss**:
   - `POST /predict` to ML Service (up to 3 retries)
   - ML embeds both names, reads Redis features, runs XGBoost → `{severity, confidence}`
   - Gateway yields `severity` SSE event to frontend
   - Gateway opens streaming `POST /explain` to GenAI Service
   - GenAI retrieves top-3 RAG passages from ChromaDB, builds prompt, streams GPT-4o tokens
   - Gateway forwards each `token` SSE event to frontend in real time
   - On stream end: result cached in Redis (1 h), appended to history list

---

## Infrastructure

### Kafka Topics

| Topic | Producer | Consumers |
|---|---|---|
| `raw_drug_events` | data-pipeline producer | data-pipeline consumer, RAG populator |
| `processed_features` | data-pipeline consumer | ML trainer |

Kafka runs in **KRaft mode** (no Zookeeper). Auto topic creation enabled.

### Redis Keys

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `cache:<drug_a>:<drug_b>` | String (JSON) | 1 h | API response cache |
| `pair:<drug_a>:<drug_b>` | String (int) | none | Co-occurrence frequency |
| `cyp450:<drug_a>:<drug_b>` | String ("0"/"1") | none | CYP450 involvement flag |
| `query_history` | List (JSON) | none | Last 10 queries (LPUSH + LTRIM) |

### Volumes

| Volume | Used by | Contents |
|---|---|---|
| `ml-model` | ml-service, ml-trainer | XGBoost model + label encoder |
| `chroma-data` | genai-service | ChromaDB vector store |
| `model-cache` | all services | HuggingFace model cache |
| `kafka-data` | kafka | Kafka log data |
| `grafana-data` | grafana | Dashboard state |

---

## Observability

### Prometheus (`observability/prometheus.yml`)
Scrapes all services every 15 s.

### Grafana Dashboards
- **Medixa AI Services** — end-to-end latency, error rates, drug pairs analysed
- **ML Performance** — inference latency, model confidence distribution
- **Data Pipeline** — consumer lag, events ingested, processed events
- **System Health** — CPU, memory, container status

---

## Environment Variables (`.env`)

| Variable | Default | Service |
|---|---|---|
| `OPENAI_API_KEY` | — | genai-service |
| `OPENAI_MODEL` | `gpt-4o` | genai-service |
| `REDIS_HOST` | `redis` | api-gateway, ml-service, consumer |
| `REDIS_PORT` | `6379` | api-gateway, ml-service, consumer |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` | all pipeline services |
| `ML_SERVICE_URL` | `http://ml-service:8001` | api-gateway |
| `GENAI_SERVICE_URL` | `http://genai-service:8002` | api-gateway |
| `MODEL_PATH` | `/app/model/severity_classifier.onnx` | ml-service, trainer |
| `LABEL_ENCODER_PATH` | `/app/model/label_encoder.json` | ml-service, trainer |
| `MIN_SAMPLES` | `500` | ml-trainer |
| `MAX_RETRIES` | `3` | multiple services |
| `POLL_INTERVAL` | `60` | producer |
| `TOP_K` | `3` | rag |
| `CHROMA_PATH` | `/app/chromadb` | genai-service |

---

## Running Locally

```bash
# Start all services
docker compose up --build

# Access points
Frontend:   http://localhost:5173
API Docs:   http://localhost:8000/docs
ML Docs:    http://localhost:8001/docs
Grafana:    http://localhost:3000  (admin/admin)
Prometheus: http://localhost:9090
```

**Startup order** (managed by Docker Compose `depends_on`):
1. Redis, Kafka (with healthcheck)
2. data-pipeline (producer + consumer), ml-trainer, genai-service, ml-service
3. api (gateway)
4. frontend

> **Note**: The ML model is unavailable until the trainer has collected ≥ 500 feature records from Kafka. The ML service returns HTTP 503 during this window, and the API Gateway retries up to 3 times.

---

## ML Model Details

| Parameter | Value |
|---|---|
| Embedding model | `dmis-lab/biobert-base-cased-v1.2` |
| Embedding dim | 768 per drug → 1536 combined |
| Extra features | `cyp450_flag` (bool), `pair_frequency` (int) |
| Total features | 1538 |
| Classifier | XGBoost (`n_estimators=100, max_depth=6, lr=0.1`) |
| Evaluation | Stratified 80/20 split, classification report logged |
| Retrain interval | 1 hour |

---

## Security Notes

- `CORS allow_origins=["*"]` — restrict in production
- OpenAI API key via `.env` — never commit
- Redis has no auth by default — add `requirepass` in production
- Kafka has no TLS — add SSL listeners for production
