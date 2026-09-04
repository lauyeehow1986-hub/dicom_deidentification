# tests/python/test_nifti_ocr.py
import numpy as np
from deid_engine import pixels


class _StubScanner:
    """Flags any word containing a digit run of >=6 (stands in for the real
    SG-aware scanner) so the test needs no Tesseract or model."""
    def scan(self, text):
        import re
        return bool(re.search(r"\d{6,}", str(text)))


def test_redact_volume_boxes_zeros_the_box_on_the_right_slice():
    vol = np.ones((3, 8, 8), dtype=np.int16) * 100  # (D,H,W), D is shortest axis
    boxes = [{"x": 2, "y": 3, "w": 3, "h": 2, "slice": 1, "axis": 0}]
    out = pixels.redact_volume_boxes(vol, boxes, fill=0)
    assert out[1, 3:5, 2:5].sum() == 0          # box zeroed on slice 1
    assert out[0].sum() == 100 * 64             # other slices untouched
    assert out[2].sum() == 100 * 64
    assert vol[1].sum() == 100 * 64             # input not mutated


def test_volume_ocr_boxes_iterates_shortest_axis_and_tags_slice():
    # A (2, 6, 6) volume: shortest axis is 0 -> two 6x6 planes scanned.
    vol = np.zeros((2, 6, 6), dtype=np.uint8)
    calls = {"planes": 0}

    class _Scan(_StubScanner):
        pass

    # Monkeypatch image_phi_boxes to avoid needing Tesseract; assert per-plane call.
    orig = pixels.image_phi_boxes
    def fake(img, scanner):
        calls["planes"] += 1
        return [{"x": 0, "y": 0, "w": 1, "h": 1, "text": "S1234567", "source": "ocr"}]
    pixels.image_phi_boxes = fake
    try:
        boxes = pixels.volume_ocr_boxes(vol, _Scan())
    finally:
        pixels.image_phi_boxes = orig
    assert calls["planes"] == 2
    assert all(b["axis"] == 0 for b in boxes)
    assert sorted(b["slice"] for b in boxes) == [0, 1]


def test_volume_ocr_boxes_skips_a_failing_slice_without_losing_others():
    vol = np.zeros((3, 6, 6), dtype=np.uint8)
    calls = {"n": 0}
    def fake(img, scanner):
        i = calls["n"]; calls["n"] += 1
        if i == 1:                       # fail on the middle plane only
            raise RuntimeError("bad slice")
        return [{"x": 0, "y": 0, "w": 1, "h": 1, "text": "S1234567", "source": "ocr"}]
    orig = pixels.image_phi_boxes
    pixels.image_phi_boxes = fake
    try:
        boxes = pixels.volume_ocr_boxes(vol, _StubScanner())
    finally:
        pixels.image_phi_boxes = orig
    assert sorted(b["slice"] for b in boxes) == [0, 2]   # slice 1 failed, others kept


# add to tests/python/test_nifti_ocr.py
import nibabel as nib
from deid_engine import core, keystore, pixels as _pixels


def test_nifti_burned_in_text_is_redacted_when_autoredact(tmp_path, monkeypatch):
    # A volume whose one slice "contains" burned-in PHI. We stub OCR so the test
    # needs no Tesseract: pretend a fixed box on slice 0 reads an NRIC.
    vol = np.ones((2, 10, 10), dtype=np.int16) * 50
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "v.nii.gz"))
    out = tmp_path / "out"

    monkeypatch.setattr(_pixels, "_ocr_available", lambda: (True, ""))
    monkeypatch.setattr(_pixels, "image_phi_boxes",
                        lambda img, scanner: [{"x": 1, "y": 1, "w": 4, "h": 3,
                                               "text": "S1234567D", "source": "ocr"}]
                        if img.shape == (10, 10) else [])

    prof = dict(core.profile_get("default"))
    # force auto-redact (as bulk/acceptance does)
    prof["pixel"] = {**(prof.get("pixel") or {}), "auto_detect": True,
                     "require_human_confirm": False}
    prof["options"] = {**(prof.get("options") or {}), "clean_pixel_data": True}

    ks = keystore.ephemeral()
    report = core.deidentify_study(str(tmp_path / "v.nii.gz"), str(out), prof, ks)
    rec = report["files"][0]
    assert rec["counts"].get("nifti_ocr_boxes", 0) >= 1
    assert rec["counts"].get("nifti_pixels_redacted", 0) >= 1
    got = nib.load(rec["output"]).get_fdata()
    # the box on both scanned planes (shortest axis is 0, size 2) is zeroed
    assert got[0, 1:4, 1:5].sum() == 0


def test_nifti_ocr_degrades_without_tesseract(tmp_path, monkeypatch):
    vol = np.ones((2, 6, 6), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "v.nii.gz"))
    out = tmp_path / "out"
    monkeypatch.setattr(_pixels, "_ocr_available", lambda: (False, "no tesseract"))
    prof = core.profile_get("default")
    ks = keystore.ephemeral()
    report = core.deidentify_study(str(tmp_path / "v.nii.gz"), str(out), prof, ks)
    rec = report["files"][0]
    assert rec["counts"].get("nifti_ocr_note")           # noted, not fatal
    assert nib.load(rec["output"]) is not None            # file still written
