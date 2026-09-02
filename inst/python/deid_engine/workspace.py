"""Writable workspace for Phase 4 self-improvement.

The shipped package (``inst/profiles``, ``inst/gazetteers``) is read-only on the
air-gapped box. Everything the reviewer *creates* — per-project profiles, custom
gazetteers grown by tagging misses, and the labeled-example store that feeds a
later NER fine-tune — lives here instead, outside git and outside the package.

Location: ``$DICOMDEID_WORKSPACE`` if set, else ``./workspace`` next to where the
app runs. Directories are created on demand so a fresh copy of the app just works.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def workspace_dir() -> Path:
    d = os.environ.get("DICOMDEID_WORKSPACE")
    base = Path(d) if d else (Path.cwd() / "workspace")
    base.mkdir(parents=True, exist_ok=True)
    return base


def profiles_dir() -> Path:
    p = workspace_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def gazetteers_dir() -> Path:
    p = workspace_dir() / "gazetteers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def labeled_store() -> Path:
    """Path to the append-only labeled-example store (JSON lines)."""
    return workspace_dir() / "labeled_examples.jsonl"


def _existing_names(path: Path) -> set[str]:
    seen: set[str] = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                seen.add(s.lower())
    return seen


def append_gazetteer(name: str, value: str) -> Path:
    """Append ``value`` to ``workspace/gazetteers/<name>.txt`` unless an entry with
    the same text (case-insensitively) is already present. Returns the file path."""
    path = gazetteers_dir() / f"{name}.txt"
    value = (value or "").strip()
    if value and value.lower() not in _existing_names(path):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(value + "\n")
    return path


def append_labeled_example(record: dict) -> dict:
    """Append one labeled example (a captured miss) to the JSONL store, stamping a
    timestamp when absent. Returns the stored record."""
    rec = dict(record)
    rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with labeled_store().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_labeled_examples() -> list[dict]:
    p = labeled_store()
    if not p.exists():
        return []
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            pass  # skip a corrupt line rather than fail the whole read
    return out
