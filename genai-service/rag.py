import json
import logging
import os
import time

import chromadb
from chromadb.config import Settings
from confluent_kafka import Consumer, KafkaError
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CHROMA_PATH = os.getenv("CHROMA_PATH", "/app/chromadb")
TOP_K = int(os.getenv("TOP_K", 3))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "rag", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self):
        logger.info("Initializing ChromaDB...")
        self.client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.collection = self.client.get_or_create_collection(
            name="drug_interactions",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Loading embedding model...")
        self.embedder = SentenceTransformer("dmis-lab/biobert-base-cased-v1.2")
        logger.info("RAG service initialized")

    def populate(self):
        existing = self.collection.count()
        if existing > 0:
            logger.info(f"ChromaDB already has {existing} documents, skipping population")
            return

        logger.info("Populating ChromaDB from raw_drug_events...")
        consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": f"rag-populator",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        })
        consumer.subscribe(["raw_drug_events"])

        documents = []
        metadatas = []
        ids = []
        empty_polls = 0
        max_empty_polls = 10

        try:
            while empty_polls < max_empty_polls:
                msg = consumer.poll(timeout=2.0)
                if msg is None:
                    empty_polls += 1
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        empty_polls += 1
                    continue

                empty_polls = 0
                try:
                    event = json.loads(msg.value().decode("utf-8"))
                    raw_text = event.get("raw_text", "")
                    if not raw_text:
                        continue

                    documents.append(raw_text[:1000])
                    metadatas.append({
                        "drug_a": event.get("drug_a", ""),
                        "drug_b": event.get("drug_b", ""),
                        "source": event.get("source", ""),
                    })
                    ids.append(event.get("event_id", str(len(ids))))

                    if len(documents) % 100 == 0:
                        logger.info(f"Collected {len(documents)} documents")

                except Exception as e:
                    logger.error(f"Parse error: {e}")
        finally:
            consumer.close()

        if not documents:
            logger.warning("No documents collected for ChromaDB")
            return

        # batch insert
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i + batch_size]
            batch_meta = metadatas[i:i + batch_size]
            batch_ids = ids[i:i + batch_size]

            embeddings = self.embedder.encode(
                batch_docs,
                normalize_embeddings=True,
            ).tolist()

            self.collection.add(
                documents=batch_docs,
                embeddings=embeddings,
                metadatas=batch_meta,
                ids=batch_ids,
            )

        logger.info(f"ChromaDB populated with {self.collection.count()} documents")

    def retrieve(self, drug_a: str, drug_b: str, k: int = TOP_K) -> list[dict]:
        query = f"{drug_a} {drug_b} drug interaction"
        try:
            query_embedding = self.embedder.encode(
                query,
                normalize_embeddings=True,
            ).tolist()

            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                where={"$or": [
                    {"drug_a": drug_a},
                    {"drug_b": drug_b},
                    {"drug_a": drug_b},
                    {"drug_b": drug_a},
                ]},
            )

            passages = []
            if results and results.get("documents"):
                for i, doc in enumerate(results["documents"][0]):
                    meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                    passages.append({
                        "text": doc,
                        "source": meta.get("source", "unknown"),
                        "drug_a": meta.get("drug_a", ""),
                        "drug_b": meta.get("drug_b", ""),
                    })
            return passages

        except Exception as e:
            logger.error(f"Retrieval error for {drug_a}+{drug_b}: {e}")
            # fallback without filter
            try:
                query_embedding = self.embedder.encode(query, normalize_embeddings=True).tolist()
                results = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=k,
                )
                passages = []
                if results and results.get("documents"):
                    for i, doc in enumerate(results["documents"][0]):
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        passages.append({
                            "text": doc,
                            "source": meta.get("source", "unknown"),
                            "drug_a": meta.get("drug_a", ""),
                            "drug_b": meta.get("drug_b", ""),
                        })
                return passages
            except Exception as e2:
                logger.error(f"Fallback retrieval error: {e2}")
                return []