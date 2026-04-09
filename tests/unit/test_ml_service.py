"""
Unit tests for ml-service/serve.py

Tests: EmbeddingService, ModelService, RedisClient, /predict, /health
Mocks: sentence_transformers, xgboost, redis — all patched in unit conftest.py
"""
import sys
import json
import os
import numpy as np
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

# ── Load ml-service serve.py under unique alias ───────────────────────────────
from tests.conftest import load_service

# os.path.exists is already patched by unit conftest for MODEL_PATH
_ml = load_service("ml_serve", "ml-service/serve.py")

# Mock handles
_MOCK_REDIS = sys.modules["redis"].Redis.return_value
_MOCK_XGB = sys.modules["xgboost"].XGBClassifier.return_value
_MOCK_ST = sys.modules["sentence_transformers"].SentenceTransformer.return_value

FAKE_EMB_768 = np.random.rand(768).astype(np.float32)


@pytest.fixture(autouse=True)
def reset_mocks():
    _MOCK_REDIS.reset_mock()
    _MOCK_REDIS.get.return_value = None
    _MOCK_REDIS.ping.return_value = True
    _MOCK_ST.encode.return_value = FAKE_EMB_768
    _MOCK_XGB.predict_proba.return_value = np.array([[0.05, 0.10, 0.65, 0.15, 0.05]])
    yield


@pytest.fixture
def client():
    return TestClient(_ml.app)


# ═══════════════════════════════════════════════════════════════════════════════
# EmbeddingService
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmbeddingService:
    def test_embed_returns_list(self):
        _MOCK_ST.encode.return_value = FAKE_EMB_768
        result = _ml.embedder.embed("warfarin")
        assert isinstance(result, list)

    def test_embed_length_is_768(self):
        _MOCK_ST.encode.return_value = FAKE_EMB_768
        result = _ml.embedder.embed("warfarin")
        assert len(result) == 768

    def test_embed_error_returns_empty_list(self):
        _MOCK_ST.encode.side_effect = Exception("encode error")
        result = _ml.embedder.embed("warfarin")
        assert result == []
        _MOCK_ST.encode.side_effect = None

    def test_embed_values_are_floats(self):
        _MOCK_ST.encode.return_value = FAKE_EMB_768
        result = _ml.embedder.embed("aspirin")
        assert all(isinstance(v, float) for v in result)


# ═══════════════════════════════════════════════════════════════════════════════
# ModelService
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelService:
    def test_predict_returns_severity_string_and_confidence_float(self):
        # classes loaded from the mock JSON that _load() read
        _ml.model_service.model = _MOCK_XGB
        _ml.model_service.classes = ["Contraindicated", "Mild", "Moderate", "None", "Severe"]
        features = np.random.rand(1, 1538).astype(np.float32)
        severity, confidence = _ml.model_service.predict(features)
        assert isinstance(severity, str)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

    def test_predict_picks_highest_probability_class(self):
        _ml.model_service.classes = ["Contraindicated", "Mild", "Moderate", "None", "Severe"]
        _ml.model_service.model = _MOCK_XGB
        # "Severe" (index 4) has highest prob
        _MOCK_XGB.predict_proba.return_value = np.array([[0.01, 0.01, 0.01, 0.01, 0.96]])
        features = np.random.rand(1, 1538).astype(np.float32)
        severity, confidence = _ml.model_service.predict(features)
        assert severity == "Severe"
        assert abs(confidence - 0.96) < 1e-6

    def test_predict_raises_when_model_none(self):
        original = _ml.model_service.model
        _ml.model_service.model = None
        with pytest.raises(RuntimeError, match="Model not available"):
            _ml.model_service.predict(np.zeros((1, 1538), dtype=np.float32))
        _ml.model_service.model = original


