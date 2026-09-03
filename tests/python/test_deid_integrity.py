"""Phase 6.5 - deid_run wiring: reversibility, before/after checksums, signing.

The project layer always passes a keystore path + an explicit ``reversible`` flag
(the old "no path -> random ephemeral salt" behaviour broke cross-run stability).
deid_run reports the input/output SHA-256 of each processed file and, when given
a signing key, drops a sidecar signature next to each output.
"""
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, keystore as ks, signing


def _src(path, pid="M99887766A"):
    ds = Dataset()
    ds.PatientName = "Tan^Ah Kow"
    ds.PatientID = pid
    ds.Modality = "US"
    ds.StudyDate = "20240115"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.6.1"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)
    return str(path)


def test_deid_run_reports_before_and_after_checksums(tmp_path):
    src = _src(tmp_path / "in.dcm")
    out = str(tmp_path / "out.dcm")
    kp = str(tmp_path / "ks.json")
    rep = core.deid_run(src, out, keystore_path=kp, passphrase="pw", reversible=False)
    f = [x for x in rep["files"] if x.get("output")][0]
    assert f["input_sha256"] == signing.file_sha256(src)
    assert f["output_sha256"] == signing.file_sha256(out)
    assert len(f["output_sha256"]) == 64


def test_irreversible_run_is_deterministic_across_time(tmp_path):
    src = _src(tmp_path / "in.dcm")
    kp = str(tmp_path / "ks.json")
    out1 = str(tmp_path / "a" / "out.dcm")
    out2 = str(tmp_path / "b" / "out.dcm")
    core.deid_run(src, out1, keystore_path=kp, passphrase="pw", reversible=False)
    core.deid_run(src, out2, keystore_path=kp, passphrase="pw", reversible=False)
    pid1 = pydicom.dcmread(out1).PatientID
    pid2 = pydicom.dcmread(out2).PatientID
    assert pid1 == pid2                       # same salt -> same pseudonym
    reopened = ks.open(kp, "pw")
    assert reopened.reversible is False        # and nothing reversible was stored


def test_deid_run_signs_each_output_when_key_given(tmp_path):
    src = _src(tmp_path / "in.dcm")
    out = str(tmp_path / "out.dcm")
    kp = str(tmp_path / "ks.json")
    keys = signing.ensure_keypair(str(tmp_path / "sign"))
    rep = core.deid_run(src, out, keystore_path=kp, passphrase="pw", reversible=True,
                        sign_key_path=keys["key_path"], signer="yh",
                        project_id="proj1")
    assert signing.verify_output(out, keys["pub_path"])["ok"] is True
    f = [x for x in rep["files"] if x.get("output")][0]
    assert f["signature"]["project_id"] == "proj1"
