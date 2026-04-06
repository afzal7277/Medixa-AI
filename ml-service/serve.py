import json
import logging
import os
import time

import numpy as np
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from prometheus_fastapi_instrumentator import Instrumentator
from sentence_transformers import SentenceTransformer

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MODEL_PATH = os.getenv("MODEL_PATH", "/app/model/severity_classifier.onnx")
LABEL_ENCODER_PATH = os.getenv("LABEL_ENCODER_PATH", "/app/model/label_encoder.json")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 5))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "ml-serve", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Medixa ML Service")
Instrumentator().instrument(app).expose(app)


# ── Request/Response models ───────────────────────────────────────────────────
class PredictRequest(BaseModel):
    drug_a: str
    drug_b: str


class PredictResponse(BaseModel):
    severity: str
    confidence: float
    drug_a: str
    drug_b: str


# ── Embedding service ─────────────────────────────────────────────────────────
class EmbeddingService:
    def __init__(self):
        logger.info("Loading BioBERT model...")
        self.model = SentenceTransformer("dmis-lab/biobert-base-cased-v1.2")
        logger.info("BioBERT loaded")

    def embed(self, text: str) -> list[float]:
        try:
            return self.model.encode(text, normalize_embeddings=True).tolist()
        except Exception as e:
            logger.error(f"Embedding error for '{text}': {e}")
            return []


# ── Model service ─────────────────────────────────────────────────────────────
import xgboost as xgb

class ModelService:
    def __init__(self):
        self.model = None
        self.classes = []
        self._load()

    def _load(self):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if not os.path.exists(MODEL_PATH):
                    logger.warning(f"Model not found at {MODEL_PATH}, attempt {attempt}")
                    time.sleep(10)
                    continue
                self.model = xgb.XGBClassifier()
                self.model.load_model(MODEL_PATH)
                with open(LABEL_ENCODER_PATH) as f:
                    self.classes = json.load(f)
                logger.info(f"Model loaded. Classes: {self.classes}")
                return
            except Exception as e:
                logger.error(f"Model load error attempt {attempt}: {e}")
                time.sleep(10)
        logger.warning("Model not loaded — will retry on first request")

    def reload(self):
        self._load()

    def predict(self, features: np.ndarray) -> tuple[str, float]:
        if self.model is None:
            self.reload()
        if self.model is None:
            raise RuntimeError("Model not available")

        probs = self.model.predict_proba(features)[0]
        predicted_idx = int(np.argmax(probs))
        confidence = float(max(probs))
        severity = self.classes[predicted_idx]
        return severity, confidence


# ── Redis client ──────────────────────────────────────────────────────────────
class RedisClient:
    def __init__(self):
        self.client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
        )
        self._verify()

    def _verify(self):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.client.ping()
                logger.info("Redis connected")
                return
            except Exception as e:
                logger.error(f"Redis connection attempt {attempt} failed: {e}")
                time.sleep(RETRY_DELAY * attempt)
        raise RuntimeError("Redis connection failed")

    def get_pair_frequency(self, drug_a: str, drug_b: str) -> int:
        key = f"pair:{':'.join(sorted([drug_a, drug_b]))}"
        try:
            val = self.client.get(key)
            return int(val) if val else 0
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return 0

    def get_cyp450_flag(self, drug_a: str, drug_b: str) -> bool:
        key = f"cyp450:{':'.join(sorted([drug_a, drug_b]))}"
        try:
            val = self.client.get(key)
            return val == "1" if val else False
        except Exception as e:
            logger.error(f"Redis cyp450 get error: {e}")
            return False


# ── Startup ───────────────────────────────────────────────────────────────────
embedder = EmbeddingService()
model_service = ModelService()
redis_client = RedisClient()


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": model_service.session is not None,
        "classes": model_service.classes,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if model_service.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet — training in progress")

    try:
        drug_a = req.drug_a.lower().strip()
        drug_b = req.drug_b.lower().strip()

        embedding_a = embedder.embed(drug_a)
        embedding_b = embedder.embed(drug_b)

        if not embedding_a or not embedding_b:
            raise HTTPException(status_code=500, detail="Embedding generation failed")

        pair_frequency = redis_client.get_pair_frequency(drug_a, drug_b)
        cyp450_flag = redis_client.get_cyp450_flag(drug_a, drug_b)

        features = np.array(
            embedding_a + embedding_b + [float(cyp450_flag), float(pair_frequency)],
            dtype=np.float32,
        ).reshape(1, -1)

        severity, confidence = model_service.predict(features)

        logger.info(f"Predicted {drug_a}+{drug_b} -> {severity} ({confidence:.3f}) freq={pair_frequency} cyp450={cyp450_flag}")

        return PredictResponse(
            severity=severity,
            confidence=confidence,
            drug_a=drug_a,
            drug_b=drug_b,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))