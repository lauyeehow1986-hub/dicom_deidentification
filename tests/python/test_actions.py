"""Tests for applying PS3.15 action codes to pydicom data elements."""
import pytest
from pydicom.dataset import Dataset

from deid_engine import actions as A


SALT = b"unit-test-salt-0123456789abcdef"


def ctx(**kw):
    base = dict(salt=SALT, truncate=16, date_offset=5, known_values=[])
    base.update(kw)
    return A.DeidContext(**base)


def ds_with(tag, value, vr=None):
    ds = Dataset()
    if vr:
        ds.add_new(tag, vr, value)
    else:
        ds[tag] = _elem(tag, value)
    return ds


def _elem(tag, value):
    from pydicom.dataelem import DataElement
    from pydicom.datadict import dictionary_VR
    return DataElement(tag, dictionary_VR(tag), value)


def test_X_removes_the_element():
    ds = ds_with(0x00100010, "Tan Wei Ming")   # PatientName
    A.apply_action(ds, 0x00100010, "X", ctx())
    assert 0x00100010 not in ds


def test_Z_empties_the_value():
    ds = ds_with(0x00100010, "Tan Wei Ming")
    A.apply_action(ds, 0x00100010, "Z", ctx())
    assert 0x00100010 in ds
    assert str(ds[0x00100010].value) in ("", "None")


def test_K_keeps_the_value():
    ds = ds_with(0x00100010, "Tan Wei Ming")
    A.apply_action(ds, 0x00100010, "K", ctx())
    assert str(ds[0x00100010].value) == "Tan Wei Ming"


def test_H_replaces_with_deterministic_hash_token():
    ds1 = ds_with(0x00100020, "MRN0099887")     # PatientID (LO)
    ds2 = ds_with(0x00100020, "MRN0099887")
    A.apply_action(ds1, 0x00100020, "H", ctx())
    A.apply_action(ds2, 0x00100020, "H", ctx())
    v1 = str(ds1[0x00100020].value)
    assert v1 == str(ds2[0x00100020].value)      # deterministic
    assert "MRN0099887" not in v1                # original gone


def test_U_remaps_uid_and_caches_within_run():
    uid = "1.2.840.113619.2.55.3.1"
    cache = {}
    ds1 = ds_with(0x0020000D, uid)               # StudyInstanceUID (UI)
    ds2 = ds_with(0x0020000D, uid)
    A.apply_action(ds1, 0x0020000D, "U", ctx(uid_cache=cache))
    A.apply_action(ds2, 0x0020000D, "U", ctx(uid_cache=cache))
    new1 = str(ds1[0x0020000D].value)
    assert new1 != uid
    assert new1 == str(ds2[0x0020000D].value)     # same original -> same new UID


def test_S_shifts_a_date_by_the_context_offset():
    ds = ds_with(0x00080020, "20240310")          # StudyDate (DA)
    A.apply_action(ds, 0x00080020, "S", ctx(date_offset=5))
    assert str(ds[0x00080020].value) == "20240315"


def test_D_pseudonymises_a_name_deterministically():
    ds1 = ds_with(0x00100010, "Nurul Aisyah Binte Rahman")
    ds2 = ds_with(0x00100010, "Nurul Aisyah Binte Rahman")
    A.apply_action(ds1, 0x00100010, "D", ctx())
    A.apply_action(ds2, 0x00100010, "D", ctx())
    v1 = str(ds1[0x00100010].value)
    assert v1 == str(ds2[0x00100010].value)       # deterministic
    assert "Nurul" not in v1 and v1 != ""         # original gone, not empty


def test_C_scrubs_known_values_from_free_text():
    ds = ds_with(0x00081030, "Echo for Tan Wei Ming MRN0099887")  # StudyDescription (LO)
    A.apply_action(ds, 0x00081030, "C",
                   ctx(known_values=["Tan Wei Ming", "MRN0099887"]))
    v = str(ds[0x00081030].value)
    assert "Tan Wei Ming" not in v
    assert "MRN0099887" not in v
    assert "Echo for" in v                          # non-PHI text retained


def test_apply_action_returns_a_record_of_what_changed():
    ds = ds_with(0x00100010, "Tan Wei Ming")
    rec = A.apply_action(ds, 0x00100010, "H", ctx())
    assert rec["action"] == "H"
    assert rec["original"] == "Tan Wei Ming"
    assert rec["result"] != "Tan Wei Ming"
