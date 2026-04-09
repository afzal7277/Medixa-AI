# Medixa AI — Data Collection Report

**Report date:** 2026-04-09  
**Reporting period:** 2018-01-01 → 2024-12-31  
**Prepared by:** Medixa AI Data Team

---

## 1. Sources Overview

| Source | Type | Access method | Records fetched | Records used after filtering |
|---|---|---|---|---|
| OpenFDA FAERS | Adverse event reports | REST API (`api.fda.gov/drug/event`) | 1 847 392 | 420 114 |
| DrugBank (v5.1) | Curated interaction database | Licensed XML export | 24 011 pairs | 12 003 pairs |
| FDA Structured Product Labels | Drug labels (XML) | DailyMed bulk download | 18 440 labels | 3 512 labels |

**Total unique drug pairs extracted:** 134 820  
**Pairs with usable severity labels:** 87 400  
**Pairs discarded (insufficient data):** 47 420

---

## 2. OpenFDA FAERS

### 2a. Collection methodology

- Queried FAERS quarterly export files (Q1 2018 – Q4 2024) via the OpenFDA REST API.
- Filters applied:
  - `serious` = 1 (serious adverse event flag)
  - Minimum 2 drugs in `patient.drug` array
  - At least one `drugcharacterization` of `1` (suspect) or `2` (concomitant)
- Rate limit: 240 requests/minute; collection ran over ~3 days with exponential back-off.

### 2b. Record breakdown by year

| Year | Raw reports fetched | After deduplication | Suspect-pair events |
|---|---|---|---|
| 2018 | 198 441 | 187 320 | 44 210 |
| 2019 | 214 882 | 201 540 | 48 650 |
| 2020 | 231 107 | 216 890 | 52 100 |
| 2021 | 268 340 | 251 200 | 61 440 |
| 2022 | 295 611 | 278 900 | 67 800 |
| 2023 | 312 448 | 293 110 | 72 340 |
| 2024 | 326 563 | 306 740 | 73 574 |
| **Total** | **1 847 392** | **1 735 700** | **420 114** |

### 2c. Outcome type distribution

| Outcome code | Description | Count | Share |
|---|---|---|---|
| DE | Death | 38 104 | 9.1 % |
| HO | Hospitalisation (initial or prolonged) | 142 580 | 33.9 % |
| LT | Life-threatening | 61 230 | 14.6 % |
| DS | Disability | 29 810 | 7.1 % |
| CA | Congenital anomaly | 4 120 | 1.0 % |
| OT | Other serious | 144 270 | 34.3 % |

### 2d. Top 10 drug classes by report volume

| Rank | Drug class | Reports |
|---|---|---|
| 1 | Anticoagulants | 64 210 |
| 2 | Antineoplastics | 58 440 |
| 3 | Antidepressants (SSRIs/SNRIs) | 41 390 |
| 4 | Antidiabetics | 38 820 |
| 5 | Statins | 35 110 |
| 6 | Antibiotics | 32 680 |
| 7 | Immunosuppressants | 29 400 |
| 8 | Antiepileptics | 24 100 |
| 9 | Antihypertensives | 21 850 |
| 10 | Opioid analgesics | 19 230 |

---

## 3. DrugBank v5.1

### 3a. Collection methodology

- Source: Licensed XML export (academic license).
- Extracted all `<drug-interaction>` elements with a non-empty `<description>` field.
- Severity labels mapped from DrugBank's categorical field:
  - `major` → Contraindicated or Severe (split by presence of "contraindicated" keyword)
  - `moderate` → Moderate
  - `minor` → Mild

### 3b. Label distribution before and after mapping

| DrugBank label | Count | Mapped to |
|---|---|---|
| major (contraindicated keyword) | 3 214 | Contraindicated |
| major (other) | 4 108 | Severe |
| moderate | 8 822 | Moderate |
| minor | 7 867 | Mild |
| **Total** | **24 011** | — |

After removing pairs already present in FAERS (deduplication by sorted drug-name key): **12 003 pairs retained** as high-confidence ground truth.

### 3c. Quality notes

- DrugBank descriptions are manually curated but may reflect historical literature. Some interactions flagged as "major" predate newer clinical data that downgraded severity.
- ~320 pairs had conflicting labels between FAERS heuristic and DrugBank label. **DrugBank label was used as the authoritative source** in all conflicts.

---

## 4. FDA Structured Product Labels (SPL)

### 4a. Collection methodology

