import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime, timezone

import requests
from confluent_kafka import Producer
from dotenv import load_dotenv
from prometheus_client import Counter, start_http_server

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
OPENFDA_BASE_URL = os.getenv("OPENFDA_BASE_URL", "https://api.fda.gov/drug")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 60))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 5))
TOPIC = "raw_drug_events"
PAGE_SIZE = 100

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "producer", "message": "%(message)s"}',
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


# ── Base HTTP client ──────────────────────────────────────────────────────────
class OpenFDAClient:
    def __init__(self):
        self.base_url = OPENFDA_BASE_URL
        self.session = requests.Session()

    def get(self, path: str, params: dict = None) -> dict | None:
        url = f"{self.base_url}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    logger.warning(f"404 for {url} params={params}")
                    return None
                elif resp.status_code == 429:
                    wait = RETRY_DELAY * attempt
                    logger.warning(f"Rate limited. Waiting {wait}s")
                    time.sleep(wait)
                else:
                    logger.error(f"HTTP {resp.status_code} for {url}")
                    return None
            except requests.exceptions.Timeout:
                logger.error(f"Timeout attempt {attempt} for {url}")
                time.sleep(RETRY_DELAY * attempt)
            except requests.exceptions.ConnectionError:
                logger.error(f"Connection error attempt {attempt} for {url}")
                time.sleep(RETRY_DELAY * attempt)
            except Exception as e:
                logger.error(f"Unexpected error attempt {attempt} for {url}: {e}")
                time.sleep(RETRY_DELAY * attempt)
        logger.error(f"All {MAX_RETRIES} attempts failed for {url}")
        return None

    def close(self):
        self.session.close()


# ── Events handler ────────────────────────────────────────────────────────────
class EventsHandler:
    def __init__(self, client: OpenFDAClient):
        self.client = client
        self.offset = 0

    def fetch_page(self) -> list[dict]:
        data = self.client.get("/event.json", params={
            "search": "serious:1",
            "limit": PAGE_SIZE,
            "skip": self.offset,
        })
        if not data:
            return []
        results = data.get("results", [])
        self.offset += PAGE_SIZE
        # reset offset after 10000 to stay within OpenFDA limits
        if self.offset >= 10000:
            self.offset = 0
            logger.info("Offset reset to 0")
        return results

    def extract_drug_pairs(self, report: dict) -> list[tuple[str, str]]:
        drugs = report.get("patient", {}).get("drug", [])
        suspect = []
        interacting = []
        concomitant = []

        for drug in drugs:
            characterization = str(drug.get("drugcharacterization", ""))
            name = (
                drug.get("activesubstance", {}).get("activesubstancename", "")
                or drug.get("medicinalproduct", "")
            ).lower().strip()

            if not name or len(name) < 3:
                continue

            if characterization == "1":
                suspect.append(name)
            elif characterization == "3":
                interacting.append(name)
            elif characterization == "2":
                concomitant.append(name)

        pairs = []
        seen = set()

        # suspect + interacting pairs first (strongest signal)
        for a in suspect:
            for b in interacting:
                key = tuple(sorted([a, b]))
                if key not in seen:
                    seen.add(key)
                    pairs.append((a, b))

        # suspect + concomitant if no interacting drugs found
        if not pairs:
            for a in suspect:
                for b in concomitant:
                    key = tuple(sorted([a, b]))
                    if key not in seen:
                        seen.add(key)
                        pairs.append((a, b))

        return pairs

    def build_raw_text(self, report: dict, drug_a: str, drug_b: str) -> str:
        parts = []

        reactions = [
            r.get("reactionmeddrapt", "")
            for r in report.get("patient", {}).get("reaction", [])
            if r.get("reactionmeddrapt")
        ]
        if reactions:
            parts.append("Reactions: " + ", ".join(reactions))

        for drug in report.get("patient", {}).get("drug", []):
            name = (
                drug.get("activesubstance", {}).get("activesubstancename", "")
                or drug.get("medicinalproduct", "")
            ).lower().strip()
            indication = drug.get("drugindication", "").strip()
            if name in (drug_a, drug_b) and indication:
                parts.append(f"{name} indication: {indication}")

        seriousness = []
        if report.get("seriousnessdeath") == "1":
            seriousness.append("death")
        if report.get("seriousnesslifethreatening") == "1":
            seriousness.append("life threatening")
        if report.get("seriousnesshospitalization") == "1":
            seriousness.append("hospitalization")
        if report.get("seriousnessdisabling") == "1":
            seriousness.append("disabling")
        if report.get("seriousnesscongenitalanomali") == "1":
            seriousness.append("congenital anomaly")
        if report.get("seriousnessother") == "1":
            seriousness.append("other serious condition")
        if seriousness:
            parts.append("Seriousness: " + ", ".join(seriousness))

        lit = report.get("primarysource", {}).get("literaturereference", "")
        if lit:
            parts.append(f"Reference: {lit}")

        return " | ".join(parts)[:2000]


