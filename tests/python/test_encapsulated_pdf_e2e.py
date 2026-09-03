"""End-to-end: a planted name burned into an embedded PDF is gone after
rasterize_redact. Requires Tesseract (the bundle ships one); skips otherwise."""
import io

import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, documents, keystore, pixels, rules

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"

pytestmark = pytest.mark.skipif(not pixels._ocr_available()[0],
                                reason="Tesseract OCR binary not available")


def _encaps_ds(name):
    img = Image.new("RGB", (480, 140), (255, 255, 255))
    ImageDraw.Draw(img).text((12, 60), name, fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PDF")
    b = buf.getvalue(); b = b if len(b) % 2 == 0 else b + b"\x00"
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.PatientName = "Tan^Wei Ming"; ds.PatientID = "S1234567D"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = b
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm; ds.is_little_endian = True; ds.is_implicit_VR = False
    return ds


def _ocr(pdf_bytes):
    import pytesseract
    words = []
    for arr in documents.render_pdf_pages(pdf_bytes, dpi=150):
        words.append(pytesseract.image_to_string(pixels._frame_to_uint8(arr)))
    return " ".join(words)


def test_planted_name_gone_from_redacted_pdf(tmp_path):
    prof = rules.load_profile("default")
    prof["encapsulated_pdf"] = {"mode": "rasterize_redact", "dpi": 150}
    pydicom.dcmwrite(str(tmp_path / "in.dcm"), _encaps_ds("Tan Wei Ming"),
                     enforce_file_format=True)
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                          prof, keystore.ephemeral())
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    ocr_text = _ocr(bytes(out.EncapsulatedDocument))
    for tok in ("Tan", "Wei", "Ming"):
        assert tok not in ocr_text, f"{tok!r} survived: {ocr_text!r}"
