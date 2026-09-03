"""deid_engine — DICOM/NIfTI + PHI de-identification engine.

Called from R (engine_bridge.R) via reticulate. Public surface is intentionally
small and importable/testable from pytest without R.

Scaffold stage exposes stubs + the PS3.15 action-code vocabulary so the R side
and the profiles can be wired against a stable interface. Real implementations
land per phase (see docs/roadmap.md).
"""

import os as _os

# torch (NER) and spaCy/thinc (Presidio) can each load their own OpenMP runtime;
# on Windows the duplicate-runtime clash can deadlock. Allow it before either is
# imported. Set once at engine import so both the R-invoked path and tests get it.
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from .core import (
    ACTION_CODES,
    engine_info,
    deidentify_dataset,  # Phase 1
    deidentify_study,    # Phase 1/3
    deid_run,            # Phase 1 high-level entry (used by the R UI)
    pixel_info,          # Phase 3 (viewer geometry + OCR boxes)
    pixel_frame_png,     # Phase 3 (base64 frame preview)
    pixel_redact,        # Phase 3 (apply boxes + strip audio -> valid DICOM)
    profiles_list,       # Phase 4 (per-project profiles)
    profile_get,         # Phase 4
    profile_save,        # Phase 4
    profile_clone,       # Phase 4
    tag_capture,         # Phase 4 (tag-a-miss self-improvement)
    ner_export_examples, # Phase 4 (NER fine-tuning hook, stub)
    scan_residual,       # Phase 6
    scan_residual_dir,   # Phase 6
    read_metadata,       # Phase 6.5 (reviewer metadata view)
    keystore_summary,    # Phase 7 (reversibility policy check)
    keystore_reverse,    # Phase 7 (authorised re-identification)
)
from .signing import (
    file_sha256,         # Phase 6.5 (integrity checksum)
    ensure_keypair,      # Phase 6.5 (Ed25519 sidecar signing)
    sign_output,
    verify_output,
)
from . import corpus    # Phase 7 (synthetic acceptance corpus)
from .corpus import build_corpus, check_survivors

__all__ = [
    "ACTION_CODES",
    "engine_info",
    "deidentify_dataset",
    "deidentify_study",
    "deid_run",
    "pixel_info",
    "pixel_frame_png",
    "pixel_redact",
    "profiles_list",
    "profile_get",
    "profile_save",
    "profile_clone",
    "tag_capture",
    "ner_export_examples",
    "scan_residual",
    "scan_residual_dir",
    "read_metadata",
    "keystore_summary",
    "keystore_reverse",
    "file_sha256",
    "ensure_keypair",
    "sign_output",
    "verify_output",
    "corpus",
    "build_corpus",
    "check_survivors",
]
