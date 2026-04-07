# Medixa-AI

## Overview
Medixa-AI is a medicine interaction analysis platform with:
- React + Vite frontend
- FastAPI API gateway
- ML service for severity scoring
- GenAI service for LLM-assisted explanations
- Kafka + Redis for streaming and caching
- Prometheus + Grafana for observability

## Run locally
1. Start services:
   ```powershell
   docker compose up -d --build
   ```
2. Open frontend:
   - http://localhost:5173
3. Open Prometheus:
   - http://localhost:9090
4. Open Grafana:
   - http://localhost:3000
   - admin/admin

## Observability
Grafana is configured to auto-load:
- Prometheus data source from `observability/grafana/provisioning/datasources/datasource.yml`
- Dashboard JSON from `observability/grafana/dashboards/medixa-ai-services.json`

If Grafana does not show the dashboard:
- restart Grafana: `docker compose up -d --build grafana`
- inspect the mounted files in the container:
  - `/etc/grafana/provisioning/datasources`
  - `/var/lib/grafana/dashboards`

### Prometheus targets
Configured in `observability/prometheus.yml`:
- api-gateway
- ml-service
- genai-service

## Loki / log shipping
Loki is not yet enabled in the stack. The next step is:
1. add a Loki server service
2. add a log shipper such as Promtail or Grafana Agent
3. configure app logs or Docker container logs to be collected

## Architecture
See `docs/architecture.md` for the system architecture diagram and service flow.
