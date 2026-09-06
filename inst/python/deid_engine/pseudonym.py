"""Deterministic pseudonymisation primitives.

All functions are pure and deterministic in ``(value, salt)`` so that:
  * the same identifier always maps to the same pseudonym within a run/patient
    (referential integrity, longitudinal linkage), and
  * reversible mode can reproduce the mapping from the stored salt.

Nothing here persists anything; the keystore (keystore.py) owns the salt.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from pydicom.uid import generate_uid


def salted_sha256(value: str, salt: bytes, truncate: int = 16) -> str:
    """Deterministic salted hash of ``value``, returned as ``truncate`` hex chars."""
    digest = hashlib.sha256(salt + str(value).encode("utf-8")).hexdigest()
    if truncate is None or truncate <= 0:
        return digest
    return digest[:truncate]


def remap_uid(original_uid: str, salt: bytes) -> str:
    """Deterministically map a DICOM UID to a new valid UID.

    Uses pydicom's ``generate_uid`` with fixed entropy sources so the same
    original UID + salt always yields the same replacement, preserving internal
    references (e.g. every instance pointing at a StudyInstanceUID stays linked).
    """
    return generate_uid(entropy_srcs=[str(original_uid), salt.hex()])


def date_offset_days(patient_key: str, salt: bytes, low: int, high: int) -> int:
    """A stable per-patient integer offset in ``[low, high]`` (inclusive)."""
    if high < low:
        low, high = high, low
    span = high - low + 1
    h = hashlib.sha256(salt + b"|date|" + str(patient_key).encode("utf-8")).digest()
    n = int.from_bytes(h[:8], "big")
    return low + (n % span)


def shift_dicom_date(dicom_date: str, offset_days: int) -> str:
    """Shift a DICOM ``DA`` value (``YYYYMMDD``) by ``offset_days``.

    Applying the same offset to two dates preserves the interval between them.
    Blank/absent dates pass through unchanged.
    """
    if not dicom_date:
        return dicom_date
    s = dicom_date.strip()
    if len(s) != 8 or not s.isdigit():
        return dicom_date  # leave malformed/partial dates untouched
    d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    shifted = d + timedelta(days=offset_days)
    return f"{shifted.year:04d}{shifted.month:02d}{shifted.day:02d}"


def shift_dicom_datetime(dicom_dt: str, offset_days: int) -> str:
    """Shift the date part of a DICOM ``DT`` value by ``offset_days``.

    A ``DT`` is ``YYYYMMDD`` optionally followed by ``HHMMSS.FFFFFF`` and a
    ``&ZZXX`` timezone; only the leading date is shifted, so the time-of-day,
    fractional seconds, and timezone are preserved and intervals stay
    consistent. Blank / partial (no full 8-digit date) values pass through.
    """
    if not dicom_dt:
        return dicom_dt
    s = dicom_dt.strip()
    if len(s) < 8 or not s[:8].isdigit():
        return dicom_dt  # not a full YYYYMMDD prefix -> leave untouched
    return shift_dicom_date(s[:8], offset_days) + s[8:]
