"""
Unit test conftest — patches all heavy external dependencies in sys.modules
BEFORE any service module is imported.  Order matters: this file is loaded
by pytest before any test file in this directory.
"""
import os
import sys
import time as _time_module
from unittest.mock import MagicMock, AsyncMock
import numpy as np

# ── Environment defaults ───────────────────────────────────────────────────────
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("ML_SERVICE_URL", "http://ml-service:8001")
os.environ.setdefault("GENAI_SERVICE_URL", "http://genai-service:8002")
os.environ.setdefault("OPENAI_API_KEY", "sk-unit-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("MODEL_PATH", "/tmp/test_model.json")
os.environ.setdefault("LABEL_ENCODER_PATH", "/tmp/test_labels.json")
os.environ.setdefault("CHROMA_PATH", "/tmp/test_chroma")
os.environ.setdefault("MIN_SAMPLES", "10")
os.environ.setdefault("MAX_RETRIES", "1")
os.environ.setdefault("RETRY_DELAY", "0")

# ── Silence time.sleep (ModelService retry loops) ─────────────────────────────
_time_module.sleep = MagicMock(return_value=None)

# ── Fake embedding vector ──────────────────────────────────────────────────────
FAKE_EMB_768 = np.random.rand(768).astype(np.float32)
FAKE_EMB_LIST = FAKE_EMB_768.tolist()

# ── Mock: sentence_transformers ───────────────────────────────────────────────
_st_instance = MagicMock()
_st_instance.encode.return_value = FAKE_EMB_768
_st_mod = MagicMock()
_st_mod.SentenceTransformer.return_value = _st_instance
sys.modules["sentence_transformers"] = _st_mod

# ── Mock: xgboost ─────────────────────────────────────────────────────────────
_xgb_clf = MagicMock()
# predict_proba returns probabilities for 5 classes
_xgb_clf.predict_proba.return_value = np.array([[0.05, 0.10, 0.65, 0.15, 0.05]])
_xgb_clf.predict.return_value = np.array([2])
_xgb_clf.get_booster.return_value = MagicMock()  # for save_model
_xgb_mod = MagicMock()
_xgb_mod.XGBClassifier.return_value = _xgb_clf
sys.modules["xgboost"] = _xgb_mod

# ── Mock: chromadb ────────────────────────────────────────────────────────────
_chroma_coll = MagicMock()
_chroma_coll.count.return_value = 10
_chroma_coll.query.return_value = {
    "documents": [["Warfarin + aspirin increases bleeding risk via CYP2C9 inhibition."]],
    "metadatas": [[{"source": "openfda_events", "drug_a": "warfarin", "drug_b": "aspirin"}]],
}
_chroma_client_inst = MagicMock()
_chroma_client_inst.get_or_create_collection.return_value = _chroma_coll
_chroma_mod = MagicMock()
_chroma_mod.PersistentClient.return_value = _chroma_client_inst
sys.modules["chromadb"] = _chroma_mod
sys.modules["chromadb.config"] = MagicMock()

# ── Mock: confluent_kafka ─────────────────────────────────────────────────────
_kafka_msg = MagicMock()
_kafka_msg.error.return_value = None
_kafka_msg.value.return_value = b"{}"
_kafka_consumer_inst = MagicMock()
_kafka_consumer_inst.poll.return_value = None
_kafka_producer_inst = MagicMock()
_kafka_error_cls = MagicMock()
_kafka_error_cls._PARTITION_EOF = -191
_kafka_mod = MagicMock()
_kafka_mod.Consumer.return_value = _kafka_consumer_inst
_kafka_mod.Producer.return_value = _kafka_producer_inst
_kafka_mod.KafkaError = _kafka_error_cls
sys.modules["confluent_kafka"] = _kafka_mod

# ── Mock: redis ────────────────────────────────────────────────────────────────
# IMPORTANT: patch redis.Redis on the REAL module — do NOT replace sys.modules['redis'].
# Replacing the whole module breaks fakeredis, which subclasses redis.Connection
# (a real class) and raises a metaclass conflict when it finds a MagicMock instead.
import redis as _real_redis_module

_redis_inst = MagicMock()
_redis_inst.ping.return_value = True
_redis_inst.get.return_value = None
_redis_inst.setex.return_value = True
_redis_inst.lpush.return_value = 1
_redis_inst.ltrim.return_value = True
_redis_inst.lrange.return_value = []
_redis_inst.incr.return_value = 1

# Patch only the Redis class — real module stays in sys.modules intact
_real_redis_module.Redis = MagicMock(return_value=_redis_inst)

# ── Mock: prometheus_fastapi_instrumentator ────────────────────────────────────
# Prevents duplicate metric registration when multiple apps are imported.
_instr_inst = MagicMock()
_instr_inst.instrument.return_value = _instr_inst
_instr_inst.expose.return_value = _instr_inst
_instr_mod = MagicMock()
_instr_mod.Instrumentator.return_value = _instr_inst
sys.modules["prometheus_fastapi_instrumentator"] = _instr_mod

# ── Mock: openai ───────────────────────────────────────────────────────────────
_openai_resp = MagicMock()
_openai_resp.choices = [MagicMock()]
_openai_resp.choices[0].message.content = '{"name":"Warfarin","drugClass":"Anticoagulant","commonUses":"blood clots","is_drug":true}'
_openai_client_inst = MagicMock()
_openai_client_inst.chat = MagicMock()
_openai_client_inst.chat.completions = MagicMock()
_openai_client_inst.chat.completions.create = AsyncMock(return_value=_openai_resp)
_openai_mod = MagicMock()
_openai_mod.AsyncOpenAI.return_value = _openai_client_inst
sys.modules["openai"] = _openai_mod

# ── Mock: os.path.exists for model files ──────────────────────────────────────
_orig_exists = os.path.exists

def _patched_exists(path: str) -> bool:
    model_path = os.environ.get("MODEL_PATH", "/tmp/test_model.json")
    label_path = os.environ.get("LABEL_ENCODER_PATH", "/tmp/test_labels.json")
    if path in (model_path, label_path):
        return True
    return _orig_exists(path)

os.path.exists = _patched_exists

# ── Expose mocks as module-level names (imported by test files) ────────────────
MOCK_REDIS = _redis_inst
MOCK_ST = _st_instance
MOCK_XGB = _xgb_clf
MOCK_CHROMA_COLL = _chroma_coll
MOCK_OPENAI = _openai_client_inst
MOCK_KAFKA_CONSUMER = _kafka_consumer_inst
MOCK_KAFKA_PRODUCER = _kafka_producer_inst
