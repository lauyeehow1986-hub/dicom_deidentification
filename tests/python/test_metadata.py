"""Phase 6.5 - reviewer metadata view.

`read_metadata` returns the de-identified header as flat rows for the reviewer
to eyeball, recursing one level into sequences. When ``mask_flagged`` is on it
runs the residual scan and masks any value that survived as PHI, so the review
screen never re-discloses a leaked identifier in clear text.
"""
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core


def _write(path, **over):
    ds = Dataset()
    ds.PatientName = over.get("name", "ANON^A1B2C3")
    ds.PatientID = over.get("pid", "PSEUDO0001")
    ds.Modality = "US"
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "dicomdeid: PS3.15 basic profile"
    if "derivation" in over:
        ds.DerivationDescription = over["derivation"]
    if "nested" in over:
        item = Dataset()
        item.TextValue = over["nested"]
        ds.ContentSequence = Sequence([item])
    if over.get("pixels"):
        arr = np.zeros((4, 4), dtype=np.uint8)
        ds.Rows, ds.Columns = 4, 4
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.PixelData = arr.tobytes()
        ds["PixelData"].VR = "OB"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.6.1"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)
    return str(path)


def _kw(rows, keyword):
    return [r for r in rows if r.get("keyword") == keyword]


def test_lists_header_rows(tmp_path):
    p = _write(tmp_path / "m.dcm")
    md = core.read_metadata(p, mask_flagged=False)
    rows = md["rows"]
    assert _kw(rows, "PatientID"), "PatientID row should be present"
    row = _kw(rows, "PatientID")[0]
    assert set(row).issuperset({"tag", "keyword", "vr", "value"})


def test_recurses_one_level_into_sequence(tmp_path):
    p = _write(tmp_path / "seq.dcm", nested="Reported by the reading cardiologist")
    md = core.read_metadata(p, mask_flagged=False)
    assert _kw(md["rows"], "TextValue"), "nested sequence element should appear"


def test_masks_flagged_value(tmp_path):
    p = _write(tmp_path / "leak.dcm", derivation="Follow-up NRIC S1234567D review")
    masked = core.read_metadata(p, mask_flagged=True)
    row = _kw(masked["rows"], "DerivationDescription")[0]
    assert "S1234567D" not in row["value"]
    assert "•" in row["value"]
    assert row.get("flagged") is True
    # without masking, the raw text is returned unchanged
    plain = core.read_metadata(p, mask_flagged=False)
    assert "S1234567D" in _kw(plain["rows"], "DerivationDescription")[0]["value"]


def test_pixel_data_is_summarised_not_dumped(tmp_path):
    p = _write(tmp_path / "px.dcm", pixels=True)
    md = core.read_metadata(p, mask_flagged=False)
    pd = _kw(md["rows"], "PixelData")
    assert pd, "PixelData row should be present"
    assert "byte" in pd[0]["value"].lower()
