"""Phase 2 - Presidio custom SG recognisers.

Pattern recognisers analyse without the spaCy NLP engine, so these run on the
air-gapped box even when no spaCy model is installed. They give Presidio the
Singapore-specific entities the default English models miss, with NRIC/FIN
checksum validation feeding the confidence score.
"""

import pytest

presidio = pytest.importorskip("presidio_analyzer")

from deid_engine import sg_recognizers as sg


def _analyze(recognizer, text):
    return recognizer.analyze(text=text, entities=recognizer.supported_entities,
                              nlp_artifacts=None)


def test_nric_recognizer_flags_checksum_valid_high():
    rec = sg.NricFinRecognizer()
    results = _analyze(rec, "NRIC S1234567D on file")
    assert len(results) == 1
    r = results[0]
    assert r.entity_type == "SG_NRIC_FIN"
    assert r.score >= 0.99


def test_nric_recognizer_validates_checksum():
    # Presidio is the high-precision layer: a valid check digit scores full, a
    # bad one is invalidated (dropped). The deterministic find_nric_fin layer
    # remains the recall net for wrong-checksum shapes.
    rec = sg.NricFinRecognizer()
    assert _analyze(rec, "S1234567D")[0].score >= 0.99
    assert _analyze(rec, "S1234567A") == []  # correct letter is D


def test_phone_recognizer_matches_sg_mobile():
    rec = sg.SgPhoneRecognizer()
    results = _analyze(rec, "call +65 9123 4567")
    assert len(results) == 1
    assert results[0].entity_type == "SG_PHONE"


def test_register_adds_recognisers_to_registry():
    from presidio_analyzer import RecognizerRegistry
    registry = RecognizerRegistry()
    before = len(registry.recognizers)
    sg.register_sg_recognizers(registry)
    entities = set()
    for rec in registry.recognizers:
        entities.update(rec.supported_entities)
    assert len(registry.recognizers) > before
    assert "SG_NRIC_FIN" in entities
    assert "SG_PHONE" in entities
