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
