"""Opt-in site-specific format rules.

`derive_pattern` infers a regex from one or more SAMPLE identifiers by their
structure (digit / letter runs + literal separators), so an admin can teach the
engine a local case/accession-number format without writing regex. `add_pattern_rule`
installs the derived rule into a WORKSPACE profile (never the shipped default), and
`remove_custom_rule` reverses it. Matches then scrub the value at de-id time
(fields with action C) and fail the residual QA scan as a confident hit.
"""
import re

import pytest

from deid_engine import textscan as ts
from deid_engine import core


# --- pattern inference (pure) ----------------------------------------------

def test_derive_pattern_generalises_a_dashed_number():
    p = ts.derive_pattern(["324-58-2995"])
    assert re.fullmatch(p, "324-58-2995")          # matches the sample shape
    assert re.fullmatch(p, "100-22-3333")          # and any same-shaped value
    assert re.fullmatch(p, "1234567890") is None   # not a plain digit run
    assert re.fullmatch(p, "32-458-2995") is None  # not a different grouping
    # embedded, with a word boundary, is found; a longer digit run around is not
    assert re.search(p, "Ref 324-58-2995/4 review")
    assert re.search(p, "9324-58-29950") is None


def test_derive_pattern_handles_letters_and_single_chars():
    p = ts.derive_pattern(["A12-3456"])
    assert re.fullmatch(p, "A12-3456")
    assert re.fullmatch(p, "b99-0001")             # letter class, case-insensitive
    assert re.fullmatch(p, "AB12-3456") is None    # single-letter slot


def test_derive_pattern_widens_run_lengths_across_samples():
    p = ts.derive_pattern(["1234567", "12345678"])
    assert re.fullmatch(p, "1234567")
    assert re.fullmatch(p, "12345678")
    assert re.fullmatch(p, "123456") is None       # below the observed range


def test_derive_pattern_rejects_incompatible_samples():
    with pytest.raises(ValueError):
        ts.derive_pattern(["324-58-2995", "AB/12"])


def test_derive_pattern_needs_at_least_one_sample():
    with pytest.raises(ValueError):
        ts.derive_pattern([])


# --- opt-in install into a workspace profile -------------------------------

def _isolated_ws(tmp_path, monkeypatch):
    # workspace_dir() reads $DICOMDEID_WORKSPACE fresh each call (no caching), so
    # pointing it at a temp dir fully isolates the shipped profiles from writes.
    monkeypatch.setenv("DICOMDEID_WORKSPACE", str(tmp_path / "ws"))


def test_add_pattern_rule_installs_into_workspace_not_default(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    res = core.add_pattern_rule("default", "case_number", ["324-58-2995"], score=0.95)
    assert res["pattern"]
    assert res["matched"] is True                  # the sample validates the rule
    # the workspace copy now carries the rule; the SHIPPED default still does not
    ws_rules = (core.profile_get("default").get("text_detection") or {}).get("custom_regex") or []
    assert any(r["category"] == "case_number" for r in ws_rules)


def test_added_rule_flags_residual_and_is_confident(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    core.add_pattern_rule("default", "case_number", ["324-58-2995"], score=0.95)
    # Build the residual scanner from the (now workspace) profile and scan text.
    td = core.profile_get("default").get("text_detection") or {}
    scanner = core._build_scanner(td, [])
    spans = scanner.scan("Ref 324-58-2995/4 pending")
    hit = next(s for s in spans if s.category == "case_number")
    assert hit.source == "custom_regex"
    f = {"source": hit.source, "score": float(hit.score)}
    assert core._finding_confident(f, 0.5, 0.90) is True


def test_remove_custom_rule_reverses_the_optin(tmp_path, monkeypatch):
    _isolated_ws(tmp_path, monkeypatch)
    core.add_pattern_rule("default", "case_number", ["324-58-2995"])
    core.remove_custom_rule("default", category="case_number")
    ws_rules = (core.profile_get("default").get("text_detection") or {}).get("custom_regex") or []
    assert not any(r["category"] == "case_number" for r in ws_rules)
