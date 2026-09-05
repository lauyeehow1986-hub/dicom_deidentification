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

import base64
import io
import json
import os
import re
from pathlib import Path

import yaml

from . import rules as _rules
from . import pseudonym as _ps
from . import keystore as _keystore
from . import signing as _signing
from . import textscan as _textscan
from . import pixels as _pixels
from . import deface as _deface
from . import documents as _documents
from . import workspace as _workspace
from .actions import DeidContext, apply_action

# Text VRs whose values may hide free-text PHI and are auto-scanned (Phase 2)
# when no explicit action already covers the element.
_TEXT_VRS = {"LO", "SH", "ST", "LT", "UT", "PN", "UC"}

# EncapsulatedDocument (e.g. an embedded PDF report). Normally removed by the
# catalog's `X` action; under `encapsulated_pdf.mode: rasterize_redact` the
# study-level pipeline redacts it instead, so `_walk` must leave it in place.
ENCAPS_DOC_TAG = 0x00420011  # EncapsulatedDocument

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


def _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan,
          pdf_mode="remove") -> None:
    """Depth-first application of actions, recursing into sequences (SQ)."""
    for tag in list(ds.keys()):
        elem = ds[tag]
        if int(tag) == ENCAPS_DOC_TAG and pdf_mode == "rasterize_redact":
            continue  # leave the PDF bytes; deidentify_study redacts them
        if elem.VR == "SQ":
            tagi = int(tag)
            # An explicit remove/blank on the sequence itself (e.g. WaveformSequence
            # -> X) wins over recursing into its items.
            if tagi in amap and amap[tagi] in ("X", "Z"):
                records.append(apply_action(ds, tagi, amap[tagi], ctx))
                continue
            for item in elem.value:
                _walk(item, amap, ctx, private_policy, allowlist, records,
                      text_scan, pdf_mode)
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


def _resolve_ner_model(ner_model):
    """Resolve a NER model directory: absolute as-is, else relative to inst/
    (so profiles can ship a portable ``models/...`` path that works no matter
    the process working directory). Returns an existing directory or None."""
    if not ner_model:
        return None
    if os.path.isabs(ner_model) and os.path.isdir(ner_model):
        return ner_model
    inst_root = _rules.PROFILE_DIR.parent  # inst/
    cand = os.path.join(str(inst_root), ner_model)
    if os.path.isdir(cand):
        return cand
    return ner_model if os.path.isdir(ner_model) else None


def _build_scanner(td: dict, known_values):
    """Assemble the layered TextScanner from the profile's text_detection block.

    Deterministic layers (header tokens + SG recognisers) always run. A gazetteer
    is loaded from an inline list and/or a name file; Presidio / transformer NER
    light up only if enabled *and* their packages/models are present (else the
    scanner degrades to the deterministic layers and notes why).
    """
    names = list(td.get("gazetteer") or [])
    files = []
    if td.get("gazetteer_file"):
        files.append(td["gazetteer_file"])
    files += list(td.get("extra_gazetteer_files") or [])  # grown by tagging misses
    for gf in files:
        gfile = _resolve_gazetteer(gf)
        if gfile:
            with open(gfile, encoding="utf-8") as fh:
                names += [ln.strip() for ln in fh
                          if ln.strip() and not ln.lstrip().startswith("#")]
    gaz = _textscan.Gazetteer(names) if names else None
    return _textscan.TextScanner(
        known_values=known_values, gazetteer=gaz,
        custom_regex=td.get("custom_regex") or [],
        use_presidio=bool(td.get("use_presidio", False)),
        use_ner=bool(td.get("use_ner", False)),
        ner_model=_resolve_ner_model(td.get("ner_model")))


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

    pdf_mode = (profile.get("encapsulated_pdf") or {}).get("mode", "remove")

    records: list[dict] = []
    _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan, pdf_mode)

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


_NIFTI_EXTS = (".nii", ".nii.gz")
# NIfTI-1/2 header text fields that can carry free-text PHI.
_NIFTI_TEXT_FIELDS = ("descrip", "aux_file", "intent_name")


def _is_nifti(path: str) -> bool:
    low = path.lower()
    return any(low.endswith(ext) for ext in _NIFTI_EXTS)


# BIDS/dcm2niix JSON sidecar keys whose values seed the header-token scrub of
# the sidecar's own free-text keys (and the paired volume's filename).
_JSON_KNOWN_KEYS = (
    "PatientName", "PatientID", "OtherPatientIDs", "PatientBirthName",
    "PerformingPhysicianName", "ReferringPhysicianName", "OperatorsName",
    "AccessionNumber",
)
_DATE_VRS = {"DA", "DT", "TM"}
_DATE_ACTION_BY_MODE = {"remove": "X", "shift": "S", "keep": "K"}


