"""Phase 7 - keystore summary + authorised re-identification.

The acceptance runner (and a reviewer) needs to confirm the reversibility
policy end to end: in reversible mode a pseudonym maps back to the planted
original; in irreversible mode nothing is persisted to map back with.
"""
import pydicom

from deid_engine import core, corpus


def test_reversible_keystore_reidentifies(tmp_path):
    src = tmp_path / "src"
    corpus.build_corpus(str(src))
    out = tmp_path / "out"
    ks_path = str(tmp_path / "ks.json")

    core.deid_run(str(src), str(out), keystore_path=ks_path,
                  passphrase="pw", reversible=True)

    summ = core.keystore_summary(ks_path, "pw")
    assert summ["reversible"] is True
    assert summ["n_crosswalk"] > 0

    # the output PatientID (a pseudonym) reverses to a planted national id
    pid = str(pydicom.dcmread(str(out / "single_frame.dcm")).PatientID)
    assert core.keystore_reverse(ks_path, "pw", pid) in corpus.PLANTED["nric_fin"]


def test_irreversible_keystore_persists_no_crosswalk(tmp_path):
    src = tmp_path / "src"
    corpus.build_corpus(str(src))
    out = tmp_path / "out"
    ks_path = str(tmp_path / "ks.json")

    core.deid_run(str(src), str(out), keystore_path=ks_path,
                  passphrase="pw", reversible=False)

    summ = core.keystore_summary(ks_path, "pw")
    assert summ["reversible"] is False
    assert summ["n_crosswalk"] == 0
    pid = str(pydicom.dcmread(str(out / "single_frame.dcm")).PatientID)
    assert core.keystore_reverse(ks_path, "pw", pid) is None
