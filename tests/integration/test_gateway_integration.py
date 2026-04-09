"""
Integration tests for api-gateway — uses fakeredis (no Docker).
Upstream ML / GenAI services are mocked with respx.

Run with:
    pytest tests/integration/test_gateway_integration.py -v
"""
import sys
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import fakeredis
import httpx

# ── Env before any import ─────────────────────────────────────────────────────
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("ML_SERVICE_URL", "http://ml-service:8001")
os.environ.setdefault("GENAI_SERVICE_URL", "http://genai-service:8002")
os.environ.setdefault("OPENAI_API_KEY", "sk-integration-test")

# ── Minimal sys.modules mocks needed to import gateway without heavy deps ─────
from unittest.mock import MagicMock
if "prometheus_fastapi_instrumentator" not in sys.modules:
    _instr = MagicMock()
    _instr_inst = MagicMock()
    _instr_inst.instrument.return_value = _instr_inst
    _instr_inst.expose.return_value = _instr_inst
    _instr.Instrumentator.return_value = _instr_inst
    sys.modules["prometheus_fastapi_instrumentator"] = _instr

# redis must be real for fakeredis patching to work
import redis as _real_redis  # noqa: F401 — ensure real redis is importable

from tests.conftest import load_service
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def fake_redis_server():
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis_client(fake_redis_server):
    client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    yield client
    client.flushall()
    client.close()


@pytest.fixture(scope="module")
def gateway_app(fake_redis_server):
    """Load gateway with redis_client replaced by a fakeredis instance."""
    # We need the module to load with a real-ish redis client, then swap it
    _gw = load_service("gw_integration", "api-gateway/main.py")
    return _gw


@pytest.fixture
def client(gateway_app, fake_redis_client):
    """TestClient with gateway's redis_client swapped for fakeredis."""
    gateway_app.redis_client = fake_redis_client
    return TestClient(gateway_app.app)


# ═══════════════════════════════════════════════════════════════════════════════
# /health
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthIntegration:
    def test_health_redis_ok(self, client, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["redis"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Redis cache integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheIntegration:
    def test_set_then_get_cache(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        data = {"severity": {"label": "Moderate", "confidence": 0.7}, "tokens": ["text"], "sources": ["openfda"]}
        gateway_app.set_cache("warfarin", "aspirin", data)
        result = gateway_app.get_cache("warfarin", "aspirin")
        assert result == data

    def test_cache_key_order_independent(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        data = {"severity": {"label": "Mild", "confidence": 0.5}, "tokens": [], "sources": []}
        gateway_app.set_cache("aspirin", "warfarin", data)
        result = gateway_app.get_cache("warfarin", "aspirin")
        assert result is not None
        assert result["severity"]["label"] == "Mild"

    def test_cache_ttl_applied(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        data = {"severity": {"label": "Severe", "confidence": 0.9}, "tokens": [], "sources": []}
        gateway_app.set_cache("warfarin", "aspirin", data)
        key = "cache:aspirin:warfarin"
        ttl = fake_redis_client.ttl(key)
        # TTL should be close to 3600 seconds
        assert 3500 < ttl <= 3600

    def test_cache_miss_returns_none(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        assert gateway_app.get_cache("unknown_drug", "other_drug") is None


# ═══════════════════════════════════════════════════════════════════════════════
# History integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoryIntegration:
    def test_add_and_get_history(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        fake_redis_client.delete("query_history")

        gateway_app.add_history("warfarin", "aspirin", "Moderate", 0.7, "explanation", ["openfda"])
        history = gateway_app.get_history()
        assert len(history) == 1
        assert history[0]["drug_a"] == "warfarin"
        assert history[0]["severity"] == "Moderate"

    def test_history_capped_at_10(self, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        fake_redis_client.delete("query_history")

        for i in range(15):
            gateway_app.add_history(f"drug_{i}", "aspirin", "Mild", 0.5, "exp", [])

        history = gateway_app.get_history()
        assert len(history) == 10

    def test_history_endpoint_returns_entries(self, client, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        fake_redis_client.delete("query_history")
        gateway_app.add_history("warfarin", "aspirin", "Severe", 0.9, "text", [])

        resp = client.get("/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["drug_a"] == "warfarin"


# ═══════════════════════════════════════════════════════════════════════════════
# /analyse — cache hit returns SSE without calling ML
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyseIntegration:
    def test_cache_hit_serves_without_ml(self, client, gateway_app, fake_redis_client):
        gateway_app.redis_client = fake_redis_client
        fake_redis_client.flushall()

        cached = {
            "severity": {"label": "Moderate", "confidence": 0.75},
            "tokens": ["interaction", " text"],
            "sources": ["openfda_events"],
        }
        gateway_app.set_cache("warfarin", "aspirin", cached)

        with patch.object(gateway_app, "call_ml_service", new_callable=AsyncMock) as mock_ml:
            with client.stream("POST", "/analyse", json={"drug_a": "warfarin", "drug_b": "aspirin"}) as resp:
                assert resp.status_code == 200
                events = []
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

        mock_ml.assert_not_called()
        types = {e["type"] for e in events}
        assert "severity" in types
        assert "done" in types

    def test_analyse_invalid_input_400(self, client):
        resp = client.post("/analyse", json={"drug_a": "", "drug_b": "aspirin"})
        assert resp.status_code == 400
