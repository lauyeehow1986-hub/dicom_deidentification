"""Integrity checksums and Ed25519 sidecar signatures for de-identified outputs.

Each output gets a detached ``<output>.sig.json`` whose signature covers a
canonical JSON of the file's SHA-256 plus de-id provenance. Verification needs
only the PUBLIC key, so a reviewer on any machine can confirm a file is
unchanged and came from this pipeline, while nobody without the private key can
forge a signature. Built on the ``cryptography`` dependency already in the venv.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_KEY_NAME = "pipeline_ed25519.key"
_PUB_NAME = "pipeline_ed25519.pub"
_SIG_SUFFIX = ".sig.json"


def file_sha256(path: str, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (1 MiB chunks; safe for very large volumes)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def ensure_keypair(directory: str) -> dict:
    """Return the pipeline keypair paths under ``directory``, generating a fresh
    Ed25519 key the first time. Idempotent: an existing key is never overwritten."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    key_path = d / _KEY_NAME
    pub_path = d / _PUB_NAME
    if not key_path.exists():
        priv = Ed25519PrivateKey.generate()
        key_path.write_bytes(priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        pub_path.write_bytes(priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        os.chmod(key_path, 0o600)
    return {"key_path": str(key_path), "pub_path": str(pub_path)}


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_private(key_path: str) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)


def _load_public(pub_path: str) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(Path(pub_path).read_bytes())


def sign_output(output_path: str, meta: dict, key_path: str) -> dict:
    """Write ``<output>.sig.json`` for ``output_path`` and return the record.

    ``meta`` may carry ``profile_id``, ``project_id``, ``signed_by`` and
    ``deid_method``; the signature covers the file hash plus this provenance.
    """
    payload = {
        "v": 1,
        "file": os.path.basename(output_path),
        "sha256": file_sha256(output_path),
        "deid_method": meta.get("deid_method", ""),
        "profile_id": meta.get("profile_id", ""),
        "project_id": meta.get("project_id", ""),
        "signed_by": meta.get("signed_by", ""),
        "signed_at": meta.get("signed_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    sig = _load_private(key_path).sign(_canonical(payload)).hex()
    record = dict(payload, sig=sig)
    with open(output_path + _SIG_SUFFIX, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return record


def verify_output(output_path: str, pub_path: str) -> dict:
    """Verify ``<output>.sig.json`` against the current bytes of ``output_path``.

    Returns ``{"ok": bool, "reason": str}``. A file verifies only when its bytes
    still match the signed hash AND the signature checks out under ``pub_path``.
    """
    sig_path = output_path + _SIG_SUFFIX
    if not os.path.exists(sig_path):
        return {"ok": False, "reason": "no signature file"}
    try:
        record = json.load(open(sig_path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"unreadable signature: {e}"}
    sig = record.get("sig", "")
    payload = {k: record[k] for k in record if k != "sig"}
    if file_sha256(output_path) != payload.get("sha256"):
        return {"ok": False, "reason": "hash mismatch (file changed since signing)"}
    try:
        _load_public(pub_path).verify(bytes.fromhex(sig), _canonical(payload))
    except Exception:  # noqa: BLE001 - InvalidSignature and friends
        return {"ok": False, "reason": "signature invalid for this key"}
    return {"ok": True, "reason": "verified"}
