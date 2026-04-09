"""
Unit tests for api-gateway/main.py

Mocks: redis, httpx upstream calls, prometheus_fastapi_instrumentator
(all patched in tests/unit/conftest.py before this module loads)
"""
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

# ── Load gateway module under unique alias ────────────────────────────────────
from tests.conftest import load_service
_gw = load_service("gateway", "api-gateway/main.py")

# The redis instance our mock returns (see unit conftest.py)
_MOCK_REDIS = sys.modules["redis"].Redis.return_value

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_redis():
    """Reset redis mock state before each test."""
    _MOCK_REDIS.reset_mock()
    _MOCK_REDIS.get.return_value = None
    _MOCK_REDIS.lrange.return_value = []
    _MOCK_REDIS.ping.return_value = True
    _MOCK_REDIS.incr.return_value = 1
    _MOCK_REDIS.setex.side_effect = None
    _MOCK_REDIS.get.side_effect = None
    _MOCK_REDIS.lrange.side_effect = None
    _MOCK_REDIS.ping.side_effect = None
    yield


@pytest.fixture
def client():
    return TestClient(_gw.app)


# ═══════════════════════════════════════════════════════════════════════════════
# Cache helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCache:
    def test_cache_miss_returns_none(self):
        _MOCK_REDIS.get.return_value = None
        assert _gw.get_cache("warfarin", "aspirin") is None

    def test_cache_hit_deserializes_json(self):
        payload = {"severity": {"label": "Moderate", "confidence": 0.7}, "tokens": ["hello"], "sources": ["openfda"]}
        _MOCK_REDIS.get.return_value = json.dumps(payload)
        result = _gw.get_cache("warfarin", "aspirin")
        assert result == payload

    def test_cache_key_is_order_independent(self):
        """get_cache("a","b") and get_cache("b","a") must hit the same Redis key."""
        _MOCK_REDIS.get.return_value = None
        _gw.get_cache("aspirin", "warfarin")
        _gw.get_cache("warfarin", "aspirin")
        keys = [call[0][0] for call in _MOCK_REDIS.get.call_args_list]
        assert keys[0] == keys[1]

    def test_redis_error_returns_none(self):
        _MOCK_REDIS.get.side_effect = Exception("Redis down")
        assert _gw.get_cache("warfarin", "aspirin") is None


class TestSetCache:
    def test_uses_1_hour_ttl(self):
        data = {"severity": "Moderate", "tokens": [], "sources": []}
        _gw.set_cache("warfarin", "aspirin", data)
        _MOCK_REDIS.setex.assert_called_once()
        _, ttl, _ = _MOCK_REDIS.setex.call_args[0]
        assert ttl == 3600

    def test_serializes_to_json(self):
        data = {"severity": "Mild", "tokens": ["a", "b"], "sources": []}
        _gw.set_cache("warfarin", "aspirin", data)
        _, _, value = _MOCK_REDIS.setex.call_args[0]
        assert json.loads(value) == data

    def test_redis_error_is_silenced(self):
        _MOCK_REDIS.setex.side_effect = Exception("Redis down")
        # Should not raise
        _gw.set_cache("warfarin", "aspirin", {})


