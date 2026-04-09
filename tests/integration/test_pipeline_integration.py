"""
Integration tests for the data pipeline.

Phase 1 (no Docker): tests feature extraction + fakeredis pair tracking.
Phase 2 (Docker):    tests Kafka produce → consume round-trip.
                     Requires: testcontainers[kafka] + Docker running.
                     Marked @pytest.mark.integration — skipped automatically
                     when Docker/testcontainers is unavailable.
"""
import sys
import os
import json
import time
import uuid
from unittest.mock import MagicMock
import pytest
import fakeredis
import numpy as np

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("MAX_RETRIES", "1")
os.environ.setdefault("RETRY_DELAY", "0")

# ── Mock heavy deps before pipeline import ────────────────────────────────────
from unittest.mock import MagicMock
import numpy as _np

if "sentence_transformers" not in sys.modules:
    _st_inst = MagicMock()
    _st_inst.encode.return_value = _np.random.rand(768).astype(_np.float32)
    _st_mod = MagicMock()
    _st_mod.SentenceTransformer.return_value = _st_inst
    sys.modules["sentence_transformers"] = _st_mod

if "confluent_kafka" not in sys.modules:
    _kafka_mod = MagicMock()
    _kafka_err = MagicMock()
    _kafka_err._PARTITION_EOF = -191
    _kafka_mod.KafkaError = _kafka_err
    sys.modules["confluent_kafka"] = _kafka_mod

if "prometheus_fastapi_instrumentator" not in sys.modules:
    _instr = MagicMock()
    _instr_i = MagicMock()
    _instr_i.instrument.return_value = _instr_i
    _instr_i.expose.return_value = _instr_i
    _instr.Instrumentator.return_value = _instr_i
    sys.modules["prometheus_fastapi_instrumentator"] = _instr

from tests.conftest import load_service

_consumer_mod = load_service("pipeline_consumer_int", "data-pipeline/consumer.py")
_producer_mod = load_service("pipeline_producer_int", "data-pipeline/producer.py")


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Feature extraction + fakeredis (no Docker)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_redis():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def tracker(fake_redis):
    """PairFrequencyTracker backed by fakeredis."""
    tracker = _consumer_mod.PairFrequencyTracker.__new__(_consumer_mod.PairFrequencyTracker)
    tracker.client = fake_redis
    return tracker


class TestPairFrequencyTrackerIntegration:
    def test_increment_starts_at_one(self, tracker):
        result = tracker.increment("warfarin", "aspirin")
        assert result == 1

    def test_increment_accumulates(self, tracker):
        tracker.increment("warfarin", "aspirin")
        tracker.increment("warfarin", "aspirin")
        result = tracker.increment("warfarin", "aspirin")
        assert result == 3

    def test_increment_key_order_independent(self, tracker):
        tracker.increment("aspirin", "warfarin")
        result = tracker.increment("warfarin", "aspirin")
        assert result == 2

    def test_get_frequency_after_increments(self, tracker):
        tracker.increment("warfarin", "aspirin")
        tracker.increment("warfarin", "aspirin")
        assert tracker.get_frequency("warfarin", "aspirin") == 2

    def test_different_pairs_tracked_independently(self, tracker):
        tracker.increment("warfarin", "aspirin")
        tracker.increment("metformin", "insulin")
        assert tracker.get_frequency("warfarin", "aspirin") == 1
        assert tracker.get_frequency("metformin", "insulin") == 1


class TestFeatureExtractionIntegration:
    """Test the full feature extraction pipeline using real Python objects (no Kafka/Redis containers)."""

    def test_full_feature_extraction_from_raw_event(self, tracker):
        raw_event = _producer_mod.build_event(
            "warfarin", "aspirin",
            "openfda_events",
            "Seriousness: death | Reactions: Haemorrhage | warfarin inhibits cyp2c9 metabolism",
        )

        extractor = _consumer_mod.FeatureExtractor()
        embedder = _consumer_mod.EmbeddingService()

        cyp450 = extractor.extract_cyp450_flag(raw_event["raw_text"])
        severity = extractor.extract_severity(raw_event["raw_text"])
        freq = tracker.increment(raw_event["drug_a"], raw_event["drug_b"])
        emb_a = embedder.embed(raw_event["drug_a"])
        emb_b = embedder.embed(raw_event["drug_b"])

        feature_event = _consumer_mod.build_feature_event(
            raw_event, emb_a, emb_b, cyp450, freq, severity
        )

        assert cyp450 is True
        assert severity == "Contraindicated"
        assert freq == 1
        assert len(feature_event["embedding_a"]) == 768
        assert feature_event["severity_label"] == "Contraindicated"
        assert feature_event["cyp450_flag"] is True

    def test_no_interaction_keywords_severity_none(self):
        extractor = _consumer_mod.FeatureExtractor()
        text = "Patient received two medications without known issues."
        assert extractor.extract_severity(text) == "None"
        assert extractor.extract_cyp450_flag(text) is False


class TestProducerEventBuilding:
    def test_build_event_structure(self):
        event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "raw text here")
        assert event["drug_a"] == "warfarin"
        assert event["drug_b"] == "aspirin"
        assert event["source"] == "openfda_events"
        assert event["raw_text"] == "raw text here"
        assert "timestamp" in event
        uuid.UUID(event["event_id"])  # validates UUID format

    def test_labels_handler_deduplication(self):
        """LabelsHandler should not fetch the same pair twice."""
        mock_client = MagicMock()
        mock_client.get.return_value = None
        handler = _producer_mod.LabelsHandler(mock_client)

        handler.fetch_label("warfarin", "aspirin")
        handler.fetch_label("warfarin", "aspirin")  # second call — should skip

        # get() only called once (first fetch), second is deduplicated
        assert mock_client.get.call_count <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Kafka round-trip (Docker required)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestKafkaRoundTrip:
    """Requires Docker. Skipped automatically if testcontainers is unavailable."""

    def test_producer_publishes_message(self, kafka_container):
        from confluent_kafka import Producer, Consumer, KafkaError

        bootstrap = kafka_container.get_bootstrap_server()
        topic = "test_raw_drug_events"

        producer = Producer({"bootstrap.servers": bootstrap})
        delivered = []

        def on_delivery(err, msg):
            if not err:
                delivered.append(msg)

        event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "test text")
        producer.produce(topic, key="warfarin_aspirin", value=json.dumps(event), callback=on_delivery)
        producer.flush(timeout=10)

        assert len(delivered) == 1

    def test_consumer_reads_producer_message(self, kafka_container):
        from confluent_kafka import Producer, Consumer, KafkaError

        bootstrap = kafka_container.get_bootstrap_server()
        topic = "test_roundtrip"

        # Produce
        producer = Producer({"bootstrap.servers": bootstrap})
        event = _producer_mod.build_event("metformin", "insulin", "openfda_events", "interaction text")
        producer.produce(topic, value=json.dumps(event))
        producer.flush(timeout=10)

        # Consume
        consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": f"test-{uuid.uuid4()}",
            "auto.offset.reset": "earliest",
        })
        consumer.subscribe([topic])

        received = []
        deadline = time.time() + 15
        while time.time() < deadline and not received:
            msg = consumer.poll(1.0)
            if msg and not msg.error():
                received.append(json.loads(msg.value()))

        consumer.close()
        assert len(received) == 1
        assert received[0]["drug_a"] == "metformin"
        assert received[0]["drug_b"] == "insulin"
