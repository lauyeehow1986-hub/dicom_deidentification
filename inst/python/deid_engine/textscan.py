"""Phase 2 - layered text PHI detection.

The strongest-configuration name/identifier stack from the plan, arranged so the
deterministic, dependency-light layers always run on the air-gapped box and the
heavier statistical layers light up only when their packages/models are present:

    1. header-token scrub  - the real PatientName/ID tokens for THIS study
    2. gazetteer           - the user's SG patient-name dictionary
    3. SG recognisers      - NRIC/FIN (checksum), phone, email  (regex + checksum)
    4. Presidio            - general PII (optional; presidio-analyzer)
    5. transformer NER     - multilingual XLM-R person/location (optional; torch)

Each layer yields ``PhiSpan``s over a string; ``TextScanner`` merges overlaps and
redacts. This module has NO hard dependency on presidio/torch so it imports and
runs (layers 1-3) even before the NER extras are installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Loaded engines are cached per process so the (heavy) spaCy / NER models load
# once, not once per file or per De-identify click.
_PRESIDIO_CACHE: dict = {}
_NER_CACHE: dict = {}


@dataclass
class PhiSpan:
    """A detected identifier occurrence within a piece of text."""
    start: int
    end: int
    category: str          # name | nric_fin | phone | email | location | ...
    text: str
    source: str            # which layer found it (header/gazetteer/sg/presidio/ner)
    score: float = 1.0

    def overlaps(self, other: "PhiSpan") -> bool:
        return self.start < other.end and other.start < self.end


# --------------------------------------------------------------------------- #
# Layer 3: Singapore recognisers (deterministic)                              #
# --------------------------------------------------------------------------- #

# NRIC/FIN: prefix letter, 7 digits, checksum letter.
_NRIC_RE = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b")
_NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_NRIC_TABLE_ST = "JZIHGFEDCBA"   # citizens / PRs (S, T)
_NRIC_TABLE_FG = "XWUTRQPNMLK"   # foreigners (F, G)


def nric_check_letter(prefix: str, digits: str) -> str | None:
    """Canonical NRIC/FIN check letter for S/T/F/G prefixes (None for others)."""
    prefix = prefix.upper()
    total = sum(int(d) * w for d, w in zip(digits, _NRIC_WEIGHTS))
    if prefix in ("T", "G"):
        total += 4
    r = total % 11
    if prefix in ("S", "T"):
        return _NRIC_TABLE_ST[r]
    if prefix in ("F", "G"):
        return _NRIC_TABLE_FG[r]
    return None  # M-series uses a distinct, less-stable scheme -> pattern tier


def find_nric_fin(text: str) -> list[PhiSpan]:
    """Find NRIC/FIN numbers. Checksum-valid -> score 1.0; right shape -> 0.5."""
    out: list[PhiSpan] = []
    for m in _NRIC_RE.finditer(text or ""):
        prefix, digits, check = m.group(1), m.group(2), m.group(3)
        expected = nric_check_letter(prefix, digits)
        score = 1.0 if (expected is not None and check == expected) else 0.5
        out.append(PhiSpan(m.start(), m.end(), "nric_fin", m.group(0), "sg", score))
    return out


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def find_email(text: str) -> list[PhiSpan]:
    return [PhiSpan(m.start(), m.end(), "email", m.group(0), "sg", 1.0)
            for m in _EMAIL_RE.finditer(text or "")]


# SG phone: optional +65 / 65, then an 8-digit local number starting 3/6/8/9,
# allowing one space/hyphen after the 4th digit. Bare 7-digit numbers are ignored.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?65[\s-]?)?([3689]\d{3})[\s-]?(\d{4})(?!\d)")


def find_phone(text: str) -> list[PhiSpan]:
    return [PhiSpan(m.start(), m.end(), "phone", m.group(0), "sg", 0.9)
            for m in _PHONE_RE.finditer(text or "")]


SG_RECOGNISERS = (find_nric_fin, find_email, find_phone)


# --------------------------------------------------------------------------- #
# Layer 2: gazetteer                                                          #
# --------------------------------------------------------------------------- #

class Gazetteer:
    """Case-insensitive, word-boundary matcher over a user-supplied name list."""

    def __init__(self, names):
        cleaned = sorted({str(n).strip() for n in (names or []) if str(n).strip()},
                         key=len, reverse=True)
        # \b handles the common Latin-script case; names are matched whole.
        self._patterns = [(n, re.compile(r"\b" + re.escape(n) + r"\b",
                                         re.IGNORECASE)) for n in cleaned]

    def find(self, text: str) -> list[PhiSpan]:
        out: list[PhiSpan] = []
        for _name, pat in self._patterns:
            for m in pat.finditer(text or ""):
                out.append(PhiSpan(m.start(), m.end(), "name", m.group(0),
                                   "gazetteer", 1.0))
        return out


# --------------------------------------------------------------------------- #
# Layer 1: header-token scrub                                                 #
# --------------------------------------------------------------------------- #

def _header_spans(text: str, known_values) -> list[PhiSpan]:
    """Flag the real identifier tokens for THIS study wherever they appear."""
    out: list[PhiSpan] = []
    for kv in sorted({str(k).strip() for k in (known_values or []) if str(k).strip()},
                     key=len, reverse=True):
        for m in re.finditer(r"\b" + re.escape(kv) + r"\b", text or "",
                             flags=re.IGNORECASE):
            # an all-digit token is an ID; otherwise treat as a name token
            cat = "id" if kv.isdigit() or re.fullmatch(r"[A-Za-z]*\d+\w*", kv) else "name"
            out.append(PhiSpan(m.start(), m.end(), cat, m.group(0), "header", 1.0))
    return out


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #

class TextScanner:
    """Combine the detection layers, merge overlaps, and redact.

    Layers 1-3 are always available. ``use_presidio`` / ``use_ner`` enable the
    optional layers; each degrades to a no-op (with a note in ``self.notes``) if
    its package or model is missing, so the scanner never hard-fails.
    """

    # Presidio contributes ONLY person/location recall here: names and addresses
    # the gazetteer misses. Email/phone/NRIC are handled precisely by the
    # deterministic SG layer, and restricting Presidio to these two entities also
    # avoids a built-in email/URL recogniser that can hang on some inputs.
    _PRESIDIO_KEEP = {"PERSON": "name", "LOCATION": "location"}

    def __init__(self, known_values=None, gazetteer=None,
                 use_presidio=False, use_ner=False,
                 ner_model=None, presidio_languages=("en",),
                 presidio_min_score=0.35):
        self.known_values = list(known_values or [])
        self.gazetteer = gazetteer
        self.presidio_min_score = presidio_min_score
        self.notes: list[str] = []
        self._analyzer = self._load_presidio(presidio_languages) if use_presidio else None
        self._ner = self._load_ner(ner_model) if use_ner else None

    def set_known_values(self, values):
        """Swap in this file's header tokens without rebuilding the (heavy)
        optional layers, so one scanner can be reused across a whole study."""
        self.known_values = list(values or [])
        return self

    # -- optional layers ---------------------------------------------------- #

    def _load_presidio(self, languages):
        key = tuple(languages)
        if key not in _PRESIDIO_CACHE:
            _PRESIDIO_CACHE[key] = self._build_presidio(languages)
        engine = _PRESIDIO_CACHE[key]
        if engine is None:
            self.notes.append("presidio unavailable: no spaCy model installed "
                              "(deterministic layers still active)")
        return engine

    def _build_presidio(self, languages):
        # Build ONLY from an already-installed spaCy model. A bare AnalyzerEngine()
        # tries to auto-download its default model, which hangs on the air-gapped
        # box; guarding on installed models makes the missing-model case fail fast.
        try:
            import spacy
            installed = set(spacy.util.get_installed_models())
            if not installed:
                return None
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from .sg_recognizers import register_sg_recognizers
            model = "en_core_web_lg" if "en_core_web_lg" in installed else sorted(installed)[0]
            provider = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": model}],
            })
            engine = AnalyzerEngine(nlp_engine=provider.create_engine())
            register_sg_recognizers(engine.registry)
            return engine
        except Exception:  # noqa: BLE001 - degrade gracefully on air-gapped box
            return None

    def _load_ner(self, ner_model):
        import os
        if not ner_model or not os.path.isdir(str(ner_model)):
            self.notes.append("ner unavailable: set text_detection.ner_model to a "
                              "bundled local model directory")
            return None
        key = os.path.abspath(str(ner_model))
        if key not in _NER_CACHE:
            _NER_CACHE[key] = self._build_ner(ner_model)
        ner = _NER_CACHE[key]
        if ner is None:
            self.notes.append(f"ner unavailable: could not load model at {ner_model}")
        return ner

    def _build_ner(self, ner_model):
        # Load ONLY from a bundled local model directory. This never touches the
        # network: on the air-gapped box the installer drops the NER weights and
        # points text_detection.ner_model at them.
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from transformers import (AutoModelForTokenClassification,
                                      AutoTokenizer, pipeline)
            tok = AutoTokenizer.from_pretrained(ner_model, local_files_only=True)
            mdl = AutoModelForTokenClassification.from_pretrained(
                ner_model, local_files_only=True)
            return pipeline("token-classification", model=mdl, tokenizer=tok,
                            aggregation_strategy="simple")
        except Exception:  # noqa: BLE001
            return None

    def _presidio_spans(self, text: str) -> list[PhiSpan]:
        if not self._analyzer:
            return []
        out: list[PhiSpan] = []
        try:
            results = self._analyzer.analyze(text=text, language="en",
                                             entities=list(self._PRESIDIO_KEEP))
            for r in results:
                cat = self._PRESIDIO_KEEP.get(r.entity_type)
                if cat is None or float(r.score) < self.presidio_min_score:
                    continue  # drop non-identifier / low-confidence entities
                out.append(PhiSpan(r.start, r.end, cat,
                                   text[r.start:r.end], "presidio", float(r.score)))
        except Exception as e:  # noqa: BLE001
            self.notes.append(f"presidio scan failed: {e}")
        return out

    def _ner_spans(self, text: str) -> list[PhiSpan]:
        if not self._ner:
            return []
        cat = {"PER": "name", "PERSON": "name", "LOC": "location", "ORG": "org"}
        out: list[PhiSpan] = []
        try:
            for e in self._ner(text):
                grp = e.get("entity_group", "")
                if grp in ("PER", "PERSON", "LOC"):
                    out.append(PhiSpan(int(e["start"]), int(e["end"]),
                                       cat.get(grp, "other"),
                                       text[int(e["start"]):int(e["end"])],
                                       "ner", float(e.get("score", 0.5))))
        except Exception as ex:  # noqa: BLE001
            self.notes.append(f"ner scan failed: {ex}")
        return out

    # -- public API --------------------------------------------------------- #

    def scan(self, text: str) -> list[PhiSpan]:
        if not text:
            return []
        spans: list[PhiSpan] = []
        spans += _header_spans(text, self.known_values)
        if self.gazetteer is not None:
            spans += self.gazetteer.find(text)
        for rec in SG_RECOGNISERS:
            spans += rec(text)
        spans += self._presidio_spans(text)
        spans += self._ner_spans(text)
        return _merge(spans)

    def redact(self, text: str, replacement="[REDACTED]"):
        """Return ``(redacted_text, spans)``. ``replacement`` is a str or a
        callable ``span -> str``. Overlaps are merged before substitution."""
        spans = self.scan(text)
        if not spans:
            return text, spans
        out = []
        cursor = 0
        for s in spans:
            out.append(text[cursor:s.start])
            out.append(replacement(s) if callable(replacement) else replacement)
            cursor = s.end
        out.append(text[cursor:])
        return "".join(out), spans


def _merge(spans: list[PhiSpan]) -> list[PhiSpan]:
    """Sort by position and collapse overlapping spans, keeping the widest/most
    confident. Prevents double-redaction when layers agree on the same text."""
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start), -s.score))
    merged: list[PhiSpan] = []
    for s in spans:
        if merged and s.overlaps(merged[-1]):
            prev = merged[-1]
            # widen to the union; keep the higher-scoring category/label
            keep = prev if (prev.end - prev.start, prev.score) >= (s.end - s.start, s.score) else s
            merged[-1] = PhiSpan(min(prev.start, s.start), max(prev.end, s.end),
                                 keep.category, keep.text, keep.source, max(prev.score, s.score))
        else:
            merged.append(s)
    return merged
