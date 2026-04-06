import json
import logging
import os
import uuid
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from rag import RAGService

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "genai", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Medixa GenAI Service")
Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ───────────────────────────────────────────────────────────────────
rag_service = RAGService()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


@app.on_event("startup")
async def startup():
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, rag_service.populate)
    logger.info("GenAI service ready")


# ── Models ────────────────────────────────────────────────────────────────────
class ExplainRequest(BaseModel):
    drug_a: str
    drug_b: str
    severity: str
    confidence: float


# ── Prompt builder ────────────────────────────────────────────────────────────
def build_prompt(drug_a: str, drug_b: str, severity: str, confidence: float, passages: list[dict]) -> str:
    context = ""
    if passages:
        context = "\n\n".join([
            f"Source [{i+1}] ({p['source']}):\n{p['text']}"
            for i, p in enumerate(passages)
        ])
    else:
        context = "No specific interaction data retrieved."

    return f"""You are a clinical pharmacology expert. Analyse the drug interaction between {drug_a} and {drug_b}.

The ML model classified this interaction as: {severity} (confidence: {confidence:.0%})

Retrieved clinical evidence:
{context}

Provide a structured explanation covering:
1. Mechanism of interaction — how do these drugs interact pharmacologically?
2. Clinical consequences — what are the potential adverse effects?
3. Recommended action — should this combination be avoided, monitored, or dose-adjusted?
4. Confidence caveat — note any limitations in this analysis.

Be concise, clinically accurate, and use plain language suitable for healthcare providers.
Do not repeat the severity classification — focus on the explanation.
"""


# ── Stream generator ──────────────────────────────────────────────────────────
async def stream_explanation(
    drug_a: str,
    drug_b: str,
    severity: str,
    confidence: float,
    trace_id: str,
) -> AsyncGenerator[str, None]:
    try:
        passages = rag_service.retrieve(drug_a, drug_b)
        logger.info(f"[{trace_id}] Retrieved {len(passages)} passages for {drug_a}+{drug_b}")

        sources = list(set([p["source"] for p in passages]))
        yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

        prompt = build_prompt(drug_a, drug_b, severity, confidence, passages)

        stream = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            max_tokens=600,
            temperature=0.3,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'type': 'token', 'data': delta})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        logger.info(f"[{trace_id}] Stream complete for {drug_a}+{drug_b}")

    except Exception as e:
        logger.error(f"[{trace_id}] Stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/explain")
async def explain(req: ExplainRequest):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")

    trace_id = str(uuid.uuid4())[:8]
    logger.info(f"[{trace_id}] Explain request: {req.drug_a}+{req.drug_b} severity={req.severity}")

    return StreamingResponse(
        stream_explanation(
            req.drug_a.lower().strip(),
            req.drug_b.lower().strip(),
            req.severity,
            req.confidence,
            trace_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Trace-ID": trace_id,
        },
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "chroma_count": rag_service.collection.count(),
    }


@app.get("/metrics")
def metrics_info():
    return {"status": "ok"}