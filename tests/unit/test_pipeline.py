"""
Unit tests for data-pipeline/consumer.py and data-pipeline/producer.py

Tests: FeatureExtractor (CYP450 / severity), EventsHandler (drug pair extraction,
raw text building), KafkaProducerClient, build_event, build_feature_event.
No Kafka or Redis connections are made — all mocked via unit conftest.py.
"""
import sys
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from tests.conftest import load_service

_consumer = load_service("pipeline_consumer", "data-pipeline/consumer.py")
_producer_mod = load_service("pipeline_producer", "data-pipeline/producer.py")

_MOCK_REDIS = sys.modules["redis"].Redis.return_value


@pytest.fixture(autouse=True)
def reset_redis():
    _MOCK_REDIS.reset_mock()
    _MOCK_REDIS.ping.return_value = True
    _MOCK_REDIS.get.return_value = None
    _MOCK_REDIS.incr.return_value = 1
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# FeatureExtractor — CYP450 detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestCYP450Detection:
    def setup_method(self):
        self.extractor = _consumer.FeatureExtractor()

    def test_cyp3a4_keyword_detected(self):
        assert self.extractor.extract_cyp450_flag("drug metabolized via CYP3A4 pathway") is True

    def test_cytochrome_keyword_detected(self):
        assert self.extractor.extract_cyp450_flag("cytochrome P450 inhibitor present") is True

    def test_enzyme_inhibitor_detected(self):
        assert self.extractor.extract_cyp450_flag("potent enzyme inhibitor of hepatic enzymes") is True

    def test_p450_keyword_detected(self):
        assert self.extractor.extract_cyp450_flag("P450 mediated drug-drug interaction") is True

    def test_substrate_of_detected(self):
        assert self.extractor.extract_cyp450_flag("warfarin is substrate of CYP2C9") is True

    def test_no_relevant_keywords_returns_false(self):
        assert self.extractor.extract_cyp450_flag("patient experienced nausea and vomiting") is False

    def test_empty_text_returns_false(self):
        assert self.extractor.extract_cyp450_flag("") is False

    def test_case_insensitive(self):
        assert self.extractor.extract_cyp450_flag("CYP2D6 INHIBITOR") is True


# ═══════════════════════════════════════════════════════════════════════════════
# FeatureExtractor — Severity extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestSeverityExtraction:
    def setup_method(self):
        self.extractor = _consumer.FeatureExtractor()

    def test_death_maps_to_contraindicated(self):
        assert self.extractor.extract_severity("Seriousness: death") == "Contraindicated"

    def test_fatal_maps_to_contraindicated(self):
        assert self.extractor.extract_severity("fatal pulmonary toxicity observed") == "Contraindicated"

    def test_contraindicated_keyword(self):
        assert self.extractor.extract_severity("combination is contraindicated") == "Contraindicated"

    def test_hospitalization_maps_to_severe(self):
        assert self.extractor.extract_severity("Seriousness: hospitalization required") == "Severe"

    def test_black_box_maps_to_severe(self):
        assert self.extractor.extract_severity("boxed warning on label") == "Severe"

    def test_monitor_closely_maps_to_moderate(self):
        assert self.extractor.extract_severity("monitor closely for adverse effects") == "Moderate"

    def test_dose_adjustment_maps_to_moderate(self):
        assert self.extractor.extract_severity("dose adjustment may be necessary") == "Moderate"

    def test_caution_maps_to_mild(self):
        assert self.extractor.extract_severity("use with caution in elderly patients") == "Mild"

    def test_monitor_alone_maps_to_mild(self):
        assert self.extractor.extract_severity("may interact — monitor patient") == "Mild"

    def test_no_keywords_returns_none(self):
        assert self.extractor.extract_severity("patient took two medications") == "None"

    def test_empty_text_returns_none(self):
        assert self.extractor.extract_severity("") == "None"

    def test_contraindicated_takes_priority_over_severe(self):
        """Both 'death' and 'hospitalization' present — Contraindicated wins."""
        text = "Seriousness: death, hospitalization, life threatening"
        assert self.extractor.extract_severity(text) == "Contraindicated"


# ═══════════════════════════════════════════════════════════════════════════════
# EventsHandler — drug pair extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _make_report(suspect=None, interacting=None, concomitant=None):
    drugs = []
    for name in (suspect or []):
        drugs.append({
            "drugcharacterization": "1",
            "activesubstance": {"activesubstancename": name},
        })
    for name in (interacting or []):
        drugs.append({
            "drugcharacterization": "3",
            "activesubstance": {"activesubstancename": name},
        })
    for name in (concomitant or []):
        drugs.append({
            "drugcharacterization": "2",
            "activesubstance": {"activesubstancename": name},
        })
    return {"patient": {"drug": drugs, "reaction": []}}


