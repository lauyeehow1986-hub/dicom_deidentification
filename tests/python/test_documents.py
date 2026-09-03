"""Encapsulated-PDF helpers (pypdfium2 render / Pillow flatten / detection)."""
import io

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import documents

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
