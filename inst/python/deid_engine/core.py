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
from . import textscan as _textscan
from .actions import DeidContext, apply_action

# Text VRs whose values may hide free-text PHI and are auto-scanned (Phase 2)
# when no explicit action already covers the element.
_TEXT_VRS = {"LO", "SH", "ST", "LT", "UT", "PN", "UC"}

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


def _auto_scan(ds, tag, ctx, records) -> None:
    """Run the text scanner over an unmapped text element; record only if it
    actually redacted something (keeps clean fields untouched, no noise)."""
    if ctx.scanner is None:
        return
    rec = apply_action(ds, tag, "C", ctx)
    if "note" in rec:
        return
    if rec.get("result") != rec.get("original"):
        rec["auto"] = True
        records.append(rec)


def _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan) -> None:
    """Depth-first application of actions, recursing into sequences (SQ)."""
    for tag in list(ds.keys()):
        elem = ds[tag]
        if elem.VR == "SQ":
            for item in elem.value:
                _walk(item, amap, ctx, private_policy, allowlist, records, text_scan)
            continue
        tagi = int(tag)
        if tagi in amap:
            records.append(apply_action(ds, tagi, amap[tagi], ctx))
        elif _is_private(tagi):
            if private_policy == "keep_all" or tagi in allowlist:
                if text_scan and elem.VR in _TEXT_VRS:
                    _auto_scan(ds, tagi, ctx, records)  # scrub kept private text
                continue
            records.append(apply_action(ds, tagi, "X", ctx))
        elif text_scan and elem.VR in _TEXT_VRS:
            _auto_scan(ds, tagi, ctx, records)


def _resolve_gazetteer(gfile):
    """Resolve a gazetteer path: absolute as-is, else relative to inst/
    (so profiles can ship a portable ``gazetteers/...`` path). Returns an
    existing path or None."""
    if not gfile:
        return None
    if os.path.isabs(gfile) and os.path.exists(gfile):
        return gfile
    inst_root = _rules.PROFILE_DIR.parent  # inst/
    cand = os.path.join(str(inst_root), gfile)
    if os.path.exists(cand):
        return cand
    return gfile if os.path.exists(gfile) else None


def _build_scanner(td: dict, known_values):
    """Assemble the layered TextScanner from the profile's text_detection block.

    Deterministic layers (header tokens + SG recognisers) always run. A gazetteer
    is loaded from an inline list and/or a name file; Presidio / transformer NER
    light up only if enabled *and* their packages/models are present (else the
    scanner degrades to the deterministic layers and notes why).
    """
    names = list(td.get("gazetteer") or [])
    gfile = _resolve_gazetteer(td.get("gazetteer_file"))
    if gfile:
        with open(gfile, encoding="utf-8") as fh:
            names += [ln.strip() for ln in fh
                      if ln.strip() and not ln.lstrip().startswith("#")]
    gaz = _textscan.Gazetteer(names) if names else None
    return _textscan.TextScanner(
        known_values=known_values, gazetteer=gaz,
        use_presidio=bool(td.get("use_presidio", False)),
        use_ner=bool(td.get("use_ner", False)),
        ner_model=td.get("ner_model"))


def deidentify_dataset(ds, profile: dict, salt: bytes, scanner=None) -> dict:
    """De-identify a pydicom Dataset in place; return a change report.

    PS3.15 actions with sequence recursion, private-tag policy, deterministic
    pseudonymisation/UID-remap, opt-in date-shift, header-token scrubbing, and the
    Phase 2 layered text scanner. Pass ``scanner`` to reuse one (heavy optional
    layers loaded once) across a study; otherwise one is built from the profile.
    """
    catalog = _rules.load_catalog()
    amap = _rules.build_action_map(catalog, profile)

    known_values = _collect_known_values(ds)
    patient_key = str(ds.get("PatientID", "") or ds.get("PatientName", ""))
    lo, hi = ((profile.get("dates") or {}).get("offset_days_range") or [-365, 365])[:2]
    date_offset = _ps.date_offset_days(patient_key, salt, int(lo), int(hi))
    truncate = int((profile.get("pseudonym") or {}).get("hash_truncate", 16))

    td = profile.get("text_detection") or {}
    text_scan = td.get("enabled", True)
    if not text_scan:
        scanner = None
    elif scanner is None:
        scanner = _build_scanner(td, known_values)
    else:
        scanner.set_known_values(known_values)  # reuse across files

    ctx = DeidContext(salt=salt, truncate=truncate, date_offset=date_offset,
                      known_values=known_values, uid_cache={}, scanner=scanner)

    pt = profile.get("private_tags") or {}
    private_policy = pt.get("policy", "strip_unknown")
    allowlist = {t for t in (_rules.parse_tag(x) for x in (pt.get("allowlist") or [])) if t}

    records: list[dict] = []
    _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan)

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

    # Build the scanner once per study so the optional Presidio/NER models load a
    # single time and are reused across every file in the batch.
    td = profile.get("text_detection") or {}
    scanner = _build_scanner(td, []) if td.get("enabled", True) else None

    for full, rel in _iter_input_files(input_path):
        out = output_path if single_file else os.path.join(output_path, rel)
        try:
            ds = pydicom.dcmread(full)
        except Exception as e:
            files_report.append({"input": full, "output": None,
                                 "skipped": f"not readable as DICOM: {e}"})
            continue

        report = deidentify_dataset(ds, profile, salt, scanner=scanner)
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
