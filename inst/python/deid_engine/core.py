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

import os
import re

from . import rules as _rules
from . import pseudonym as _ps
from . import keystore as _keystore
from .actions import DeidContext, apply_action

# Tags whose values seed the header-token scrub of free text / pixels.
_KNOWN_VALUE_TAGS = (
    0x00100010,  # PatientName
    0x00100020,  # PatientID
    0x00081050,  # PerformingPhysicianName
    0x00080090,  # ReferringPhysicianName
    0x00080050,  # AccessionNumber
    0x00101000,  # OtherPatientIDs
)

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


def _is_private(tag: int) -> bool:
    """Private tags live in odd-numbered groups."""
    return ((tag >> 16) & 1) == 1


def _collect_known_values(ds) -> list[str]:
    """Gather the real identifier values (names, IDs) to scrub from free text/pixels."""
    vals: set[str] = set()
    for tag in _KNOWN_VALUE_TAGS:
        if tag in ds:
            raw = ds[tag].value
            for piece in (raw if isinstance(raw, (list, tuple)) else [raw]):
                s = str(piece).strip()
                if not s:
                    continue
                vals.add(s)
                # name components (split on the DICOM '^' separator and whitespace)
                for tok in re.split(r"[\^\s]+", s):
                    if len(tok) > 1:
                        vals.add(tok)
    return [v for v in vals if v]


def _walk(ds, amap, ctx, private_policy, allowlist, records) -> None:
    """Depth-first application of actions, recursing into sequences (SQ)."""
    for tag in list(ds.keys()):
        elem = ds[tag]
        if elem.VR == "SQ":
            for item in elem.value:
                _walk(item, amap, ctx, private_policy, allowlist, records)
            continue
        tagi = int(tag)
        if tagi in amap:
            records.append(apply_action(ds, tagi, amap[tagi], ctx))
        elif _is_private(tagi):
            if private_policy == "keep_all" or tagi in allowlist:
                continue
            records.append(apply_action(ds, tagi, "X", ctx))


def deidentify_dataset(ds, profile: dict, salt: bytes) -> dict:
    """De-identify a pydicom Dataset in place; return a change report.

    This is the metadata core (Phase 1): PS3.15 actions with sequence recursion,
    private-tag policy, deterministic pseudonymisation/UID-remap, opt-in
    date-shift, and header-token scrubbing of free text.
    """
    catalog = _rules.load_catalog()
    amap = _rules.build_action_map(catalog, profile)

    known_values = _collect_known_values(ds)
    patient_key = str(ds.get("PatientID", "") or ds.get("PatientName", ""))
    lo, hi = ((profile.get("dates") or {}).get("offset_days_range") or [-365, 365])[:2]
    date_offset = _ps.date_offset_days(patient_key, salt, int(lo), int(hi))
    truncate = int((profile.get("pseudonym") or {}).get("hash_truncate", 16))

    ctx = DeidContext(salt=salt, truncate=truncate, date_offset=date_offset,
                      known_values=known_values, uid_cache={})

    pt = profile.get("private_tags") or {}
    private_policy = pt.get("policy", "strip_unknown")
    allowlist = {t for t in (_rules.parse_tag(x) for x in (pt.get("allowlist") or [])) if t}

    records: list[dict] = []
    _walk(ds, amap, ctx, private_policy, allowlist, records)

    changed = [r for r in records if r.get("action") != "K" and "note" not in r]
    by_action: dict[str, int] = {}
    for r in changed:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return {"records": records, "counts": {"total": len(changed), "by_action": by_action}}


_DEID_METHOD = "dicomdeid: PS3.15 basic profile + header-scrub"


def _iter_input_files(input_path):
    """Yield (abs_file, rel_path) for a single file or every file under a folder."""
    if os.path.isdir(input_path):
        for root, _dirs, files in os.walk(input_path):
            for fn in files:
                full = os.path.join(root, fn)
                yield full, os.path.relpath(full, input_path)
    else:
        yield input_path, os.path.basename(input_path)


def _record_crosswalk(keystore, records) -> None:
    """Populate the reversible crosswalk from the change records (H/D/U)."""
    for r in records:
        if r.get("action") in ("H", "D", "U") and r.get("original") and r.get("result"):
            keystore.record(hex(r["tag"]), r["original"], r["result"])


def deidentify_study(input_path: str, output_path: str, profile: dict, keystore) -> dict:
    """De-identify one DICOM file or a folder of them; write valid DICOM out.

    ``keystore`` supplies the salt and (in reversible mode) records the crosswalk.
    Returns a per-file report.
    """
    import pydicom

    salt = keystore.salt
    single_file = not os.path.isdir(input_path)
    files_report = []

    for full, rel in _iter_input_files(input_path):
        out = output_path if single_file else os.path.join(output_path, rel)
        try:
            ds = pydicom.dcmread(full)
        except Exception as e:
            files_report.append({"input": full, "output": None,
                                 "skipped": f"not readable as DICOM: {e}"})
            continue

        report = deidentify_dataset(ds, profile, salt)
        _record_crosswalk(keystore, report["records"])
        enriched = _enrich_records(report["records"])

        # keep file-meta consistent so the output stays a valid, viewable object
        if getattr(ds, "file_meta", None) is not None and "SOPInstanceUID" in ds:
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.PatientIdentityRemoved = "YES"
        ds.DeidentificationMethod = _DEID_METHOD

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        pydicom.dcmwrite(out, ds, enforce_file_format=True)
        files_report.append({"input": full, "output": out,
                             "counts": report["counts"], "records": enriched})

    processed = [f for f in files_report if f.get("output")]
    return {"files": files_report, "count": len(processed)}


def _enrich_records(records) -> list:
    """Add a human-readable keyword to each change record for the diff table."""
    from pydicom.datadict import keyword_for_tag
    out = []
    for r in records:
        rr = dict(r)
        try:
            rr["keyword"] = keyword_for_tag(r["tag"]) or hex(r["tag"])
        except Exception:
            rr["keyword"] = hex(r.get("tag", 0))
        out.append(rr)
    return out


def deid_run(input_path: str, output_path: str, profile_id: str = "default",
             keystore_path: str | None = None, passphrase: str | None = None) -> dict:
    """High-level entry used by the R UI.

    Loads the named profile, opens/creates a keystore (or an ephemeral,
    irreversible one when no path is given), de-identifies, and returns the
    per-file report with human-readable change records.
    """
    profile = _rules.load_profile(profile_id)
    if keystore_path:
        if os.path.exists(keystore_path):
            ks_obj = _keystore.open(keystore_path, passphrase)
        else:
            ks_obj = _keystore.create(keystore_path, passphrase)
    else:
        ks_obj = _keystore.ephemeral()

    report = deidentify_study(input_path, output_path, profile, ks_obj)
    if keystore_path:
        ks_obj.save()
    report["reversible"] = bool(keystore_path)
    return report


def scan_residual(path: str, catalog: dict) -> dict:
    """Phase 6. Re-run all detectors on an OUTPUT and report any residual PHI."""
    raise NotImplementedError("scan_residual lands in Phase 6 (QA)")
