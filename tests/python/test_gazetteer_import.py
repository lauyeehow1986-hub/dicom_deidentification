"""Opt-in name-list (gazetteer) import.

`add_gazetteer_names` installs a list of names into the project's WORKSPACE
gazetteer file and wires that file into the profile's ``extra_gazetteer_files``
(never the shipped default). The names then scrub at de-id time and fail the
residual QA scan, exactly like a tagging-grown gazetteer. The UI parses a CSV
(column pick + drop-header) down to this plain list; the engine stays
format-agnostic.
"""
import pytest

from deid_engine import core


def _isolated_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("DICOMDEID_WORKSPACE", str(tmp_path / "ws"))


def test_add_gazetteer_names_installs_into_workspace_and_catches(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    res = core.add_gazetteer_names("default", ["Zylandra Testalon", "Yeo KK"])
    assert res["added"] == 2
    # workspace copy now wires the file; scanning finds the listed name
    td = core.profile_get("default").get("text_detection") or {}
    assert td.get("extra_gazetteer_files")
    scanner = core._build_scanner(td, [])
    spans = [s for s in scanner.scan("seen: zylandra testalon today")
             if s.category == "name"]
    assert spans and spans[0].source == "gazetteer"


def test_add_gazetteer_names_is_idempotent(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    core.add_gazetteer_names("default", ["Yeo KK", "Tan Chorh Chuan"])
    res2 = core.add_gazetteer_names("default", ["yeo kk", "Tan Chorh Chuan"])
    assert res2["added"] == 0            # case-insensitive dupes skipped
    assert res2["total"] == 2


def test_add_gazetteer_names_skips_blanks_and_comments(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    res = core.add_gazetteer_names(
        "default", ["Yeo KK", "", "  ", "# a comment", "Tan Chorh Chuan"])
    assert res["added"] == 2


def test_add_gazetteer_names_rejects_empty(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        core.add_gazetteer_names("default", ["", "  ", "# only comments"])
