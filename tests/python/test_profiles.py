"""Phase 4 - per-project profiles and the tag-a-miss self-improvement loop.

Profiles the reviewer creates live in the writable workspace and override the
shipped defaults; capturing a missed identifier grows the project's gazetteer /
custom-regex rules AND records a labeled example for later NER fine-tuning.
"""

import pytest

from deid_engine import core, workspace as ws


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DICOMDEID_WORKSPACE", str(tmp_path / "ws"))
    yield


# --- per-project profiles --------------------------------------------------- #

def test_profiles_list_includes_shipped_default():
    ids = [p["id"] for p in core.profiles_list()]
    assert "default" in ids
    src = {p["id"]: p["source"] for p in core.profiles_list()}
    assert src["default"] == "shipped"


def test_clone_creates_a_workspace_profile_that_lists_and_loads():
    core.profile_clone("default", "cardio_ct", label="Cardiac CT")
    listed = {p["id"]: p for p in core.profiles_list()}
    assert "cardio_ct" in listed
    assert listed["cardio_ct"]["source"] == "workspace"
    prof = core.profile_get("cardio_ct")
    assert prof["profile_id"] == "cardio_ct"
    # inherits the shipped structure
    assert "text_detection" in prof


def test_workspace_profile_overrides_shipped_of_same_id():
    prof = core.profile_get("default")
    prof["dates"]["mode"] = "shift"
    core.profile_save("default", prof)
    assert core.profile_get("default")["dates"]["mode"] == "shift"
    assert core.profile_get("default")["_source"] == "workspace"


# --- tag-a-miss capture ----------------------------------------------------- #

def test_capture_to_gazetteer_grows_the_project_gazetteer_and_wires_the_profile():
    core.profile_clone("default", "proj")
    res = core.tag_capture("name", "Dr Muthusamy", profile_id="proj",
                           fix=["gazetteer"], context="Reported by Dr Muthusamy")
    # value landed in a workspace gazetteer
    gpath = ws.gazetteers_dir() / "proj_custom.txt"
    assert "Dr Muthusamy" in gpath.read_text(encoding="utf-8")
    # and the profile now references that gazetteer
    td = core.profile_get("proj")["text_detection"]
    assert str(gpath) in td["extra_gazetteer_files"]
    assert res["labeled"] is True


def test_capture_regex_appends_a_rule_that_the_built_scanner_applies():
    core.profile_clone("default", "proj")
    core.tag_capture("mrn", "MRN0099887", profile_id="proj",
                     fix=["regex"], pattern=r"\bMRN\d{6,}\b")
    prof = core.profile_get("proj")
    rules = prof["text_detection"]["custom_regex"]
    assert {"category": "mrn", "pattern": r"\bMRN\d{6,}\b", "score": 1.0} in rules
    # the scanner built from this profile actually catches a NEW mrn value
    scanner = core._build_scanner(prof["text_detection"], known_values=[])
    hits = [s for s in scanner.scan("see MRN0123456") if s.category == "mrn"]
    assert hits and hits[0].text == "MRN0123456"


def test_capture_regex_requires_a_pattern():
    core.profile_clone("default", "proj")
    with pytest.raises(ValueError):
        core.tag_capture("mrn", "x", profile_id="proj", fix=["regex"])


def test_capture_always_stores_a_labeled_example_even_without_a_rule_fix():
    core.tag_capture("name", "Dr Muthusamy", profile_id="default", fix=[])
    ex = ws.read_labeled_examples()
    assert len(ex) == 1
    assert ex[0]["value"] == "Dr Muthusamy"


# --- NER fine-tuning hook (stub) -------------------------------------------- #

def test_ner_export_writes_training_ready_jsonl_with_char_spans():
    core.tag_capture("name", "Dr Muthusamy", profile_id="default", fix=[],
                     context="Reported by Dr Muthusamy for the study")
    out = core.ner_export_examples()
    assert out["count"] == 1
    import json
    line = json.loads(open(out["path"], encoding="utf-8").read().splitlines()[0])
    s, e, label = line["entities"][0]
    assert line["text"][s:e] == "Dr Muthusamy"
    assert label == "NAME"
