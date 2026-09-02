"""Phase 4 - writable workspace (per-project profiles, custom gazetteers,
labeled-example store) kept OUTSIDE the shipped, read-only package.
"""

import json

import pytest

from deid_engine import workspace as ws


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DICOMDEID_WORKSPACE", str(tmp_path / "ws"))
    yield


def test_workspace_dir_honours_env_and_is_created():
    d = ws.workspace_dir()
    assert d.is_dir()
    assert d.name == "ws"


def test_append_gazetteer_is_case_insensitively_deduped():
    ws.append_gazetteer("proj_custom", "Muthusamy")
    ws.append_gazetteer("proj_custom", "muthusamy")   # dup (different case)
    ws.append_gazetteer("proj_custom", "Lim Wei")
    path = ws.gazetteers_dir() / "proj_custom.txt"
    names = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert names == ["Muthusamy", "Lim Wei"]


def test_labeled_example_round_trips_with_timestamp():
    ws.append_labeled_example({"category": "name", "value": "Dr Muthusamy"})
    got = ws.read_labeled_examples()
    assert len(got) == 1
    assert got[0]["value"] == "Dr Muthusamy"
    assert got[0]["category"] == "name"
    assert "ts" in got[0]          # stamped automatically


def test_read_labeled_examples_empty_when_absent():
    assert ws.read_labeled_examples() == []


def test_labeled_store_is_jsonl():
    ws.append_labeled_example({"category": "name", "value": "A"})
    ws.append_labeled_example({"category": "phone", "value": "91234567"})
    lines = ws.labeled_store().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["value"] == "91234567"
