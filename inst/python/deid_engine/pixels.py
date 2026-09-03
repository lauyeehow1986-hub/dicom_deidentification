"""Phase 3 - pixel / burned-in PHI.

Decode pixels for every photometric interpretation and transfer syntax
(MONOCHROME/RGB, single/multiframe, JPEG2000/JPEG-LS/RLE via pylibjpeg/gdcm),
redact rectangular regions (per-frame or across frames), and write the result
back as VALID, viewable DICOM. Also strips audio/waveform sequences.

The OCR auto-detect layer (Tesseract) is optional and degrades gracefully; the
redaction/decoding core needs no external binary.
"""

from __future__ import annotations

import numpy as np
from pydicom.uid import ExplicitVRLittleEndian


def load_frames(ds) -> np.ndarray:
    """Return pixels normalised to (N, H, W) or (N, H, W, C) with a frame axis."""
    arr = ds.pixel_array
    nframes = int(getattr(ds, "NumberOfFrames", 1) or 1)
    if nframes == 1 and (arr.ndim == 2 or (arr.ndim == 3 and arr.shape[-1] in (3, 4))):
        arr = arr[np.newaxis, ...]
    return arr


def apply_boxes(frames: np.ndarray, boxes, fill=0) -> np.ndarray:
    """Black out rectangles. Each box is {x, y, w, h[, frame]}; frame omitted
    means every frame. Coordinates are clamped to the image bounds."""
    out = frames.copy()
    nframes, h, w = out.shape[0], out.shape[1], out.shape[2]
    for b in boxes:
        x0 = max(0, int(b["x"])); y0 = max(0, int(b["y"]))
        x1 = min(w, x0 + int(b["w"])); y1 = min(h, y0 + int(b["h"]))
        if x1 <= x0 or y1 <= y0:
            continue
        idx = range(nframes) if b.get("frame") is None else [int(b["frame"])]
        for i in idx:
            if 0 <= i < nframes:
                out[i, y0:y1, x0:x1, ...] = fill
    return out


def _store_frames(ds, frames: np.ndarray) -> None:
    """Write frames back into an uncompressed dataset, keeping it valid."""
    nframes = frames.shape[0]
    arr = frames[0] if nframes == 1 else frames
    if nframes > 1:
        ds.NumberOfFrames = nframes
    ds.PixelData = np.ascontiguousarray(arr).tobytes()
    if getattr(ds, "SamplesPerPixel", 1) == 3:
        ds.PlanarConfiguration = 0  # interleaved, matching pixel_array
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    if "PixelData" in ds:
        ds["PixelData"].VR = "OW" if ds.BitsAllocated > 8 else "OB"


def redact_pixels(ds, boxes, fill=0) -> dict:
    """Redact ``boxes`` from a dataset in place; write valid uncompressed DICOM.

    A compressed input is decompressed first so the output is always a valid,
    viewable object (transfer syntax becomes Explicit VR Little Endian).
    """
    was_compressed = bool(ds.file_meta.TransferSyntaxUID.is_compressed)
    if was_compressed:
        ds.decompress()  # decode + switch to an uncompressed transfer syntax
    frames = load_frames(ds)
    redacted = apply_boxes(frames, boxes, fill)
    _store_frames(ds, redacted)
    return {"frames": int(frames.shape[0]), "boxes": len(boxes),
            "decompressed": was_compressed}


def strip_waveforms(ds) -> int:
    """Remove audio/waveform data (voice is biometric PHI). Returns count removed.

    Standard voice/ECG lives in WaveformSequence (5400,0100); vendor audio lives in
    private tags, which the metadata pass already strips.
    """
    removed = 0
    if "WaveformSequence" in ds:
        del ds["WaveformSequence"]
        removed += 1
    return removed


def _frame_to_uint8(frame: np.ndarray) -> np.ndarray:
    """Best-effort 8-bit image for OCR (handle >8-bit + grayscale/RGB)."""
    f = frame
    if f.dtype != np.uint8:
        f = f.astype(np.float32)
        rng = float(f.max() - f.min())
        f = np.zeros_like(f, dtype=np.uint8) if rng == 0 else \
            (((f - f.min()) / rng) * 255).astype(np.uint8)
    return f


def _configure_tesseract():
    """Point pytesseract at the bundled Tesseract when ``DICOMDEID_TESSERACT`` is
    set to an existing binary. On Windows a bare ``tesseract`` command does not
    resolve off PATH via subprocess, so a copy-over air-gap bundle sets this env
    var (in its launcher) to the absolute path of ``bin/tesseract/tesseract.exe``.
    A missing/unset pointer is a no-op, so a machine with tesseract already on
    PATH keeps working. Returns the effective command string."""
    import os
    import pytesseract
    cmd = os.environ.get("DICOMDEID_TESSERACT")
    if cmd and os.path.isfile(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract.pytesseract.tesseract_cmd


def _ocr_available():
    """(-> (bool, note)) True when the Tesseract binary is usable. Degrades like
    the Presidio/NER layers: a missing binary is reported, never fatal."""
    try:
        import pytesseract
        _configure_tesseract()
        pytesseract.get_tesseract_version()
        return True, ""
    except Exception as e:  # noqa: BLE001 - no binary on this box / air-gap
        return False, f"ocr unavailable: {e}"


def image_phi_boxes(img_uint8, scanner) -> list:
    """OCR one 8-bit image; return PHI word boxes the scanner flags.

    Assumes the caller has already confirmed OCR is available (``_ocr_available``).
    Boxes are ``{x, y, w, h, text, source}`` with no frame key.
    """
    import pytesseract
    from pytesseract import Output
    data = pytesseract.image_to_data(img_uint8, output_type=Output.DICT)
    boxes = []
    for j, word in enumerate(data.get("text", [])):
        if word and word.strip() and scanner.scan(word):
            boxes.append({"x": int(data["left"][j]), "y": int(data["top"][j]),
                          "w": int(data["width"][j]), "h": int(data["height"][j]),
                          "text": word, "source": "ocr"})
    return boxes


def ocr_phi_boxes(ds, scanner) -> dict:
    """Optional: OCR each frame, keep boxes whose text the scanner flags as PHI.

    Degrades to an empty result (with a note) when the Tesseract binary is absent,
    exactly like the Presidio/NER layers in Phase 2.
    """
    ok, note = _ocr_available()
    if not ok:
        return {"boxes": [], "note": note}
    frames = load_frames(ds)
    boxes = []
    for i in range(frames.shape[0]):
        img = _frame_to_uint8(frames[i])
        try:
            page_boxes = image_phi_boxes(img, scanner)
        except Exception as e:  # noqa: BLE001
            return {"boxes": boxes, "note": f"ocr failed on frame {i}: {e}"}
        for b in page_boxes:
            b["frame"] = i
            boxes.append(b)
    return {"boxes": boxes}
