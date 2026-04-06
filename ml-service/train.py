import json
import logging
import os
import time
import uuid

import numpy as np
import redis
from confluent_kafka import Consumer, KafkaError
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
INPUT_TOPIC = "processed_features"
MODEL_PATH = os.getenv("MODEL_PATH", "/app/model/severity_classifier.onnx")
LABEL_ENCODER_PATH = os.getenv("LABEL_ENCODER_PATH", "/app/model/label_encoder.json")
MIN_SAMPLES = int(os.getenv("MIN_SAMPLES", 500))
GROUP_ID = f"ml-training-{uuid.uuid4()}"
# GROUP_ID = "ml-training-consumer"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "trainer", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)


def collect_training_data() -> list[dict]:
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": f"ml-training-{uuid.uuid4()}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([INPUT_TOPIC])
    logger.info(f"Collecting training data from {INPUT_TOPIC}")

    records = []
    empty_polls = 0
    max_empty_polls = 10
    max_records = 7000

    try:
        while empty_polls < max_empty_polls and len(records) < max_records:
            msg = consumer.poll(timeout=2.0)
            if msg is None:
                empty_polls += 1
                logger.info(f"Empty poll {empty_polls}/{max_empty_polls} — records so far: {len(records)}")
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    empty_polls += 1
                else:
                    logger.error(f"Kafka error: {msg.error()}")
                continue

            empty_polls = 0
            try:
                record = json.loads(msg.value().decode("utf-8"))
                records.append(record)
                if len(records) % 100 == 0:
                    logger.info(f"Collected {len(records)} records")
            except Exception as e:
                logger.error(f"Parse error: {e}")
    finally:
        consumer.close()

    logger.info(f"Total records collected: {len(records)}")
    return records


def build_features(records: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    skipped = 0

    for record in records:
        try:
            emb_a = record.get("embedding_a", [])
            emb_b = record.get("embedding_b", [])
            cyp450 = float(record.get("cyp450_flag", False))
            freq = float(record.get("pair_frequency", 0))
            label = record.get("severity_label", "None")

            if not emb_a or not emb_b:
                skipped += 1
                continue
            if label not in ["None", "Mild", "Moderate", "Severe", "Contraindicated"]:
                skipped += 1
                continue

            features = emb_a + emb_b + [cyp450, freq]
            X.append(features)
            y.append(label)
        except Exception as e:
            logger.error(f"Feature build error: {e}")
            skipped += 1

    logger.info(f"Built {len(X)} feature vectors, skipped {skipped}")
    return np.array(X, dtype=np.float32), np.array(y)


def train(X: np.ndarray, y: np.ndarray) -> tuple:
    logger.info(f"Training on {len(X)} samples")

    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    logger.info(f"Classes: {le.classes_.tolist()}")
    logger.info(f"Label distribution: { {c: int((y == c).sum()) for c in le.classes_} }")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded,
        test_size=0.2,
        random_state=42,
        stratify=y_encoded,
    )

    model = XGBClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    report = classification_report(y_test, y_pred, target_names=le.classes_)
    logger.info(f"Classification report:\n{report}")

    return model, le


def export_onnx(model, le, n_features: int):
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)

    # use XGBoost native ONNX export
    model.get_booster().save_model(MODEL_PATH)
    logger.info(f"XGBoost model saved to {MODEL_PATH}")

    with open(LABEL_ENCODER_PATH, "w") as f:
        json.dump(le.classes_.tolist(), f)
    logger.info(f"Label encoder saved to {LABEL_ENCODER_PATH}")


def run():
    while True:
        records = collect_training_data()

        if len(records) < MIN_SAMPLES:
            logger.warning(f"Only {len(records)} records, need {MIN_SAMPLES}. Waiting 60s...")
            time.sleep(60)
            continue

        X, y = build_features(records)

        if len(X) < MIN_SAMPLES:
            logger.warning(f"Only {len(X)} valid features. Waiting 60s...")
            time.sleep(60)
            continue

        model, le = train(X, y)
        export_onnx(model, le, X.shape[1])
        logger.info("Training complete. Retraining in 3600s...")
        time.sleep(3600)


if __name__ == "__main__":
    run()