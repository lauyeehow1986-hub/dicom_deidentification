"""Phase 3 - pixel / burned-in PHI core.

Covers the always-available pieces (no external binary needed): frame decoding
for mono / RGB / multiframe / compressed transfer syntaxes, manual-box redaction
per-frame and across frames, writing VALID viewable DICOM back, and audio/
waveform stripping. The OCR auto-detect layer is covered by its graceful-degrade
path here (no Tesseract binary on the dev box).
"""

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (ExplicitVRLittleEndian, RLELossless, generate_uid)

from deid_engine import pixels


def _base_meta(ds):
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    fm.MediaStorageSOPInstanceUID = generate_uid()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    ds.SOPInstanceUID = fm.MediaStorageSOPInstanceUID


def _mono(h=8, w=8, val=200):
    ds = Dataset()
    _base_meta(ds)
    ds.Rows, ds.Columns = h, w
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = np.full((h, w), val, dtype=np.uint8).tobytes()
    return ds


def _rgb(h=8, w=8):
    ds = Dataset()
    _base_meta(ds)
    ds.Rows, ds.Columns = h, w
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "RGB"
    ds.PlanarConfiguration = 0
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = np.full((h, w, 3), 150, dtype=np.uint8).tobytes()
    return ds


def _multiframe(n=4, h=8, w=8):
    ds = _mono(h, w)
    ds.NumberOfFrames = n
    ds.PixelData = np.full((n, h, w), 100, dtype=np.uint8).tobytes()
    return ds


# --- frame decoding ---------------------------------------------------------

def test_load_frames_mono_adds_frame_axis():
    frames = pixels.load_frames(_mono())
    assert frames.shape == (1, 8, 8)


def test_load_frames_rgb_keeps_channels():
    frames = pixels.load_frames(_rgb())
    assert frames.shape == (1, 8, 8, 3)


def test_load_frames_multiframe():
    frames = pixels.load_frames(_multiframe(n=5))
    assert frames.shape == (5, 8, 8)


# --- box redaction ----------------------------------------------------------

def test_apply_boxes_blacks_out_region_all_frames():
    frames = pixels.load_frames(_multiframe(n=3))
    out = pixels.apply_boxes(frames, [{"x": 2, "y": 1, "w": 3, "h": 2}])
    assert (out[:, 1:3, 2:5] == 0).all()
    assert (out[:, 0, 0] == 100).all()  # outside the box untouched


def test_apply_boxes_single_frame_only():
    frames = pixels.load_frames(_multiframe(n=3))
    out = pixels.apply_boxes(frames, [{"x": 0, "y": 0, "w": 4, "h": 4, "frame": 1}])
    assert (out[1, 0:4, 0:4] == 0).all()
    assert (out[0, 0:4, 0:4] == 100).all()  # other frames untouched


def test_apply_boxes_rgb_fills_all_channels():
    frames = pixels.load_frames(_rgb())
    out = pixels.apply_boxes(frames, [{"x": 1, "y": 1, "w": 2, "h": 2}])
    assert (out[0, 1:3, 1:3, :] == 0).all()


# --- write valid DICOM back -------------------------------------------------

def test_redact_pixels_writes_valid_uncompressed(tmp_path):
    ds = _mono(val=200)
    rec = pixels.redact_pixels(ds, [{"x": 0, "y": 0, "w": 4, "h": 4}])
    out = tmp_path / "r.dcm"
    pydicom.dcmwrite(str(out), ds, enforce_file_format=True)
    reread = pydicom.dcmread(str(out))
    arr = reread.pixel_array
    assert (arr[0:4, 0:4] == 0).all()
    assert arr[7, 7] == 200
    assert rec["frames"] == 1


def test_redact_compressed_decompresses_and_stays_valid(tmp_path):
    ds = _mono(val=180)
    ds.compress(RLELossless)  # now a compressed transfer syntax
    assert ds.file_meta.TransferSyntaxUID.is_compressed
    rec = pixels.redact_pixels(ds, [{"x": 0, "y": 0, "w": 3, "h": 3}])
    assert rec["decompressed"] is True
    out = tmp_path / "c.dcm"
    pydicom.dcmwrite(str(out), ds, enforce_file_format=True)
    reread = pydicom.dcmread(str(out))
    assert not reread.file_meta.TransferSyntaxUID.is_compressed
    assert (reread.pixel_array[0:3, 0:3] == 0).all()


# --- audio / waveform strip -------------------------------------------------

def test_strip_waveforms_removes_sequence():
    ds = _mono()
    item = Dataset()
    item.WaveformData = b"\x01\x02\x03\x04"
    ds.WaveformSequence = Sequence([item])
    n = pixels.strip_waveforms(ds)
    assert "WaveformSequence" not in ds
    assert n == 1


# --- OCR graceful degrade ---------------------------------------------------

def test_metadata_pass_removes_waveform_sequence():
    # WaveformSequence is mapped to X in the catalog; the walker must remove the
    # whole sequence, not recurse into it and leave it behind.
    from deid_engine import core, rules
    ds = _mono()
    ds.PatientName = "Tan Wei Ming"
    item = Dataset()
    item.WaveformData = b"\x01\x02\x03\x04"
    ds.WaveformSequence = Sequence([item])
    core.deidentify_dataset(ds, rules.load_profile(), b"s" * 16)
    assert "WaveformSequence" not in ds


def test_ocr_phi_boxes_degrades_without_tesseract(monkeypatch):
    # Force Tesseract absent so the degrade path is exercised deterministically,
    # even on a box where a real binary is installed or DICOMDEID_TESSERACT is set.
    import pytesseract
    from deid_engine import textscan
    monkeypatch.delenv("DICOMDEID_TESSERACT", raising=False)
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd",
                        "definitely-not-a-real-tesseract-binary")
    scanner = textscan.TextScanner(use_presidio=False, use_ner=False)
    res = pixels.ocr_phi_boxes(_mono(), scanner)
    assert res["boxes"] == []
    assert "note" in res  # explains why (no tesseract binary)
