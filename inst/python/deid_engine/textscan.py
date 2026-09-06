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
# The boundaries reject alphanumerics on either side (not just digits) so an
# 8-digit run embedded in a hashed pseudonym (e.g. "87591237cef8c902") is not
# mistaken for a phone number by the QA residual scan.
_PHONE_RE = re.compile(
    r"(?<![0-9A-Za-z])(?:\+?65[\s-]?)?([3689]\d{3})[\s-]?(\d{4})(?![0-9A-Za-z])")


def find_phone(text: str) -> list[PhiSpan]:
    return [PhiSpan(m.start(), m.end(), "phone", m.group(0), "sg", 0.9)
            for m in _PHONE_RE.finditer(text or "")]


# Singapore hospital temporary IC (assigned when a patient has no usable NRIC/FIN):
# an X or Y prefix, 7 or 10 digits, and a trailing letter. There is no published
# check-letter algorithm for it, so it stays pattern-tier (recall over precision).
_TEMP_IC_RE = re.compile(
    r"(?<![0-9A-Za-z])([XY])(\d{7}|\d{10})([A-Z])(?![0-9A-Za-z])", re.IGNORECASE)


def find_temp_ic(text: str) -> list[PhiSpan]:
    return [PhiSpan(m.start(), m.end(), "temp_ic", m.group(0), "sg", 0.7)
            for m in _TEMP_IC_RE.finditer(text or "")]


# Admission case number: ten digits followed by one letter (e.g. 1234567890A).
# Alphanumeric boundaries stop it carving a run out of a hashed pseudonym or a
# longer identifier; a bare 10-digit run (no trailing letter) does NOT match.
_CASE_NO_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{10})([A-Za-z])(?![0-9A-Za-z])")


def find_case_number(text: str) -> list[PhiSpan]:
    return [PhiSpan(m.start(), m.end(), "case_number", m.group(0), "sg", 0.7)
            for m in _CASE_NO_RE.finditer(text or "")]


SG_RECOGNISERS = (find_nric_fin, find_temp_ic, find_case_number,
                  find_email, find_phone)


# --------------------------------------------------------------------------- #
# Layer 3b: dates in FREE TEXT (deterministic)                                #
# --------------------------------------------------------------------------- #
# Individual-linked dates are PHI wherever they appear. Structured DICOM date
# VRs (DA/DT/TM) are format-fixed by the standard and handled by the action
# map; this layer catches dates written into free-text / SR / PDF / pixel-OCR
# strings, where they arrive in arbitrary human formats. Separated and
# month-name forms are matched by default (low false-positive risk); bare
# 8/14-digit runs collide with IDs, so they are opt-in via ``include_bare``.

_MONTH_ALT = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
              r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|"
              r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")

# Numeric with a repeated separator: D-M-Y / M-D-Y / Y-M-D (sep in / - .).
# The back-reference forces the SAME separator both times, so "1-2/3" is not a
# date. Boundaries reject an alphanumeric neighbour so a date glued to an ID or
# a hashed pseudonym is not carved out mid-token.
_DATE_NUM_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{1,4})([/.\-])(\d{1,2})\2(\d{1,4})(?![0-9A-Za-z])")

# ISO date-time: yyyy-mm-dd, optional [ T]hh:mm[:ss] (covers "yyyy-mm-dd hh:mm:ss").
_DATE_ISO_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{4})-(\d{2})-(\d{2})"
    r"(?:[ T](\d{2}):(\d{2})(?::\d{2})?)?(?![0-9A-Za-z])")

# Month-name forms: "1 Jan 2024" / "01 January 2024" / "1st Jan 2024".
_DATE_DMY_NAME_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{1,2})(?:st|nd|rd|th)?[ \-]" + _MONTH_ALT +
    r"[ ,\-]+(\d{2,4})(?![0-9A-Za-z])", re.IGNORECASE)
# Month-first forms: "Jan 1, 2024" / "January 1 2024".
_DATE_MDY_NAME_RE = re.compile(
    r"(?<![A-Za-z])" + _MONTH_ALT + r"\.?[ \-](\d{1,2})(?:st|nd|rd|th)?"
    r"[ ,\-]+(\d{2,4})(?![0-9A-Za-z])", re.IGNORECASE)

