"""deid_engine — DICOM/NIfTI + PHI de-identification engine.

Called from R (engine_bridge.R) via reticulate. Public surface is intentionally
small and importable/testable from pytest without R.

Scaffold stage exposes stubs + the PS3.15 action-code vocabulary so the R side
and the profiles can be wired against a stable interface. Real implementations
land per phase (see docs/roadmap.md).
"""

from .core import (
    ACTION_CODES,
    engine_info,
    deidentify_study,   # Phase 1/3
    scan_residual,      # Phase 6
)

__all__ = [
    "ACTION_CODES",
    "engine_info",
    "deidentify_study",
    "scan_residual",
]
