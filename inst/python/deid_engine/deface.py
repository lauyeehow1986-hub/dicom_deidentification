# inst/python/deid_engine/deface.py
"""Phase 3 (deferred) - optional ML defacing of head-inclusive MRI volumes.

Removes the facial biometric surface (eyes/nose/mouth) from a head-inclusive MR
volume, keeping the brain intact. Off by default; enabled per run. Two cheap
gates decide *whether* to run before the model is even loaded:

  * ``is_head_inclusive`` - geometry: enough through-plane depth AND an air
    border AND a compact central blob. A chest-only cardiac stack fails this and
    passes through untouched.
  * ``looks_like_ct`` - intensity: CT stores Hounsfield units (air ~ -1000), so a
    large negative floor marks CT-like data. Used only for format-less NIfTI;
    DICOM passes its ``Modality`` tag directly. Head-inclusive CT is flagged for
    manual handling, never defaced by an MR-trained model.

The face mask itself comes from a bundled CPU TorchScript model under
``inst/models/deface/model.pt`` (shipped like the NER model). When the weights
are absent the whole step degrades to a noted skip - never fatal - exactly like
the OCR/NER layers. Efficacy on real MR is validated separately once weights are
pinned; the logic here is exercised with a stub model in tests.
"""
from __future__ import annotations

import numpy as np

_MODEL_SUBDIR = "deface"


def _model_dir():
    from . import rules as _rules
    return _rules.PROFILE_DIR.parent / "models" / _MODEL_SUBDIR  # inst/models/deface


def _model_path():
    return _model_dir() / "model.pt"


def deface_available():
    """(bool, note). True when the bundled TorchScript defacing model is present
    and torch is importable. Degrades like ``pixels._ocr_available``."""
    p = _model_path()
    try:
        present = p.is_file()
    except Exception:  # noqa: BLE001
        present = False
    if not present:
        return False, f"deface model unavailable: no weights at {p}"
    try:
        import torch  # noqa: F401
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"deface model unavailable: {e}"


def looks_like_ct(array) -> bool:
    """CT-like intensity gate: a large negative floor (Hounsfield air) marks CT."""
    a = np.asarray(array)
    if a.size == 0:
        return False
    return float(np.percentile(np.asarray(a, dtype=np.float32), 1)) < -300.0


def is_head_inclusive(array, min_axial: int = 16) -> bool:
    """Cheap geometry gate for a head-inclusive FOV.

    Requires: >=3-D with >= ``min_axial`` slices on the shortest axis; a
    predominantly-background (air) volume border; and a compact central
    foreground blob (not an edge-to-edge slab). The "air border" check is a
    cheap proxy: it only inspects the two end-slices along the shortest axis,
    not the full 3-D perimeter. Conservative - a false negative (skipping a
    real head) is safer than mangling a chest scan."""
    a = np.asarray(array, dtype=np.float32)
    if a.ndim < 3 or min(a.shape) < min_axial:
        return False
    thr = float(a.mean())
    fg = a > thr
    if not fg.any():
        return False
    ax = int(np.argmin(a.shape))
    lo = np.take(fg, 0, axis=ax)
    hi = np.take(fg, a.shape[ax] - 1, axis=ax)
    border_bg_frac = 1.0 - float((lo.mean() + hi.mean()) / 2.0)
    frac = float(fg.mean())
    return border_bg_frac > 0.7 and 0.02 < frac < 0.6
