"""Phase 6.5 - integrity checksums and Ed25519 sidecar signing.

A de-identified output carries a detached ``<output>.sig.json`` signed with the
pipeline's Ed25519 key. A reviewer holding only the PUBLIC key can confirm the
file is unchanged and genuinely came from this pipeline; nobody without the
private key can forge it.
"""
import json

from deid_engine import signing


def test_file_sha256_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "x.bin"
    data = b"cardiac ultrasound bytes" * 1000
    p.write_bytes(data)
    assert signing.file_sha256(str(p)) == hashlib.sha256(data).hexdigest()


def test_ensure_keypair_is_idempotent(tmp_path):
    d = str(tmp_path / "signing")
    kp1 = signing.ensure_keypair(d)
    priv1 = open(kp1["key_path"], "rb").read()
    kp2 = signing.ensure_keypair(d)          # must NOT regenerate
    priv2 = open(kp2["key_path"], "rb").read()
    assert priv1 == priv2
    assert kp1["pub_path"] == kp2["pub_path"]


def test_sign_then_verify_roundtrip(tmp_path):
    kp = signing.ensure_keypair(str(tmp_path / "s"))
    out = tmp_path / "deid.dcm"
    out.write_bytes(b"a valid de-identified object")
    rec = signing.sign_output(str(out), {"profile_id": "default",
                                          "project_id": "proj1",
                                          "signed_by": "yh",
                                          "deid_method": "dicomdeid"},
                              kp["key_path"])
    sig_path = str(out) + ".sig.json"
    assert json.load(open(sig_path))["project_id"] == "proj1"
    res = signing.verify_output(str(out), kp["pub_path"])
    assert res["ok"] is True


def test_tampered_output_fails_verification(tmp_path):
    kp = signing.ensure_keypair(str(tmp_path / "s"))
    out = tmp_path / "deid.dcm"
    out.write_bytes(b"original signed bytes")
    signing.sign_output(str(out), {"signed_by": "yh"}, kp["key_path"])
    out.write_bytes(b"tampered bytes after signing")   # change the file
    res = signing.verify_output(str(out), kp["pub_path"])
    assert res["ok"] is False


def test_wrong_key_fails_verification(tmp_path):
    kp = signing.ensure_keypair(str(tmp_path / "s"))
    other = signing.ensure_keypair(str(tmp_path / "other"))
    out = tmp_path / "deid.dcm"
    out.write_bytes(b"signed by s")
    signing.sign_output(str(out), {"signed_by": "yh"}, kp["key_path"])
    res = signing.verify_output(str(out), other["pub_path"])
    assert res["ok"] is False
