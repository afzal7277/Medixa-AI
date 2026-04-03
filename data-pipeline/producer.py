import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

import requests
from confluent_kafka import Producer
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
OPENFDA_BASE_URL = os.getenv("OPENFDA_BASE_URL", "https://api.fda.gov/drug")
PUBMED_BASE_URL = os.getenv("PUBMED_BASE_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils")
RXNORM_BASE_URL = os.getenv("RXNORM_BASE_URL", "https://rxnav.nlm.nih.gov/REST")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 60))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 5))
TOPIC = "raw_drug_events"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "producer", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

# ── Shutdown flag ─────────────────────────────────────────────────────────────
shutdown = False

def handle_shutdown(signum, frame):
    global shutdown
    logger.info("Shutdown signal received")
    shutdown = True

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


# ── HTTP base client ──────────────────────────────────────────────────────────
class BaseClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
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
                    logger.warning(f"Rate limited on {url}. Waiting {wait}s")
                    time.sleep(wait)
                else:
                    logger.error(f"HTTP {resp.status_code} for {url}")
                    return None
            except requests.exceptions.Timeout:
                logger.error(f"Timeout on attempt {attempt} for {url}")
                time.sleep(RETRY_DELAY * attempt)
            except requests.exceptions.ConnectionError:
                logger.error(f"Connection error on attempt {attempt} for {url}")
                time.sleep(RETRY_DELAY * attempt)
            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt} for {url}: {e}")
                time.sleep(RETRY_DELAY * attempt)
        logger.error(f"All {MAX_RETRIES} attempts failed for {url}")
        return None

    def close(self):
        self.session.close()


# ── RxNorm client ─────────────────────────────────────────────────────────────
class RxNormClient(BaseClient):
    def __init__(self):
        super().__init__(RXNORM_BASE_URL)

    def get_drug_names(self, drug_class: str) -> list[str]:
        data = self.get(f"/drugs.json", params={"name": drug_class})
        if not data:
            return []
        drugs = []
        try:
            drug_group = data.get("drugGroup", {}).get("conceptGroup", [])
            for group in drug_group:
                for concept in group.get("conceptProperties", []):
                    name = concept.get("synonym") or concept.get("name")
                    if name:
                        drugs.append(name.lower())
        except Exception as e:
            logger.error(f"RxNorm parse error for {drug_class}: {e}")
        return drugs

    def build_drug_pairs(self, drug_classes: list[str]) -> list[tuple[str, str]]:
        all_drugs = []
        for cls in drug_classes:
            names = self.get_drug_names(cls)
            logger.info(f"RxNorm: {len(names)} drugs found for class '{cls}'")
            all_drugs.extend(names[:5])  # cap per class to avoid explosion

        pairs = []
        seen = set()
        for i in range(len(all_drugs)):
            for j in range(i + 1, len(all_drugs)):
                a, b = all_drugs[i], all_drugs[j]
                key = tuple(sorted([a, b]))
                if key not in seen:
                    seen.add(key)
                    pairs.append((a, b))
        logger.info(f"RxNorm: built {len(pairs)} drug pairs")
        return pairs


# ── OpenFDA client ────────────────────────────────────────────────────────────
class OpenFDAClient(BaseClient):
    def __init__(self):
        super().__init__(OPENFDA_BASE_URL)

    def fetch_adverse_events(self, drug_a: str, drug_b: str, limit: int = 10) -> list[dict]:
        params = {
            "search": f'patient.drug.medicinalproduct:"{drug_a}"+AND+patient.drug.medicinalproduct:"{drug_b}"',
            "limit": limit,
        }
        data = self.get("/event.json", params=params)
        if not data:
            return []
        return data.get("results", [])

    def extract_raw_text(self, event: dict) -> str:
        parts = []
        for drug in event.get("patient", {}).get("drug", []):
            name = drug.get("medicinalproduct", "")
            indication = drug.get("drugindication", "")
            if name:
                parts.append(f"{name}: {indication}")
        reactions = [
            r.get("reactionmeddrapt", "")
            for r in event.get("patient", {}).get("reaction", [])
            if r.get("reactionmeddrapt")
        ]
        if reactions:
            parts.append("Reactions: " + ", ".join(reactions))
        return " | ".join(parts)


# ── PubMed client ─────────────────────────────────────────────────────────────
class PubMedClient(BaseClient):
    def __init__(self):
        super().__init__(PUBMED_BASE_URL)

    def search_ids(self, drug_a: str, drug_b: str, max_results: int = 5) -> list[str]:
        query = f"{drug_a} {drug_b} drug interaction"
        data = self.get("/esearch.fcgi", params={
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
        })
        if not data:
            return []
        return data.get("esearchresult", {}).get("idlist", [])

    def fetch_abstract(self, pmid: str) -> str:
        data = self.get("/efetch.fcgi", params={
            "db": "pubmed",
            "id": pmid,
            "rettype": "abstract",
            "retmode": "text",
        })
        if not data:
            return ""
        return str(data)[:1000]  # cap at 1000 chars


# ── Kafka producer ────────────────────────────────────────────────────────────
class KafkaProducerClient:
    def __init__(self):
        self.producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "retries": MAX_RETRIES,
            "retry.backoff.ms": RETRY_DELAY * 1000,
        })

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


# ── Drug classes for RxNorm discovery ────────────────────────────────────────
DRUG_CLASSES = [
    "warfarin", "aspirin", "metformin", "lisinopril",
    "atorvastatin", "amoxicillin", "ibuprofen", "omeprazole",
    "metoprolol", "amlodipine",
]


# ── Main loop ─────────────────────────────────────────────────────────────────
def run():
    rxnorm = RxNormClient()
    openfda = OpenFDAClient()
    pubmed = PubMedClient()
    kafka = KafkaProducerClient()

    logger.info("Producer started")

    try:
        while not shutdown:
            logger.info("Starting poll cycle")

            pairs = rxnorm.build_drug_pairs(DRUG_CLASSES)
            if not pairs:
                logger.warning("No drug pairs discovered from RxNorm, retrying next cycle")
                time.sleep(POLL_INTERVAL)
                continue

            for drug_a, drug_b in pairs:
                if shutdown:
                    break

                # OpenFDA
                try:
                    events = openfda.fetch_adverse_events(drug_a, drug_b)
                    for event in events:
                        raw_text = openfda.extract_raw_text(event)
                        if raw_text:
                            kafka.publish(build_event(drug_a, drug_b, "openfda", raw_text))
                except Exception as e:
                    logger.error(f"OpenFDA error for {drug_a}+{drug_b}: {e}")

                # PubMed
                try:
                    pmids = pubmed.search_ids(drug_a, drug_b)
                    for pmid in pmids:
                        abstract = pubmed.fetch_abstract(pmid)
                        if abstract:
                            kafka.publish(build_event(drug_a, drug_b, "pubmed", abstract))
                        time.sleep(0.4)  # respect 3 req/sec free tier
                except Exception as e:
                    logger.error(f"PubMed error for {drug_a}+{drug_b}: {e}")

                time.sleep(1)  # be polite to APIs

            kafka.flush()
            logger.info(f"Cycle complete. Sleeping {POLL_INTERVAL}s")
            time.sleep(POLL_INTERVAL)

    finally:
        rxnorm.close()
        openfda.close()
        pubmed.close()
        kafka.close()
        logger.info("Producer shut down cleanly")


if __name__ == "__main__":
    run()