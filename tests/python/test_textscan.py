"""Phase 2 - text PHI detection (deterministic layers).

These cover the dependency-light detectors that always work on the air-gapped
box: SG NRIC/FIN (checksum), phone, email, gazetteer, and the header-token
scrub, unified through ``TextScanner``. The optional Presidio / transformer-NER
layers are covered by separate, capability-gated tests.
"""

import pytest

from deid_engine import textscan as ts


# --- Singapore NRIC / FIN checksum -----------------------------------------

def test_nric_checksum_is_computed_canonically():
    # S1234567 -> weighted sum 106, 106 % 11 = 7, "JZIHGFEDCBA"[7] = "D".
    assert ts.nric_check_letter("S", "1234567") == "D"


def test_finds_checksum_valid_nric_with_high_score():
    spans = ts.find_nric_fin("Patient NRIC S1234567D admitted today.")
    assert len(spans) == 1
    s = spans[0]
    assert s.text == "S1234567D"
    assert s.category == "nric_fin"
    assert s.score >= 0.99  # checksum verified


def test_finds_checksum_valid_fin_g_prefix():
    # G: foreigner table "XWUTRQPNMLK", +4 offset like T.
    letter = ts.nric_check_letter("G", "1234567")
    spans = ts.find_nric_fin(f"FIN G1234567{letter}")
    assert len(spans) == 1
    assert spans[0].score >= 0.99


def test_wrong_checksum_nric_is_pattern_tier_not_dropped():
    # De-id favours recall: a right-shaped NRIC with a bad check digit is still
    # flagged, but at a lower score so the reviewer can tell them apart.
    spans = ts.find_nric_fin("ID S1234567A here")  # correct letter is D
    assert len(spans) == 1
    assert 0 < spans[0].score < 0.99


def test_m_series_fin_matched_by_pattern():
    spans = ts.find_nric_fin("New FIN M1234567K on file")
    assert len(spans) == 1
    assert spans[0].category == "nric_fin"


def test_random_alnum_is_not_an_nric():
    assert ts.find_nric_fin("Series AB12 protocol X7") == []


# --- Email ------------------------------------------------------------------

def test_finds_email():
    spans = ts.find_email("contact nurul.aisyah@hospital.sg for results")
    assert len(spans) == 1
    assert spans[0].text == "nurul.aisyah@hospital.sg"
    assert spans[0].category == "email"


# --- Singapore phone --------------------------------------------------------

def test_finds_sg_mobile_with_country_code():
    spans = ts.find_phone("call +65 9123 4567 now")
    assert len(spans) == 1
    assert spans[0].category == "phone"
    assert "9123" in spans[0].text


def test_finds_bare_sg_mobile():
    spans = ts.find_phone("mobile 91234567")
    assert len(spans) == 1
    assert spans[0].text.strip() == "91234567"


def test_does_not_flag_seven_digit_number_as_phone():
    assert ts.find_phone("value 1234567 units") == []


def test_does_not_flag_digit_run_inside_alnum_hash_as_phone():
    # A pseudonym like an 8-digit run glued to hex letters is NOT a phone
    # number; the QA residual scan must not false-flag de-identified output.
    assert ts.find_phone("87591237cef8c902") == []
    assert ts.find_phone("id 87591237cef8c902 end") == []
    assert ts.find_phone("AB87591237") == []


def test_still_flags_real_phone_at_token_boundaries():
    assert ts.find_phone("mobile 91234567")[0].text.strip() == "91234567"
    assert ts.find_phone("call +65 9123 4567 now")[0].category == "phone"
    assert ts.find_phone("(62345678)")[0].text.strip() == "62345678"


# --- Gazetteer --------------------------------------------------------------

def test_gazetteer_matches_multiracial_names_case_insensitively():
    gaz = ts.Gazetteer(["Nurul Aisyah", "Tan Wei Ming", "Muthusamy"])
    spans = gaz.find("Report for tan wei ming and Muthusamy s/o Raju")
    found = {s.text.lower() for s in spans}
    assert "tan wei ming" in found
    assert "muthusamy" in found
    assert all(s.category == "name" for s in spans)


def test_gazetteer_respects_word_boundaries():
    gaz = ts.Gazetteer(["Tan"])
    # "Tanjong" must not match the standalone name "Tan"
    assert gaz.find("Scan at Tanjong Pagar") == []


# --- TextScanner: merge + redact -------------------------------------------

def test_scanner_merges_overlapping_spans():
    # header-token "Aisyah" and gazetteer "Nurul Aisyah" overlap -> one span.
    scanner = ts.TextScanner(known_values=["Aisyah"],
                             gazetteer=ts.Gazetteer(["Nurul Aisyah"]),
                             use_presidio=False, use_ner=False)
    spans = scanner.scan("Echo for Nurul Aisyah today")
    name_spans = [s for s in spans if s.category == "name"]
    assert len(name_spans) == 1
    assert name_spans[0].text == "Nurul Aisyah"


def test_scanner_redacts_all_categories():
    scanner = ts.TextScanner(known_values=["Rahman"],
                             gazetteer=ts.Gazetteer(["Rahman"]),
                             use_presidio=False, use_ner=False)
    text = "Rahman NRIC S1234567D email a@b.sg phone 91234567"
    redacted, spans = scanner.redact(text)
    assert "Rahman" not in redacted
    assert "S1234567D" not in redacted
    assert "a@b.sg" not in redacted
    assert "91234567" not in redacted
    cats = {s.category for s in spans}
    assert {"name", "nric_fin", "email", "phone"} <= cats


def test_redact_leaves_non_phi_intact():
    scanner = ts.TextScanner(known_values=[], gazetteer=None,
                             use_presidio=False, use_ner=False)
    redacted, spans = scanner.redact("Echo cardiac ultrasound study")
    assert redacted == "Echo cardiac ultrasound study"
    assert spans == []


def test_optional_layers_degrade_without_hard_failing():
    # On an air-gapped box with no spaCy model / no NER weights, asking for the
    # optional layers must NOT crash: the deterministic layers still work and the
    # scanner records why the heavy layers are off.
    scanner = ts.TextScanner(known_values=[], gazetteer=None,
                             use_presidio=True, use_ner=True)
    spans = scanner.scan("NRIC S1234567D email a@b.sg")
    cats = {s.category for s in spans}
    assert "nric_fin" in cats and "email" in cats  # layer 3 unaffected
    # at least one note explaining a disabled layer (model/package missing)
    assert scanner.notes


def test_header_token_layer_flags_known_values_as_name():
    scanner = ts.TextScanner(known_values=["Muthusamy", "MRN0099887"],
                             gazetteer=None, use_presidio=False, use_ner=False)
    spans = scanner.scan("Study by Muthusamy id MRN0099887")
    texts = {s.text for s in spans}
    assert "Muthusamy" in texts
    assert "MRN0099887" in texts
