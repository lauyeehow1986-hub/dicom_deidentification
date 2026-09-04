"""The acceptance corpus includes an encapsulated-PDF fixture, and the survivor
check inspects embedded PDFs."""
import io

import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import corpus, documents, pixels

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _pdf_with_name(name):
    img = Image.new("RGB", (400, 120), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), name, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    b = buf.getvalue()
    return b if len(b) % 2 == 0 else b + b"\x00"


def _encaps_ds_with(pdf_bytes):
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
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


def test_corpus_emits_encapsulated_pdf_fixture(tmp_path):
    man = corpus.build_corpus(str(tmp_path))
    encaps = [f for f in man["fixtures"] if f.get("encapsulated_pdf")]
    assert len(encaps) == 1
    ds = pydicom.dcmread(str(tmp_path / encaps[0]["rel"]))
    assert str(ds.SOPClassUID) == ENCAPS_PDF_SOP
    assert "EncapsulatedDocument" in ds


def test_check_survivors_clean_on_phi_free_pdf(tmp_path):
    # A PDF carrying no planted PHI must not be flagged -- neither via its (empty)
    # text layer nor via OCR of the rendered page. Guards against false positives
    # whether or not Tesseract is present. (Note: flattening removes the TEXT
    # layer but NOT the visible ink, so a *visible* name would still be caught by
    # OCR -- see test_check_survivors_catches_visible_name_via_ocr.)
    flat = documents.flatten_to_pdf(
        documents.render_pdf_pages(_pdf_with_name("cardiac ultrasound report page"),
                                   dpi=100))
    ds = _encaps_ds_with(flat)
    pydicom.dcmwrite(str(tmp_path / "doc.dcm"), ds, enforce_file_format=True)
    res = corpus.check_survivors(str(tmp_path))
    assert res["passed"] is True, res["metadata_survivors"]


@pytest.mark.skipif(not pixels._ocr_available()[0],
                    reason="Tesseract OCR binary not available")
def test_check_survivors_catches_visible_name_via_ocr(tmp_path):
    # With Tesseract present, the survivor sweep OCRs the rendered PDF pages, so a
    # name left VISIBLE in a flattened-but-unredacted PDF is caught (this is why a
    # PDF must be redacted, not merely flattened).
    name = corpus.PLANTED["names"][1]  # "Tan Wei Ming"
    flat = documents.flatten_to_pdf(documents.render_pdf_pages(_pdf_with_name(name), dpi=150))
    ds = _encaps_ds_with(flat)
    pydicom.dcmwrite(str(tmp_path / "doc.dcm"), ds, enforce_file_format=True)
    res = corpus.check_survivors(str(tmp_path))
    assert res["passed"] is False
    assert name in res["metadata_survivors"].get("names", [])


def test_check_survivors_catches_name_in_pdf_text(tmp_path, monkeypatch):
    # Prove the embedded-PDF inspection actually folds recoverable PDF text into
    # the survivor sweep: force pdf_text to return a planted name for the doc.
    name = corpus.PLANTED["names"][1]  # "Tan Wei Ming"
    monkeypatch.setattr(documents, "pdf_text", lambda b: name)
    ds = _encaps_ds_with(_pdf_with_name(name))
    pydicom.dcmwrite(str(tmp_path / "doc.dcm"), ds, enforce_file_format=True)
    res = corpus.check_survivors(str(tmp_path))
    assert res["passed"] is False
    assert name in res["metadata_survivors"].get("names", [])
