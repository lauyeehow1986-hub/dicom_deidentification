import numpy as np
import nibabel as nib
from deid_engine import core, pixels as _pixels


def test_scan_residual_flags_nifti_header_phi(tmp_path):
    vol = np.zeros((3, 4, 4), dtype=np.int16)
    img = nib.Nifti1Image(vol, np.eye(4))
    img.header["descrip"] = b"contact patient@example.sg"   # planted email in header
    p = tmp_path / "leak.nii"
    nib.save(img, str(p))
    res = core.scan_residual(str(p))
    assert res["passed"] is False
    assert res["counts"]["metadata"] >= 1
    assert any(f["location"] == "metadata" for f in res["findings"])


def test_scan_residual_nifti_pixels_flags_burned_in(tmp_path, monkeypatch):
    vol = np.zeros((3, 8, 8), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "px.nii"))
    # Stub OCR so no Tesseract is needed: pretend every slice reads an email.
    monkeypatch.setattr(_pixels, "_ocr_available", lambda: (True, ""))
    monkeypatch.setattr(_pixels, "image_phi_boxes",
                        lambda img, scanner: [{"x": 0, "y": 0, "w": 1, "h": 1,
                                               "text": "patient@example.sg",
                                               "source": "ocr"}])
    res = core.scan_residual(str(tmp_path / "px.nii"))
    assert res["passed"] is False
    assert res["counts"]["pixels"] >= 1
    assert any(f["location"] == "pixels" for f in res["findings"])


def test_scan_residual_clean_nifti_passes(tmp_path):
    vol = np.zeros((3, 4, 4), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "clean.nii"))
    res = core.scan_residual(str(tmp_path / "clean.nii"))
    assert res["passed"] is True
    assert res["counts"]["total"] == 0


def test_scan_residual_nifti_pseudonym_not_flagged(tmp_path):
    # A de-identified NIfTI header carries only a race-neutral pseudonym token,
    # which the (unseeded, always-on) residual detectors must NOT flag as PHI.
    vol = np.zeros((3, 4, 4), dtype=np.int16)
    img = nib.Nifti1Image(vol, np.eye(4))
    img.header["descrip"] = b"PATIENT_7F3A series MR"
    p = tmp_path / "clean_pseudo.nii"
    nib.save(img, str(p))
    res = core.scan_residual(str(p))
    assert res["passed"] is True
    assert res["counts"]["metadata"] == 0


def test_scan_residual_nifti_identity_removed_is_na(tmp_path):
    vol = np.zeros((3, 4, 4), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "x.nii"))
    res = core.scan_residual(str(tmp_path / "x.nii"))
    assert res["identity_removed"] is None
