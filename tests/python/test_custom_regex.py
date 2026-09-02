"""Phase 4 - a profile can carry custom regex rules (grown by tagging misses).
They become an extra scanner layer alongside the built-in SG recognisers.
"""

from deid_engine.textscan import TextScanner


def test_custom_regex_span_is_detected():
    sc = TextScanner(custom_regex=[
        {"category": "mrn", "pattern": r"\bMRN\d{6,}\b", "score": 1.0}])
    spans = sc.scan("study for MRN0099887 today")
    hits = [s for s in spans if s.category == "mrn"]
    assert len(hits) == 1
    assert hits[0].text == "MRN0099887"
    assert hits[0].source == "custom_regex"


def test_custom_regex_redacts():
    sc = TextScanner(custom_regex=[
        {"category": "case", "pattern": r"CASE-\d+"}])
    out, _ = sc.redact("ref CASE-4412 attached", replacement="[X]")
    assert out == "ref [X] attached"


def test_bad_custom_regex_degrades_with_a_note_not_a_crash():
    sc = TextScanner(custom_regex=[{"category": "oops", "pattern": r"("}])
    # scanner still usable; the broken rule is skipped and noted
    assert sc.scan("nothing here") == []
    assert any("custom regex" in n.lower() for n in sc.notes)


def test_custom_regex_default_score_when_omitted():
    sc = TextScanner(custom_regex=[{"category": "acc", "pattern": r"AC\d+"}])
    spans = sc.scan("AC123")
    assert spans and spans[0].score == 1.0
