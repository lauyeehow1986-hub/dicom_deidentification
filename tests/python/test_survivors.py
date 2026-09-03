"""Phase 7 - literal planted-PHI survivor check over de-identified outputs.

The acceptance gate is only meaningful if the fixtures carry PHI *before*
de-id and none *after*. ``check_survivors`` does an exact-literal sweep of every
output's metadata (recursing sequences + private tags, normalising PN
separators) and reports which planted values survived, per category.
"""
from deid_engine import core, corpus, keystore


def test_survivors_present_before_deid(tmp_path):
    src = tmp_path / "src"
    corpus.build_corpus(str(src))
    res = corpus.check_survivors(str(src))
    assert res["n_files"] >= 4
    assert not res["passed"], "fixtures should carry planted PHI before de-id"
    # the planted Chinese name and an NRIC are among the survivors pre-de-id
    flat = sum(res["metadata_survivors"].values(), [])
    assert "Tan Wei Ming" in flat
    assert "S1234567D" in flat


def test_no_survivors_after_deid(tmp_path):
    src = tmp_path / "src"
    corpus.build_corpus(str(src))
    out = tmp_path / "out"
    ks = keystore.ephemeral()
    core.deidentify_study(str(src), str(out), core.profile_get("default"), ks)

    res = corpus.check_survivors(str(out))
    assert res["passed"], res["metadata_survivors"]
    assert res["metadata_survivors"] == {}
