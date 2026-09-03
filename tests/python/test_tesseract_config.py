"""Phase 7 - the bundled Tesseract must be reachable on the air-gapped target.

On Windows, pytesseract's bare ``tesseract`` command does NOT resolve off PATH
via subprocess, so a copy-over bundle needs an explicit pointer to the binary.
The engine honours ``DICOMDEID_TESSERACT`` (the launcher sets it to the bundled
``bin/tesseract/tesseract.exe``) by setting pytesseract's ``tesseract_cmd``.
"""
import pytesseract

from deid_engine import pixels


def test_configure_tesseract_sets_cmd_from_env(tmp_path, monkeypatch):
    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"stub")
    monkeypatch.setenv("DICOMDEID_TESSERACT", str(fake))
    # start from a known-different value so we can see the change
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")

    cmd = pixels._configure_tesseract()

    assert cmd == str(fake)
    assert pytesseract.pytesseract.tesseract_cmd == str(fake)


def test_configure_tesseract_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv("DICOMDEID_TESSERACT", raising=False)
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")

    pixels._configure_tesseract()

    assert pytesseract.pytesseract.tesseract_cmd == "tesseract"


def test_configure_tesseract_ignores_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DICOMDEID_TESSERACT", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")

    pixels._configure_tesseract()

    # a bad pointer must not clobber the default (which may still be on PATH)
    assert pytesseract.pytesseract.tesseract_cmd == "tesseract"
