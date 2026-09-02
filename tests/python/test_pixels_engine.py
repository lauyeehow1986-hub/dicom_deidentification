"""Phase 3 - high-level pixel entries used by the R UI (viewer + redaction)."""

import base64
import io

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core

PIL = pytest.importorskip("PIL")
from PIL import Image


def _write_image_dicom(path, val=200, frames=1):
    ds = Dataset()
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    fm.MediaStorageSOPInstanceUID = generate_uid()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    ds.SOPInstanceUID = fm.MediaStorageSOPInstanceUID
    ds.PatientName = "Tan Wei Ming"
    ds.Rows, ds.Columns = 10, 12
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    if frames > 1:
        ds.NumberOfFrames = frames
        ds.PixelData = np.full((frames, 10, 12), val, np.uint8).tobytes()
    else:
        ds.PixelData = np.full((10, 12), val, np.uint8).tobytes()
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)


def test_pixel_info_reports_geometry(tmp_path):
    p = tmp_path / "a.dcm"
    _write_image_dicom(p, frames=4)
    info = core.pixel_info(str(p))
    assert info["frames"] == 4
    assert info["rows"] == 10 and info["cols"] == 12
    assert info["has_pixels"] is True
    assert "boxes" in info  # OCR auto-boxes (empty without Tesseract)


def test_pixel_frame_png_returns_decodable_image(tmp_path):
    p = tmp_path / "a.dcm"
    _write_image_dicom(p)
    b64 = core.pixel_frame_png(str(p), frame=0)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.size == (12, 10)  # (width, height)


def test_pixel_frame_png_overlays_boxes(tmp_path):
    p = tmp_path / "a.dcm"
    _write_image_dicom(p, val=200)
    plain = core.pixel_frame_png(str(p), frame=0)
    boxed = core.pixel_frame_png(str(p), frame=0,
                                 boxes=[{"x": 1, "y": 1, "w": 5, "h": 4}])
    assert boxed != plain  # the outline changes pixels


def test_pixel_redact_writes_blacked_valid_dicom(tmp_path):
    src = tmp_path / "in.dcm"
    out = tmp_path / "out.dcm"
    _write_image_dicom(src, val=200)
    rec = core.pixel_redact(str(src), str(out),
                            boxes=[{"x": 0, "y": 0, "w": 5, "h": 5}])
    reread = pydicom.dcmread(str(out))          # raises if invalid
    assert (reread.pixel_array[0:5, 0:5] == 0).all()
    assert reread.pixel_array[9, 11] == 200
    assert rec["boxes"] == 1
