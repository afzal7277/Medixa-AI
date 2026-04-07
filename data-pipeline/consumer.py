import json
import logging
import os
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone

import redis
from confluent_kafka import Consumer, KafkaError, Producer
from dotenv import load_dotenv
from prometheus_client import Counter, Gauge, start_http_server
from sentence_transformers import SentenceTransformer

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
INPUT_TOPIC = "raw_drug_events"
OUTPUT_TOPIC = "processed_features"
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 5))
GROUP_ID = "feature-engineering-consumer"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "consumer", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

# ── Shutdown ──────────────────────────────────────────────────────────────────
shutdown = False

def handle_shutdown(signum, frame):
    global shutdown
    logger.info("Shutdown signal received")
    shutdown = True

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# ── CYP450 keywords ───────────────────────────────────────────────────────────
CYP450_KEYWORDS = [
    "cyp450", "cyp3a4", "cyp2d6", "cyp2c9", "cyp2c19", "cyp1a2",
    "cyp2b6", "cyp2e1", "cytochrome", "enzyme inhibitor", "enzyme inducer",
    "p450", "hepatic metabolism", "metabolized by", "inhibits metabolism",
    "induces metabolism", "substrate of",
]

# ── Severity mapping ──────────────────────────────────────────────────────────
SEVERITY_MAP = {
    "Contraindicated": [
        "death", "fatal", "life threatening", "contraindicated",
        "do not use", "not recommended", "severe pulmonary toxicity",
        "fatal pulmonary", "bone marrow suppression",
    ],
    "Severe": [
        "hospitalization", "hospitalisation", "serious", "severe",
        "life-threatening", "intensive care", "emergency",
        "boxed warning", "black box",
    ],
    "Moderate": [
        "disabling", "disability", "congenital", "anomaly",
        "monitor closely", "dose adjustment", "significant interaction",
        "moderate interaction",
    ],
    "Mild": [
        "other serious", "caution", "mild interaction",
        "monitor", "use with caution", "may interact",
    ],
}


# ── Embedding service ─────────────────────────────────────────────────────────
class EmbeddingService:
    def __init__(self):
        logger.info("Loading BioBERT model...")
        self.model = SentenceTransformer("dmis-lab/biobert-base-cased-v1.2")
        logger.info("BioBERT model loaded")

    def embed(self, text: str) -> list[float]:
        try:
            embedding = self.model.encode(text, normalize_embeddings=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding error for '{text}': {e}")
            return []


# ── Feature extractor ─────────────────────────────────────────────────────────
class FeatureExtractor:
    def extract_cyp450_flag(self, raw_text: str) -> bool:
        text_lower = raw_text.lower()
        return any(keyword in text_lower for keyword in CYP450_KEYWORDS)

    def extract_severity(self, raw_text: str) -> str:
        text_lower = raw_text.lower()
        for severity, keywords in SEVERITY_MAP.items():
            if any(keyword in text_lower for keyword in keywords):
                return severity
        return "None"


# ── Redis pair frequency tracker ──────────────────────────────────────────────
class PairFrequencyTracker:
    def __init__(self):
        self.client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
        )
        self._verify_connection()

    def _verify_connection(self):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.client.ping()
                logger.info("Redis connected")
                return
            except Exception as e:
                logger.error(f"Redis connection attempt {attempt} failed: {e}")
                time.sleep(RETRY_DELAY * attempt)
        raise RuntimeError("Failed to connect to Redis after retries")

    def increment(self, drug_a: str, drug_b: str) -> int:
        key = f"pair:{':'.join(sorted([drug_a, drug_b]))}"
        try:
            return self.client.incr(key)
        except Exception as e:
            logger.error(f"Redis increment error for {key}: {e}")
            return 0

    def get_frequency(self, drug_a: str, drug_b: str) -> int:
        key = f"pair:{':'.join(sorted([drug_a, drug_b]))}"
        try:
            val = self.client.get(key)
            return int(val) if val else 0
        except Exception as e:
            logger.error(f"Redis get error for {key}: {e}")
            return 0