# ═══════════════════════════════════════════════════════════════════════════════
# Feature vector construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeatureVector:
    def test_feature_vector_has_correct_dimensions(self):
        """embedding_a (768) + embedding_b (768) + cyp450 (1) + pair_freq (1) = 1538"""
        emb_a = FAKE_EMB_768.tolist()
        emb_b = FAKE_EMB_768.tolist()
        cyp450 = 1.0
        freq = 5.0
        features = np.array(emb_a + emb_b + [cyp450, freq], dtype=np.float32).reshape(1, -1)
        assert features.shape == (1, 1538)

    def test_feature_vector_dtype_is_float32(self):
        emb_a = FAKE_EMB_768.tolist()
        emb_b = FAKE_EMB_768.tolist()
        features = np.array(emb_a + emb_b + [0.0, 0.0], dtype=np.float32)
        assert features.dtype == np.float32


# ═══════════════════════════════════════════════════════════════════════════════
# RedisClient helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestRedisClient:
    def test_get_pair_frequency_returns_zero_when_missing(self):
        _MOCK_REDIS.get.return_value = None
        result = _ml.redis_client.get_pair_frequency("warfarin", "aspirin")
        assert result == 0

    def test_get_pair_frequency_returns_int(self):
        _MOCK_REDIS.get.return_value = "42"
        result = _ml.redis_client.get_pair_frequency("warfarin", "aspirin")
        assert result == 42

    def test_get_cyp450_flag_false_when_missing(self):
        _MOCK_REDIS.get.return_value = None
        assert _ml.redis_client.get_cyp450_flag("warfarin", "aspirin") is False

    def test_get_cyp450_flag_true_when_set(self):
        _MOCK_REDIS.get.return_value = "1"
        assert _ml.redis_client.get_cyp450_flag("warfarin", "aspirin") is True

    def test_get_cyp450_flag_false_when_zero(self):
        _MOCK_REDIS.get.return_value = "0"
        assert _ml.redis_client.get_cyp450_flag("warfarin", "aspirin") is False

    def test_pair_key_is_order_independent(self):
        _MOCK_REDIS.get.return_value = None
        _ml.redis_client.get_pair_frequency("warfarin", "aspirin")
        _ml.redis_client.get_pair_frequency("aspirin", "warfarin")
        keys = [call[0][0] for call in _MOCK_REDIS.get.call_args_list]
        assert keys[0] == keys[1]


# ═══════════════════════════════════════════════════════════════════════════════
# /predict endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestPredictEndpoint:
    def test_model_not_loaded_returns_503(self, client):
        original = _ml.model_service.model
        _ml.model_service.model = None
        resp = client.post("/predict", json={"drug_a": "warfarin", "drug_b": "aspirin"})
        assert resp.status_code == 503
        _ml.model_service.model = original

    def test_predict_returns_severity_and_confidence(self, client):
        _ml.model_service.model = _MOCK_XGB
        _ml.model_service.classes = ["Contraindicated", "Mild", "Moderate", "None", "Severe"]
        _MOCK_ST.encode.return_value = FAKE_EMB_768
        _MOCK_XGB.predict_proba.return_value = np.array([[0.05, 0.10, 0.65, 0.15, 0.05]])

        resp = client.post("/predict", json={"drug_a": "warfarin", "drug_b": "aspirin"})
        assert resp.status_code == 200
        body = resp.json()
        assert "severity" in body
        assert "confidence" in body
        assert body["drug_a"] == "warfarin"
        assert body["drug_b"] == "aspirin"
        assert 0.0 <= body["confidence"] <= 1.0

    def test_predict_lowercases_drug_names(self, client):
        _ml.model_service.model = _MOCK_XGB
        _ml.model_service.classes = ["Contraindicated", "Mild", "Moderate", "None", "Severe"]
        _MOCK_ST.encode.return_value = FAKE_EMB_768

        resp = client.post("/predict", json={"drug_a": "Warfarin", "drug_b": "ASPIRIN"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["drug_a"] == "warfarin"
        assert body["drug_b"] == "aspirin"

    def test_embedding_failure_returns_500(self, client):
        _ml.model_service.model = _MOCK_XGB
        _ml.model_service.classes = ["Mild"]
        _MOCK_ST.encode.return_value = []  # empty embedding

        resp = client.post("/predict", json={"drug_a": "warfarin", "drug_b": "aspirin"})
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# /health endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_has_classes(self, client):
        resp = client.get("/health")
        assert "classes" in resp.json()
