# tests/python/test_deface.py
import numpy as np
from deid_engine import core
from deid_engine import deface


def _head_volume():
    """A crude 'head': a filled ellipsoid centred in an air-bordered 24x40x40
    volume (depth 24 >= min_axial; strong air border; compact central blob)."""
    d, h, w = 24, 40, 40
    zz, yy, xx = np.ogrid[:d, :h, :w]
    cz, cy, cx = d / 2, h / 2, w / 2
    ell = ((zz - cz) / (d * 0.35))**2 + ((yy - cy) / (h * 0.3))**2 + \
          ((xx - cx) / (w * 0.3))**2 <= 1.0
    vol = np.zeros((d, h, w), dtype=np.float32)
    vol[ell] = 500.0
    return vol


def test_is_head_inclusive_true_for_air_bordered_deep_blob():
    assert deface.is_head_inclusive(_head_volume()) is True


def test_is_head_inclusive_false_for_shallow_chest_slab():
    # Too few slices through-plane (a short cardiac cine stack).
    assert deface.is_head_inclusive(np.ones((4, 40, 40), dtype=np.float32)) is False


def test_is_head_inclusive_false_for_uniform_volume():
    # A uniform volume has no voxel above its own mean, so there is no foreground
    # at all -> rejected by the no-foreground guard (before the border/frac logic).
    assert deface.is_head_inclusive(np.ones((24, 40, 40), dtype=np.float32)) is False


def test_is_head_inclusive_false_when_foreground_reaches_border():
    # Foreground touches an end-slice of the shortest axis (no air border there),
    # so the border-background condition rejects it. This exercises border_bg_frac,
    # not the earlier no-foreground guard.
    d, h, w = 24, 40, 40
    vol = np.zeros((d, h, w), dtype=np.float32)
    vol[5:] = 100.0          # slices 5..23 bright; mean ~79, so fg = slices 5..23
    # shortest axis is 0: end-slice z=0 is background, z=23 is foreground
    # -> border_bg_frac = 1 - (0 + 1)/2 = 0.5, which is <= 0.7 -> rejected
    assert deface.is_head_inclusive(vol) is False


def test_is_head_inclusive_false_when_foreground_too_large():
    # Air border present (both shortest-axis end-slices are background) but the
    # interior is almost entirely foreground -> frac exceeds the 0.6 compactness
    # bound -> rejected. Exercises the frac upper bound with the border passing.
    d, h, w = 24, 40, 40
    vol = np.zeros((d, h, w), dtype=np.float32)
    vol[2:-2] = 100.0        # 20 bright slices, 2-slice air margin each end
    # border_bg_frac = 1.0 (both ends background), frac ~0.83 > 0.6 -> rejected
    assert deface.is_head_inclusive(vol) is False


def test_looks_like_ct_true_for_hounsfield_floor():
    ct = _head_volume() - 1000.0   # air ~ -1000 HU
    assert deface.looks_like_ct(ct) is True


def test_looks_like_ct_false_for_nonnegative_mr():
    assert deface.looks_like_ct(_head_volume()) is False


def test_deface_available_false_without_weights(monkeypatch, tmp_path):
    monkeypatch.setattr(deface, "_model_path", lambda: tmp_path / "model.pt")
    ok, note = deface.deface_available()
    assert ok is False and "unavailable" in note


def test_dilate_does_not_wrap_across_edges():
    m = np.zeros((3, 5, 5), dtype=bool)
    m[1, 0, 2] = True                     # a voxel on the y=0 face
    d = deface._dilate(m, 1)
    assert d[1, 1, 2]                     # grows to the true in-bounds neighbour
    assert not d[1, -1, 2]                # must NOT wrap to the opposite face
    assert not d[1, 0, -1]                # nor wrap along the x axis


def _face_model(vol):
    """Stub model: 'face' = the anterior quarter (low-y rows) of foreground."""
    a = np.asarray(vol)
    mask = np.zeros(a.shape, dtype=bool)
    h = a.shape[1]
    mask[:, : h // 4, :] = a[:, : h // 4, :] > a.mean()
    return mask


def test_deface_array_zeros_face_keeps_brain_with_stub_model():
    vol = _head_volume()
    brain_before = vol[:, vol.shape[1] // 2:, :].sum()
    out, info = deface.deface_array(vol, modality="MR", model=_face_model, margin=1)
    assert info["defaced"] is True
    assert info["voxels_removed"] > 0
    # the stub's face window (rows 0..h//4) genuinely HAD signal, and is now erased
    face_window = slice(None), slice(None, vol.shape[1] // 4), slice(None)
    assert vol[face_window].sum() > 0            # non-vacuous: the region had voxels
    assert out[face_window].sum() == 0           # ...and defacing zeroed all of it
    assert out[:, vol.shape[1] // 2:, :].sum() == brain_before


def test_deface_array_skips_chest_fov_untouched():
    chest = np.ones((4, 40, 40), dtype=np.float32)
    out, info = deface.deface_array(chest, modality="MR", model=_face_model)
    assert info == {"defaced": False, "reason": "no-head-fov"}
    assert np.array_equal(out, chest)


def test_deface_array_flags_head_ct_for_review():
    ct = _head_volume() - 1000.0
    out, info = deface.deface_array(ct, modality=None, model=_face_model)  # NIfTI: modality unknown
    assert info == {"defaced": False, "flagged_for_review": "head-inclusive non-MR"}
    assert np.array_equal(out, ct)


def test_deface_array_degrades_without_model(monkeypatch):
    monkeypatch.setattr(deface, "deface_available", lambda: (False, "no weights"))
    vol = _head_volume()
    out, info = deface.deface_array(vol, modality="MR", model=None)
    assert info["defaced"] is False and "no weights" in info["note"]
    assert np.array_equal(out, vol)


def test_deface_enabled_reads_profile():
    assert core._deface_enabled({"deface": {"enabled": True}}) is True
    assert core._deface_enabled({"deface": {"enabled": False}}) is False
    assert core._deface_enabled({}) is False


def test_deid_run_deface_flag_turns_on_profile(tmp_path, monkeypatch):
    # Capture the profile deidentify_study receives to prove the flag threads in.
    seen = {}

    def _capture(ip, op, profile, ks):
        seen["p"] = profile
        return {"files": [], "count": 0}

    monkeypatch.setattr(core, "deidentify_study", _capture)
    core.deid_run(str(tmp_path), str(tmp_path / "o"), deface=True)
    assert seen["p"]["deface"]["enabled"] is True
