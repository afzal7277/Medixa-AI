# Medixa-AI Development Log

## Project: Drug Interaction Intelligence Platform
## Started: 2026-04-03

---

## Stack
- Python 3.11 (containers) / 3.14 (local)
- FastAPI + Uvicorn
- Kafka (KRaft mode) + Redis
- XGBoost + ONNX
- ChromaDB + LLM (streaming)
- React + Vite + TailwindCSS
- Prometheus + Grafana + Loki

---

## Session 1 — 2026-04-03

### Infrastructure
- Set up Docker Compose with KRaft Kafka, Redis, Prometheus, Grafana
- Removed Zookeeper, using KRaft mode (cleaner, no dependency)
- All 6 containers running healthy
- Created observability/prometheus.yml with scrape targets for api, ml-service, genai-service

### Project Structure
- Cleaned root folder (removed DECISION_LOG, FAILURE_LOG, REQUIREMENTS_FREEZE, TASK_TRACKER, uv.lock)
- Aligned to spec structure: data-pipeline, ml-service, genai-service, api-gateway, frontend, observability, notebooks, tests
- Fixed .gitignore (env file, venv, json files, etc)
- Created .env for all environment variables
- Squashed git history to single clean commit

### Data Pipeline
- Built single producer (producer.py) hitting RxNorm + OpenFDA + PubMed
- RxNorm used for dynamic drug name/pair discovery
- OpenFDA for adverse event reports
- PubMed for scientific abstracts
- Canonical schema: event_id, drug_a, drug_b, source, raw_text, timestamp
- Retry logic with exponential backoff on all HTTP calls
- Graceful shutdown on SIGTERM/SIGINT
- Structured JSON logs on every action

### Challenges
- Python 3.14 local — too new for ML libs (XGBoost, ONNX). Decision: use python:3.11-slim in all Dockerfiles
- retry.backoff.ms > retry.backoff.max.ms Kafka warning — fixed config values
- RxNorm connection error from container — investigating Docker network access
- curl not available in python:3.11-slim — testing network with python requests instead

### Pending
- Confirm container can reach external APIs
- Fix RxNorm connection issue if network blocked
- Write feature engineering consumer
- Write ML training script
- Wire up FastAPI /analyse SSE endpoint
- Build React UI
- Prometheus metrics instrumentation
- Grafana dashboards

---

## Task Checklist

### Block 1 - Infrastructure
- [x] Add Kafka KRaft to docker-compose
- [x] Add Prometheus + Grafana to docker-compose
- [x] All containers running healthy

### Block 2 - Data Pipeline
- [x] Producer running, delivering to raw_drug_events
- [x] Clean canonical schema confirmed in Kafka
- [x] Drug pairs discovered dynamically from adverse event co-occurrence
- [x] Two sources: openfda_events + openfda_labels
- [x] Old topic cleared, fresh data flowing

### Block 3 - ML Model
- [ ] Data collection script pulling 5k records
- [ ] XGBoost classifier trained
- [ ] F1 >= 0.75 on held-out test set
- [ ] ONNX export
- [ ] /predict endpoint running

### Block 4 - GenAI Layer
- [ ] ChromaDB populated
- [ ] Top-k retrieval working
- [ ] LLM prompt template
- [ ] SSE token streaming

### Block 5 - API Gateway
- [ ] POST /analyse SSE endpoint
- [ ] GET /health
- [ ] GET /metrics
- [ ] Structured JSON logs with trace_id

### Block 6 - React UI
- [ ] Drug search with autocomplete
- [ ] Severity badge
- [ ] Streaming text panel
- [ ] Query history sidebar

### Block 7 - Observability
- [ ] 10 Prometheus metrics instrumented
- [ ] 3 Grafana dashboards live
- [ ] Loki log shipping

### Block 8 - Demo Prep
- [ ] 5 drug pairs covering all severity levels
- [ ] README + architecture diagram
- [ ] ML model card
- [ ] Full demo dry run

---