# Bare compact runs (opt-in): yyyymmdd / ddmmyyyy / mmddyyyy and yyyymmddhhmmss.
_DATE_BARE8_RE = re.compile(r"(?<![0-9A-Za-z])(\d{8})(?![0-9A-Za-z])")
_DATE_BARE14_RE = re.compile(r"(?<![0-9A-Za-z])(\d{14})(?![0-9A-Za-z])")


def _dm_plausible(a: int, b: int) -> bool:
    """True if (a, b) works as day/month in EITHER order (dd/mm vs mm/dd)."""
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= b <= 31 and 1 <= a <= 12)


def _ymd_from_bare(s: str):
    """Return (y, m, d) if the 8-digit run reads as a plausible calendar date in
    YYYYMMDD or [DM]M[DM]MYYYY layout with a 1900-2099 year; else None."""
    # YYYYMMDD
    y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    if 1900 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31:
        return (y, m, d)
    # DDMMYYYY / MMDDYYYY (year at the end)
    y2, a, b = int(s[4:8]), int(s[:2]), int(s[2:4])
    if 1900 <= y2 <= 2099 and _dm_plausible(a, b):
        return (y2, a, b)
    return None


def find_dates(text: str, include_bare: bool = False) -> list[PhiSpan]:
    """Find individual-linked dates in free text.

    Separated (``dd/mm/yyyy``, ``yyyy-mm-dd``, ISO date-time) and month-name
    (``01 Jan 2024``, ``Jan 1, 2024``) formats are always scanned. When
    ``include_bare`` is set, bare ``ddmmyyyy`` / ``yyyymmdd`` / ``yyyymmddhhmmss``
    runs are added too, validated as plausible calendar dates to limit the
    false positives that bare 8-digit IDs would otherwise cause.
    """
    out: list[PhiSpan] = []

    def _add(start: int, end: int, score: float):
        # Skip a candidate that overlaps one already accepted; the ISO matcher
        # runs first, so its wider date-time span wins over the bare date part.
        for s in out:
            if start < s.end and s.start < end:
                return
        out.append(PhiSpan(start, end, "date", (text or "")[start:end], "date", score))

    text = text or ""
    for m in _DATE_ISO_RE.finditer(text):
        if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
            _add(m.start(), m.end(), 0.85)
    for m in _DATE_NUM_RE.finditer(text):
        a, c = m.group(1), m.group(4)
        if len(a) == 4 and len(c) <= 2:                 # Y sep M sep D
            if 1 <= int(m.group(3)) <= 12 and 1 <= int(c) <= 31:
                _add(m.start(), m.end(), 0.8)
        elif len(c) in (2, 4) and len(a) <= 2:          # D/M/Y or M/D/Y
            if _dm_plausible(int(a), int(m.group(3))):
                _add(m.start(), m.end(), 0.8)
    for rx in (_DATE_DMY_NAME_RE, _DATE_MDY_NAME_RE):
        for m in rx.finditer(text):
            if 1 <= int(m.group(1)) <= 31:
                _add(m.start(), m.end(), 0.9)
    if include_bare:
        for m in _DATE_BARE14_RE.finditer(text):
            if _ymd_from_bare(m.group(1)[:8]):
                _add(m.start(), m.end(), 0.6)
        for m in _DATE_BARE8_RE.finditer(text):
            if _ymd_from_bare(m.group(1)):
                _add(m.start(), m.end(), 0.6)
    return out


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
                 custom_regex=None, use_presidio=False, use_ner=False,
                 ner_model=None, presidio_languages=("en",),
                 presidio_min_score=0.35, detect_dates=True,
                 dates_include_bare=False):
        self.known_values = list(known_values or [])
        self.gazetteer = gazetteer
        self.presidio_min_score = presidio_min_score
        self.detect_dates = detect_dates
        self.dates_include_bare = dates_include_bare
        self.notes: list[str] = []
        self._custom = self._compile_custom(custom_regex)
        self._analyzer = self._load_presidio(presidio_languages) if use_presidio else None
        self._ner = self._load_ner(ner_model) if use_ner else None

    def _compile_custom(self, specs):
        """Compile project custom-regex rules (category/pattern/score). A broken
        pattern is skipped with a note so one bad rule never breaks the scan."""
        out = []
        for spec in (specs or []):
            try:
                pat = re.compile(spec["pattern"])
            except Exception as e:  # bad pattern or missing key
                self.notes.append(f"custom regex skipped ({spec!r}): {e}")
                continue
            out.append((spec.get("category", "custom"), pat,
                        float(spec.get("score", 1.0))))
        return out

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

    def _custom_spans(self, text: str) -> list[PhiSpan]:
        spans: list[PhiSpan] = []
        for cat, pat, score in self._custom:
            for m in pat.finditer(text):
                if m.group(0):
                    spans.append(PhiSpan(m.start(), m.end(), cat,
                                         m.group(0), "custom_regex", score))
        return spans

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
        if self.detect_dates:
            spans += find_dates(text, include_bare=self.dates_include_bare)
        spans += self._custom_spans(text)
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


