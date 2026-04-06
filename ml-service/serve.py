import json
import logging
import os
import time

import numpy as np
import redis
import onnxruntime as rt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MODEL_PATH = os.getenv("MODEL_PATH", "/app/model/severity_classifier.onnx")
LABEL_ENCODER_PATH = os.getenv("LABEL_ENCODER_PATH", "/app/model/label_encoder.json")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "ml-serve", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Medixa ML Service")


# ── Request/Response models ───────────────────────────────────────────────────
class PredictRequest(BaseModel):
    drug_a: str
    drug_b: str
    embedding_a: list[float]
    embedding_b: list[float]
    cyp450_flag: bool = False
    pair_frequency: int = 0


class PredictResponse(BaseModel):
    severity: str
    confidence: float


# ── Model loader ──────────────────────────────────────────────────────────────
class ModelService:
    def __init__(self):
        self.session = None
        self.classes = []
        self._load()

    def _load(self):
        for attempt in range(1, 4):
            try:
                if not os.path.exists(MODEL_PATH):
                    logger.warning(f"Model not found at {MODEL_PATH}, attempt {attempt}")
                    time.sleep(10)
                    continue
                self.session = rt.InferenceSession(MODEL_PATH)
                with open(LABEL_ENCODER_PATH) as f:
                    self.classes = json.load(f)
                logger.info("Model loaded successfully")
                return
            except Exception as e:
                logger.error(f"Model load error attempt {attempt}: {e}")
                time.sleep(10)
        logger.error("Failed to load model after retries")

    def predict(self, features: np.ndarray) -> tuple[str, float]:
        if self.session is None:
            raise RuntimeError("Model not loaded")
        input_name = self.session.get_inputs()[0].name
        output = self.session.run(None, {input_name: features})
        predicted_idx = int(np.argmax(output[1][0].values()))
        confidence = float(max(output[1][0].values()))
        severity = self.classes[predicted_idx]
        return severity, confidence


model_service = ModelService()
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": model_service.session is not None,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if model_service.session is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    try:
        features = np.array(
            req.embedding_a + req.embedding_b + [float(req.cyp450_flag), float(req.pair_frequency)],
            dtype=np.float32,
        ).reshape(1, -1)

        severity, confidence = model_service.predict(features)
        logger.info(f"Predicted {req.drug_a}+{req.drug_b} -> {severity} ({confidence:.3f})")
        return PredictResponse(severity=severity, confidence=confidence)

    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
def metrics():
    return {"status": "ok"}