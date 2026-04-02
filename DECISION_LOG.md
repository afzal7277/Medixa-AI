# Medixa AI — DECISION LOG

## Format

[DATE] Decision

* Context:
* Options Considered:
* Decision:
* Reason:
* Impact:

---

## Entry 1 — Project Strategy

* Context:
  Need to build within 5 days

* Options Considered:

1. Full production (Kafka, BioBERT)
2. Smart production (simplified, scalable later)

* Decision:
  Smart production

* Reason:
  Time constraint, faster iteration, lower failure risk

* Impact:
  Faster delivery, but infra less production-grade

---

## Entry 2 — Streaming Architecture

* Context:
  Spec suggests Kafka

* Options Considered:

1. Kafka
2. Redis Streams
3. Python async queue

* Decision:
  Python async queue (initial)

* Reason:
  Fastest setup, minimal infra overhead

* Impact:
  Not fully production-level, but easily replaceable

---

## Entry 3 — ML Model Choice

* Context:
  Need fast, interpretable model

* Options Considered:

1. Deep learning (BERT)
2. XGBoost
3. Logistic Regression

* Decision:
  XGBoost

* Reason:
  Handles tabular + embeddings, fast inference, strong baseline

* Impact:
  Faster training, easier debugging

---

## Entry 4 — Embeddings

* Context:
  Need text representation

* Options Considered:

1. BioBERT
2. Sentence-transformers

* Decision:
  Sentence-transformers

* Reason:
  Lightweight, fast, good enough for MVP

* Impact:
  Slightly lower accuracy vs BioBERT, but much faster

---

## Entry 5 — Vector DB

* Context:
  Need RAG retrieval

* Options Considered:

1. FAISS
2. ChromaDB
3. Qdrant

* Decision:
  ChromaDB

* Reason:
  Simple setup, good developer experience

* Impact:
  Quick integration

---
