import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8001")
GENAI_SERVICE_URL = os.getenv("GENAI_SERVICE_URL", "http://genai-service:8002")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
HISTORY_MAX = 10

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "api-gateway", "message": "%(message)s"}',
)



class TraceFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "trace_id"):
            record.trace_id = "none"
        return True

logger = logging.getLogger(__name__)
logger.addFilter(TraceFilter())

app = FastAPI(title="Medixa API Gateway")

api_request_latency_ms = Histogram(
    "api_request_latency_ms",
    "End-to-end API response time per request",
    ["path", "method"],
    buckets=[50, 100, 250, 500, 1000, 2000, 5000, 10000],
)
api_error_count = Counter(
    "api_error_count",
    "HTTP 5xx errors per endpoint",
    ["path", "method", "status"],
)
drug_pairs_analysed_total = Counter(
    "drug_pairs_analysed_total",
    "Count of unique drug pairs analysed",
)
severity_class_distribution = Counter(
    "severity_class_distribution",
    "Severity classification counts",
    ["severity"],
)

Instrumentator().instrument(app).expose(app)

@app.middleware("http")
async def add_metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    latency_ms = (time.time() - start) * 1000
    path = request.url.path
    method = request.method
    api_request_latency_ms.labels(path=path, method=method).observe(latency_ms)
    if 500 <= response.status_code < 600:
        api_error_count.labels(path=path, method=method, status=str(response.status_code)).inc()
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Redis ─────────────────────────────────────────────────────────────────────
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
)


# ── Models ────────────────────────────────────────────────────────────────────
class AnalyseRequest(BaseModel):
    drug_a: str
    drug_b: str


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_cache(drug_a: str, drug_b: str) -> dict | None:
    key = f"cache:{':'.join(sorted([drug_a, drug_b]))}"
    try:
        val = redis_client.get(key)
        return json.loads(val) if val else None
    except Exception as e:
        logger.error("Redis cache get error: %s", e, extra={"trace_id": "none"})
        return None


def set_cache(drug_a: str, drug_b: str, data: dict):
    key = f"cache:{':'.join(sorted([drug_a, drug_b]))}"
    try:
        redis_client.setex(key, 3600, json.dumps(data))
    except Exception as e:
        logger.error("Redis cache set error: %s", e, extra={"trace_id": "none"})


def add_history(drug_a: str, drug_b: str, severity: str, confidence: float, explanation: str, sources: list):
    try:
        entry = json.dumps({
            "drug_a": drug_a,
            "drug_b": drug_b,
            "severity": severity,
            "confidence": confidence,
            "explanation": explanation,
            "sources": sources,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        redis_client.lpush("query_history", entry)
        redis_client.ltrim("query_history", 0, HISTORY_MAX - 1)
    except Exception as e:
        logger.error("Redis history error: %s", e, extra={"trace_id": "none"})


def get_history() -> list[dict]:
    try:
        items = redis_client.lrange("query_history", 0, -1)
        return [json.loads(i) for i in items]
    except Exception as e:
        logger.error("Redis history get error: %s", e, extra={"trace_id": "none"})
        return []


async def call_ml_service(drug_a: str, drug_b: str, trace_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.post(
                    f"{ML_SERVICE_URL}/predict",
                    json={"drug_a": drug_a, "drug_b": drug_b},
                )
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 503:
                    logger.warning("ML service not ready attempt %d", attempt, extra={"trace_id": trace_id})
                else:
                    logger.error("ML service error %d attempt %d", resp.status_code, attempt, extra={"trace_id": trace_id})
            except Exception as e:
                logger.error("ML service call error attempt %d: %s", attempt, e, extra={"trace_id": trace_id})
    raise HTTPException(status_code=503, detail="ML service unavailable")


# ── Stream generator ──────────────────────────────────────────────────────────
async def generate_stream(drug_a: str, drug_b: str, trace_id: str):
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})

    # check cache
    cached = get_cache(drug_a, drug_b)
    if cached:
        log.info("Cache hit for %s+%s", drug_a, drug_b)
        yield f"data: {json.dumps({'type': 'severity', 'data': cached['severity']})}\n\n"
        yield f"data: {json.dumps({'type': 'sources', 'data': cached.get('sources', [])})}\n\n"
        for token in cached.get("tokens", []):
            yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # call ml service
    try:
        ml_result = await call_ml_service(drug_a, drug_b, trace_id)
        severity = ml_result.get("severity", "Unknown")
        confidence = ml_result.get("confidence", 0.0)
        drug_pairs_analysed_total.inc()
        severity_class_distribution.labels(severity=severity).inc()
        log.info("ML result: %s+%s -> %s (%.2f)", drug_a, drug_b, severity, confidence)

        severity_data = {
            "label": severity,
            "confidence": confidence,
        }
        yield f"data: {json.dumps({'type': 'severity', 'data': severity_data})}\n\n"

    except HTTPException as e:
        yield f"data: {json.dumps({'type': 'error', 'data': e.detail})}\n\n"
        return

    # stream from genai service
    tokens = []
    sources = []

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{GENAI_SERVICE_URL}/explain",
                json={
                    "drug_a": drug_a,
                    "drug_b": drug_b,
                    "severity": severity,
                    "confidence": confidence,
                },
            ) as resp:
                if resp.status_code != 200:
                    log.error("GenAI service error %d", resp.status_code)
                    yield f"data: {json.dumps({'type': 'error', 'data': 'GenAI service error'})}\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                        event_type = event.get("type")

                        if event_type == "sources":
                            sources = event.get("data", [])
                            yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

                        elif event_type == "token":
                            token = event.get("data", "")
                            tokens.append(token)
                            yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                        elif event_type == "done":
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            break

                        elif event_type == "error":
                            yield f"data: {json.dumps({'type': 'error', 'data': event.get('data')})}\n\n"
                            return

                    except json.JSONDecodeError:
                        continue

    except Exception as e:
        log.error("GenAI stream error: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'data': 'GenAI streaming failed'})}\n\n"
        return

    # store cache and history
    explanation = "".join(tokens)
    set_cache(drug_a, drug_b, {
        "severity": severity_data,
        "tokens": tokens,
        "sources": sources,
    })
    add_history(drug_a, drug_b, severity, confidence, explanation, sources)
    log.info("Stream complete for %s+%s", drug_a, drug_b)


# ── Routes ────────────────────────────────────────────────────────────────────
async def fetch_drug_info_from_llm(drug_name: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{GENAI_SERVICE_URL}/drug-info",
                json={"name": drug_name},
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error("Drug info fetch error: %s", e, extra={"trace_id": "none"})
    return {"name": drug_name, "drugClass": "Unknown", "commonUses": "Unknown", "ai_generated": False}


@app.get("/drug-info")
async def drug_info(name: str):
    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Drug name too short")
    result = await fetch_drug_info_from_llm(name)
    return result


@app.post("/analyse")
async def analyse(req: AnalyseRequest):
    trace_id = str(uuid.uuid4())[:8]
    drug_a = req.drug_a.lower().strip()
    drug_b = req.drug_b.lower().strip()

    if not drug_a or not drug_b:
        raise HTTPException(status_code=400, detail="Both drug names are required")

    logger.info("Analyse request: %s+%s", drug_a, drug_b, extra={"trace_id": trace_id})

    return StreamingResponse(
        generate_stream(drug_a, drug_b, trace_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Trace-ID": trace_id,
        },
    )


@app.get("/history")
def history():
    return get_history()


@app.get("/health")
def health():
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok",
        "redis": redis_ok,
        "ml_service": ML_SERVICE_URL,
        "genai_service": GENAI_SERVICE_URL,
    }


