"""
Unit tests for genai-service/main.py and genai-service/rag.py

Tests: build_prompt, RAGService.retrieve, /drug-info, /explain
Mocks: openai, chromadb, sentence_transformers — all patched in unit conftest.py
"""
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
import numpy as np

from tests.conftest import load_service

_genai = load_service("genai_main", "genai-service/main.py")

_MOCK_OPENAI = sys.modules["openai"].AsyncOpenAI.return_value
_MOCK_CHROMA_COLL = (
    sys.modules["chromadb"]
    .PersistentClient.return_value
    .get_or_create_collection.return_value
)
_MOCK_ST = sys.modules["sentence_transformers"].SentenceTransformer.return_value

FAKE_EMB = np.random.rand(768).astype(np.float32)


@pytest.fixture(autouse=True)
def reset_mocks():
    _MOCK_ST.encode.return_value = FAKE_EMB
    _MOCK_CHROMA_COLL.reset_mock()
    _MOCK_CHROMA_COLL.count.return_value = 10
    _MOCK_CHROMA_COLL.query.return_value = {
        "documents": [["Warfarin + aspirin increases bleeding risk."]],
        "metadatas": [[{"source": "openfda_events", "drug_a": "warfarin", "drug_b": "aspirin"}]],
    }
    yield


@pytest.fixture
def client():
    return TestClient(_genai.app)


# ═══════════════════════════════════════════════════════════════════════════════
# build_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildPrompt:
    def test_contains_drug_names(self):
        prompt = _genai.build_prompt("warfarin", "aspirin", "Severe", 0.9, [])
        assert "warfarin" in prompt
        assert "aspirin" in prompt

    def test_contains_severity(self):
        prompt = _genai.build_prompt("warfarin", "aspirin", "Contraindicated", 0.95, [])
        assert "Contraindicated" in prompt

    def test_contains_confidence_formatted(self):
        prompt = _genai.build_prompt("warfarin", "aspirin", "Moderate", 0.75, [])
        assert "75%" in prompt

    def test_with_passages_includes_source(self):
        passages = [{"text": "CYP2C9 inhibition increases warfarin exposure.", "source": "openfda_labels"}]
        prompt = _genai.build_prompt("warfarin", "aspirin", "Severe", 0.9, passages)
        assert "openfda_labels" in prompt
        assert "CYP2C9" in prompt

    def test_no_passages_uses_fallback_text(self):
        prompt = _genai.build_prompt("warfarin", "aspirin", "Mild", 0.6, [])
        assert "No specific interaction data retrieved" in prompt

    def test_prompt_has_four_required_sections(self):
        prompt = _genai.build_prompt("warfarin", "aspirin", "Moderate", 0.7, [])
        for section in ["Mechanism", "Clinical consequences", "Recommended action", "Confidence caveat"]:
            assert section in prompt or section.lower() in prompt.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# RAGService.retrieve
# ═══════════════════════════════════════════════════════════════════════════════

class TestRAGRetrieve:
    def test_returns_list_of_passages(self):
        passages = _genai.rag_service.retrieve("warfarin", "aspirin")
        assert isinstance(passages, list)
        assert len(passages) >= 1

    def test_passage_has_text_and_source(self):
        passages = _genai.rag_service.retrieve("warfarin", "aspirin")
        for p in passages:
            assert "text" in p
            assert "source" in p

    def test_fallback_on_filtered_query_error(self):
        """If filtered query fails, RAG falls back to unfiltered query."""
        _MOCK_CHROMA_COLL.query.side_effect = [
            Exception("filter error"),  # first call (filtered) fails
            {  # second call (fallback) succeeds
                "documents": [["Fallback passage."]],
                "metadatas": [[{"source": "fallback", "drug_a": "", "drug_b": ""}]],
            },
        ]
        passages = _genai.rag_service.retrieve("warfarin", "aspirin")
        assert len(passages) >= 1
        _MOCK_CHROMA_COLL.query.side_effect = None

    def test_both_queries_fail_returns_empty(self):
        _MOCK_CHROMA_COLL.query.side_effect = Exception("total failure")
        passages = _genai.rag_service.retrieve("warfarin", "aspirin")
        assert passages == []
        _MOCK_CHROMA_COLL.query.side_effect = None

    def test_empty_chroma_result_returns_empty(self):
        _MOCK_CHROMA_COLL.query.return_value = {"documents": [[]], "metadatas": [[]]}
        passages = _genai.rag_service.retrieve("warfarin", "aspirin")
        assert passages == []


