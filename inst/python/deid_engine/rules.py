"""Turn the identifier catalog + a profile into a tag -> action map, and provide
the tag-parsing / profile-loading helpers the core walker needs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profiles"

# Common free-text elements scrubbed of known identifier values (action C).
# (Phase 2 adds full NLP detection; Phase 1 does the header-token scrub.)
FREETEXT_TAGS = {
    0x00081030: "C",  # StudyDescription
    0x0008103E: "C",  # SeriesDescription
    0x00204000: "C",  # ImageComments
    0x00104000: "C",  # PatientComments
    0x00181030: "C",  # ProtocolName
    0x00081080: "C",  # AdmittingDiagnosesDescription
}

_TAG_RE = re.compile(r"\(([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{4})\)")
_DATE_ACTION = {"remove": "X", "shift": "S", "keep": "K"}


def parse_tag(s: str):
    """'(0010,0010) PatientName' -> 0x00100010, or None if no tag is present."""
    m = _TAG_RE.search(s or "")
    if not m:
        return None
    return (int(m.group(1), 16) << 16) | int(m.group(2), 16)


def load_catalog(path: str | None = None) -> dict:
    p = Path(path) if path else PROFILE_DIR / "identifier_catalog.yml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_profile(profile_id: str = "default", path: str | None = None) -> dict:
    p = Path(path) if path else PROFILE_DIR / f"{profile_id}_profile.yml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def build_action_map(catalog: dict, profile: dict) -> dict[int, str]:
    """Map specific DICOM tags to action codes, resolving date policy from profile."""
    dates_mode = (profile.get("dates") or {}).get("mode", "remove")
    date_action = _DATE_ACTION.get(dates_mode, "X")

    amap: dict[int, str] = {}
    for cat in catalog.get("categories", []):
        action = cat.get("action")
        is_dates = cat.get("id") == "linked_dates" or action == "profile_dates"
        for tagstr in (cat.get("dicom_tags") or []):
            t = parse_tag(tagstr)
            if t is None:
                continue
            if is_dates:
                amap[t] = date_action
            elif action in ("D", "Z", "X", "K", "C", "U", "H", "S"):
                amap[t] = action

    # UID remap section.
    for tagstr in ((catalog.get("uid_remap") or {}).get("tags") or []):
        t = parse_tag(tagstr)
        if t is not None:
            amap[t] = "U"

    # Free-text scrub tags (don't override an explicit catalog mapping).
    for t, act in FREETEXT_TAGS.items():
        amap.setdefault(t, act)

    return amap
