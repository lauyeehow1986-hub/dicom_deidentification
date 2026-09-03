"""Phase 7 - the transformer-NER model path must resolve like the gazetteer:
relative paths are taken relative to ``inst/`` so a profile can ship a portable
``models/<name>`` path that works regardless of the process working directory.

Without this, a bundled NER model silently disables whenever the app runs from a
directory other than the repo root - exactly the trap an air-gapped bundle hits.
"""
import os

from deid_engine import core, rules


def test_resolve_ner_model_relative_is_rooted_at_inst(tmp_path, monkeypatch):
    # Point PROFILE_DIR (hence inst_root = its parent) at a throwaway tree.
    monkeypatch.setattr(rules, "PROFILE_DIR", tmp_path / "profiles")
    model_dir = tmp_path / "models" / "xlmr-ner"
    model_dir.mkdir(parents=True)

    resolved = core._resolve_ner_model("models/xlmr-ner")

    assert resolved is not None
    assert os.path.isdir(resolved)
    assert os.path.abspath(resolved) == os.path.abspath(str(model_dir))


def test_resolve_ner_model_absolute_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "PROFILE_DIR", tmp_path / "profiles")
    model_dir = tmp_path / "abs_model"
    model_dir.mkdir()

    resolved = core._resolve_ner_model(str(model_dir))

    assert os.path.abspath(resolved) == os.path.abspath(str(model_dir))


def test_resolve_ner_model_empty_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "PROFILE_DIR", tmp_path / "profiles")
    assert core._resolve_ner_model("") is None
    assert core._resolve_ner_model(None) is None


def test_resolve_ner_model_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "PROFILE_DIR", tmp_path / "profiles")
    assert core._resolve_ner_model("models/does-not-exist") is None
