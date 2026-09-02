"""Tests for reading DICOM files, de-identifying, and writing valid DICOM out."""
import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, keystore, rules


def _write_dicom(path, name="Tan Wei Ming", pid="MRN0099887"):
    ds = Dataset()
    ds.PatientName = name
    ds.PatientID = pid
    ds.StudyDate = "20240310"
    ds.Modality = "US"
    ds.StudyDescription = f"Echo for {name}"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.6.1"
    arr = np.arange(16, dtype=np.uint8).reshape(4, 4)
    ds.Rows, ds.Columns = 4, 4
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = arr.tobytes()

    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)
    return arr.tobytes()


def test_deidentify_study_writes_valid_readable_dicom(tmp_path):
    src = tmp_path / "in.dcm"
    out = tmp_path / "out.dcm"
    pixels = _write_dicom(src)
    k = keystore.ephemeral()

    core.deidentify_study(str(src), str(out), rules.load_profile(), k)

    assert out.exists()
    ds = pydicom.dcmread(str(out))          # raises if not a valid DICOM
    assert "Tan" not in str(ds.PatientName)
    assert str(ds.PatientID) != "MRN0099887"
    # pixel data untouched in the metadata phase
    assert ds.PixelData == pixels


def test_output_file_meta_uid_matches_dataset(tmp_path):
    src = tmp_path / "in.dcm"
    out = tmp_path / "out.dcm"
    _write_dicom(src)
    core.deidentify_study(str(src), str(out), rules.load_profile(), keystore.ephemeral())

    ds = pydicom.dcmread(str(out))
    assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID


def test_output_marks_identity_removed(tmp_path):
    src = tmp_path / "in.dcm"
    out = tmp_path / "out.dcm"
    _write_dicom(src)
    core.deidentify_study(str(src), str(out), rules.load_profile(), keystore.ephemeral())

    ds = pydicom.dcmread(str(out))
    assert str(ds.PatientIdentityRemoved) == "YES"


def test_reversible_keystore_can_reverse_the_pseudonym(tmp_path):
    src = tmp_path / "in.dcm"
    out = tmp_path / "out.dcm"
    _write_dicom(src, pid="MRN0099887")
    k = keystore.create(str(tmp_path / "s.keystore"), "pw12345678")

    core.deidentify_study(str(src), str(out), rules.load_profile(), k)

    ds = pydicom.dcmread(str(out))
    assert k.reverse(str(ds.PatientID)) == "MRN0099887"


def test_deidentify_study_handles_a_folder(tmp_path):
    d_in = tmp_path / "study"
    d_out = tmp_path / "study_deid"
    d_in.mkdir()
    _write_dicom(d_in / "a.dcm")
    _write_dicom(d_in / "b.dcm")

    report = core.deidentify_study(str(d_in), str(d_out), rules.load_profile(),
                                   keystore.ephemeral())
    assert report["count"] == 2
    assert (d_out / "a.dcm").exists() and (d_out / "b.dcm").exists()