# ═══════════════════════════════════════════════════════════════════════════════
# History helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoryHelpers:
    def test_add_history_calls_lpush(self):
        _gw.add_history("warfarin", "aspirin", "Moderate", 0.7, "explanation text", ["openfda"])
        _MOCK_REDIS.lpush.assert_called_once()

    def test_add_history_trims_to_10(self):
        _gw.add_history("warfarin", "aspirin", "Moderate", 0.7, "text", [])
        _MOCK_REDIS.ltrim.assert_called_once_with("query_history", 0, 9)

    def test_add_history_entry_contains_expected_fields(self):
        _gw.add_history("warfarin", "aspirin", "Severe", 0.9, "explanation", ["src1"])
        call_args = _MOCK_REDIS.lpush.call_args[0]
        entry = json.loads(call_args[1])
        assert entry["drug_a"] == "warfarin"
        assert entry["drug_b"] == "aspirin"
        assert entry["severity"] == "Severe"
        assert entry["confidence"] == 0.9
        assert "timestamp" in entry

    def test_get_history_returns_empty_list_when_no_entries(self):
        _MOCK_REDIS.lrange.return_value = []
        assert _gw.get_history() == []

    def test_get_history_deserializes_all_entries(self):
        entries = [
            {"drug_a": "warfarin", "drug_b": "aspirin", "severity": "Moderate"},
            {"drug_a": "metformin", "drug_b": "insulin", "severity": "Mild"},
        ]
        _MOCK_REDIS.lrange.return_value = [json.dumps(e) for e in entries]
        result = _gw.get_history()
        assert result == entries

    def test_get_history_redis_error_returns_empty(self):
        _MOCK_REDIS.lrange.side_effect = Exception("Redis down")
        assert _gw.get_history() == []


# ═══════════════════════════════════════════════════════════════════════════════
# /health endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_redis_up(self, client):
        _MOCK_REDIS.ping.return_value = True
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["redis"] is True

    def test_redis_down(self, client):
        _MOCK_REDIS.ping.side_effect = Exception("Connection refused")
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["redis"] is False

    def test_contains_service_urls(self, client):
        resp = client.get("/health")
        body = resp.json()
        assert "ml_service" in body
        assert "genai_service" in body


