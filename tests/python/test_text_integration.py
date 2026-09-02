"""Phase 2 - text scanner wired into the DICOM engine.

Proves the layered scanner reaches PHI the fixed action map does NOT cover:
unmapped text elements and free text nested inside sequences (e.g. SR
ContentSequence), driven off the study's own header tokens, a gazetteer, and the
SG recognisers.
"""

import pydicom
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from deid_engine import core, rules


def _dataset_with_hidden_phi():
    ds = Dataset()
    ds.PatientName = "Tan Wei Ming"
    ds.PatientID = "MRN0099887"
    # DerivationDescription (0008,2111 ST) is NOT in the action map or FREETEXT_TAGS
    ds.DerivationDescription = "Follow-up for Tan Wei Ming, NRIC S1234567D, call 91234567"
    # SR-style nested free text inside a sequence
    item = Dataset()
    item.TextValue = "Reported by Dr Muthusamy for patient S1234567D"
    ds.ContentSequence = Sequence([item])
    return ds


def test_unmapped_text_element_is_scrubbed():
    ds = _dataset_with_hidden_phi()
    profile = rules.load_profile()
    core.deidentify_dataset(ds, profile, salt=b"s" * 16)
    scrubbed = str(ds.DerivationDescription)
    assert "S1234567D" not in scrubbed   # SG NRIC recogniser
    assert "91234567" not in scrubbed    # SG phone recogniser
    assert "Tan Wei Ming" not in scrubbed  # header-token layer


def test_phi_inside_sequence_is_scrubbed():
    ds = _dataset_with_hidden_phi()
    profile = rules.load_profile()
    profile["text_detection"] = {"enabled": True, "gazetteer": ["Muthusamy"]}
    core.deidentify_dataset(ds, profile, salt=b"s" * 16)
    nested = str(ds.ContentSequence[0].TextValue)
    assert "S1234567D" not in nested
    assert "Muthusamy" not in nested


def test_change_records_report_detection_categories():
    ds = _dataset_with_hidden_phi()
    report = core.deidentify_dataset(ds, rules.load_profile(), salt=b"s" * 16)
    text_changes = [r for r in report["records"]
                    if r.get("action") == "C" and r.get("categories")]
    assert text_changes, "expected at least one categorised text scrub"
    cats = set()
    for r in text_changes:
        cats.update(r["categories"])
    assert "nric_fin" in cats


def test_injected_scanner_is_reused_with_per_file_header_tokens():
    # deidentify_study builds ONE scanner (heavy optional layers loaded once) and
    # reuses it per file; each file's own header tokens must still be applied.
    from deid_engine import textscan
    ds = Dataset()
    ds.PatientName = "Lim Ah Kow"
    ds.DerivationDescription = "handover note for Lim Ah Kow"
    scanner = textscan.TextScanner(use_presidio=False, use_ner=False)
    core.deidentify_dataset(ds, rules.load_profile(), b"s" * 16, scanner=scanner)
    assert "Lim Ah Kow" not in str(ds.DerivationDescription)


def test_encapsulated_document_is_removed():
    ds = Dataset()
    ds.PatientName = "Tan Wei Ming"
    # embedded PDF bytes that can't be text-scrubbed in place -> must be dropped
    ds.add_new(0x00420011, "OB", b"%PDF-1.4 fake pdf with Tan Wei Ming inside")
    core.deidentify_dataset(ds, rules.load_profile(), b"s" * 16)
    assert 0x00420011 not in ds


def test_clean_text_field_is_left_unchanged():
    ds = Dataset()
    ds.PatientName = "Tan Wei Ming"
    ds.DerivationDescription = "Cardiac ultrasound derived volume rendering"
    report = core.deidentify_dataset(ds, rules.load_profile(), salt=b"s" * 16)
    assert str(ds.DerivationDescription) == "Cardiac ultrasound derived volume rendering"
    # no spurious change record for the clean field
    assert not any(r.get("tag") == 0x00082111 for r in report["records"])