def _deidentify_nifti(full: str, out: str, profile: dict | None = None,
                      scanner=None, ocr_autoredact: bool = False,
                      known_values=None) -> dict:
    """Copy a NIfTI volume through, blanking header PHI surfaces, and (when the
    profile turns pixel cleaning on) OCR-scanning its slices for burned-in text.

    Header text fields + all extensions are removed; the NIfTI-1/2 class is kept.
    Burned-in-text OCR reuses the DICOM OCR layer: with ``ocr_autoredact`` the
    detected PHI boxes are zeroed; otherwise only their count is recorded (there
    is no NIfTI pixel reviewer workflow yet). The image data is otherwise
    preserved exactly.
    """
    import nibabel as nib
    import numpy as np

    img = nib.load(full)
    hdr = img.header.copy()
    records = []
    scrubbed = 0
    for field in _NIFTI_TEXT_FIELDS:
        try:
            cur = bytes(hdr[field]).split(b"\x00", 1)[0]
        except (KeyError, ValueError):
            continue
        if cur:
            scrubbed += 1
            records.append({"tag": 0, "keyword": f"nifti:{field}", "action": "Z",
                            "original": cur.decode("latin-1", "replace"),
                            "result": ""})
        hdr[field] = b""

    # Strip ALL header extensions: any may embed DICOM (code 2) or freeform PHI.
    ext_removed = 0
    try:
        exts = hdr.extensions
        ext_removed = len(exts)
        if ext_removed:
            del exts[:]
    except (AttributeError, TypeError):
        pass

    data = np.asanyarray(img.dataobj)
    counts = {"nifti_header_fields_scrubbed": scrubbed,
              "nifti_extensions_removed": ext_removed}

    # Optional ML defacing (MR-only, FOV-gated; degrades when weights absent).
    if _deface_enabled(profile or {}):
        try:
            data, dinfo = _deface.deface_array(data, modality=None)
            counts["deface"] = dinfo
        except Exception as e:  # noqa: BLE001 - deface must not lose the file
            counts["deface_error"] = str(e)

    # Burned-in-text OCR (optional; degrades like the DICOM pixel layer).
    clean_pixels = bool((profile or {}).get("options", {}).get("clean_pixel_data", True))
    if clean_pixels and scanner is not None:
        if data.ndim != 3:
            counts["nifti_ocr_note"] = "skipped: not 3-D"
        else:
            ok, note = _pixels._ocr_available()
            if not ok:
                counts["nifti_ocr_note"] = note
            else:
                try:
                    if known_values:
                        try:
                            scanner.set_known_values(known_values)
                        except Exception:  # noqa: BLE001 - a minimal scanner may lack it
                            pass
                    boxes = _pixels.volume_ocr_boxes(data, scanner)
                    counts["nifti_ocr_boxes"] = len(boxes)
                    if boxes and ocr_autoredact:
                        data = _pixels.redact_volume_boxes(data, boxes, fill=0)
                        counts["nifti_pixels_redacted"] = len(boxes)
                except Exception as e:  # noqa: BLE001 - pixel step must not lose the file
                    counts["nifti_ocr_error"] = str(e)

    # type(img) keeps NIfTI-1 as NIfTI-1 and NIfTI-2 as NIfTI-2.
    out_img = type(img)(np.ascontiguousarray(data), img.affine, header=hdr)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    nib.save(out_img, out)
    # nib.save appends the class's default extension when `out` lacks one
    # nibabel recognises (e.g. "out" -> "out.nii"); report the real path.
    saved_as = out_img.get_filename() or out
    return {"input": full, "output": saved_as, "counts": counts, "records": records}


def _is_json_sidecar(path: str) -> bool:
    return path.lower().endswith(".json")


def _basename_stem(name: str) -> str:
    """Stem of a filename, treating ``.nii.gz`` as one extension."""
    base = os.path.basename(name)
    low = base.lower()
    if low.endswith(".nii.gz"):
        return base[:-7]
    return os.path.splitext(base)[0]


def _collect_known_values_from_map(d: dict) -> list[str]:
    """Real identifier values (+ name/id tokens) from a JSON sidecar's ID keys."""
    vals: set[str] = set()
    for key in _JSON_KNOWN_KEYS:
        raw = d.get(key)
        if raw is None:
            continue
        for piece in (raw if isinstance(raw, (list, tuple)) else [raw]):
            s = str(piece).strip()
            if not s:
                continue
            vals.add(s)
            for tok in re.split(r"[\^\s]+", s):
                if len(tok) > 1:
                    vals.add(tok)
    return [v for v in vals if v]


def _json_key_action(key: str, amap: dict, date_action: str):
    """Resolve the PS3.15 action for a BIDS key by reusing the DICOM action map
    (BIDS keys mirror DICOM keywords). Date-VR keys get the profile's date policy
    even when the specific tag isn't in the catalog. Unknown keys return None
    (scan their values, keep the key)."""
    from pydicom.datadict import tag_for_keyword, dictionary_VR
    try:
        tag = tag_for_keyword(key)
    except Exception:
        tag = None
    if tag is not None:
        act = amap.get(int(tag))
        if act:
            return act
        try:
            if dictionary_VR(int(tag)) in _DATE_VRS:
                return date_action
        except Exception:
            pass
    return None