# ═══════════════════════════════════════════════════════════════════════════════
# /history endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoryEndpoint:
    def test_empty(self, client):
        _MOCK_REDIS.lrange.return_value = []
        resp = client.get("/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_parsed_entries(self, client):
        entry = {"drug_a": "warfarin", "drug_b": "aspirin", "severity": "Moderate", "confidence": 0.7}
        _MOCK_REDIS.lrange.return_value = [json.dumps(entry)]
        resp = client.get("/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["drug_a"] == "warfarin"


# ═══════════════════════════════════════════════════════════════════════════════
# /drug-info endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestDrugInfoEndpoint:
    def test_name_too_short_returns_400(self, client):
        resp = client.get("/drug-info?name=a")
        assert resp.status_code == 400

    def test_empty_name_returns_400(self, client):
        resp = client.get("/drug-info?name=")
        assert resp.status_code in (400, 422)

    def test_valid_name_proxied_to_genai(self, client):
        with patch.object(_gw, "fetch_drug_info_from_llm", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {
                "name": "warfarin", "drugClass": "Anticoagulant",
                "commonUses": "Blood clots", "ai_generated": True, "is_drug": True,
            }
            resp = client.get("/drug-info?name=warfarin")
        assert resp.status_code == 200
        assert resp.json()["name"] == "warfarin"


# ═══════════════════════════════════════════════════════════════════════════════
# /analyse endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyseEndpoint:
    def test_empty_drug_a_returns_400(self, client):
        resp = client.post("/analyse", json={"drug_a": "", "drug_b": "aspirin"})
        assert resp.status_code == 400

    def test_empty_drug_b_returns_400(self, client):
        resp = client.post("/analyse", json={"drug_a": "warfarin", "drug_b": ""})
        assert resp.status_code == 400

    def test_cache_hit_streams_sse_without_ml_call(self, client):
        cached = {
            "severity": {"label": "Moderate", "confidence": 0.7},
            "tokens": ["clinical ", "explanation ", "here"],
            "sources": ["openfda_events"],
        }
        _MOCK_REDIS.get.return_value = json.dumps(cached)

        with patch.object(_gw, "call_ml_service", new_callable=AsyncMock) as mock_ml:
            with client.stream("POST", "/analyse", json={"drug_a": "warfarin", "drug_b": "aspirin"}) as resp:
                assert resp.status_code == 200
                content = resp.read().decode()

        # ML should NOT have been called on cache hit
        mock_ml.assert_not_called()
        # SSE events must be present
        assert "severity" in content
        assert "token" in content
        assert "done" in content

    def test_cache_hit_streams_all_event_types(self, client):
        cached = {
            "severity": {"label": "Severe", "confidence": 0.9},
            "tokens": ["token1", "token2"],
            "sources": ["openfda_events", "openfda_labels"],
        }
        _MOCK_REDIS.get.return_value = json.dumps(cached)
        events = []
        with client.stream("POST", "/analyse", json={"drug_a": "warfarin", "drug_b": "aspirin"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        types = {e["type"] for e in events}
        assert "severity" in types
        assert "sources" in types
        assert "token" in types
        assert "done" in types

    def test_ml_service_unavailable_yields_error_event(self, client):
        _MOCK_REDIS.get.return_value = None

        with patch.object(_gw, "call_ml_service", new_callable=AsyncMock) as mock_ml:
            mock_ml.side_effect = HTTPException(status_code=503, detail="ML service unavailable")
            with client.stream("POST", "/analyse", json={"drug_a": "warfarin", "drug_b": "aspirin"}) as resp:
                assert resp.status_code == 200
                content = resp.read().decode()

        assert "error" in content

    def test_drug_names_normalised_to_lowercase(self, client):
        _MOCK_REDIS.get.return_value = None
        captured = {}

        async def _fake_ml(drug_a, drug_b, trace_id):
            captured["drug_a"] = drug_a
            captured["drug_b"] = drug_b
            raise HTTPException(status_code=503, detail="stop")

        with patch.object(_gw, "call_ml_service", side_effect=_fake_ml):
            with client.stream("POST", "/analyse", json={"drug_a": "Warfarin", "drug_b": "ASPIRIN"}) as resp:
                resp.read()

        assert captured.get("drug_a") == "warfarin"
        assert captured.get("drug_b") == "aspirin"

    def test_result_cached_after_successful_stream(self, client):
        """set_cache should be called after a successful ML+GenAI stream."""
        _MOCK_REDIS.get.return_value = None

        ml_result = {"severity": "Mild", "confidence": 0.8}

        async def fake_ml(drug_a, drug_b, trace_id):
            return ml_result

        async def fake_genai_stream(*args, **kwargs):
            import httpx
            lines = [
                b'data: {"type": "sources", "data": ["openfda"]}\n\n',
                b'data: {"type": "token", "data": "text "}\n\n',
                b'data: {"type": "done"}\n\n',
            ]
            # Build a minimal mock async context manager
            mock_resp = AsyncMock()
            mock_resp.__aenter__.return_value = mock_resp
            mock_resp.__aexit__.return_value = False
            mock_resp.status_code = 200

            async def _aiter_lines():
                for ln in [
                    'data: {"type": "sources", "data": ["openfda"]}',
                    'data: {"type": "token", "data": "text "}',
                    'data: {"type": "done"}',
                ]:
                    yield ln

            mock_resp.aiter_lines = _aiter_lines
            return mock_resp

        mock_http_client = MagicMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.stream = MagicMock()

        # Just verify set_cache is called — complex stream wiring tested via cache hit path
        with patch.object(_gw, "set_cache") as mock_set:
            with patch.object(_gw, "call_ml_service", side_effect=fake_ml):
                import httpx
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_ctx = AsyncMock()
                    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
                    mock_ctx.__aexit__ = AsyncMock(return_value=False)
                    mock_ctx.status_code = 200

                    async def _lines():
                        for ln in [
                            'data: {"type": "sources", "data": ["openfda"]}',
                            'data: {"type": "token", "data": "text "}',
                            'data: {"type": "done"}',
                        ]:
                            yield ln

                    mock_ctx.aiter_lines = _lines
                    stream_ctx = MagicMock()
                    stream_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
                    stream_ctx.__aexit__ = AsyncMock(return_value=False)
                    mock_http = MagicMock()
                    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
                    mock_http.__aexit__ = AsyncMock(return_value=False)
                    mock_http.stream.return_value = stream_ctx
                    mock_client_cls.return_value = mock_http

                    with client.stream("POST", "/analyse", json={"drug_a": "warfarin", "drug_b": "aspirin"}) as resp:
                        resp.read()

            mock_set.assert_called_once()