# --------------------------------------------------------------------------- #
# Learn a regex from example identifiers (opt-in site-specific formats)        #
# --------------------------------------------------------------------------- #

def _char_kind(ch: str) -> str:
    if ch.isdigit():
        return "d"
    if ch.isascii() and ch.isalpha():
        return "a"
    return "sep"


def _tokenize_sample(sample: str) -> list:
    """Split a sample into structural tokens: ('d', run_len) for a digit run,
    ('a', run_len) for an ASCII-letter run, ('sep', char) for anything else
    (kept per-character so mixed separators like '-' and '/' stay distinct)."""
    tokens: list = []
    i, n = 0, len(sample)
    while i < n:
        kind = _char_kind(sample[i])
        if kind == "sep":
            tokens.append(("sep", sample[i]))
            i += 1
        else:
            j = i
            while j < n and _char_kind(sample[j]) == kind:
                j += 1
            tokens.append((kind, j - i))
            i = j
    return tokens


def _skeleton(tokens: list) -> tuple:
    """Structural key that ignores run LENGTHS (so '1234567' and '12345678' share
    a skeleton) but keeps separator literals (so '-' vs '/' do not)."""
    return tuple((t[0], t[1]) if t[0] == "sep" else (t[0],) for t in tokens)


def derive_pattern(samples, anchor: bool = True) -> str:
    """Infer a regex from one or more SAMPLE identifiers by structure.

    Each maximal digit run becomes ``\\d{n}``, each ASCII-letter run
    ``[A-Za-z]{n}`` (case-insensitive by class), and every other character its
    escaped literal. Given several samples of the SAME shape, run lengths widen
    to ``{min,max}``. Word boundaries anchor the ends so the format is matched as
    a whole token, not inside a longer digit/letter run.

    Raises ``ValueError`` if there is no sample, or the samples do not share one
    structural skeleton (teach those as separate rules instead of one fuzzy one).
    """
    cleaned = [s.strip() for s in (samples or []) if s and s.strip()]
    if not cleaned:
        raise ValueError("derive_pattern needs at least one non-empty sample")
    toks = [_tokenize_sample(s) for s in cleaned]
    skel0 = _skeleton(toks[0])
    for t in toks[1:]:
        if _skeleton(t) != skel0:
            raise ValueError(
                "samples do not share one structural format; give samples of the "
                "same shape, or add a separate rule for each format")
    parts: list[str] = []
    for pos, base in enumerate(toks[0]):
        if base[0] == "sep":
            parts.append(re.escape(base[1]))
            continue
        lengths = [toks[k][pos][1] for k in range(len(toks))]
        mn, mx = min(lengths), max(lengths)
        cls = r"\d" if base[0] == "d" else "[A-Za-z]"
        if mn == mx == 1:
            parts.append(cls)
        elif mn == mx:
            parts.append(f"{cls}{{{mn}}}")
        else:
            parts.append(f"{cls}{{{mn},{mx}}}")
    body = "".join(parts)
    if anchor:
        left = r"\b" if toks[0][0][0] in ("d", "a") else ""
        right = r"\b" if toks[0][-1][0] in ("d", "a") else ""
        body = f"{left}{body}{right}"
    return body


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
