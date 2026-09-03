"""Encapsulated-PDF policy wiring through the dataset/study pipeline."""
import io

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, keystore, rules

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _image_pdf_bytes(text="Tan Wei Ming"):
    img = Image.new("RGB", (400, 120), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    b = buf.getvalue()
    return b if len(b) % 2 == 0 else b + b"\x00"


def _encaps_ds():
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "DOC"
    ds.PatientName = "Tan^Wei Ming"
    ds.PatientID = "S1234567D"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = _image_pdf_bytes()
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _profile(mode):
    prof = rules.load_profile("default")
    prof["encapsulated_pdf"] = {"mode": mode, "dpi": 100}
    return prof


def test_remove_mode_strips_encapsulated_document():
    ds = _encaps_ds()
    core.deidentify_dataset(ds, _profile("remove"), salt=b"0" * 16)
    assert "EncapsulatedDocument" not in ds


def test_rasterize_mode_leaves_document_for_study_step():
    ds = _encaps_ds()
    core.deidentify_dataset(ds, _profile("rasterize_redact"), salt=b"0" * 16)
    # dataset step must NOT delete it; the study step redacts it later.
    assert "EncapsulatedDocument" in ds
