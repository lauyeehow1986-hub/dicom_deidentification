"""Shared OCR helper used by both pixel frames and PDF pages."""
import numpy as np
import pytest

from deid_engine import pixels


class _Scanner:
    """Fake scanner: flags any word containing 'Tan'."""
    def scan(self, text):
        return [object()] if "Tan" in text else []


def _tesseract_ok():
    ok, _ = pixels._ocr_available()
    return ok


@pytest.mark.skipif(not _tesseract_ok(), reason="Tesseract OCR binary not available")
def test_ocr_available_returns_bool_and_note():
    ok, note = pixels._ocr_available()
    assert isinstance(ok, bool)
    assert isinstance(note, str)


@pytest.mark.skipif(not _tesseract_ok(), reason="Tesseract OCR binary not available")
def test_image_phi_boxes_flags_matching_word():
    from PIL import Image, ImageDraw
    img = Image.new("L", (256, 64), 255)
    ImageDraw.Draw(img).text((6, 24), "Tan Wei Ming", fill=0)
    arr = np.asarray(img, dtype=np.uint8)
    boxes = pixels.image_phi_boxes(arr, _Scanner())
    assert any("Tan" in b["text"] for b in boxes)
    for b in boxes:
        assert {"x", "y", "w", "h", "text", "source"} <= set(b)


def test_image_phi_boxes_shapes_boxes_via_monkeypatched_ocr(monkeypatch):
    import pytesseract
    fake = {"text": ["Echo", "for", "Tan", ""],
            "left": [1, 2, 3, 4], "top": [5, 6, 7, 8],
            "width": [9, 10, 11, 12], "height": [13, 14, 15, 16]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake)
    boxes = pixels.image_phi_boxes(np.zeros((4, 4), np.uint8), _Scanner())
    assert len(boxes) == 1
    b = boxes[0]
    assert b["text"] == "Tan" and b["source"] == "ocr"
    assert (b["x"], b["y"], b["w"], b["h"]) == (3, 7, 11, 15)
