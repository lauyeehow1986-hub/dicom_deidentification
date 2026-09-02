"""Encrypted keystore: the secret salt + the reversible pseudonym crosswalk.

At rest the store is a JSON envelope whose payload is encrypted with AES-256-GCM
under a key derived from the passphrase via scrypt. In irreversible mode nothing
is persisted and the crosswalk is never populated.
"""

from __future__ import annotations

import builtins
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1


def _derive_key(passphrase: str, kdf_salt: bytes) -> bytes:
    kdf = Scrypt(salt=kdf_salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


class Keystore:
    def __init__(self, path, salt, crosswalk, passphrase, reversible):
        self.path = path
        self._salt = salt
        self._crosswalk = crosswalk
        self._passphrase = passphrase
        self._reversible = reversible

    @property
    def salt(self) -> bytes:
        return self._salt

    @property
    def reversible(self) -> bool:
        return self._reversible

    def record(self, category: str, original: str, pseudonym: str) -> None:
        """Store an orig<->pseudonym mapping (only in reversible mode)."""
        if self._reversible:
            self._crosswalk[pseudonym] = {"category": category, "original": original}

    def reverse(self, pseudonym: str):
        """Return the original value for a pseudonym, or None."""
        if not self._reversible:
            return None
        entry = self._crosswalk.get(pseudonym)
        return entry["original"] if entry else None

    def save(self) -> None:
        """Encrypt and write the store (no-op for irreversible/ephemeral stores)."""
        if not self._reversible or not self.path:
            return
        kdf_salt = os.urandom(16)
        key = _derive_key(self._passphrase, kdf_salt)
        nonce = os.urandom(12)
        payload = json.dumps(
            {"salt": self._salt.hex(), "crosswalk": self._crosswalk}
        ).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, payload, None)
        envelope = {
            "v": 1, "kdf": "scrypt",
            "kdf_salt": kdf_salt.hex(), "nonce": nonce.hex(), "ciphertext": ct.hex(),
        }
        with builtins.open(self.path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)


def create(path: str, passphrase: str) -> Keystore:
    """Create a new reversible keystore with a fresh random salt and persist it."""
    k = Keystore(path, os.urandom(32), {}, passphrase, reversible=True)
    k.save()
    return k


def open(path: str, passphrase: str) -> Keystore:  # noqa: A001 (shadow builtin by design)
    """Open an existing keystore; raises if the passphrase is wrong (AEAD auth)."""
    with builtins.open(path, "r", encoding="utf-8") as fh:
        envelope = json.load(fh)
    key = _derive_key(passphrase, bytes.fromhex(envelope["kdf_salt"]))
    plaintext = AESGCM(key).decrypt(
        bytes.fromhex(envelope["nonce"]), bytes.fromhex(envelope["ciphertext"]), None
    )
    data = json.loads(plaintext)
    return Keystore(path, bytes.fromhex(data["salt"]), data["crosswalk"],
                    passphrase, reversible=True)


def ephemeral() -> Keystore:
    """An in-memory, irreversible keystore: random salt, nothing persisted."""
    return Keystore(None, os.urandom(32), {}, None, reversible=False)
