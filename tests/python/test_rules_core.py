"""Integration tests for the rules engine + dataset de-identification walker."""
import copy
import pytest
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from deid_engine import rules, core


SALT = b"unit-test-salt-0123456789abcdef"


def make_profile(dates_mode="remove"):
    prof = rules.load_profile()
    prof.setdefault("dates", {})["mode"] = dates_mode
    return prof


def sample_dataset():
    ds = Dataset()
    ds.PatientName = "Tan Wei Ming"
    ds.PatientID = "MRN0099887"                 # (0010,0020)
    ds.AccessionNumber = "ACC-2024-000123"      # (0008,0050)
    ds.StudyDate = "20240310"                   # (0008,0020)
    ds.SeriesDate = "20240315"                  # (0008,0021)
    ds.StudyDescription = "Echo for Tan Wei Ming"  # (0008,1030)
    ds.StudyInstanceUID = "1.2.3.4"             # (0020,000D)
    ds.SOPInstanceUID = "1.2.3.5"               # (0008,0018)
    # a private tag carrying a secret
    ds.add_new(0x00090010, "LO", "ACME")
    ds.add_new(0x00091001, "LO", "secret-financial-id")
    # nested sequence: a name inside an item + a reference back to the SOP UID
    item = Dataset()
    item.PatientName = "Tan Wei Ming"
    item.ReferencedSOPInstanceUID = "1.2.3.5"   # (0008,1155)
    ds.ReferencedImageSequence = Sequence([item])  # (0008,1140) SQ
    return ds


# --- rules-level units --------------------------------------------------------

def test_parse_tag_extracts_the_group_element():
    assert rules.parse_tag("(0010,0010) PatientName") == 0x00100010
    assert rules.parse_tag("(0008,1155) ReferencedSOPInstanceUID  # comment") == 0x00081155


def test_build_action_map_assigns_expected_actions():
    amap = rules.build_action_map(rules.load_catalog(), make_profile())
    assert amap[0x00100010] == "D"   # PatientName
    assert amap[0x00100020] == "H"   # PatientID
    assert amap[0x00080050] == "H"   # AccessionNumber
    assert amap[0x0020000D] == "U"   # StudyInstanceUID
    assert amap[0x00080018] == "U"   # SOPInstanceUID


def test_dates_mode_resolves_the_linked_date_action():
    remove_map = rules.build_action_map(rules.load_catalog(), make_profile("remove"))
    shift_map = rules.build_action_map(rules.load_catalog(), make_profile("shift"))
    assert remove_map[0x00080020] == "X"
    assert shift_map[0x00080020] == "S"


# --- core walker --------------------------------------------------------------

def test_names_and_ids_are_replaced():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile(), SALT)
    assert "Tan" not in str(ds.PatientName)
    assert str(ds.PatientID) != "MRN0099887"
    assert str(ds.AccessionNumber) != "ACC-2024-000123"


def test_default_removes_dates():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile("remove"), SALT)
    assert 0x00080020 not in ds        # StudyDate removed


def test_shift_mode_preserves_interval_between_dates():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile("shift"), SALT)
    from datetime import date
    s, se = str(ds.StudyDate), str(ds.SeriesDate)
    d = lambda x: date(int(x[:4]), int(x[4:6]), int(x[6:8]))
    assert (d(se) - d(s)).days == 5   # original interval 20240310 -> 20240315


def test_free_text_is_scrubbed_of_the_patient_name():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile(), SALT)
    assert "Tan Wei Ming" not in str(ds.StudyDescription)


def test_uids_are_remapped_consistently_across_references():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile(), SALT)
    assert str(ds.StudyInstanceUID) != "1.2.3.4"
    new_sop = str(ds.SOPInstanceUID)
    ref = str(ds.ReferencedImageSequence[0].ReferencedSOPInstanceUID)
    assert ref == new_sop              # same original UID -> same new UID everywhere


def test_recurses_into_sequences():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile(), SALT)
    assert "Tan" not in str(ds.ReferencedImageSequence[0].PatientName)


def test_private_tag_is_stripped_by_default_policy():
    ds = sample_dataset()
    core.deidentify_dataset(ds, make_profile(), SALT)
    assert 0x00091001 not in ds


def test_report_lists_changes():
    ds = sample_dataset()
    report = core.deidentify_dataset(ds, make_profile(), SALT)
    assert report["counts"]["total"] > 0
    assert any(r.get("tag") == 0x00100010 for r in report["records"])
