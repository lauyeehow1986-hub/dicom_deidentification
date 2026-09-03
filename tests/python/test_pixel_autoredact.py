"""Phase 7 - burned-in pixel redaction can run UNattended in the automated
pipeline, but only when the operator opts out of human confirmation.

Default profiles keep ``require_human_confirm: true`` (a reviewer confirms boxes
in the Pixels tab). A bulk/acceptance profile that sets it false lets
``deidentify_study`` auto-apply the OCR-proposed boxes so burned-in PHI does not
silently survive an unattended run. The gate below is the pure decision; the
end-to-end redaction is exercised by the live acceptance run.
"""
from deid_engine import core


def _profile(clean, auto, confirm):
    return {"options": {"clean_pixel_data": clean},
            "pixel": {"auto_detect": auto, "require_human_confirm": confirm}}


def test_autoredact_enabled_only_when_opted_out_of_confirmation():
    assert core._pixel_autoredact_enabled(_profile(True, True, False)) is True


def test_autoredact_disabled_when_human_confirm_required():
    assert core._pixel_autoredact_enabled(_profile(True, True, True)) is False


def test_autoredact_disabled_when_pixel_cleaning_off():
    assert core._pixel_autoredact_enabled(_profile(False, True, False)) is False


def test_autoredact_disabled_when_auto_detect_off():
    assert core._pixel_autoredact_enabled(_profile(True, False, False)) is False


def test_autoredact_defaults_are_safe():
    # empty profile -> no auto redaction (human-confirm is the safe default)
    assert core._pixel_autoredact_enabled({}) is False
