"""Core engine surface (scaffold).

The real pipeline (Phases 1-6):

    read (pydicom/gdcm/nibabel, any transfer syntax incl. JPEG2000)
      -> apply PS3.15 action codes per the profile, recursing into sequences (SQ)
      -> private-tag policy (strip unknown / keep allowlisted)
      -> text PHI: header-token scrub + gazetteer + Presidio (+ SG recognisers) + NER
      -> pixel PHI: OCR + NER + redaction boxes (auto + manual), per-frame/RGB
      -> pseudonymise (salted SHA-256), remap UIDs, shift dates (preserve intervals)
      -> write valid, viewable DICOM (or NIfTI for NIfTI input)

Everything here is deliberately dependency-light so `import deid_engine` works
even before the PHI/NER extras are installed.
"""

from __future__ import annotations

# DICOM PS3.15 Annex E action codes + app extensions (H hash, S date-shift).
ACTION_CODES = {
    "D": "replace with dummy / pseudonym of same VR",
    "Z": "replace with zero-length value",
    "X": "remove element",
    "K": "keep",
    "C": "clean embedded PHI from text",
    "U": "replace UID with consistent remapped UID",
    "H": "deterministic salted SHA-256 pseudonym",
    "S": "shift date/time by per-patient interval-preserving offset",
}


def engine_info() -> dict:
    """Report what the installed engine can currently do (used by the R status badge)."""
    caps = {"dicom_io": False, "compressed": False, "nifti": False,
            "presidio": False, "ner": False, "ocr": False}
    try:
        import pydicom  # noqa: F401
        caps["dicom_io"] = True
    except Exception:
        pass
    try:
        import gdcm  # noqa: F401
        caps["compressed"] = True
    except Exception:
        pass
    try:
        import nibabel  # noqa: F401
        caps["nifti"] = True
    except Exception:
        pass
    try:
        import presidio_analyzer  # noqa: F401
        caps["presidio"] = True
    except Exception:
        pass
    try:
        import pytesseract  # noqa: F401
        caps["ocr"] = True
    except Exception:
        pass
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
        caps["ner"] = True
    except Exception:
        pass
    return {"version": "0.0.1", "capabilities": caps, "actions": ACTION_CODES}


def deidentify_study(input_path: str, output_path: str, profile: dict) -> dict:
    """Phase 1/3. De-identify one study/folder; return a per-tag report.

    Returns a dict summarising actions taken, so the R side can render the
    before/after diff and the QA layer can verify.
    """
    raise NotImplementedError("deidentify_study lands in Phase 1 (metadata) + Phase 3 (pixels)")


def scan_residual(path: str, catalog: dict) -> dict:
    """Phase 6. Re-run all detectors on an OUTPUT and report any residual PHI."""
    raise NotImplementedError("scan_residual lands in Phase 6 (QA)")
