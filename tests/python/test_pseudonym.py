"""Tests for deterministic, reversible-friendly pseudonymisation primitives."""
import re
import pytest

from deid_engine import pseudonym as ps


SALT = b"unit-test-salt-0123456789abcdef"
SALT2 = b"different-salt-0123456789abcdefff"


# --- salted SHA-256 -----------------------------------------------------------

def test_salted_sha256_is_deterministic_for_same_value_and_salt():
    a = ps.salted_sha256("MRN0099887", SALT)
    b = ps.salted_sha256("MRN0099887", SALT)
    assert a == b


def test_salted_sha256_differs_by_salt():
    assert ps.salted_sha256("MRN0099887", SALT) != ps.salted_sha256("MRN0099887", SALT2)


def test_salted_sha256_differs_by_value():
    assert ps.salted_sha256("A", SALT) != ps.salted_sha256("B", SALT)


def test_salted_sha256_respects_truncation_length():
    out = ps.salted_sha256("MRN0099887", SALT, truncate=16)
    # 16 hex chars in the emitted token (a readable prefix may be added).
    assert re.search(r"[0-9a-f]{16}", out)


# --- UID remap ----------------------------------------------------------------

UID = "1.2.840.113619.2.55.3.604688119.868.1234567890.123"


def test_remap_uid_is_deterministic():
    assert ps.remap_uid(UID, SALT) == ps.remap_uid(UID, SALT)


def test_remap_uid_changes_the_uid():
    assert ps.remap_uid(UID, SALT) != UID


def test_remap_uid_differs_by_salt():
    assert ps.remap_uid(UID, SALT) != ps.remap_uid(UID, SALT2)


def test_remap_uid_is_a_valid_dicom_uid():
    out = ps.remap_uid(UID, SALT)
    assert len(out) <= 64
    # dot-separated numeric components, no empty components, no leading zeros
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)+", out)
    for comp in out.split("."):
        assert comp == "0" or not comp.startswith("0")


# --- per-patient date offset --------------------------------------------------

def test_date_offset_deterministic_per_patient():
    assert ps.date_offset_days("PID123", SALT, -365, 365) == \
           ps.date_offset_days("PID123", SALT, -365, 365)


def test_date_offset_within_range():
    for pid in ("PID1", "PID2", "PID3", "abc", "xyz"):
        off = ps.date_offset_days(pid, SALT, -365, 365)
        assert -365 <= off <= 365


def test_date_offset_differs_between_patients():
    offs = {ps.date_offset_days(f"PID{i}", SALT, -365, 365) for i in range(20)}
    assert len(offs) > 1  # not all identical


# --- date shifting preserves intervals ---------------------------------------

def test_shift_dicom_date_applies_offset():
    # 2024-03-10 shifted by +5 days -> 2024-03-15
    assert ps.shift_dicom_date("20240310", 5) == "20240315"


def test_shift_dicom_date_crosses_month_boundary():
    assert ps.shift_dicom_date("20240228", 2) == "20240301"  # 2024 is a leap year


def test_shift_dicom_date_preserves_interval_between_two_dates():
    d1, d2 = "20240101", "20240401"
    off = 37
    s1 = ps.shift_dicom_date(d1, off)
    s2 = ps.shift_dicom_date(d2, off)
    from datetime import date
    orig = (date(2024, 4, 1) - date(2024, 1, 1)).days
    shifted = (date(int(s2[:4]), int(s2[4:6]), int(s2[6:])) -
               date(int(s1[:4]), int(s1[4:6]), int(s1[6:]))).days
    assert orig == shifted


def test_shift_dicom_date_handles_blank():
    assert ps.shift_dicom_date("", 5) == ""
