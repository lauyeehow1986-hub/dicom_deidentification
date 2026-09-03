"""Encapsulated-PDF helpers (pypdfium2 render / Pillow flatten / detection)."""
import io

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import documents
from deid_engine import pixels

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _image_pdf_bytes(text="Tan Wei Ming", size=(400, 120)):
    """A one-page image PDF (no text layer) with visible text."""
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _encapsulated_pdf_ds(pdf_bytes):
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.Modality = "DOC"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = pdf_bytes if len(pdf_bytes) % 2 == 0 else pdf_bytes + b"\x00"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def test_is_encapsulated_pdf_by_sop_class():
    ds = _encapsulated_pdf_ds(_image_pdf_bytes())
    assert documents.is_encapsulated_pdf(ds) is True


def test_is_encapsulated_pdf_false_for_plain_image():
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture
    assert documents.is_encapsulated_pdf(ds) is False


def test_render_pdf_pages_returns_rgb_arrays():
    pages = documents.render_pdf_pages(_image_pdf_bytes(), dpi=100)
    assert len(pages) == 1
    assert pages[0].ndim == 3 and pages[0].shape[2] == 3
    assert pages[0].dtype == np.uint8


def test_flatten_to_pdf_has_no_text_layer():
    pages = documents.render_pdf_pages(_image_pdf_bytes(), dpi=100)
    flat = documents.flatten_to_pdf(pages)
    assert flat[:5] == b"%PDF-"
    assert documents.pdf_text(flat).strip() == ""


class _NameScanner:
    """Fake scanner flagging any word containing a planted token."""
    def __init__(self, tokens=("Tan", "Wei", "Ming")):
        self.tokens = tokens
    def scan(self, text):
        return [object()] if any(t in text for t in self.tokens) else []


def _pixels_ocr_ok():
    from deid_engine import pixels
    return pixels._ocr_available()


def test_redact_pdf_bytes_boxes_are_black_over_the_name():
    ok, _ = _pixels_ocr_ok()
    if not ok:
        pytest.skip("Tesseract not available")
    src = _image_pdf_bytes("Tan Wei Ming")
    out, info = documents.redact_pdf_bytes(src, _NameScanner(), dpi=150)
    assert info["pages"] == 1
    assert info["boxes"] >= 1
    # the flattened output has no text layer at all
    assert documents.pdf_text(out).strip() == ""


def test_redact_encapsulated_pdf_removes_doc_when_ocr_absent(monkeypatch):
    # Force "OCR unavailable" so the fallback path runs deterministically.
    monkeypatch.setattr(documents._pixels, "_ocr_available",
                        lambda: (False, "forced-off"))
    ds = _encapsulated_pdf_ds(_image_pdf_bytes("Tan Wei Ming"))
    info = documents.redact_encapsulated_pdf(ds, _NameScanner(), dpi=100)
    assert info["mode"] == "removed_fallback"
    assert "EncapsulatedDocument" not in ds


def test_redact_pdf_bytes_blacks_the_boxed_region_monkeypatched(monkeypatch):
    from deid_engine import pixels
    monkeypatch.setattr(pixels, "_ocr_available", lambda: (True, ""))
    import pytesseract
    # one OCR word with a known bounding box in the 72-dpi render space
    fake = {"text": ["Tan"], "left": [10], "top": [45], "width": [120], "height": [30]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake)
    src = _image_pdf_bytes("Tan Wei Ming")
    out, info = documents.redact_pdf_bytes(src, _NameScanner(), dpi=72)
    assert info["pages"] == 1 and info["boxes"] == 1
    assert documents.pdf_text(out).strip() == ""            # flattened, no text layer
    base = documents.render_pdf_pages(src, dpi=72)[0]
    red = documents.render_pdf_pages(out, dpi=72)[0]
    def dark(im):  # fraction of near-black pixels inside the injected box
        return float((im[46:74, 12:128] < 10).mean())
    assert dark(red) > dark(base) + 0.3, (dark(red), dark(base))


def test_redact_encapsulated_pdf_reembeds_flattened_pdf_monkeypatched(monkeypatch):
    from deid_engine import pixels
    monkeypatch.setattr(pixels, "_ocr_available", lambda: (True, ""))
    import pytesseract
    fake = {"text": ["Tan"], "left": [10], "top": [45], "width": [120], "height": [30]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake)
    ds = _encapsulated_pdf_ds(_image_pdf_bytes("Tan Wei Ming"))
    ds.EncapsulatedDocumentLength = len(bytes(ds.EncapsulatedDocument))
    info = documents.redact_encapsulated_pdf(ds, _NameScanner(), dpi=72)
    assert info["mode"] == "rasterize_redact"
    assert info["boxes"] == 1
    blob = bytes(ds.EncapsulatedDocument)
    assert blob[:5] == b"%PDF-"
    assert len(blob) % 2 == 0                       # valid even-length OB
    assert documents.pdf_text(blob).strip() == ""   # flattened, no text layer
    assert ds.EncapsulatedDocumentLength == len(blob)
