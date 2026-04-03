from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json
import time
import random
import redis
import os

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis client (local)
# r = redis.Redis(host="localhost", port=6379, decode_responses=True)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

class AnalyseRequest(BaseModel):
    drug_a: str
    drug_b: str


def mock_severity():
    labels = ["none", "mild", "moderate", "severe", "contraindicated"]
    return random.choice(labels), round(random.uniform(0.6, 0.95), 2)


def generate_stream(drug_a: str, drug_b: str):
    cache_key = f"{drug_a}:{drug_b}".lower()

    # 🔥 1. CHECK CACHE
    cached = r.get(cache_key)

    if cached:
        data = json.loads(cached)

        yield f"data: {json.dumps({'type': 'severity', 'data': data['severity']})}\n\n"

        for token in data["tokens"]:
            yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # 🔥 2. MISS → GENERATE
    severity_label, confidence = mock_severity()

    severity_data = {
        "label": severity_label,
        "confidence": confidence,
    }

    yield f"data: {json.dumps({'type': 'severity', 'data': severity_data})}\n\n"

    explanation = f"The combination of {drug_a} and {drug_b} may increase risk of adverse effects such as bleeding or toxicity. Monitoring is advised."

    tokens = []

    for word in explanation.split():
        token = word + " "
        tokens.append(token)

        yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"
        time.sleep(0.05)

    yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # 🔥 3. STORE IN CACHE
    r.set(cache_key, json.dumps({
        "severity": severity_data,
        "tokens": tokens
    }))


@app.post("/analyse")
def analyse(req: AnalyseRequest):
    return StreamingResponse(
        generate_stream(req.drug_a, req.drug_b),
        media_type="text/event-stream"
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return {"message": "metrics placeholder"}