# ── Kafka consumer client ─────────────────────────────────────────────────────
class KafkaConsumerClient:
    def __init__(self):
        self.consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        })
        self.consumer.subscribe([INPUT_TOPIC])
        logger.info(f"Subscribed to {INPUT_TOPIC}")

        self.pipeline_consumer_lag = Gauge(
            "pipeline_consumer_lag",
            "Kafka consumer lag for raw drug events",
            ["topic", "group"],
        )

        self.pipeline_processed_events_total = Counter(
            "pipeline_processed_events_total",
            "Count of pipeline events processed",
            ["source"],
        )

    def poll(self, timeout: float = 1.0):
        return self.consumer.poll(timeout)

    def close(self):
        self.consumer.close()


# ── Kafka producer client ─────────────────────────────────────────────────────
class KafkaProducerClient:
    def __init__(self):
        self.producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "retry.backoff.ms": 1000,
            "retry.backoff.max.ms": 5000,
        })

    def delivery_report(self, err, msg):
        if err:
            logger.error(f"Kafka delivery failed: {err}")
        else:
            logger.info(f"Delivered to {msg.topic()} partition={msg.partition()} offset={msg.offset()}")

    def publish(self, event: dict):
        try:
            self.producer.produce(
                OUTPUT_TOPIC,
                key=f"{event['drug_a']}_{event['drug_b']}",
                value=json.dumps(event),
                callback=self.delivery_report,
            )
            self.producer.poll(0)
        except Exception as e:
            logger.error(f"Kafka publish error: {e}")

    def flush(self):
        self.producer.flush()

    def close(self):
        self.flush()


# ── Feature event builder ─────────────────────────────────────────────────────
def build_feature_event(
    raw_event: dict,
    embedding_a: list[float],
    embedding_b: list[float],
    cyp450_flag: bool,
    pair_frequency: int,
    severity_label: str,
) -> dict:
    return {
        "event_id": raw_event["event_id"],
        "drug_a": raw_event["drug_a"],
        "drug_b": raw_event["drug_b"],
        "embedding_a": embedding_a,
        "embedding_b": embedding_b,
        "cyp450_flag": cyp450_flag,
        "pair_frequency": pair_frequency,
        "severity_label": severity_label,
        "raw_text": raw_event["raw_text"],
        "source": raw_event["source"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Main loop ─────────────────────────────────────────────────────────────────
def run():
    embedder = EmbeddingService()
    extractor = FeatureExtractor()
    tracker = PairFrequencyTracker()
    consumer = KafkaConsumerClient()
    producer = KafkaProducerClient()

    logger.info("Consumer started")

    try:
        while not shutdown:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.info("Reached end of partition")
                else:
                    logger.error(f"Kafka error: {msg.error()}")
                continue

            try:
                raw_event = json.loads(msg.value().decode("utf-8"))

                drug_a = raw_event.get("drug_a", "")
                drug_b = raw_event.get("drug_b", "")
                raw_text = raw_event.get("raw_text", "")

                if not drug_a or not drug_b or not raw_text:
                    logger.warning(f"Skipping incomplete event {raw_event.get('event_id')}")
                    continue

                embedding_a = embedder.embed(drug_a)
                embedding_b = embedder.embed(drug_b)
                cyp450_flag = extractor.extract_cyp450_flag(raw_text)
                severity_label = extractor.extract_severity(raw_text)
                pair_frequency = tracker.increment(drug_a, drug_b)

                feature_event = build_feature_event(
                    raw_event,
                    embedding_a,
                    embedding_b,
                    cyp450_flag,
                    pair_frequency,
                    severity_label,
                )

                producer.publish(feature_event)
                self_topic = msg.topic()
                try:
                    low, high = consumer.consumer.get_watermark_offsets(self_topic, msg.partition(), cached=False)
                    lag = max(0, high - msg.offset() - 1)
                    consumer.pipeline_consumer_lag.labels(topic=self_topic, group=GROUP_ID).set(lag)
                except Exception:
                    pass
                consumer.pipeline_processed_events_total.labels(source=raw_event.get("source", "unknown")).inc()
                logger.info(f"Processed {drug_a} + {drug_b} severity={severity_label} cyp450={cyp450_flag} freq={pair_frequency}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except Exception as e:
                logger.error(f"Processing error: {e}")

    finally:
        consumer.close()
        producer.close()
        logger.info("Consumer shut down cleanly")


if __name__ == "__main__":
    start_http_server(8004)
    logger.info("Consumer metrics available at :8004/metrics")
    run()