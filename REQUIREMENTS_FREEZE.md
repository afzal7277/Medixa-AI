# Medixa AI — REQUIREMENTS FREEZE

## 1. Project Name

Medixa AI — Real-Time Drug Interaction Intelligence Platform

---

## 2. Problem Statement

Given two drugs, determine:

* Interaction severity (None / Mild / Moderate / Severe / Contraindicated)
* Explanation of interaction (mechanism, risk, recommendation)

---

## 3. Core Features (IN SCOPE)

### 3.1 Input

* Drug A (string)
* Drug B (string)

---

### 3.2 Output

#### ML Output (must come first)

* Severity label
* Confidence score

#### LLM Output (streamed)

* Mechanism of interaction
* Clinical impact
* Recommendation (avoid / monitor / adjust)
* Confidence caveat

---

### 3.3 System Capabilities

* Real-time response
* Streaming explanation (SSE)
* ML-based classification (NOT rule-based)
* RAG-based explanation

---

## 4. Technical Scope

### Backend

* FastAPI
* SSE (Server-Sent Events)

### ML

* XGBoost (initial)
* Feature-based classification

### RAG

* ChromaDB
* Embeddings (sentence-transformers)

### Data Sources

* OpenFDA (primary)
* PubMed (optional - later phase)

---

## 5. Constraints (STRICT)

* No prebuilt datasets
* ML must drive severity classification
* LLM must NOT decide severity
* System must simulate streaming (even if simplified)

---

## 6. Out of Scope (PHASE 1)

* Kafka (deferred)
* Full observability stack (Prometheus/Grafana)
* Large-scale dataset (5k+)
* BioBERT (heavy models)
* Production deployment

---

## 7. Performance Targets (Adjusted for 5 Days)

* ML response: < 1 second
* LLM streaming start: < 3 seconds
* End-to-end response: < 6 seconds

---

## 8. Success Criteria

* End-to-end working system
* ML model trained and evaluated
* RAG returns relevant context
* Streaming explanation visible
* Clean architecture + documentation

---

## 9. Freeze Rule

NO new features unless:

* Logged in DECISION_LOG.md
* Approved explicitly

---
