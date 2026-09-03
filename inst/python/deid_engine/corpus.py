"""Synthetic acceptance corpus with PLANTED PHI (Phase 7).

Emits *real* DICOM/NIfTI fixtures carrying known identifiers in known
locations - structured tags, free-text, private tags, nested sequences, and
burned into the pixels - across single-frame, multiframe/cine, RGB, JPEG2000,
and NIfTI encodings. The acceptance runner de-identifies this corpus and
asserts every planted value is gone from the outputs (metadata *and* pixels).

No real patient data ever appears here: all values below are invented. The
``PLANTED`` dict is the single source of truth the QA residual scan checks
against; ``generate_synthetic.py`` is a thin CLI over ``build_corpus``.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    ExplicitVRLittleEndian,
    JPEG2000Lossless,
    generate_uid,
)

# --------------------------------------------------------------------------- #
# Ground truth: the identifiers planted into the fixtures.                     #
# --------------------------------------------------------------------------- #
PLANTED = {
    "names": [
        "Nurul Aisyah Binte Rahman",   # Malay
        "Tan Wei Ming",                 # Chinese
        "Ramasamy Muthu",               # Indian
        "Bernadette Pereira",           # Eurasian
        "Michael O'Sullivan",           # Western/foreigner
    ],
    "nric_fin": ["S1234567D", "G9876543N"],
    "passport": ["E1234567"],
    "phone": ["+65 9123 4567", "62345678"],
    "email": ["patient@example.sg"],
    "mrn": ["MRN0099887"],
    "accession": ["ACC-2024-000123"],
    "address": ["Blk 123 Bishan St 12 #08-45", "Singapore 570123"],
    "locations": {
        "structured_tags": True,
        "free_text": True,
        "burned_in_pixels": True,
        "private_tags": True,
        "nested_sequence": True,
    },
    "encodings": ["single_frame", "multiframe_cine", "rgb", "jpeg2000", "nifti"],
}

_SC_SOP = "1.2.840.10008.5.1.4.1.1.7"       # Secondary Capture Image Storage
_US_MF_SOP = "1.2.840.10008.5.1.4.1.1.3.1"  # US Multiframe Image Storage
_PRIVATE_CREATOR = "DICOMDEID_TEST"


def _burn_text_gray(rows: int, cols: int, text: str) -> np.ndarray:
    """Light 16-bit text burned onto a black ground (deterministic bitmap font)."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (cols, rows), 0)
    ImageDraw.Draw(img).text((4, rows // 2 - 4), text, fill=255)
    return (np.asarray(img, dtype=np.uint16) * 12)  # scale into a plausible range


def _burn_text_rgb(rows: int, cols: int, text: str) -> np.ndarray:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (cols, rows), (0, 0, 0))
    ImageDraw.Draw(img).text((4, rows // 2 - 4), text, fill=(240, 240, 200))
    return np.asarray(img, dtype=np.uint8)


def _base_ds(sop_class: str) -> Dataset:
    ds = Dataset()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "OT"
    # --- planted structured identifiers ---
    ds.PatientName = "Tan^Wei Ming"                 # names[1]
    ds.PatientID = PLANTED["nric_fin"][0]           # S1234567D
    ds.AccessionNumber = PLANTED["accession"][0]
    ds.PatientBirthDate = "19700101"
    ds.StudyDate = "20240115"
    # --- planted free-text ---
    ds.ImageComments = f"Contact {PLANTED['email'][0]} / {PLANTED['phone'][0]}"
    ds.StudyDescription = f"Echo for {PLANTED['names'][0]}"   # Malay name in free text
    ds.PatientAddress = PLANTED["address"][0]
    # --- planted private tag block ---
    block = ds.private_block(0x0011, _PRIVATE_CREATOR, create=True)
    block.add_new(0x01, "LO", PLANTED["names"][2])           # Ramasamy Muthu
    block.add_new(0x02, "LO", PLANTED["nric_fin"][1])        # G9876543N
    # --- planted nested sequence (one level deep) ---
    item = Dataset()
    item.PatientID = PLANTED["nric_fin"][1]                  # G9876543N in SQ
    item.TypeOfPatientID = "TEXT"
    ds.OtherPatientIDsSequence = Sequence([item])
    req = Dataset()
    req.ScheduledProcedureStepDescription = f"Referred by {PLANTED['names'][3]}"
    ds.RequestAttributesSequence = Sequence([req])
    ds.PatientIdentityRemoved = "NO"
    return ds


def _finalise(ds: Dataset, sop_class: str) -> Dataset:
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = sop_class
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _set_gray_pixels(ds: Dataset, arr: np.ndarray, frames: int = 1) -> None:
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows, ds.Columns = arr.shape[-2], arr.shape[-1]
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    if frames > 1:
        ds.NumberOfFrames = frames
    ds.PixelData = arr.astype("<u2").tobytes()


def build_corpus(out_dir: str) -> dict:
    """Write the fixtures under ``out_dir`` and return a manifest describing them.

    The manifest lists every fixture (rel path, encoding, transfer syntax, and
    whether it carries burned-in pixel text) plus the canonical ``PLANTED``
    ground truth, and is also written to ``planted_manifest.json``.
    """
    os.makedirs(out_dir, exist_ok=True)
    fixtures = []

    # 1. single frame (uncompressed) - the fullest metadata fixture ------------
    ds = _finalise(_base_ds(_SC_SOP), _SC_SOP)
    _set_gray_pixels(ds, _burn_text_gray(128, 256, PLANTED["names"][1]))
    rel = "single_frame.dcm"
    pydicom.dcmwrite(os.path.join(out_dir, rel), ds, enforce_file_format=True)
    fixtures.append({"rel": rel, "encoding": "single_frame",
                     "transfer_syntax": str(ExplicitVRLittleEndian),
                     "burned_in": True})

    # 2. multiframe / cine -----------------------------------------------------
    ds = _finalise(_base_ds(_US_MF_SOP), _US_MF_SOP)
    ds.Modality = "US"
    nf = 8
    vol = np.stack([_burn_text_gray(96, 192, PLANTED["names"][1]) for _ in range(nf)])
    _set_gray_pixels(ds, vol, frames=nf)
    rel = "multiframe_cine.dcm"
    pydicom.dcmwrite(os.path.join(out_dir, rel), ds, enforce_file_format=True)
    fixtures.append({"rel": rel, "encoding": "multiframe_cine",
                     "transfer_syntax": str(ExplicitVRLittleEndian),
                     "burned_in": True})

    # 3. RGB (colour-Doppler-like) ---------------------------------------------
    ds = _finalise(_base_ds(_US_MF_SOP), _US_MF_SOP)
    ds.Modality = "US"
    rgb = _burn_text_rgb(96, 192, PLANTED["names"][1])
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "RGB"
    ds.PlanarConfiguration = 0
    ds.Rows, ds.Columns = rgb.shape[0], rgb.shape[1]
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = rgb.tobytes()
    ds["PixelData"].VR = "OB"
    rel = "rgb.dcm"
    pydicom.dcmwrite(os.path.join(out_dir, rel), ds, enforce_file_format=True)
    fixtures.append({"rel": rel, "encoding": "rgb",
                     "transfer_syntax": str(ExplicitVRLittleEndian),
                     "burned_in": True})

    # 4. JPEG2000 (compressed transfer syntax) ---------------------------------
    ds = _finalise(_base_ds(_SC_SOP), _SC_SOP)
    _set_gray_pixels(ds, _burn_text_gray(128, 256, PLANTED["names"][1]))
    ds.compress(JPEG2000Lossless)
    rel = "jpeg2000.dcm"
    pydicom.dcmwrite(os.path.join(out_dir, rel), ds, enforce_file_format=True)
    fixtures.append({"rel": rel, "encoding": "jpeg2000",
                     "transfer_syntax": str(ds.file_meta.TransferSyntaxUID),
                     "burned_in": True})

    # 5. NIfTI (planted name in the header description) -------------------------
    import nibabel as nib

    vol = (np.arange(8 * 8 * 4, dtype=np.float32).reshape(8, 8, 4) % 97)
    img = nib.Nifti1Image(vol, affine=np.eye(4))
    img.header["descrip"] = PLANTED["names"][4].encode("ascii", "replace")[:80]
    rel = "volume.nii.gz"
    nib.save(img, os.path.join(out_dir, rel))
    fixtures.append({"rel": rel, "encoding": "nifti",
                     "transfer_syntax": "nifti-1", "burned_in": False})

    manifest = {"out": out_dir, "planted": PLANTED, "fixtures": fixtures}
    with open(os.path.join(out_dir, "planted_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


# --------------------------------------------------------------------------- #
# Acceptance gate: literal planted-PHI survivor check over outputs.           #
# --------------------------------------------------------------------------- #
_NIFTI_EXTS = (".nii", ".nii.gz")
_NIFTI_TEXT_FIELDS = ("descrip", "aux_file", "intent_name")
_LITERAL_CATS = ("names", "nric_fin", "passport", "phone", "email",
                 "mrn", "accession", "address")
# VRs whose values are numeric/binary, never free-text PHI.
_NONTEXT_VR = {"OB", "OW", "OF", "OD", "UN", "US", "SS", "FL", "FD",
               "SL", "SL", "UL", "AT", "FE"}


def _element_text(ds) -> str:
    parts = []
    for elem in ds:
        if elem.VR == "SQ":
            for item in elem.value:
                parts.append(_element_text(item))
            continue
        if elem.VR in _NONTEXT_VR:
            continue
        v = elem.value
        if isinstance(v, (bytes, bytearray)):
            continue
        if isinstance(v, (list, tuple)):
            parts.append(" ".join(str(x) for x in v))
        else:
            parts.append(str(v))
    return " ".join(parts)


def check_survivors(out_dir: str, planted: dict | None = None) -> dict:
    """Exact-literal sweep for planted PHI over every output's metadata.

    Recurses sequences and reads private tags; PN separators (``^`` / ``=``) are
    normalised to spaces so a name split across components still matches. NIfTI
    header text fields are checked too. Returns ``{n_files, metadata_survivors,
    passed}`` where ``metadata_survivors`` maps category -> surviving literals.
    """
    planted = planted or PLANTED
    survivors: dict[str, list] = {}
    n_files = 0
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            if fn.endswith(".sig.json") or fn == "planted_manifest.json":
                continue
            full = os.path.join(root, fn)
            low = full.lower()
            if any(low.endswith(ext) for ext in _NIFTI_EXTS):
                import nibabel as nib
                try:
                    hdr = nib.load(full).header
                except Exception:  # noqa: BLE001
                    continue
                text = " ".join(
                    bytes(hdr[f]).split(b"\x00", 1)[0].decode("latin-1", "replace")
                    for f in _NIFTI_TEXT_FIELDS)
            else:
                try:
                    ds = pydicom.dcmread(full, force=True)
                except Exception:  # noqa: BLE001
                    continue
                text = _element_text(ds)
            n_files += 1
            norm = text.replace("^", " ").replace("=", " ")
            for cat in _LITERAL_CATS:
                for val in planted.get(cat, []):
                    if val and val in norm:
                        survivors.setdefault(cat, [])
                        if val not in survivors[cat]:
                            survivors[cat].append(val)
    return {"n_files": n_files, "metadata_survivors": survivors,
            "passed": not survivors}