def _shift_iso_datetime(value: str, offset_days: int):
    """Shift the date part of an ISO date/datetime by ``offset_days``; keep the
    time/zone suffix. Returns None when the string isn't ISO-dated."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(.*)$", value or "")
    if not m:
        return None
    from datetime import date, timedelta
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (d + timedelta(days=offset_days)).isoformat() + m.group(4)


def _scrub_json_value(val, scanner):
    """Recursively redact PHI from a JSON value; return (clean, n_redacted)."""
    if isinstance(val, str):
        red, spans = scanner.redact(val)
        return red, len(spans)
    if isinstance(val, list):
        out, n = [], 0
        for v in val:
            rv, k = _scrub_json_value(v, scanner)
            out.append(rv)
            n += k
        return out, n
    if isinstance(val, dict):
        out, n = {}, 0
        for k, v in val.items():
            rv, c = _scrub_json_value(v, scanner)
            out[k] = rv
            n += c
        return out, n
    return val, 0


def _shift_json_value(val, offset_days: int, scanner):
    """Date-shift ISO date strings in a JSON value; fall back to scrubbing."""
    if isinstance(val, str):
        sv = _shift_iso_datetime(val, offset_days)
        if sv is not None:
            return sv, 1
        return _scrub_json_value(val, scanner)
    if isinstance(val, list):
        out, n = [], 0
        for v in val:
            rv, k = _shift_json_value(v, offset_days, scanner)
            out.append(rv)
            n += k
        return out, n
    return val, 0


def _deidentify_json_sidecar(full: str, out: str, scanner, amap: dict,
                             date_action: str, salt: bytes, lo: int, hi: int) -> dict:
    """De-identify a dcm2niix/BIDS ``.json`` sidecar in place.

    Identifier keys (via the DICOM action map) are dropped; date keys are shifted
    or removed per the profile; every remaining string value is run through the
    text scanner (seeded on the sidecar's own identifiers) so free-text PHI is
    caught. Non-object JSON is value-scrubbed wholesale.
    """
    try:
        with open(full, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:  # noqa: BLE001 - a malformed sidecar must not lose the run
        return {"input": full, "output": None, "skipped": f"not valid JSON: {e}"}

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if not isinstance(data, dict):
        cleaned, n = _scrub_json_value(data, scanner)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(cleaned, fh, indent=2, ensure_ascii=False)
        return {"input": full, "output": out,
                "counts": {"json_values_scrubbed": n}, "records": []}

    scanner.set_known_values(_collect_known_values_from_map(data))
    patient_key = str(data.get("PatientID", "") or data.get("PatientName", ""))
    offset = _ps.date_offset_days(patient_key, salt, int(lo), int(hi))

    cleaned: dict = {}
    removed = scrubbed = shifted = 0
    for key, val in data.items():
        act = _json_key_action(key, amap, date_action)
        if act in ("X", "Z", "D", "H", "U"):
            removed += 1
            continue
        if act == "S":
            nv, n = _shift_json_value(val, offset, scanner)
            cleaned[key] = nv
            shifted += n
            continue
        if act == "K":
            cleaned[key] = val
            continue
        nv, n = _scrub_json_value(val, scanner)
        cleaned[key] = nv
        scrubbed += n

    with open(out, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, indent=2, ensure_ascii=False)
    return {"input": full, "output": out,
            "counts": {"json_keys_removed": removed,
                       "json_values_scrubbed": scrubbed,
                       "json_dates_shifted": shifted}, "records": []}


def _sanitise_basename(name: str, scanner, salt: bytes, truncate: int) -> str:
    """Replace whole identifier tokens (names/IDs/NRIC/phone) in a filename with a
    stable pseudonym, preserving separators and the (``.nii.gz``-aware) extension.
    Free-form concatenations that don't tokenise are left as-is (can't be edited
    safely without breaking references)."""
    base = os.path.basename(name)
    low = base.lower()
    if low.endswith(".nii.gz"):
        stem, ext = base[:-7], base[-7:]
    else:
        stem, ext = os.path.splitext(base)
    parts = re.split(r"([_\-.\s]+)", stem)
    out = []
    for p in parts:
        if not p or re.fullmatch(r"[_\-.\s]+", p):
            out.append(p)
            continue
        spans = scanner.scan(p)
        if spans and any((s.end - s.start) >= len(p) for s in spans):
            out.append(_ps.salted_sha256(p, salt, truncate))
        elif spans:
            red, _ = scanner.redact(
                p, replacement=lambda s: _ps.salted_sha256(s.text, salt, truncate))
            out.append(red)
        else:
            out.append(p)
    return "".join(out) + ext


def _pixel_autoredact_enabled(profile: dict) -> bool:
    """True when burned-in pixel PHI should be redacted UNattended.

    Only when the profile turns pixel cleaning on, keeps auto-detect, AND opts
    out of human confirmation. The shipped default keeps ``require_human_confirm``
    true, so a reviewer confirms boxes in the Pixels tab; a bulk profile can set
    it false to let the pipeline paint the OCR-proposed boxes on its own."""
    opts = profile.get("options") or {}
    px = profile.get("pixel") or {}
    return (bool(opts.get("clean_pixel_data"))
            and bool(px.get("auto_detect", True))
            and not bool(px.get("require_human_confirm", True)))


def _deface_enabled(profile: dict) -> bool:
    """True when the profile opts into ML defacing for this run (off by default)."""
    return bool((profile.get("deface") or {}).get("enabled"))


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
    # A deterministic scanner (no Presidio/NER) for JSON sidecars and filename
    # scrubbing even when the profile disables the optional text layers.
    aux_scanner = scanner if scanner is not None else _build_scanner(
        {**td, "use_presidio": False, "use_ner": False}, [])

    # NIfTI/JSON support: the action map, date policy, and a pre-pass mapping each
    # basename stem to the identifiers in its paired sidecar, so a volume's
    # filename can be sanitised from the sidecar that names its patient.
    _catalog = _rules.load_catalog()
    amap = _rules.build_action_map(_catalog, profile)
    date_mode = (profile.get("dates") or {}).get("mode", "remove")
    date_action = _DATE_ACTION_BY_MODE.get(date_mode, "X")
    truncate = int((profile.get("pseudonym") or {}).get("hash_truncate", 16))
    lo, hi = ((profile.get("dates") or {}).get("offset_days_range") or [-365, 365])[:2]
    sidecar_known: dict[str, list] = {}
    if not single_file:
        for jf, jrel in _iter_input_files(input_path):
            if not _is_json_sidecar(jf):
                continue
            try:
                with open(jf, encoding="utf-8") as fh:
                    jd = json.load(fh)
                if isinstance(jd, dict):
                    sidecar_known[_basename_stem(jrel)] = \
                        _collect_known_values_from_map(jd)
            except Exception:  # noqa: BLE001 - a bad sidecar just yields no seeds
                pass

    def _sanitised_out(out_path, rel_path, known):
        """Rename a folder-mode output basename if it carries identifier tokens."""
        if single_file:
            return out_path
        aux_scanner.set_known_values(known or [])
        new_base = _sanitise_basename(rel_path, aux_scanner, salt, truncate)
        return os.path.join(os.path.dirname(out_path), new_base)

    for full, rel in _iter_input_files(input_path):
        out = output_path if single_file else os.path.join(output_path, rel)
        if _is_nifti(full):
            out = _sanitised_out(out, rel, sidecar_known.get(_basename_stem(rel)))
            files_report.append(_deidentify_nifti(
                full, out, profile=profile, scanner=scanner,
                ocr_autoredact=_pixel_autoredact_enabled(profile),
                known_values=sidecar_known.get(_basename_stem(rel))))
            continue
        if _is_json_sidecar(full):
            out = _sanitised_out(out, rel, sidecar_known.get(_basename_stem(rel)))
            files_report.append(_deidentify_json_sidecar(
                full, out, aux_scanner, amap, date_action, salt, lo, hi))
            continue
        try:
            ds = pydicom.dcmread(full)
        except Exception as e:
            files_report.append({"input": full, "output": None,
                                 "skipped": f"not readable as DICOM: {e}"})
            continue

        # Capture the study's real identifiers BEFORE metadata de-id mutates them,
        # so unattended pixel OCR (and PDF OCR-redaction) can seed on the true
        # name/ID tokens.
        _need_original = (_pixel_autoredact_enabled(profile)
                          or (profile.get("encapsulated_pdf") or {}).get("mode")
                          == "rasterize_redact")
        known_original = _collect_known_values(ds) if _need_original else None
        known_for_pixels = known_original if _pixel_autoredact_enabled(profile) else None

        report = deidentify_dataset(ds, profile, salt, scanner=scanner)
        _record_crosswalk(keystore, report["records"])
        enriched = _enrich_records(report["records"])

        # Opt-in unattended burned-in-pixel redaction: paint the OCR-proposed PHI
        # boxes before writing. Metadata de-id leaves the pixels untouched, so the
        # boxes detected on ds's original frames still line up. Never fatal to the
        # metadata de-id already done.
        counts = dict(report["counts"])
        if known_for_pixels is not None and "PixelData" in ds:
            try:
                pscanner = _build_scanner(td, known_for_pixels)
                res = _pixels.ocr_phi_boxes(ds, pscanner)
                boxes = res.get("boxes", [])
                if boxes:
                    _pixels.redact_pixels(ds, boxes, fill=0)
                counts["pixel_boxes_redacted"] = len(boxes)
                if res.get("note"):
                    counts["pixel_ocr_note"] = res["note"]
            except Exception as e:  # noqa: BLE001 - pixel step must not lose the file
                counts["pixel_redact_error"] = str(e)

        # Optional ML defacing of a head-inclusive MR *volume* object (multiframe
        # grayscale = a whole 3-D volume in one file). Single-instance-per-slice
        # series (a 3-D face across many files) is out of scope. Degrades when the
        # model is absent; never fatal to the metadata de-id already done.
        if _deface_enabled(profile) and "PixelData" in ds:
            try:
                modality = str(getattr(ds, "Modality", "") or "")
                frames = _pixels.load_frames(ds)  # decodes even if compressed; no mutation
                if frames.ndim == 3 and frames.shape[0] > 1:
                    new, dinfo = _deface.deface_array(frames, modality=modality)
                    if dinfo.get("defaced"):
                        _pixels.store_frames(ds, new)  # writes valid uncompressed
                    counts["deface"] = dinfo
                else:
                    counts["deface"] = {"defaced": False,
                                        "reason": "not-a-multiframe-grayscale-volume"}
            except Exception as e:  # noqa: BLE001 - deface must not lose the file
                counts["deface_error"] = str(e)

        # Opt-in encapsulated-PDF redaction: rasterize + OCR-redact + flatten, or
        # (Tesseract absent) fall back to removal. Seed the scanner on the study's
        # ORIGINAL identifiers so the patient's real name is caught in the PDF.
        pdf_mode = (profile.get("encapsulated_pdf") or {}).get("mode", "remove")
        if pdf_mode == "rasterize_redact" and _documents.is_encapsulated_pdf(ds):
            try:
                pscanner = _build_scanner(td, known_original or [])
                pdpi = int((profile.get("encapsulated_pdf") or {}).get("dpi", 150))
                res = _documents.redact_encapsulated_pdf(ds, pscanner, dpi=pdpi)
                counts["encapsulated_pdf"] = res.get("mode")
                if res.get("boxes") is not None:
                    counts["encapsulated_pdf_boxes"] = res["boxes"]
                if res.get("note"):
                    counts["encapsulated_pdf_note"] = res["note"]
            except Exception as e:  # noqa: BLE001 - never lose the file over the PDF
                # Fail CLOSED: if the PDF step blew up before it could redact or
                # remove, strip the document so an un-redacted, identifiable PDF
                # is never shipped in a study stamped PatientIdentityRemoved=YES.
                _documents._remove_encapsulated_doc(ds)
                counts["encapsulated_pdf_error"] = str(e)

        # keep file-meta consistent so the output stays a valid, viewable object
        if getattr(ds, "file_meta", None) is not None and "SOPInstanceUID" in ds:
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.PatientIdentityRemoved = "YES"
        ds.DeidentificationMethod = _DEID_METHOD

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        pydicom.dcmwrite(out, ds, enforce_file_format=True)
        files_report.append({"input": full, "output": out,
                             "counts": counts, "records": enriched})

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
             keystore_path: str | None = None, passphrase: str | None = None,
             reversible: bool = True, sign_key_path: str | None = None,
             signer: str | None = None, project_id: str | None = None,
             autoredact_pixels: bool = False,
             pdf_mode: str | None = None, pdf_dpi: int | None = None,
             deface: bool = False) -> dict:
    """High-level entry used by the R UI.

    Loads the named profile, opens/creates a keystore (persisting the salt so
    pseudonyms stay stable across runs; the crosswalk is kept only when
    ``reversible``), de-identifies, records each output's before/after SHA-256,
    and — when ``sign_key_path`` is given — drops an Ed25519 sidecar signature
    next to every output. Returns the per-file report.

    ``autoredact_pixels`` forces unattended burned-in-pixel redaction for this run
    (as if a reviewer confirmed every OCR-proposed box): it turns the profile's
    pixel cleaning on and human-confirm off. The shipped profile is unchanged for
    every other caller - it keeps human confirmation as the safe default.

    ``pdf_mode``/``pdf_dpi`` override the profile's ``encapsulated_pdf`` policy
    (mode/dpi) for this run only; ``None`` (the default) leaves the profile's
    setting untouched. As with ``autoredact_pixels``, the shipped profile object
    is never mutated - only a local copy.

    ``deface`` turns on ML defacing of head-inclusive MR volumes for this run
    only (default off). As with the overrides above, the shipped profile object
    is never mutated - only a local copy.
    """
    profile = profile_get(profile_id)
    if autoredact_pixels:
        profile = dict(profile)
        profile["options"] = {**(profile.get("options") or {}),
                              "clean_pixel_data": True}
        profile["pixel"] = {**(profile.get("pixel") or {}),
                            "auto_detect": True, "require_human_confirm": False}
    if pdf_mode is not None or pdf_dpi is not None:
        profile = dict(profile)
        ep = dict(profile.get("encapsulated_pdf") or {})
        if pdf_mode is not None:
            ep["mode"] = pdf_mode
        if pdf_dpi is not None:
            ep["dpi"] = int(pdf_dpi)
        profile["encapsulated_pdf"] = ep
    if deface:
        profile = dict(profile)
        profile["deface"] = {**(profile.get("deface") or {}), "enabled": True}
    if keystore_path:
        if os.path.exists(keystore_path):
            ks_obj = _keystore.open(keystore_path, passphrase)
        else:
            ks_obj = _keystore.create(keystore_path, passphrase, reversible=reversible)
    else:
        # No persisted keystore: an ephemeral, irreversible salt (single run only).
        ks_obj = _keystore.ephemeral()

    report = deidentify_study(input_path, output_path, profile, ks_obj)
    if keystore_path:
        ks_obj.save()
    report["reversible"] = bool(keystore_path) and bool(ks_obj.reversible)

    # Integrity: before/after checksums, and an optional sidecar signature.
    for f in report["files"]:
        out = f.get("output")
        if not out:
            continue
        f["input_sha256"] = _signing.file_sha256(f["input"])
        f["output_sha256"] = _signing.file_sha256(out)
        if sign_key_path:
            f["signature"] = _signing.sign_output(
                out, {"deid_method": _DEID_METHOD, "profile_id": profile_id,
                      "project_id": project_id or "", "signed_by": signer or ""},
                sign_key_path)
    return report


def keystore_summary(path: str, passphrase: str) -> dict:
    """Reversibility policy + crosswalk size of a persisted keystore.

    Used by the acceptance runner and the keystore review screen to confirm the
    policy is honoured: reversible stores keep a populated crosswalk;
    irreversible stores persist only the salt (never a mapping).
    """
    ks = _keystore.open(path, passphrase)
    return {"reversible": ks.reversible, "n_crosswalk": len(ks._crosswalk)}


def keystore_reverse(path: str, passphrase: str, pseudonym: str):
    """Authorised re-identification: map a pseudonym back to its original.

    Returns the original value in reversible mode, or ``None`` (irreversible, or
    unknown pseudonym).
    """
    return _keystore.open(path, passphrase).reverse(pseudonym)


# --------------------------------------------------------------------------- #
# Phase 4 - per-project profiles + the tag-a-miss self-improvement loop        #
# --------------------------------------------------------------------------- #

_PROFILE_SUFFIX = "_profile.yml"


def _workspace_profile_path(profile_id: str) -> Path:
    return _workspace.profiles_dir() / f"{profile_id}{_PROFILE_SUFFIX}"


def _shipped_profile_ids() -> list[str]:
    return sorted(p.name[: -len(_PROFILE_SUFFIX)]
                  for p in _rules.PROFILE_DIR.glob(f"*{_PROFILE_SUFFIX}"))


def profile_get(profile_id: str = "default") -> dict:
    """The effective profile for an id. A workspace copy (edited/cloned in the
    app) wins over the shipped default; ``_source`` records which was used."""
    wp = _workspace_profile_path(profile_id)
    if wp.exists():
        prof = yaml.safe_load(wp.read_text(encoding="utf-8")) or {}
        prof["_source"] = "workspace"
        return prof
    prof = _rules.load_profile(profile_id)
    prof["_source"] = "shipped"
    return prof


def profiles_list() -> list[dict]:
    """Every selectable profile: shipped defaults plus workspace project profiles
    (a workspace profile shadows a shipped one of the same id)."""
    out: dict[str, dict] = {}
    for pid in _shipped_profile_ids():
        prof = _rules.load_profile(pid)
        out[pid] = {"id": pid, "label": prof.get("label", pid),
                    "based_on": prof.get("based_on", ""), "source": "shipped"}
    for p in sorted(_workspace.profiles_dir().glob(f"*{_PROFILE_SUFFIX}")):
        pid = p.name[: -len(_PROFILE_SUFFIX)]
        prof = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out[pid] = {"id": pid, "label": prof.get("label", pid),
                    "based_on": prof.get("based_on", ""), "source": "workspace"}
    return list(out.values())


def profile_save(profile_id: str, profile: dict) -> dict:
    """Persist a profile to the writable workspace (never the shipped package)."""
    prof = {k: v for k, v in dict(profile).items() if k != "_source"}
    prof["profile_id"] = profile_id
    path = _workspace_profile_path(profile_id)
    path.write_text(yaml.safe_dump(prof, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return {"path": str(path), "profile_id": profile_id}


def profile_clone(src_id: str, new_id: str, label: str | None = None) -> dict:
    """Copy an existing profile into the workspace under a new id."""
    prof = profile_get(src_id)
    prof.pop("_source", None)
    prof["profile_id"] = new_id
    prof["based_on"] = f"clone of {src_id}"
    if label:
        prof["label"] = label
    return profile_save(new_id, prof)


def tag_capture(category: str, value: str, profile_id: str = "default",
                fix=("gazetteer",), pattern: str | None = None,
                source: str | None = None, context: str | None = None,
                score: float = 1.0) -> dict:
    """Feed back a missed identifier. ALWAYS stores a labeled example (for a later
    NER fine-tune); when ``fix`` asks, it also grows the project's gazetteer
    and/or appends a custom-regex rule, materialising a workspace copy of the
    profile so the change takes effect on the next run."""
    fix = list(fix or [])
    result: dict = {"category": category, "value": value,
                    "profile_id": profile_id, "fix": [], "labeled": False}

    result["example"] = _workspace.append_labeled_example(
        {"category": category, "value": value, "source": source,
         "context": context, "profile_id": profile_id})
    result["labeled"] = True

    if not fix:
        return result

    prof = profile_get(profile_id)
    prof.pop("_source", None)
    td = prof.setdefault("text_detection", {})
    changed = False

    if "gazetteer" in fix and (value or "").strip():
        gpath = _workspace.append_gazetteer(f"{profile_id}_custom", value)
        extra = td.setdefault("extra_gazetteer_files", [])
        if str(gpath) not in extra:
            extra.append(str(gpath))
            changed = True
        result["fix"].append({"type": "gazetteer", "path": str(gpath)})

    if "regex" in fix:
        if not pattern:
            raise ValueError("fix 'regex' requires a pattern")
        rules_list = td.setdefault("custom_regex", [])
        entry = {"category": category, "pattern": pattern, "score": float(score)}
        if entry not in rules_list:
            rules_list.append(entry)
            changed = True
        result["fix"].append({"type": "regex", "pattern": pattern,
                              "category": category})

    if changed:
        result["profile_path"] = profile_save(profile_id, prof)["path"]
    return result


def derive_pattern(samples, anchor: bool = True) -> str:
    """Infer a regex from example identifiers (see textscan.derive_pattern). Thin
    re-export so the R side can preview a rule before installing it."""
    return _textscan.derive_pattern(samples, anchor=anchor)


def add_pattern_rule(profile_id: str, category: str, samples,
                     score: float = 0.95) -> dict:
    """OPT-IN: learn a regex from ``samples`` and install it as a ``custom_regex``
    rule on a WORKSPACE copy of the profile (never the shipped default).

    The rule then scrubs matching text at de-id time (fields with action ``C``)
    and fails the residual QA scan as a confident hit. Returns the derived
    ``pattern``, whether it re-matches every sample, and the saved profile path.
    Raises ``ValueError`` via ``derive_pattern`` on empty/inconsistent samples.
    """
    pattern = _textscan.derive_pattern(samples)
    rx = re.compile(pattern)
    cleaned = [s.strip() for s in (samples or []) if s and s.strip()]
    matched = all(rx.search(s) for s in cleaned)

    prof = profile_get(profile_id)
    prof.pop("_source", None)
    td = prof.setdefault("text_detection", {})
    rules_list = td.setdefault("custom_regex", [])
    entry = {"category": category, "pattern": pattern, "score": float(score)}
    added = entry not in rules_list
    if added:
        rules_list.append(entry)
        saved = profile_save(profile_id, prof)
    else:
        saved = {"path": str(_workspace_profile_path(profile_id))}
    return {"profile_id": profile_id, "category": category, "pattern": pattern,
            "score": float(score), "matched": matched, "added": added,
            "profile_path": saved.get("path")}


def remove_custom_rule(profile_id: str, category: str | None = None,
                       pattern: str | None = None) -> dict:
    """Reverse an opt-in rule: drop custom_regex rules on the WORKSPACE profile
    matching ``category`` and/or ``pattern`` (both given -> both must match; give
    neither and nothing is removed). No-op if the profile has no workspace copy."""
    wp = _workspace_profile_path(profile_id)
    if not wp.exists():
        return {"profile_id": profile_id, "removed": 0, "profile_path": None}
    prof = profile_get(profile_id)
    prof.pop("_source", None)
    td = prof.setdefault("text_detection", {})
    rules_list = td.get("custom_regex") or []
    if category is None and pattern is None:
        return {"profile_id": profile_id, "removed": 0, "profile_path": str(wp)}

    def _keep(r: dict) -> bool:
        hit = ((category is None or r.get("category") == category) and
               (pattern is None or r.get("pattern") == pattern))
        return not hit
    kept = [r for r in rules_list if _keep(r)]
    removed = len(rules_list) - len(kept)
    td["custom_regex"] = kept
    saved = profile_save(profile_id, prof)
    return {"profile_id": profile_id, "removed": removed,
            "profile_path": saved["path"]}


def list_custom_rules(profile_id: str = "default") -> list:
    """The custom_regex rules currently in effect for a profile (for the editor)."""
    td = profile_get(profile_id).get("text_detection") or {}
    return list(td.get("custom_regex") or [])


def ner_export_examples(out_path: str | None = None) -> dict:
    """Fine-tuning HOOK (stub): turn captured labeled examples into a training-
    ready JSON-lines file (``{"text", "entities":[[start,end,LABEL]]}``). It does
    NOT train — an offline ``spacy train`` / transformers fine-tune runs on the
    air-gapped box, and the app then consumes the model via
    ``text_detection.ner_model``."""
    examples = _workspace.read_labeled_examples()
    out = Path(out_path) if out_path else (_workspace.workspace_dir()
                                           / "ner_export" / "train.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for ex in examples:
            value = (ex.get("value") or "").strip()
            if not value:
                continue
            text = ex.get("context") or value
            start = text.find(value)
            if start < 0:  # context doesn't contain the value verbatim
                text, start = value, 0
            label = (ex.get("category") or "PHI").upper()
            fh.write(json.dumps(
                {"text": text, "entities": [[start, start + len(value), label]]},
                ensure_ascii=False) + "\n")
            n += 1
    return {"count": n, "path": str(out),
            "note": "Training-ready JSONL written. Run an offline fine-tune "
                    "(spaCy/transformers) on the air-gapped box; point "
                    "text_detection.ner_model at the resulting model directory."}


# --------------------------------------------------------------------------- #
# Phase 3 - pixel / burned-in PHI (high-level entries for the R viewer)        #
# --------------------------------------------------------------------------- #

def pixel_info(path: str, profile_id: str = "default") -> dict:
    """Geometry + OCR-proposed redaction boxes for a file, for the viewer."""
    import pydicom
    ds = pydicom.dcmread(path)
    has_pixels = "PixelData" in ds
    info = {
        "has_pixels": has_pixels,
        "frames": int(getattr(ds, "NumberOfFrames", 1) or 1) if has_pixels else 0,
        "rows": int(getattr(ds, "Rows", 0) or 0),
        "cols": int(getattr(ds, "Columns", 0) or 0),
        "samples": int(getattr(ds, "SamplesPerPixel", 1) or 1),
        "photometric": str(getattr(ds, "PhotometricInterpretation", "")),
        "boxes": [], "ocr_note": None,
    }
    if has_pixels:
        td = (profile_get(profile_id).get("text_detection") or {})
        scanner = _build_scanner(td, _collect_known_values(ds))
        res = _pixels.ocr_phi_boxes(ds, scanner)
        info["boxes"] = res.get("boxes", [])
        info["ocr_note"] = res.get("note")
    return info


def pixel_frame_png(path: str, frame: int = 0, boxes=None, max_side: int = 640) -> str:
    """A base64 PNG of one frame, with any ``boxes`` drawn as outlines (for review)."""
    import pydicom
    from PIL import Image, ImageDraw
    ds = pydicom.dcmread(path)
    if bool(ds.file_meta.TransferSyntaxUID.is_compressed):
        ds.decompress()
    frames = _pixels.load_frames(ds)
    frame = max(0, min(int(frame), frames.shape[0] - 1))
    img = _pixels._frame_to_uint8(frames[frame])
    pil = Image.fromarray(img)
    if pil.mode not in ("L", "RGB"):
        pil = pil.convert("RGB")
    if boxes:
        pil = pil.convert("RGB")
        draw = ImageDraw.Draw(pil)
        for b in boxes:
            if b.get("frame") in (None, frame):
                x, y = int(b["x"]), int(b["y"])
                draw.rectangle([x, y, x + int(b["w"]), y + int(b["h"])],
                               outline=(255, 0, 0), width=2)
    w, h = pil.size
    scale = min(1.0, max_side / max(w, h)) if max(w, h) else 1.0
    if scale < 1.0:
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def pixel_redact(input_path: str, output_path: str, boxes=None,
                 strip_audio: bool = True) -> dict:
    """Apply redaction boxes + strip audio; write a valid, viewable DICOM."""
    import pydicom
    ds = pydicom.dcmread(input_path)
    rec = _pixels.redact_pixels(ds, boxes or [])
    if strip_audio:
        rec["waveforms_removed"] = _pixels.strip_waveforms(ds)
    if getattr(ds, "file_meta", None) is not None and "SOPInstanceUID" in ds:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    pydicom.dcmwrite(output_path, ds, enforce_file_format=True)
    rec["output"] = output_path
    return rec


def _mask_preview(text: str) -> str:
    """Mask a detected identifier so the QA report can locate it WITHOUT
    re-disclosing it verbatim: keep the first/last character, bullet the middle."""
    s = str(text or "")
    if len(s) <= 2:
        return "•" * len(s)
    return s[0] + ("•" * (len(s) - 2)) + s[-1]


# Layers precise enough to fail the residual verdict on their own. The header
# scrub, gazetteer, SG recognisers and profile regexes match concrete known
# values or checksummed formats; the probabilistic layers (Presidio / NER) guess
# from context and over-fire on short technical strings ("HU", "Knee (R)"), so
# they need a higher score before they count against a study.
_CONFIDENT_SOURCES = frozenset({"header", "gazetteer", "sg", "custom_regex"})


def _finding_confident(f: dict, min_score: float, ner_min_score: float) -> bool:
    """True when a finding should count toward the fail verdict: a deterministic
    layer at >= ``min_score``, or a probabilistic layer at >= ``ner_min_score``.

    Probabilistic hits below the higher bar are still listed for the reviewer;
    they just do not fail the study by themselves, which stops ML false positives
    from burying the real deterministic hits."""
    src = f.get("source", "")
    threshold = min_score if src in _CONFIDENT_SOURCES else ner_min_score
    return float(f.get("score", 0.0)) >= threshold


def _residual_verdict(findings: list, min_score: float,
                      ner_min_score: float) -> tuple:
    """Stamp each finding with a ``confident`` flag and return
    ``(passed, n_confident)``. ``passed`` is true when nothing confident survived."""
    n_conf = 0
    for f in findings:
        conf = _finding_confident(f, min_score, ner_min_score)
        f["confident"] = conf
        if conf:
            n_conf += 1
    return (n_conf == 0), n_conf


# De-identification provenance fields the pipeline writes itself. Their values
# are ours, not patient PHI, so the residual scan skips them (Presidio otherwise
# mistakes the method string "dicomdeid: ..." for a person name).
_DEID_PROVENANCE_TAGS = frozenset({
    0x00120062,  # PatientIdentityRemoved
    0x00120063,  # DeidentificationMethod
    0x00120064,  # DeidentificationMethodCodeSequence
})


def _iter_text_elements(ds, _tag=None):
    """Yield (tagi, keyword, value_str) for every free-text element, recursing
    into sequences (SQ) so PHI hidden in SR ContentSequence is reached too."""
    from pydicom.datadict import keyword_for_tag
    for tag in list(ds.keys()):
        elem = ds[tag]
        if elem.VR == "SQ":
            for item in elem.value:
                yield from _iter_text_elements(item)
            continue
        if elem.VR not in _TEXT_VRS:
            continue
        tagi = int(tag)
        raw = elem.value
        for piece in (raw if isinstance(raw, (list, tuple)) else [raw]):
            s = str(piece).strip()
            if s:
                kw = keyword_for_tag(tagi) or hex(tagi)
                yield tagi, kw, s


def _scan_residual_nifti(path: str, scanner, scan_pixels: bool, min_score: float,
                         ner_min_score: float = 0.90) -> dict:
    """Residual-PHI scan for a NIfTI output: the header text fields (metadata) and,
    when OCR is available, the burned-in text on each slice (pixels). Returns the
    same result shape as ``scan_residual`` so the batch aggregator treats DICOM and
    NIfTI outputs uniformly."""
    import numpy as np
    import nibabel as nib

    img = nib.load(path)
    hdr = img.header
    findings: list[dict] = []
    for field in _NIFTI_TEXT_FIELDS:
        try:
            val = bytes(hdr[field]).split(b"\x00", 1)[0].decode("latin-1", "replace")
        except (KeyError, ValueError):
            continue
        if not val:
            continue
        for span in scanner.scan(val):
            findings.append({
                "location": "metadata", "tag": 0, "keyword": f"nifti:{field}",
                "category": span.category, "source": span.source,
                "score": float(span.score), "preview": _mask_preview(span.text),
            })
    n_meta = len(findings)
    notes = list(getattr(scanner, "notes", []))

    if scan_pixels:
        ok, note = _pixels._ocr_available()
        if not ok:
            notes.append(note)
        else:
            try:
                data = np.asanyarray(img.dataobj)
                if data.ndim == 3:
                    for b in _pixels.volume_ocr_boxes(data, scanner):
                        spans = scanner.scan(b.get("text", ""))
                        cat = spans[0].category if spans else "text"
                        score = spans[0].score if spans else 1.0
                        findings.append({
                            "location": "pixels", "tag": None, "keyword": "PixelData",
                            "category": cat, "source": "ocr", "score": float(score),
                            "preview": _mask_preview(b.get("text", "")),
                        })
                else:
                    notes.append("pixel scan skipped: not 3-D")
            except Exception as e:  # noqa: BLE001 - pixel scan must never crash QA
                notes.append(f"pixel scan skipped: {e}")

    n_pixels = len(findings) - n_meta
    by_category: dict[str, int] = {}
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
    passed, n_conf = _residual_verdict(findings, min_score, ner_min_score)
    return {
        "path": path,
        "findings": findings,
        "by_category": by_category,
        "counts": {"total": len(findings), "metadata": n_meta,
                   "pixels": n_pixels, "confident": n_conf},
        "passed": passed,
        # NIfTI has no PatientIdentityRemoved tag, and this scan runs independently
        # of the de-id step, so it cannot attest that identity was removed. Report
        # None (rendered as "n/a" on the R side) rather than falsely asserting True.
        "identity_removed": None,
        "notes": notes,
    }


def scan_residual(path: str, profile_id: str = "default",
                  scan_pixels: bool = True, min_score: float = 0.5,
                  ner_min_score: float = 0.90) -> dict:
    """Phase 6. Re-run the detectors on an OUTPUT file and report residual PHI.

    Independent of the de-id run: it builds the layered scanner from the
    profile's ``text_detection`` block but with an EMPTY header-token layer, so
    the pseudonyms already written into the output header are not themselves
    counted as PHI. The gazetteer + SG recognisers (+ optional Presidio/NER)
    then catch any real identifier that survived in metadata or pixels.

    Returns per-category counts, a masked findings list, and a pass/fail verdict.
    ``passed`` is true when nothing *confident* survived: a deterministic-layer
    hit at >= ``min_score``, or a probabilistic-layer (Presidio/NER) hit at
    >= ``ner_min_score``. Lower-scoring ML hits are still listed for review but do
    not fail the study, so their false positives cannot bury real findings.
    """
    td = profile_get(profile_id).get("text_detection") or {}
    scanner = _build_scanner(td, [])   # no header-token seeding on outputs
    if _is_nifti(path):
        return _scan_residual_nifti(path, scanner, scan_pixels, min_score,
                                    ner_min_score)

    import pydicom
    ds = pydicom.dcmread(path)

    findings: list[dict] = []
    for tagi, kw, value in _iter_text_elements(ds):
        if tagi in _DEID_PROVENANCE_TAGS:
            continue  # our own de-id provenance string, not patient PHI
        for span in scanner.scan(value):
            findings.append({
                "location": "metadata", "tag": tagi, "keyword": kw,
                "category": span.category, "source": span.source,
                "score": float(span.score), "preview": _mask_preview(span.text),
            })

    n_meta = len(findings)
    notes = list(getattr(scanner, "notes", []))

    if scan_pixels and "PixelData" in ds:
        try:
            res = _pixels.ocr_phi_boxes(ds, scanner)
            for b in res.get("boxes", []):
                spans = scanner.scan(b.get("text", ""))
                cat = spans[0].category if spans else "text"
                score = spans[0].score if spans else 1.0
                findings.append({
                    "location": "pixels", "tag": None, "keyword": "PixelData",
                    "category": cat, "source": "ocr", "score": float(score),
                    "preview": _mask_preview(b.get("text", "")),
                })
            if res.get("note"):
                notes.append(res["note"])
        except Exception as e:  # noqa: BLE001 - pixel scan must never crash QA
            notes.append(f"pixel scan skipped: {e}")

    n_pixels = len(findings) - n_meta

    by_category: dict[str, int] = {}
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1

    passed, n_conf = _residual_verdict(findings, min_score, ner_min_score)
    identity_removed = str(getattr(ds, "PatientIdentityRemoved", "")) == "YES"

    return {
        "path": path,
        "findings": findings,
        "by_category": by_category,
        "counts": {"total": len(findings), "metadata": n_meta,
                   "pixels": n_pixels, "confident": n_conf},
        "passed": passed,
        "identity_removed": identity_removed,
        "notes": notes,
    }


def scan_residual_dir(output_path: str, profile_id: str = "default",
                      scan_pixels: bool = True, min_score: float = 0.5,
                      ner_min_score: float = 0.90) -> dict:
    """Residual scan over every file under an output folder (or a single file).

    Aggregates per-file results into a batch verdict. Unreadable files are
    reported as skipped rather than failing the whole scan.
    """
    per_file = []
    total_by_cat: dict[str, int] = {}
    n_pass = n_fail = 0
    n_findings = n_confident = 0
    for full, rel in _iter_input_files(output_path):
        try:
            r = scan_residual(full, profile_id, scan_pixels, min_score,
                              ner_min_score)
        except Exception as e:  # noqa: BLE001
            per_file.append({"path": full, "rel": rel, "skipped": str(e)})
            continue
        r["rel"] = rel
        per_file.append(r)
        for c, n in r["by_category"].items():
            total_by_cat[c] = total_by_cat.get(c, 0) + n
        n_findings += r["counts"].get("total", 0)
        n_confident += r["counts"].get("confident", 0)
        if r["passed"]:
            n_pass += 1
        else:
            n_fail += 1
    return {
        "root": output_path,
        "files": per_file,
        "by_category": total_by_cat,
        # `confident` = hits that fail a study; `findings` includes the lower-score
        # ML hits shown for review only, so a reviewer can see the noise volume.
        "summary": {"scanned": n_pass + n_fail, "passed": n_pass, "flagged": n_fail,
                    "findings": n_findings, "confident": n_confident},
        "passed": n_fail == 0,
    }


# --------------------------------------------------------------------------- #
# Phase 6.5 - reviewer metadata view                                          #
# --------------------------------------------------------------------------- #

def _tag_str(tagi: int) -> str:
    return "(%04X,%04X)" % (tagi >> 16, tagi & 0xFFFF)


def _metadata_rows(ds, depth: int = 0) -> list:
    """Flat (tag, keyword, vr, value) rows for the reviewer, recursing exactly one
    level into sequences. Bulk binaries (PixelData etc.) are summarised, not dumped."""
    from pydicom.datadict import keyword_for_tag
    rows = []
    for tag in list(ds.keys()):
        elem = ds[tag]
        tagi = int(tag)
        kw = keyword_for_tag(tagi) or _tag_str(tagi)
        if elem.VR == "SQ":
            n = len(elem.value)
            rows.append({"tag": _tag_str(tagi), "tagi": tagi, "keyword": kw,
                         "vr": "SQ", "value": f"<Sequence: {n} item(s)>",
                         "depth": depth})
            if depth == 0:                       # one level only
                for item in elem.value:
                    rows.extend(_metadata_rows(item, depth + 1))
            continue
        if kw == "PixelData" or elem.VR in ("OB", "OW", "OF", "OD", "UN"):
            raw = elem.value
            nbytes = len(raw) if isinstance(raw, (bytes, bytearray)) else 0
            rows.append({"tag": _tag_str(tagi), "tagi": tagi, "keyword": kw,
                         "vr": elem.VR, "value": f"<{nbytes} bytes>", "depth": depth})
            continue
        raw = elem.value
        value = ("\\".join(str(x) for x in raw)
                 if isinstance(raw, (list, tuple)) else str(raw))
        rows.append({"tag": _tag_str(tagi), "tagi": tagi, "keyword": kw,
                     "vr": elem.VR, "value": value, "depth": depth})
    return rows


def read_metadata(path: str, profile_id: str = "default",
                  mask_flagged: bool = True, min_score: float = 0.5) -> dict:
    """Return a de-identified file's header as flat rows for reviewer inspection.

    Recurses one level into sequences. When ``mask_flagged`` is set, runs the
    residual scan and masks any metadata value that survived as PHI (scored
    >= ``min_score``), so the review screen locates a leak without re-disclosing it.
    """
    import pydicom
    ds = pydicom.dcmread(path)
    rows = _metadata_rows(ds)

    flagged_tags: set[int] = set()
    if mask_flagged:
        try:
            res = scan_residual(path, profile_id, scan_pixels=False, min_score=min_score)
            flagged_tags = {f["tag"] for f in res["findings"]
                            if f.get("tag") is not None and f["score"] >= min_score}
        except Exception:  # noqa: BLE001 - a scan hiccup must not break the view
            flagged_tags = set()

    for r in rows:
        r["flagged"] = r["tagi"] in flagged_tags
        if r["flagged"]:
            r["value"] = _mask_preview(r["value"])
        r.pop("tagi", None)

    return {"path": path, "rows": rows, "masked": bool(mask_flagged)}