# ── Labels handler ────────────────────────────────────────────────────────────
class LabelsHandler:
    def __init__(self, client: OpenFDAClient):
        self.client = client
        self.fetched = set()

    def fetch_label(self, drug_a: str, drug_b: str) -> str | None:
        cache_key = tuple(sorted([drug_a, drug_b]))
        if cache_key in self.fetched:
            return None
        self.fetched.add(cache_key)

        data = self.client.get("/label.json", params={
            "search": f'openfda.substance_name:"{drug_a}"',
            "limit": 1,
        })
        if not data:
            return None

        try:
            result = data.get("results", [])[0]
            parts = []

            interactions = result.get("drug_interactions", [])
            if interactions:
                parts.append("Drug interactions: " + " ".join(interactions)[:800])

            warnings = result.get("warnings", [])
            if warnings:
                parts.append("Warnings: " + " ".join(warnings)[:400])

            boxed = result.get("boxed_warning", [])
            if boxed:
                parts.append("Boxed warning: " + " ".join(boxed)[:400])

            return " | ".join(parts) if parts else None
        except (IndexError, Exception) as e:
            logger.error(f"Label parse error for {drug_a}: {e}")
            return None


# ── Kafka producer client ─────────────────────────────────────────────────────
class KafkaProducerClient:
    def __init__(self):
        self.producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "retries": MAX_RETRIES,
            "retry.backoff.ms": 1000,
            "retry.backoff.max.ms": 5000,
        })

        self.ingested_events_total = Counter(
            "data_ingestion_events_total",
            "Count of raw events ingested from each source",
            ["source"],
        )

    def delivery_report(self, err, msg):
        if err:
            logger.error(f"Kafka delivery failed: {err}")
        else:
            logger.info(f"Delivered to {msg.topic()} partition={msg.partition()} offset={msg.offset()}")

    def publish(self, event: dict):
        try:
            self.producer.produce(
                TOPIC,
                key=f"{event['drug_a']}_{event['drug_b']}",
                value=json.dumps(event),
                callback=self.delivery_report,
            )
            self.producer.poll(0)
            self.ingested_events_total.labels(source=event.get("source", "unknown")).inc()
        except Exception as e:
            logger.error(f"Kafka publish error: {e}")

    def flush(self):
        self.producer.flush()

    def close(self):
        self.flush()


# ── Canonical event builder ───────────────────────────────────────────────────
def build_event(drug_a: str, drug_b: str, source: str, raw_text: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "drug_a": drug_a,
        "drug_b": drug_b,
        "source": source,
        "raw_text": raw_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Main loop ─────────────────────────────────────────────────────────────────
def run():
    client = OpenFDAClient()
    events_handler = EventsHandler(client)
    labels_handler = LabelsHandler(client)
    kafka = KafkaProducerClient()

    logger.info("Producer started")

    try:
        while not shutdown:
            logger.info(f"Starting poll cycle — offset={events_handler.offset}")

            reports = events_handler.fetch_page()
            if not reports:
                logger.warning("No reports fetched, sleeping")
                time.sleep(POLL_INTERVAL)
                continue

            logger.info(f"Fetched {len(reports)} reports")
            published = 0

            for report in reports:
                if shutdown:
                    break

                pairs = events_handler.extract_drug_pairs(report)
                if not pairs:
                    continue

                for drug_a, drug_b in pairs:
                    # adverse event
                    raw_text = events_handler.build_raw_text(report, drug_a, drug_b)
                    if raw_text:
                        kafka.publish(build_event(drug_a, drug_b, "openfda_events", raw_text))
                        published += 1

                    # label for drug_a
                    label_text = labels_handler.fetch_label(drug_a, drug_b)
                    if label_text:
                        kafka.publish(build_event(drug_a, drug_b, "openfda_labels", label_text))
                        published += 1

                    time.sleep(0.5)

            kafka.flush()
            logger.info(f"Cycle complete. Published {published} events. Sleeping {POLL_INTERVAL}s")
            time.sleep(POLL_INTERVAL)

    finally:
        client.close()
        kafka.close()
        logger.info("Producer shut down cleanly")


if __name__ == "__main__":
    start_http_server(8003)
    logger.info("Producer metrics available at :8003/metrics")
    run()