- Source: DailyMed full release download (ZIP, ~8 GB), April 2024 snapshot.
- Parsed XML to extract `<section>` elements with code `34073-7` (Drug Interactions section).
- Applied regex patterns to identify:
  - CYP enzyme mentions (`CYP3A4`, `CYP2D6`, `CYP2C9`, `CYP1A2`, `P-gp`)
  - Black-box warning keywords (`contraindicated`, `avoid concomitant`, `fatal`)
  - Severity language (`severe`, `serious`, `monitor closely`, `caution`)

### 4b. Signal extraction results

| Signal type | Labels extracted | Unique drug pairs |
|---|---|---|
| CYP450 interaction mentions | 9 841 | 3 512 |
| Black-box contraindication | 1 204 | 892 |
| "Avoid concomitant use" language | 2 388 | 1 731 |
| "Monitor closely" language | 4 912 | 2 840 |

These signals augment the feature vector (CYP450 flag) but do not directly provide severity labels — SPL text is used to enrich the pair frequency and CYP450 binary feature only.

---

## 5. Label Distribution — Final Training Dataset

After merging all sources, deduplication, and quality filtering:

| Severity class | Count | Share | Primary source |
|---|---|---|---|
| None | 28 100 | 32.2 % | FAERS (no adverse outcome) + DrugBank minor |
| Mild | 21 300 | 24.4 % | DrugBank minor + FAERS low-outcome |
| Moderate | 18 900 | 21.6 % | DrugBank moderate + FAERS hospitalisation |
| Severe | 12 400 | 14.2 % | DrugBank major + FAERS life-threatening |
| Contraindicated | 6 700 | 7.6 % | DrugBank major + FAERS death/black-box |
| **Total** | **87 400** | **100 %** | — |

**Split:** 70 % train / 15 % validation (early stopping) / 15 % test (held out, never seen during training)

---

## 6. Data Quality Issues Found

| # | Issue | Affected records | Resolution |
|---|---|---|---|
| 1 | Duplicate FAERS reports (same case ID, multiple quarters) | ~112 000 duplicates | Deduplicated by `safetyreportid`; kept the latest version |
| 2 | Misspelled drug names in FAERS free text (e.g. "Warfrin", "Aspirn") | ~18 000 records | Fuzzy matched to WHO INN list using rapidfuzz (threshold 0.85); unmatched names discarded |
| 3 | Drug names listed as brand name only with no INN equivalent in dataset | ~6 400 pairs | FDA NDC → INN lookup applied; 4 100 resolved, 2 300 discarded |
| 4 | Conflicting severity labels between FAERS heuristic and DrugBank | 320 pairs | DrugBank label used as ground truth (manually curated source preferred) |
| 5 | Very short drug names (≤ 2 chars) — likely coding artefacts | 840 records | Discarded |
| 6 | Pairs where both drugs are the same (self-pair artefact) | 1 240 records | Discarded |
| 7 | FAERS reports with > 20 suspect drugs (likely data entry errors) | 2 890 reports | Excluded from pair extraction (combinatorial explosion would create noise) |
| 8 | Missing outcome field in older FAERS records (pre-2019) | ~34 000 records | Outcome imputed as "OT" (other serious) if report flagged serious=1; otherwise discarded |

---

## 7. Kafka Pipeline Throughput (Production Simulation)

The data pipeline was load-tested with a 30-day simulated FAERS stream:

| Metric | Value |
|---|---|
| Peak ingestion rate | 420 events/second |
| Average processing latency (producer → consumer) | 180 ms |
| Events lost (consumer restart simulation) | 0 (Kafka offset commit after process) |
| Consumer group lag at peak | < 500 messages |
| Redis pair_freq keys written | 2 847 unique pairs |
| ML retrain triggers (1-hour cadence) | 720 over 30 days |

---

## 8. Recommendations for Next Data Cycle

1. **Add EMA EudraVigilance data** — European adverse event database; will improve coverage for EU-approved biologics.
2. **Include PubMed interaction abstracts** — NLP-extracted pairs from literature could add ~40 000 more labelled pairs.
3. **Adjudication by clinical pharmacist** — Sample 1 000 borderline Moderate/Severe pairs for human review to reduce label noise at the decision boundary.
4. **Temporal validation** — Evaluate model performance separately on 2023–2024 data to detect any distribution shift from newly approved drugs.
5. **Paediatric drug data** — Current FAERS extract is dominated by adult reports; paediatric interaction patterns may differ significantly.
