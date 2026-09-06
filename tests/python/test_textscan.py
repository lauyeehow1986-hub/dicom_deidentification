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


# --- SG short-forms + full Chinese names: recall & the deterministic gap -----
# These pin the MODEL-INDEPENDENT guarantees. When the name is the study's own
# registered PatientName, the header scrub owns it deterministically (score 1.0)
# in any case. When it is an UNLISTED bystander name, the deterministic layers
# alone miss it -- it needs a gazetteer entry (below) or the probabilistic
# Presidio/NER layers (covered by the capability-gated tests, not here).

def test_header_scrub_catches_shortform_in_any_case():
    # A short-form like "Yeo KK" registered in the header is scrubbed wherever it
    # appears, regardless of the casing typed into free text.
    scanner = ts.TextScanner(known_values=["Yeo KK"], gazetteer=None,
                             use_presidio=False, use_ner=False)
    spans = [s for s in scanner.scan("Report for yeo kk. Echo normal.")
             if s.category == "name"]
    assert len(spans) == 1
    assert spans[0].source == "header"
    assert spans[0].score == 1.0


def test_header_scrub_catches_full_chinese_name_lowercase():
    scanner = ts.TextScanner(known_values=["Tan Chorh Chuan"], gazetteer=None,
                             use_presidio=False, use_ner=False)
    spans = [s for s in scanner.scan("seen: tan chorh chuan today")
             if s.category == "name"]
    assert len(spans) == 1
    assert spans[0].source == "header" and spans[0].score == 1.0


def test_gazetteer_catches_shortform_when_listed():
    # The gazetteer is how you make an unregistered short-form a deterministic
    # catch: list "Yeo KK" and it fires case-insensitively.
    gaz = ts.Gazetteer(["Yeo KK"])
    spans = gaz.find("aka yeo kk")
    assert len(spans) == 1
    assert spans[0].text.lower() == "yeo kk"
    assert spans[0].source == "gazetteer"


def test_gazetteer_full_name_does_not_catch_bare_surname():
    # Whole-phrase matching is deliberate: a full-name entry must NOT redact a
    # lone common surname (listing every "Tan" would over-redact). The bare
    # surname of the actual patient is covered by the per-study header scrub.
    gaz = ts.Gazetteer(["Tan Chorh Chuan"])
    assert gaz.find("Mr Tan came in for an echo") == []


def test_deterministic_layers_alone_miss_unlisted_bystander_name():
    # An unlisted bystander name (e.g. a referring doctor) in free text produces
    # NO name span from the deterministic layers -- documenting exactly why the
    # probabilistic layers exist and why a real name list should be loaded.
    scanner = ts.TextScanner(known_values=[], gazetteer=None,
                             use_presidio=False, use_ner=False)
    spans = [s for s in scanner.scan("Referred by Dr Tan Chorh Chuan for MRI.")
             if s.category == "name"]
    assert spans == []


# --- Dates in free text (deterministic) ------------------------------------
# Structured DICOM date VRs are format-fixed and handled by the action map;
# these pin the FREE-TEXT date recall in the human formats a reviewer worries
# about. Separated + month-name forms are on by default; bare 8/14-digit runs
# are opt-in because they collide with IDs.

@pytest.mark.parametrize("s", [
    "DOB 15/01/2024 noted",
    "seen 15-01-2024",
    "on 15.01.2024",
    "study 2024-01-15",
    "acquired 2024-01-15 13:45:00",
    "born 1 Jan 2024",
    "born 01 January 2024",
    "dated 1st Jan 2024",
    "on Jan 1, 2024 today",
    "on January 1 2024 here",
])
def test_finds_separated_and_month_name_dates_by_default(s):
    spans = [x for x in ts.find_dates(s) if x.category == "date"]
    assert len(spans) == 1, s
    assert spans[0].source == "date"


def test_iso_datetime_span_covers_the_time_part():
    spans = ts.find_dates("acquired 2024-01-15 13:45:00 done")
    assert len(spans) == 1
    assert spans[0].text == "2024-01-15 13:45:00"


def test_bare_eight_digit_date_is_off_by_default_on_by_toggle():
    # A bare yyyymmdd is ambiguous with an ID, so it is NOT flagged by default.
    assert ts.find_dates("scan 20240115 end") == []
    spans = ts.find_dates("scan 20240115 end", include_bare=True)
    assert len(spans) == 1 and spans[0].text == "20240115"


def test_bare_ddmmyyyy_and_datetime_when_enabled():
    assert [s.text for s in ts.find_dates("d 15012024 x", include_bare=True)] == ["15012024"]
    # yyyymmddhhmmss compact stamp
    assert [s.text for s in ts.find_dates("t 20240115134500 x", include_bare=True)] == \
        ["20240115134500"]


def test_bare_toggle_ignores_implausible_and_id_like_runs():
    # Not a calendar date (month 99) -> not flagged even with bare enabled.
    assert ts.find_dates("id 99999999 x", include_bare=True) == []
    # An 8-digit run glued to hex is an ID token, not a date (boundary guard).
    assert ts.find_dates("87591237cef8", include_bare=True) == []


def test_does_not_mistake_ratios_ip_or_versions_for_dates():
    assert ts.find_dates("ratio 1/2/3 here") == []          # 1-digit "year"
    assert ts.find_dates("host 192.168.1.1 up") == []       # octets, not d/m/y
    assert ts.find_dates("v1.2.3 released") == []


def test_scanner_redacts_a_free_text_date_by_default():
    scanner = ts.TextScanner(known_values=[], gazetteer=None,
                             use_presidio=False, use_ner=False)
    redacted, spans = scanner.redact("Echo on 15/01/2024 normal")
    assert "15/01/2024" not in redacted
    assert "Echo on" in redacted and "normal" in redacted
    assert any(s.category == "date" for s in spans)


def test_scanner_bare_dates_follow_the_toggle():
    off = ts.TextScanner(known_values=[], gazetteer=None,
                         use_presidio=False, use_ner=False)
    assert "20240115" in off.redact("stamp 20240115 x")[0]
    on = ts.TextScanner(known_values=[], gazetteer=None, use_presidio=False,
                        use_ner=False, dates_include_bare=True)
    assert "20240115" not in on.redact("stamp 20240115 x")[0]
