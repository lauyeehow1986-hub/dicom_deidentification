"""Presidio custom recognisers for Singapore identifiers.

These are pattern recognisers, so they contribute to Presidio's ensemble without
needing the spaCy NLP engine. NRIC/FIN results are checksum-validated to set the
confidence score; phone/postal use SG-specific shapes. Registered into an
``AnalyzerEngine``'s registry by ``register_sg_recognizers``.
"""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer

from .textscan import nric_check_letter


class NricFinRecognizer(PatternRecognizer):
    """Singapore NRIC / FIN, with check-digit validation."""

    PATTERNS = [Pattern("nric_fin", r"\b[STFGM]\d{7}[A-Z]\b", 0.5)]

    def __init__(self):
        super().__init__(supported_entity="SG_NRIC_FIN", patterns=self.PATTERNS,
                         context=["nric", "fin", "identity", "ic", "id"])

    def validate_result(self, pattern_text: str):
        prefix, digits, check = pattern_text[0], pattern_text[1:8], pattern_text[8]
        expected = nric_check_letter(prefix, digits)
        if expected is None:
            return None            # M-series: keep the pattern score, can't verify
        return check == expected   # True -> full score; False -> invalidated/low


class SgTempIcRecognizer(PatternRecognizer):
    """Singapore hospital temporary IC: X/Y prefix, 7 or 10 digits, a letter."""

    PATTERNS = [Pattern("sg_temp_ic", r"\b[XY](?:\d{7}|\d{10})[A-Z]\b", 0.6)]

    def __init__(self):
        super().__init__(supported_entity="SG_TEMP_IC", patterns=self.PATTERNS,
                         context=["ic", "temporary", "temp", "identity", "patient"])


class SgCaseNumberRecognizer(PatternRecognizer):
    """Singapore admission case number: ten digits followed by one letter."""

    PATTERNS = [Pattern("sg_case_no", r"\b\d{10}[A-Za-z]\b", 0.5)]

    def __init__(self):
        super().__init__(supported_entity="SG_CASE_NUMBER", patterns=self.PATTERNS,
                         context=["case", "admission", "visit", "encounter"])


class SgPhoneRecognizer(PatternRecognizer):
    """Singapore phone number (optional +65, 8-digit local starting 3/6/8/9)."""

    PATTERNS = [
        Pattern("sg_phone_cc", r"\+?65[\s-]?[3689]\d{3}[\s-]?\d{4}\b", 0.7),
        Pattern("sg_phone_local", r"\b[3689]\d{3}[\s-]?\d{4}\b", 0.4),
    ]

    def __init__(self):
        super().__init__(supported_entity="SG_PHONE", patterns=self.PATTERNS,
                         context=["phone", "tel", "mobile", "hp", "contact", "call"])


class SgPostalRecognizer(PatternRecognizer):
    """Singapore 6-digit postal code (context-gated to limit false positives)."""

    PATTERNS = [Pattern("sg_postal", r"\bSingapore\s+\d{6}\b", 0.6),
                Pattern("sg_postal_s", r"\bS\(\d{6}\)", 0.6)]

    def __init__(self):
        super().__init__(supported_entity="SG_POSTAL", patterns=self.PATTERNS,
                         context=["postal", "address", "singapore"])


def register_sg_recognizers(registry) -> None:
    """Add the SG recognisers to a Presidio ``RecognizerRegistry``."""
    for rec in (NricFinRecognizer(), SgTempIcRecognizer(), SgCaseNumberRecognizer(),
                SgPhoneRecognizer(), SgPostalRecognizer()):
        registry.add_recognizer(rec)
