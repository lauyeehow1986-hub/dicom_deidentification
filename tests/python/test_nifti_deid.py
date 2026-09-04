"""Phase 7 - NIfTI de-identification (closing the 'NIfTI out for NIfTI in' gap).

DICOM is the primary format, but the plan keeps NIfTI in scope. NIfTI's PHI
surface is small: the header's free-text ``descrip`` / ``aux_file`` /
``intent_name`` fields, header *extensions* (which can embed a whole DICOM
dataset or freeform text), and the BIDS ``.json`` sidecars dcm2niix emits next
to each volume. De-id copies the volume through unchanged, blanks the header
text, strips extensions, preserves the NIfTI-1/2 class, de-identifies the JSON
sidecar, and sanitises identifier tokens in the output filename. Previously
non-DICOM inputs were skipped, leaving any planted text in the output.
"""
import json
import os

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


def test_nifti_header_extensions_are_stripped(tmp_path):
    # A freeform comment extension (code 6) can carry PHI; an embedded-DICOM
    # extension (code 2) can carry an entire identifiable dataset. Neither is
    # needed for the image, so de-id must drop all extensions.
    vol = np.zeros((3, 3, 2), dtype=np.int16)
    img = nib.Nifti1Image(vol, affine=np.eye(4))
    img.header.extensions.append(
        nib.nifti1.Nifti1Extension(6, b"Patient: Lim Ah Seng S1234567A"))
    infile = tmp_path / "ext.nii"
    nib.save(img, str(infile))
    outfile = tmp_path / "ext_out.nii"

    ks = keystore.ephemeral()
    core.deidentify_study(str(infile), str(outfile), _profile(), ks)

    got = nib.load(str(outfile))
    assert len(got.header.extensions) == 0


def test_nifti2_input_is_written_back_as_nifti2(tmp_path):
    # A NIfTI-2 input must not be silently downcast to NIfTI-1 (which caps dims
    # and changes the on-disk format).
    vol = np.ones((3, 3, 2), dtype=np.int16)
    img = nib.Nifti2Image(vol, affine=np.eye(4))
    img.header["descrip"] = b"Kumar s/o Raju"
    infile = tmp_path / "v2.nii"
    nib.save(img, str(infile))
    outfile = tmp_path / "v2_out.nii"

    ks = keystore.ephemeral()
    core.deidentify_study(str(infile), str(outfile), _profile(), ks)

    got = nib.load(str(outfile))
    assert isinstance(got, nib.Nifti2Image)
    descrip = bytes(got.header["descrip"]).split(b"\x00", 1)[0].decode("latin-1")
    assert "Kumar" not in descrip


def test_bids_json_sidecar_is_deidentified_not_dropped(tmp_path):
    # dcm2niix pairs each volume with a JSON sidecar full of PHI keys. It must be
    # de-identified in place, not dropped as "unreadable".
    src = tmp_path / "in"
    src.mkdir()
    vol = np.zeros((3, 3, 2), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(src / "sub.nii.gz"))
    sidecar = {
        "PatientName": "Nurul Huda binte Ahmad",
        "PatientID": "S7654321Z",
        "AcquisitionDateTime": "2024-05-01T13:45:00",
        "InstitutionName": "Some Hospital",
        "SeriesDescription": "Cardiac MR for Nurul Huda binte Ahmad",
        "MagneticFieldStrength": 3.0,
        "RepetitionTime": 2.5,
    }
    (src / "sub.json").write_text(json.dumps(sidecar), encoding="utf-8")
    out = tmp_path / "out"

    ks = keystore.ephemeral()
    core.deidentify_study(str(src), str(out), _profile(), ks)

    outjson = out / "sub.json"
    assert outjson.is_file(), "sidecar must be de-identified, not dropped"
    data = json.loads(outjson.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "Nurul Huda" not in blob            # name gone from every value
    assert "S7654321Z" not in blob             # id gone from every value
    assert "PatientName" not in data           # identifier key removed
    assert "AcquisitionDateTime" not in data   # date removed (default mode: remove)
    assert data.get("MagneticFieldStrength") == 3.0  # useful acq metadata kept


def test_nifti_filename_identifier_tokens_are_sanitised(tmp_path):
    # Identifier tokens in the filename (an NRIC, or name tokens matching the
    # paired sidecar) must not survive; the volume and its sidecar must keep a
    # shared basename so the pairing is preserved.
    src = tmp_path / "in"
    src.mkdir()
    vol = np.zeros((3, 3, 2), dtype=np.int16)
    stem = "Tan_Ah_Kow_S1234567A"
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(src / f"{stem}.nii.gz"))
    (src / f"{stem}.json").write_text(
        json.dumps({"PatientName": "Tan Ah Kow", "PatientID": "S1234567A"}),
        encoding="utf-8")
    out = tmp_path / "out"

    ks = keystore.ephemeral()
    report = core.deidentify_study(str(src), str(out), _profile(), ks)

    names = sorted(os.path.basename(f["output"])
                   for f in report["files"] if f.get("output"))
    assert len(names) == 2, names
    for n in names:
        assert "S1234567A" not in n
        assert "Tan" not in n and "Kow" not in n
    # nifti + sidecar still share a basename stem (pairing intact)
    stems = {n.split(".")[0] for n in names}
    assert len(stems) == 1, names