class TestDrugPairExtraction:
    def setup_method(self):
        mock_client = MagicMock()
        self.handler = _producer_mod.EventsHandler(mock_client)

    def test_suspect_interacting_pair_extracted(self):
        report = _make_report(suspect=["warfarin"], interacting=["aspirin"])
        pairs = self.handler.extract_drug_pairs(report)
        assert ("warfarin", "aspirin") in pairs

    def test_multiple_suspects_multiple_interacting(self):
        report = _make_report(suspect=["warfarin", "digoxin"], interacting=["aspirin"])
        pairs = self.handler.extract_drug_pairs(report)
        assert len(pairs) == 2

    def test_no_duplicates(self):
        report = _make_report(suspect=["warfarin"], interacting=["aspirin"])
        pairs = self.handler.extract_drug_pairs(report)
        assert len(pairs) == len(set(pairs))

    def test_fallback_to_concomitant_when_no_interacting(self):
        report = _make_report(suspect=["warfarin"], concomitant=["metformin"])
        pairs = self.handler.extract_drug_pairs(report)
        assert ("warfarin", "metformin") in pairs

    def test_no_pairs_when_no_drugs(self):
        report = {"patient": {"drug": [], "reaction": []}}
        pairs = self.handler.extract_drug_pairs(report)
        assert pairs == []

    def test_short_drug_names_skipped(self):
        report = _make_report(suspect=["ab"], interacting=["cd"])  # < 3 chars
        pairs = self.handler.extract_drug_pairs(report)
        assert pairs == []

    def test_suspect_interacting_preferred_over_concomitant(self):
        """When interacting drugs exist, concomitant should NOT be used."""
        report = _make_report(
            suspect=["warfarin"],
            interacting=["aspirin"],
            concomitant=["metformin"],
        )
        pairs = self.handler.extract_drug_pairs(report)
        drug_names = [name for pair in pairs for name in pair]
        assert "metformin" not in drug_names


# ═══════════════════════════════════════════════════════════════════════════════
# EventsHandler — raw text building
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildRawText:
    def setup_method(self):
        mock_client = MagicMock()
        self.handler = _producer_mod.EventsHandler(mock_client)

    def test_reactions_included(self):
        report = {
            "patient": {
                "drug": [{"drugcharacterization": "1", "activesubstance": {"activesubstancename": "warfarin"}}],
                "reaction": [{"reactionmeddrapt": "Haemorrhage"}, {"reactionmeddrapt": "Anaemia"}],
            }
        }
        text = self.handler.build_raw_text(report, "warfarin", "aspirin")
        assert "Haemorrhage" in text
        assert "Anaemia" in text

    def test_seriousness_death_included(self):
        report = {
            "seriousnessdeath": "1",
            "patient": {"drug": [], "reaction": []},
        }
        text = self.handler.build_raw_text(report, "warfarin", "aspirin")
        assert "death" in text

    def test_seriousness_hospitalization_included(self):
        report = {
            "seriousnesshospitalization": "1",
            "patient": {"drug": [], "reaction": []},
        }
        text = self.handler.build_raw_text(report, "warfarin", "aspirin")
        assert "hospitalization" in text

    def test_max_length_2000(self):
        long_reactions = [{"reactionmeddrapt": "x" * 200} for _ in range(20)]
        report = {"patient": {"drug": [], "reaction": long_reactions}}
        text = self.handler.build_raw_text(report, "warfarin", "aspirin")
        assert len(text) <= 2000


# ═══════════════════════════════════════════════════════════════════════════════
# build_event / build_feature_event
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildEvent:
    def test_event_has_required_fields(self):
        event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "raw text")
        for key in ("event_id", "drug_a", "drug_b", "source", "raw_text", "timestamp"):
            assert key in event

    def test_event_id_is_uuid(self):
        event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "raw text")
        uuid.UUID(event["event_id"])  # raises ValueError if not UUID

    def test_event_drug_names(self):
        event = _producer_mod.build_event("warfarin", "aspirin", "openfda_labels", "text")
        assert event["drug_a"] == "warfarin"
        assert event["drug_b"] == "aspirin"
        assert event["source"] == "openfda_labels"


class TestBuildFeatureEvent:
    def test_feature_event_has_embeddings(self):
        raw_event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "raw")
        emb = [0.1] * 768
        event = _consumer.build_feature_event(raw_event, emb, emb, True, 5, "Moderate")
        assert "embedding_a" in event
        assert "embedding_b" in event
        assert len(event["embedding_a"]) == 768

    def test_feature_event_preserves_severity_and_flags(self):
        raw_event = _producer_mod.build_event("warfarin", "aspirin", "openfda_events", "raw")
        emb = [0.0] * 768
        event = _consumer.build_feature_event(raw_event, emb, emb, True, 3, "Severe")
        assert event["severity_label"] == "Severe"
        assert event["cyp450_flag"] is True
        assert event["pair_frequency"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# PairFrequencyTracker
# ═══════════════════════════════════════════════════════════════════════════════

class TestPairFrequencyTracker:
    def test_increment_returns_int(self):
        _MOCK_REDIS.incr.return_value = 7
        tracker = _consumer.PairFrequencyTracker()
        result = tracker.increment("warfarin", "aspirin")
        assert result == 7

    def test_increment_key_is_sorted(self):
        tracker = _consumer.PairFrequencyTracker()
        tracker.increment("aspirin", "warfarin")
        tracker.increment("warfarin", "aspirin")
        keys = [call[0][0] for call in _MOCK_REDIS.incr.call_args_list]
        assert keys[0] == keys[1]

    def test_get_frequency_returns_zero_when_missing(self):
        _MOCK_REDIS.get.return_value = None
        tracker = _consumer.PairFrequencyTracker()
        assert tracker.get_frequency("warfarin", "aspirin") == 0

    def test_get_frequency_returns_value(self):
        _MOCK_REDIS.get.return_value = "12"
        tracker = _consumer.PairFrequencyTracker()
        assert tracker.get_frequency("warfarin", "aspirin") == 12
