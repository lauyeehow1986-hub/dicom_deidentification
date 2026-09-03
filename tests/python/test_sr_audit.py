"""Audit: SR ContentSequence PHI is scrubbed by the existing SQ-recursing
scanner (no SR-specific engine change was expected). This test exercises the
two deterministic guarantees inside an SR TextValue:

  * the SG NRIC/FIN recogniser removes a national id, and
  * the header-token scrub removes the study's own PatientName tokens,

both reached by ``deidentify_dataset`` recursing into ContentSequence. If either
assertion fails, it marks a real gap to close in ``core`` (e.g. an SR value-type
VR missing from ``_TEXT_VRS``) -- do not weaken the assertion.
"""
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from deid_engine import core, rules


def _sr_dataset():
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.88.11"  # Basic Text SR
    ds.SOPInstanceUID = "1.2.3.4"
    ds.PatientName = "Tan^Wei Ming"
    ds.PatientID = "S1234567D"
    item = Dataset()
    item.ValueType = "TEXT"
    item.TextValue = ("Study for Tan Wei Ming; reported by "
                      "Nurul Aisyah Binte Rahman, NRIC S1234567D")
    name_item = Dataset()
    name_item.ValueType = "PNAME"
    name_item.PersonName = "Ramasamy^Muthu"
    ds.ContentSequence = Sequence([item, name_item])
    return ds


def test_sr_textvalue_phi_scrubbed():
    ds = _sr_dataset()
    prof = rules.load_profile("default")
    core.deidentify_dataset(ds, prof, salt=b"0" * 16)
    tv = str(ds.ContentSequence[0].TextValue).replace("^", " ")
    # deterministic SG NRIC/FIN recogniser reaches the SR TextValue
    assert "S1234567D" not in tv, tv
    # header-token scrub (seeded on PatientName) reaches the SR TextValue
    assert "Tan Wei Ming" not in tv, tv
