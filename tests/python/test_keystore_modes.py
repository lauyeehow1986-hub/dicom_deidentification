"""Phase 6.5 - keystore hashing modes and scopes.

Two requirements drive these tests:

* "Same NRIC hashes the same way regardless of WHEN we de-identify" - the salt
  must PERSIST even in irreversible mode, so the deterministic-irreversible
  keystore stores the salt but never the orig<->pseudonym crosswalk.
* "Global vs project hashing" - two DIFFERENT keystore files carry two different
  salts, so the same value hashes differently; one shared keystore hashes it
  identically. That property is exercised here at the keystore level.
"""
from deid_engine import keystore as ks
from deid_engine import pseudonym


def test_reversible_roundtrip_persists_salt_and_crosswalk(tmp_path):
    p = str(tmp_path / "rev.json")
    k = ks.create(p, "pw", reversible=True)
    salt = k.salt
    k.record("0x00100010", "Tan Ah Kow", "ANON^1")
    k.save()

    reopened = ks.open(p, "pw")
    assert reopened.reversible is True
    assert reopened.salt == salt                       # stable salt
    assert reopened.reverse("ANON^1") == "Tan Ah Kow"  # crosswalk survived


def test_irreversible_persists_salt_but_never_crosswalk(tmp_path):
    p = str(tmp_path / "irr.json")
    k = ks.create(p, "pw", reversible=False)
    salt = k.salt
    # record() must be a no-op in irreversible mode
    k.record("0x00100010", "Lim Bee Hwa", "ANON^2")
    k.save()

    reopened = ks.open(p, "pw")
    assert reopened.reversible is False
    assert reopened.salt == salt                 # salt DID persist -> stable hash
    assert reopened.reverse("ANON^2") is None    # but nothing is reversible


def test_persisted_salt_gives_stable_hash_across_time(tmp_path):
    p = str(tmp_path / "stable.json")
    k1 = ks.create(p, "pw", reversible=False)
    h1 = pseudonym.salted_sha256("S1234567D", k1.salt)
    # a later, independent "session" reopens the same keystore
    k2 = ks.open(p, "pw")
    h2 = pseudonym.salted_sha256("S1234567D", k2.salt)
    assert h1 == h2


def test_separate_keystores_hash_the_same_value_differently(tmp_path):
    a = ks.create(str(tmp_path / "projA.json"), "pw", reversible=False)
    b = ks.create(str(tmp_path / "projB.json"), "pw", reversible=False)
    assert a.salt != b.salt
    assert (pseudonym.salted_sha256("S1234567D", a.salt)
            != pseudonym.salted_sha256("S1234567D", b.salt))


def test_shared_keystore_hashes_the_same_value_identically(tmp_path):
    p = str(tmp_path / "global.json")
    ks.create(p, "pw", reversible=False)
    g1 = ks.open(p, "pw")
    g2 = ks.open(p, "pw")   # a second project pointing at the same global store
    assert (pseudonym.salted_sha256("S1234567D", g1.salt)
            == pseudonym.salted_sha256("S1234567D", g2.salt))
