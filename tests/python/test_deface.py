# tests/python/test_deface.py
import numpy as np
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
