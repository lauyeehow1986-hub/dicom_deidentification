"""Phase 2 - Presidio layer (gated on a spaCy model being installed).

Skips cleanly on a box without a spaCy model (the deterministic layers carry
detection there); runs where en_core_web_* is present to prove layer 4 adds
free-text person/location recall beyond the gazetteer.
"""

import pytest

spacy = pytest.importorskip("spacy")
if not spacy.util.get_installed_models():
    pytest.skip("no spaCy model installed", allow_module_level=True)

from deid_engine import textscan as ts


def test_presidio_layer_activates():
    sc = ts.TextScanner(use_presidio=True, use_ner=False)
    assert sc._analyzer is not None
    assert not any("presidio unavailable" in n for n in sc.notes)


def test_presidio_catches_free_name_without_gazetteer():
    # gazetteer OFF and the name is not a header token -> only layer 4 can catch it
    sc = ts.TextScanner(known_values=[], gazetteer=None,
                        use_presidio=True, use_ner=False)
    spans = sc.scan("Echo referred by Dr Muthusamy at the clinic")
    names = [s for s in spans if s.category == "name" and s.source == "presidio"]
    assert any("Muthusamy" in s.text for s in names)


def test_presidio_does_not_over_redact_clinical_terms():
    # spaCy tags "Cardiac" as ORGANIZATION; that is NOT a patient identifier and
    # must not be redacted out of ordinary descriptions.
    sc = ts.TextScanner(use_presidio=True, use_ner=False)
    redacted, spans = sc.redact("Cardiac ultrasound derived volume rendering")
    assert redacted == "Cardiac ultrasound derived volume rendering"
    assert spans == []


def test_presidio_sg_nric_recogniser_is_registered():
    sc = ts.TextScanner(use_presidio=True, use_ner=False)
    spans = sc.scan("patient NRIC S1234567D")
    assert any(s.category == "nric_fin" for s in spans)
