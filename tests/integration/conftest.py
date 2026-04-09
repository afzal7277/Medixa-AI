"""
Integration test conftest.
- fakeredis: in-process Redis compatible implementation (no Docker).
- testcontainers Kafka: requires Docker (tests marked @pytest.mark.integration).
"""
import os
import json
import pytest
import fakeredis

# ── Env for integration tests ─────────────────────────────────────────────────
os.environ.setdefault("OPENAI_API_KEY", "sk-integration-test")
os.environ.setdefault("ML_SERVICE_URL", "http://ml-service:8001")
os.environ.setdefault("GENAI_SERVICE_URL", "http://genai-service:8002")
os.environ.setdefault("MODEL_PATH", "/tmp/test_model.json")
os.environ.setdefault("LABEL_ENCODER_PATH", "/tmp/test_labels.json")


@pytest.fixture
def fake_redis():
    """A fresh fakeredis instance for each test."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def fake_redis_bytes():
    """fakeredis without decode_responses (for binary-compatible usage)."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server)
    yield client
    client.close()


@pytest.fixture
def kafka_container():
    """Real Kafka via testcontainers. Requires Docker. Mark tests with @pytest.mark.integration."""
    pytest.importorskip("testcontainers", reason="testcontainers not installed")
    try:
        from testcontainers.kafka import KafkaContainer
    except ImportError:
        pytest.skip("testcontainers[kafka] not available")

    try:
        container = KafkaContainer("confluentinc/cp-kafka:7.7.0")
        container.start()
    except Exception as e:
        pytest.skip(f"Docker not available (run with Docker socket mounted): {e}")

    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = container.get_bootstrap_server()
    yield container
    container.stop()
    os.environ.pop("KAFKA_BOOTSTRAP_SERVERS", None)
