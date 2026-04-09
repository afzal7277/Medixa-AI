# Medixa AI

**Drug interaction analysis platform** — enter two drug names, get an AI-powered severity score, mechanism explanation, and supporting literature passages in real time.

---

## Architecture

```
Browser (React/Vite)
      │  HTTP / SSE
      ▼
API Gateway  ──── Redis (cache + history)
  │       │
  │       └──── GenAI Service (ChromaDB RAG + GPT-4o streaming)
  │
  └──────────── ML Service (BioBERT + XGBoost severity classifier)

Background pipeline
  OpenFDA ──► Kafka ──► Data Consumer ──► ML Trainer (retrain every 60 min)

Observability
  Prometheus ──► Grafana
```

| Service | Tech | Port |
|---|---|---|
| Frontend | React 18 + Vite + Nginx | 5173 |
| API Gateway | FastAPI + httpx | 8000 |
| ML Service | FastAPI + XGBoost + BioBERT | 8001 |
| GenAI Service | FastAPI + ChromaDB + GPT-4o | 8002 |
| Data Pipeline | confluent-kafka producer | — |
| Data Consumer | confluent-kafka consumer | — |
| ML Trainer | XGBoost retrain loop | — |
| Redis | Cache + history | 6379 |
| Kafka | KRaft mode | 9092 |
| Prometheus | Metrics scrape | 9090 |
| Grafana | Dashboards | 3000 |

---

## Quick Start

### Prerequisites

- Docker Desktop (≥ 4.25) with Compose V2
- OpenAI API key

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY
```

### 2. Start all services

```bash
docker compose up -d --build
```

### 3. Open in browser

| URL | What |
|---|---|
| http://localhost:5173 | Frontend UI |
| http://localhost:8000/docs | API Gateway Swagger |
| http://localhost:8001/docs | ML Service Swagger |
| http://localhost:8002/docs | GenAI Service Swagger |
| http://localhost:9090 | Prometheus |
| http://localhost:3000 | Grafana (admin / admin) |

---

## API

### Analyse a drug pair

```http
GET /analyse?drug_a=warfarin&drug_b=aspirin
Accept: text/event-stream
```

Returns a Server-Sent Events stream:

```
data: {"type":"severity","severity":"Contraindicated","confidence":0.88}
data: {"type":"token","token":"Warfarin and aspirin combined..."}
data: {"type":"sources","sources":[{"text":"...","source":"openfda_events"}]}
data: {"type":"done"}
```

### Other endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Gateway health + downstream status |
| GET | `/history` | Last 10 queries (Redis) |
| GET | `/drug-info?name=warfarin` | Drug class, uses, is_drug flag |
| GET | `/analyse` | SSE stream (severity + explanation) |
| GET | `/metrics` | Prometheus metrics |

---

## Key Design Decisions

| Decision | Why |
|---|---|
| SSE for streaming | GPT-4o tokens appear instantly; no websocket overhead |
| Redis sorted key (`a:b` always alphabetical) | Cache hit is order-independent |
| BioBERT 768-d embeddings | Domain-specific; outperforms generic embeddings on drug NER |
| XGBoost over neural net | Fast retrain (< 30 s on 87k pairs); interpretable feature importance |
| ChromaDB HNSW cosine similarity | Sub-10 ms RAG retrieval at 100k passage scale |
| Kafka KRaft | No ZooKeeper dependency; simpler single-node dev setup |

---

## Running Tests

### Unit + integration (Docker, one command)

```bash
docker compose -f docker-compose.test.yml run --rm --build test-unit
```

Reports written to `tests/results/`:
- `report.html` — pytest-html test report
- `coverage/index.html` — line coverage

### Unit tests only

```bash
docker compose -f docker-compose.test.yml run --rm --build test-unit-only
```

### E2E tests (requires full stack running)

```bash
docker compose up -d --build                          # start stack
docker compose -f docker-compose.test.yml --profile e2e up --build e2e-runner
```

### Test structure

```
tests/
├── conftest.py               # load_service() helper + fakeredis pre-import
├── pytest.ini
├── requirements-test.txt
├── unit/
│   ├── conftest.py           # sys.modules mocks for all heavy deps
│   ├── test_gateway.py       # 17 tests
│   ├── test_ml_service.py    # 17 tests
│   ├── test_genai_service.py # 15 tests
│   └── test_pipeline.py      # 20 tests
├── integration/
│   ├── conftest.py           # fakeredis fixtures, kafka testcontainer
│   ├── test_gateway_integration.py   # 9 tests
│   └── test_pipeline_integration.py  # 9 tests
└── e2e/
    ├── conftest.py           # Playwright fixtures
    ├── test_analyse_flow.py  # 14 tests
    └── test_history.py       # 10 tests
```

---

## Observability

Grafana auto-loads on first start:
- **Datasource:** `observability/grafana/provisioning/datasources/datasource.yml`
- **Dashboard:** `observability/grafana/dashboards/medixa-ai-services.json`

If the dashboard is missing, restart Grafana:

```bash
docker compose restart grafana
```

Prometheus targets (configured in `observability/prometheus.yml`):
- `api-gateway:8000/metrics`
- `ml-service:8001/metrics`
- `genai-service:8002/metrics`

---

## ML Pipeline

```
OpenFDA FAERS API
      │
      ▼  Kafka: raw_drug_events
Data Consumer
  → extract drug pairs + reaction text
  → BioBERT embed each drug name
  → detect CYP450 involvement, severity keywords
      │
      ▼  Kafka: processed_features
ML Trainer (runs every 60 min)
  → XGBoost refit on accumulated features
  → saves model.json + labels.json
      │
      ▼
ML Service hot-swaps model on next request
```

Feature vector: **1 538 dimensions** = drug A embedding (768) + drug B embedding (768) + CYP450 flag (1) + pair frequency (1)

Severity classes: `None` · `Mild` · `Moderate` · `Severe` · `Contraindicated`

---

## Project Structure

```
medixa-ai/
├── api-gateway/          # FastAPI gateway, Redis cache, SSE relay
├── ml-service/           # XGBoost severity classifier + BioBERT embedding
├── genai-service/        # ChromaDB RAG + GPT-4o explanation streaming
├── data-pipeline/        # OpenFDA producer, Kafka consumer, ML trainer
├── frontend/             # React 18 + Vite + Tailwind
├── observability/        # Prometheus config + Grafana dashboards
├── tests/                # pytest unit / integration / Playwright E2E
├── docs/                 # Architecture diagram, model card, data report
├── docker-compose.yml
├── docker-compose.test.yml
└── .env.example
```

---

## Docs

| File | Contents |
|---|---|
| `docs/DOCUMENTATION.md` | Full end-to-end technical documentation |
| `docs/architecture.svg` | System architecture diagram |
| `docs/model_card.md` | ML model: features, metrics, limitations |
| `docs/data_collection_report.md` | Data sources, label distribution, quality issues |
| `docs/drug_pairs.md` | Reference drug pairs for each severity level |
| `docs/Medixa_AI_Presentation.pptx` | 6-slide pitch deck |
| `docs/Medixa_AI_Documentation.docx` | Detailed Word document |

---

## License

MIT
