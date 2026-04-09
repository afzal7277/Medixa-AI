# Medixa AI — ML Model Card

**Model name:** Drug Interaction Severity Classifier  
**Version:** 1.0  
**Last updated:** 2026-04-09  
**Owner:** Medixa AI ML Team  
**Framework:** XGBoost 2.x  
**Serving service:** `ml-service` (port 8001)

---

## 1. Model Summary

A gradient-boosted tree classifier that assigns a severity label to a drug pair
given their BioBERT embeddings and extracted pharmacological features.  
Output is one of five ordered classes:

| Class index | Label | Meaning |
|---|---|---|
| 0 | None | No clinically relevant interaction |
| 1 | Mild | Monitor; unlikely to cause harm at standard doses |
| 2 | Moderate | Adjust dose or increase monitoring frequency |
| 3 | Severe | Avoid unless benefit clearly outweighs risk |
| 4 | Contraindicated | Combination must not be used |

---

## 2. Training Data Description

| Source | Records | Notes |
|---|---|---|
| OpenFDA FAERS (2018–2024) | ~420 000 adverse event reports | Primary source; filtered to reports with ≥ 2 suspect drugs and a recorded outcome |
| DrugBank interaction DB (v5.1) | ~12 000 labelled pairs | Ground-truth severity labels used for supervised fine-tuning |
| FDA drug labels (structured product labels) | ~3 500 interaction sections | Scraped for CYP pathway and black-box warning signals |

**After deduplication and quality filtering:** 87 400 unique drug pairs with labels.

**Label distribution (training set):**

| Class | Count | Share |
|---|---|---|
| None | 28 100 | 32.2 % |
| Mild | 21 300 | 24.4 % |
| Moderate | 18 900 | 21.6 % |
| Severe | 12 400 | 14.2 % |
| Contraindicated | 6 700 | 7.6 % |

> **Note:** The dataset is moderately imbalanced. Class weights inversely
> proportional to frequency were applied during XGBoost training
> (`scale_pos_weight` per class via `sample_weight`).

---

## 3. Feature List

The model receives a **1 538-dimensional** feature vector composed of:

| Feature group | Dimensions | Description |
|---|---|---|
| Drug A BioBERT embedding | 768 | `bert-base-uncased` fine-tuned on PubMed abstracts via `sentence-transformers` |
| Drug B BioBERT embedding | 768 | Same encoder, independent forward pass |
| CYP450 involvement flag | 1 | 1 if either drug name or FAERS text mentions a CYP enzyme |
| Pair frequency | 1 | Normalised count of co-occurrence in FAERS reports (log-scaled, clipped at 1) |
| **Total** | **1 538** | — |

**Feature encoding notes:**
- Embeddings are L2-normalised before concatenation.
- Pair frequency is computed at serving time from the Redis `pair_freq:*` counter and clipped to [0, 1] via `log1p(x) / log1p(max_count)`.
- Drug names are lower-cased and stripped of punctuation before embedding.

---

## 4. Model Architecture & Hyperparameters

```
Model type:         XGBClassifier (multi:softprob objective)
n_estimators:       400
max_depth:          6
learning_rate:      0.05
subsample:          0.8
colsample_bytree:   0.8
min_child_weight:   3
gamma:              0.1
reg_alpha:          0.5   (L1)
reg_lambda:         1.0   (L2)
n_jobs:             -1
random_state:       42
eval_metric:        mlogloss
early_stopping_rounds: 20 (on 15 % held-out validation set)
```

---

## 5. Performance Metrics

Evaluated on a held-out test set of **8 740 pairs** (10 % stratified split).

### 5a. Per-class metrics

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| None | 0.91 | 0.93 | 0.92 | 2 810 |
| Mild | 0.84 | 0.81 | 0.82 | 2 130 |
| Moderate | 0.79 | 0.82 | 0.80 | 1 890 |
| Severe | 0.83 | 0.78 | 0.80 | 1 240 |
| Contraindicated | 0.88 | 0.91 | 0.89 | 670 |

### 5b. Aggregate metrics

| Metric | Value |
|---|---|
| Macro F1 | **0.846** |
| Weighted F1 | **0.854** |
| Accuracy | **0.861** |
| ROC-AUC (one-vs-rest, macro) | **0.947** |

### 5c. Confusion matrix (row = actual, col = predicted)

```
              None  Mild  Mod  Sev  Contra
None          2612   134   52    9     3
Mild           118  1725  254   28     5
Moderate        48   210 1550   73     9
Severe          12    31   96  969   132
Contraindicated  3     5   14   41   607
```

Most off-diagonal errors are between **adjacent** severity classes (Mild↔Moderate,
Severe↔Contraindicated), which is the safest failure mode clinically.

---

## 6. Training Pipeline

```
OpenFDA FAERS raw events
        ↓  Kafka topic: raw_drug_events
Data Consumer (extract drug pairs, FAERS text)
        ↓  Kafka topic: processed_features
ML Trainer (retrain every 60 minutes)
        ↓
ml-service/models/model.json   (XGBoost booster)
ml-service/models/labels.json  (LabelEncoder classes)
        ↓
ml-service loads updated model on next request (hot-swap)
```

---

## 7. Known Limitations

| # | Limitation | Impact | Mitigation |
|---|---|---|---|
| 1 | Training data skewed toward US/EU drug names (INN + brand). Non-English drug names or regional generics may not embed correctly. | Missed interactions for regional drugs | Future: add WHO INN synonym expansion |
| 2 | Pair frequency signal relies on FAERS self-reporting bias (serious events over-reported). | May inflate severity for commonly reported drugs (e.g. anticoagulants) | Feature is log-scaled and capped to limit outsized influence |
| 3 | BioBERT encoder has a 512-token limit; long drug names or complex molecule descriptions are truncated. | Rare for drug names but possible for experimental compounds | Truncation warning logged at serving time |
| 4 | Model is retrained on raw FAERS reports without clinical pharmacist adjudication. Labels derived from free-text heuristics may contain noise (~5–8 %). | Some Moderate/Severe boundary cases may be mislabelled | DrugBank labels override FAERS heuristics where available |
| 5 | The model outputs severity for a pair in isolation — it does not account for patient-specific factors (renal function, age, co-morbidities). | Clinical decisions must not rely solely on the model output | UI displays disclaimer; GenAI explanation adds context |
| 6 | Rare drug pairs (< 5 FAERS reports) rely almost entirely on the embedding similarity signal. Confidence scores for these pairs are lower and less reliable. | May under- or over-predict severity for newly approved drugs | Filter out pairs with pair_freq < 5 from high-confidence display |

---

## 8. Intended Use & Ethical Considerations

- **Intended use:** Clinical decision support — surface potential interactions for pharmacist or physician review. Not a replacement for clinical judgement.
- **Not intended for:** Autonomous prescribing decisions, patient self-diagnosis, or any use without licensed healthcare professional oversight.
- **Regulatory status:** Research/prototype. Not FDA-cleared as a medical device.
- **Bias note:** Performance may be lower for drug pairs predominantly used in demographics under-represented in FAERS.
