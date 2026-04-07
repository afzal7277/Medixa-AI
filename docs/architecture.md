# Architecture Overview

## System components
- `frontend`: React/Vite UI serving user input and queries.
- `api-gateway`: FastAPI gateway and routing layer.
- `ml-service`: model scoring service for severity estimation.
- `genai-service`: RAG/LLM enrichment and explanation service.
- `kafka`: streaming backbone for data and event pipelines.
- `redis`: caching and fast lookup.
- `prometheus`: metrics scraping.
- `grafana`: dashboard visualization.

## Service flow

```mermaid
flowchart LR
  subgraph User
    A[Browser] -->|HTTP| B[Frontend]
  end

  subgraph API
    B -->|POST /analyse| C[API Gateway]
    C -->|REST| D[ML Service]
    C -->|REST| E[GenAI Service]
  end

  subgraph Infra
    D -->|metrics| P[Prometheus]
    E -->|metrics| P
    C -->|metrics| P
    P -->|visualize| G[Grafana]
  end

  subgraph Streaming
    C -->|Kafka events| K[Kafka]
    K --> F[Consumer / Pipeline]
  end

  subgraph Cache
    C -->|Redis lookup| R[Redis]
  end
```

## Notes
- Grafana provisioning should auto-configure Prometheus as the data source and import dashboards from `observability/grafana/dashboards`.
- Loki is a recommended addition for log aggregation, but it is not wired into this repo yet.
- If you want a next step, add a Loki + Promtail service, then instrument FastAPI app logs/containers.