# ═══════════════════════════════════════════════════════════════════════════════
# /drug-info endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestDrugInfoEndpoint:
    def _make_openai_response(self, content: str):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        return resp

    def test_is_drug_true_returns_profile(self, client):
        payload = '{"name":"Warfarin","drugClass":"Anticoagulant","commonUses":"blood clots","is_drug":true}'
        _MOCK_OPENAI.chat.completions.create = AsyncMock(
            return_value=self._make_openai_response(payload)
        )
        resp = client.get("/drug-info?name=warfarin")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_drug"] is True
        assert body["ai_generated"] is True
        assert body["drugClass"] == "Anticoagulant"

    def test_is_drug_false_returns_not_medication(self, client):
        payload = '{"is_drug":false}'
        _MOCK_OPENAI.chat.completions.create = AsyncMock(
            return_value=self._make_openai_response(payload)
        )
        resp = client.get("/drug-info?name=rice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_drug"] is False
        assert body["drugClass"] == "Not a medication"

    def test_strips_markdown_code_block(self, client):
        payload = '```json\n{"name":"Aspirin","drugClass":"NSAID","commonUses":"pain","is_drug":true}\n```'
        _MOCK_OPENAI.chat.completions.create = AsyncMock(
            return_value=self._make_openai_response(payload)
        )
        resp = client.post("/drug-info", json={"name": "aspirin"})
        assert resp.status_code == 200
        assert resp.json()["drugClass"] == "NSAID"

    def test_openai_error_returns_unknown(self, client):
        _MOCK_OPENAI.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        resp = client.get("/drug-info?name=warfarin")
        assert resp.status_code == 200
        assert resp.json()["drugClass"] == "Unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# /explain endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestExplainEndpoint:
    def test_no_api_key_returns_500(self, client):
        import os
        original = os.environ.get("OPENAI_API_KEY")
        # Temporarily blank the key on the module
        original_key = _genai.OPENAI_API_KEY
        _genai.OPENAI_API_KEY = None  # type: ignore[assignment]
        try:
            resp = client.post("/explain", json={
                "drug_a": "warfarin", "drug_b": "aspirin",
                "severity": "Moderate", "confidence": 0.7,
            })
            assert resp.status_code == 500
        finally:
            _genai.OPENAI_API_KEY = original_key

    def test_explain_streams_sse_events(self, client):
        """Stream should emit: sources → token(s) → done."""
        # Build a mock async stream of chunks
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Warfarin and aspirin "
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "interact via CYP2C9."
        done_chunk = MagicMock()
        done_chunk.choices = [MagicMock()]
        done_chunk.choices[0].delta.content = None

        async def _stream():
            for c in [chunk1, chunk2, done_chunk]:
                yield c

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=_stream())
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        _MOCK_OPENAI.chat.completions.create = AsyncMock(return_value=_stream())

        resp = client.post("/explain", json={
            "drug_a": "warfarin", "drug_b": "aspirin",
            "severity": "Severe", "confidence": 0.9,
        })
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "sources" in content
        assert "done" in content


# ═══════════════════════════════════════════════════════════════════════════════
# /health endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_contains_chroma_count(self, client):
        _MOCK_CHROMA_COLL.count.return_value = 42
        resp = client.get("/health")
        assert resp.json()["chroma_count"] == 42
