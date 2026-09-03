"""Phase 7 - end-to-end burned-in redaction through deidentify_study.

Requires a real Tesseract (the bundle ships one; point DICOMDEID_TESSERACT at it,
or have it on PATH). Skips cleanly otherwise so the suite stays green on boxes
without OCR - the gate logic is covered by test_pixel_autoredact.py regardless.
"""
import copy
import os

import pydicom
import pytest

from deid_engine import corpus, core, keystore, pixels, rules


def _bundled_tesseract():
    """Path to the repo's portable Tesseract, or None. Does NOT mutate the global
    environment (that would leak into other tests in the same pytest process)."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cand = os.path.join(repo, "vendor", "tesseract", "tesseract.exe")
    return cand if os.path.isfile(cand) else None


def _tesseract_available():
    try:
        import pytesseract
        cmd = _bundled_tesseract()
        old = pytesseract.pytesseract.tesseract_cmd
        try:
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            pytesseract.get_tesseract_version()
            return True
        finally:
            pytesseract.pytesseract.tesseract_cmd = old
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _tesseract_available(),
                                reason="Tesseract OCR binary not available")


def _use_tesseract(monkeypatch):
    cmd = _bundled_tesseract()
    if cmd:
        monkeypatch.setenv("DICOMDEID_TESSERACT", cmd)


def _burned_in_output(tmp_path, confirm):
    man = corpus.build_corpus(str(tmp_path / "corpus"))
    prof = copy.deepcopy(rules.load_profile("default"))
    prof.setdefault("options", {})["clean_pixel_data"] = True
    prof.setdefault("pixel", {})
    prof["pixel"]["auto_detect"] = True
    prof["pixel"]["require_human_confirm"] = confirm
    ks = keystore.create(str(tmp_path / "ks.json"), "acc", reversible=True)
    core.deidentify_study(str(tmp_path / "corpus"), str(tmp_path / "out"), prof, ks)
    fx = next(f for f in man["fixtures"] if f.get("burned_in"))
    return str(tmp_path / "out" / fx["rel"])


def _ocr_words(path):
    import pytesseract
    from pytesseract import Output
    pixels._configure_tesseract()
    ds = pydicom.dcmread(path)
    frames = pixels.load_frames(ds)
    img = pixels._frame_to_uint8(frames[0])
    data = pytesseract.image_to_data(img, output_type=Output.DICT)
    return " ".join(w for w in data.get("text", []) if w and w.strip())


def test_autoredact_removes_burned_in_name(tmp_path, monkeypatch):
    _use_tesseract(monkeypatch)
    out = _burned_in_output(tmp_path, confirm=False)   # opt out of confirmation
    words = _ocr_words(out)
    # the planted burned-in name must be gone from the pixels
    for tok in ("Tan", "Wei", "Ming"):
        assert tok not in words, f"burned-in {tok!r} survived auto-redaction: {words!r}"


def test_human_confirm_default_leaves_pixels_for_review(tmp_path, monkeypatch):
    _use_tesseract(monkeypatch)
    out = _burned_in_output(tmp_path, confirm=True)     # shipped default
    words = _ocr_words(out)
    # with human-confirm on, the pipeline must NOT silently alter pixels; the
    # burned-in text is still there for the reviewer to redact in the app.
    assert any(tok in words for tok in ("Tan", "Wei", "Ming")), words
