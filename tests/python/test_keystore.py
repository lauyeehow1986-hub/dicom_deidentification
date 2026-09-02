"""Tests for the encrypted keystore (salt + reversible crosswalk)."""
import pytest

from deid_engine import keystore as ks


PW = "correct horse battery staple"


def test_create_persists_and_reopens_with_same_salt(tmp_path):
    path = tmp_path / "store.keystore"
    k = ks.create(str(path), PW)
    assert len(k.salt) == 32
    assert path.exists()
    salt = k.salt
    k.save()

    reopened = ks.open(str(path), PW)
    assert reopened.salt == salt           # salt is stable -> pseudonyms reproduce


def test_open_with_wrong_passphrase_fails(tmp_path):
    path = tmp_path / "store.keystore"
    ks.create(str(path), PW).save()
    with pytest.raises(Exception):
        ks.open(str(path), "wrong passphrase")


def test_crosswalk_round_trips_after_save_and_reopen(tmp_path):
    path = tmp_path / "store.keystore"
    k = ks.create(str(path), PW)
    k.record("mrn", "MRN0099887", "a1b2c3d4")
    k.save()

    reopened = ks.open(str(path), PW)
    assert reopened.reverse("a1b2c3d4") == "MRN0099887"


def test_ephemeral_is_irreversible_and_writes_nothing(tmp_path):
    k = ks.ephemeral()
    assert k.reversible is False
    assert len(k.salt) == 32
    k.record("mrn", "MRN0099887", "a1b2c3d4")   # no-op in irreversible mode
    assert k.reverse("a1b2c3d4") is None
    # nothing created on disk
    assert list(tmp_path.iterdir()) == []


def test_reversible_flag_is_true_for_created_store(tmp_path):
    k = ks.create(str(tmp_path / "s.keystore"), PW)
    assert k.reversible is True
