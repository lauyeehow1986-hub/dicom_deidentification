"""Apply DICOM PS3.15 action codes (plus H hash, S date-shift) to data elements.

Each function mutates the dataset in place and returns a small record describing
the change, which the caller aggregates into the before/after report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import pseudonym as ps


@dataclass
class DeidContext:
    """Everything an action needs, threaded through a whole study."""
    salt: bytes
    truncate: int = 16
    date_offset: int = 0
    known_values: list[str] = field(default_factory=list)
    uid_cache: dict[str, str] = field(default_factory=dict)
    scanner: Any = None  # Phase 2 TextScanner (layered PHI detection)


def _pseudonym_for_vr(original: str, vr: str, ctx: DeidContext) -> str:
    """A deterministic dummy value shaped to the element's VR (action D)."""
    token = ps.salted_sha256(original, ctx.salt, 8).upper()
    if vr == "PN":
        return f"ANON^{token}"
    return f"ANON{token}"


def _scrub_known(text: str, known_values: list[str]) -> str:
    """Remove known identifier values (names, MRN, ...) from free text (action C)."""
    out = text
    for kv in sorted((k for k in known_values if k), key=len, reverse=True):
        out = re.sub(re.escape(kv), " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


def apply_action(ds, tag: int, code: str, ctx: DeidContext) -> dict[str, Any]:
    """Apply action ``code`` to element ``tag`` in ``ds``; return a change record."""
    if tag not in ds:
        return {"tag": tag, "action": code, "note": "absent"}

    elem = ds[tag]
    vr = elem.VR
    original = elem.value
    orig_str = "" if original is None else str(original)
    rec = {"tag": tag, "vr": vr, "action": code, "original": orig_str}

    if code == "K":
        rec["result"] = orig_str
        return rec

    if code == "X":
        del ds[tag]
        rec["result"] = None
        rec["removed"] = True
        return rec

    if code == "Z":
        elem.value = ""
        rec["result"] = ""
        return rec

    if code == "H":
        elem.value = ps.salted_sha256(orig_str, ctx.salt, ctx.truncate)
    elif code == "D":
        elem.value = _pseudonym_for_vr(orig_str, vr, ctx)
    elif code == "U":
        new = ctx.uid_cache.get(orig_str)
        if new is None:
            new = ps.remap_uid(orig_str, ctx.salt)
            ctx.uid_cache[orig_str] = new
        elem.value = new
    elif code == "S":
        if vr == "DA":
            elem.value = ps.shift_dicom_date(orig_str, ctx.date_offset)
        # TM/DT and non-date VRs are left untouched at this phase
    elif code == "C":
        if ctx.scanner is not None:
            redacted, spans = ctx.scanner.redact(orig_str, replacement=" ")
            elem.value = re.sub(r"\s{2,}", " ", redacted).strip()
            if spans:
                rec["categories"] = sorted({s.category for s in spans})
        else:
            elem.value = _scrub_known(orig_str, ctx.known_values)
    else:
        rec["note"] = f"unknown action '{code}'"
        rec["result"] = orig_str
        return rec

    rec["result"] = str(elem.value)
    return rec
