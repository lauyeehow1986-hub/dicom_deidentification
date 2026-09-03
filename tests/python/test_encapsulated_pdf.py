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


from deid_engine import documents


def _write(ds, path):
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)


def test_study_remove_mode_output_has_no_pdf(tmp_path):
    _write(_encaps_ds(), tmp_path / "in.dcm")
    ks = keystore.ephemeral()
    prof = _profile("remove")
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"), prof, ks)
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    assert "EncapsulatedDocument" not in out
    assert str(out.PatientIdentityRemoved) == "YES"


def test_study_rasterize_mode_flattens_pdf(tmp_path):
    from deid_engine import pixels
    ok, _ = pixels._ocr_available()
    ks = keystore.ephemeral()
    prof = _profile("rasterize_redact")
    _write(_encaps_ds(), tmp_path / "in.dcm")
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"), prof, ks)
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    assert str(out.PatientIdentityRemoved) == "YES"
    if ok:
        # redacted + flattened: still present, but no recoverable text layer
        assert "EncapsulatedDocument" in out
        assert documents.pdf_text(bytes(out.EncapsulatedDocument)).strip() == ""
    else:
        # no Tesseract -> fell back to removal
        assert "EncapsulatedDocument" not in out


def test_study_rasterize_success_branch_monkeypatched(tmp_path, monkeypatch):
    from deid_engine import pixels
    monkeypatch.setattr(pixels, "_ocr_available", lambda: (True, ""))
    import pytesseract
    fake = {"text": ["Tan"], "left": [10], "top": [45], "width": [120], "height": [30]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake)
    _write(_encaps_ds(), tmp_path / "in.dcm")
    report = core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                                   _profile("rasterize_redact"), keystore.ephemeral())
    # the study actually invoked PDF redaction (these counts only exist if the block ran)
    counts = report["files"][0]["counts"]
    assert counts["encapsulated_pdf"] == "rasterize_redact"
    assert counts["encapsulated_pdf_boxes"] == 1
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))          # reloads => valid DICOM
    assert str(out.PatientIdentityRemoved) == "YES"
    assert "EncapsulatedDocument" in out
    assert documents.pdf_text(bytes(out.EncapsulatedDocument)).strip() == ""
    # the DICOM-level identifiers were still de-identified
    assert "Tan" not in str(out.get("PatientName", "")).replace("^", " ")


def test_deid_run_pdf_mode_override(tmp_path):
    _write(_encaps_ds(), tmp_path / "in.dcm")
    rep = core.deid_run(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                        profile_id="default", pdf_mode="rasterize_redact", pdf_dpi=100)
    assert rep["count"] == 1
    from deid_engine import pixels
    ok, _ = pixels._ocr_available()
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    if ok:
        assert "EncapsulatedDocument" in out
    else:
        assert "EncapsulatedDocument" not in out


def test_study_pdf_failure_fails_closed(tmp_path, monkeypatch):
    """If the PDF redaction step raises for any reason, the study must NOT ship
    the original identifiable PDF -- it removes the document (fail closed) even
    though the object is still stamped de-identified."""
    from deid_engine import documents as _d

    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(_d, "redact_encapsulated_pdf", _boom)
    _write(_encaps_ds(), tmp_path / "in.dcm")
    report = core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                                   _profile("rasterize_redact"), keystore.ephemeral())
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    assert str(out.PatientIdentityRemoved) == "YES"
    assert "EncapsulatedDocument" not in out
    assert "kaboom" in report["files"][0]["counts"].get("encapsulated_pdf_error", "")
