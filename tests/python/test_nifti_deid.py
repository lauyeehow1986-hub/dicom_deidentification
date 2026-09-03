"""Phase 7 - NIfTI de-identification (closing the 'NIfTI out for NIfTI in' gap).

DICOM is the primary format, but the plan keeps NIfTI in scope. NIfTI's PHI
surface is small: the header's free-text ``descrip`` / ``aux_file`` /
``intent_name`` fields. De-id copies the volume through unchanged and blanks
those text fields, writing a valid NIfTI. Previously non-DICOM inputs were
skipped, leaving any planted text in the output.
"""
import numpy as np
import nibabel as nib

from deid_engine import core, keystore


def _profile():
    return core.profile_get("default")


def test_nifti_input_is_deidentified_not_skipped(tmp_path):
    vol = (np.arange(6 * 6 * 3, dtype=np.float32).reshape(6, 6, 3) % 41)
    img = nib.Nifti1Image(vol, affine=np.eye(4))
    img.header["descrip"] = b"Michael O'Sullivan MRN0099887"
    src = tmp_path / "in"
    src.mkdir()
    nib.save(img, str(src / "scan.nii.gz"))
    out = tmp_path / "out"

    ks = keystore.ephemeral()
    report = core.deidentify_study(str(src), str(out), _profile(), ks)

    produced = [f for f in report["files"] if f.get("output")]
    assert len(produced) == 1, report["files"]
    outfile = produced[0]["output"]
    assert outfile.endswith(".nii.gz")

    got = nib.load(outfile)
    # volume preserved exactly
    assert np.array_equal(got.get_fdata(), vol)
    # the planted text is gone from the header
    descrip = bytes(got.header["descrip"]).split(b"\x00", 1)[0].decode("latin-1")
    assert "O'Sullivan" not in descrip
    assert "MRN0099887" not in descrip


def test_single_nifti_file_input(tmp_path):
    vol = np.ones((4, 4, 2), dtype=np.int16)
    img = nib.Nifti1Image(vol, affine=np.eye(4))
    img.header["aux_file"] = b"Tan Wei Ming"
    infile = tmp_path / "one.nii"
    nib.save(img, str(infile))
    outfile = tmp_path / "one_out.nii"

    ks = keystore.ephemeral()
    report = core.deidentify_study(str(infile), str(outfile), _profile(), ks)
    assert report["count"] == 1
    got = nib.load(str(outfile))
    aux = bytes(got.header["aux_file"]).split(b"\x00", 1)[0].decode("latin-1")
    assert "Tan" not in aux
