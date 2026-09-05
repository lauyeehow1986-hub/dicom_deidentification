"""Phase 6 - residual-PHI scan on OUTPUTS.

`scan_residual` re-runs the detectors on an already-de-identified file and
reports any PHI that survived, per category, with a pass/fail verdict. It must
NOT re-flag the pseudonyms it finds in the (already-anonymised) header, so it
runs the gazetteer + SG recognisers + optional NER layers WITHOUT seeding the
header-token layer from the output's own header.
"""
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core


def _write(path, **over):
    ds = Dataset()
    ds.PatientName = over.get("name", "ANON^A1B2C3")
    ds.PatientID = over.get("pid", "PSEUDO0001")
    ds.Modality = "US"
    ds.PatientIdentityRemoved = over.get("identity_removed", "YES")
    # The pipeline stamps this provenance field on every output; the residual
    # scan must not flag our own de-id method string as PHI.
    ds.DeidentificationMethod = "dicomdeid: PS3.15 basic profile + header-scrub"
    if "derivation" in over:
        ds.DerivationDescription = over["derivation"]
    if "nested" in over:
        item = Dataset()
        item.TextValue = over["nested"]
        ds.ContentSequence = Sequence([item])
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.6.1"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    ds.file_meta = fm
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)
    return str(path)


def test_clean_output_passes(tmp_path):
    p = _write(tmp_path / "clean.dcm",
               derivation="Cardiac ultrasound derived volume rendering")
    res = core.scan_residual(p)
    assert res["passed"] is True
    assert res["counts"]["total"] == 0
    assert res["identity_removed"] is True


def test_leaked_nric_in_text_is_flagged(tmp_path):
    p = _write(tmp_path / "leak.dcm",
               derivation="Follow-up, NRIC S1234567D, please review")
    res = core.scan_residual(p)
    assert res["passed"] is False
    assert res["by_category"].get("nric_fin", 0) >= 1
    assert res["counts"]["metadata"] >= 1


def test_leaked_name_in_sequence_is_flagged(tmp_path):
    # "Muthusamy" is in the shipped default profile's sample gazetteer.
    p = _write(tmp_path / "seq.dcm",
               nested="Reported by Dr Muthusamy")
    res = core.scan_residual(p)
    assert res["passed"] is False
    assert res["by_category"].get("name", 0) >= 1


def test_pseudonymised_header_is_not_flagged(tmp_path):
    # The anonymised PatientName/ID must NOT count as residual PHI.
    p = _write(tmp_path / "pseudo.dcm", name="ANON^ZZ99", pid="PSEUDO7777")
    res = core.scan_residual(p)
    assert res["passed"] is True
    assert res["counts"]["total"] == 0


def test_finding_preview_is_masked(tmp_path):
    # The QA report itself must not re-disclose the raw identifier verbatim.
    p = _write(tmp_path / "mask.dcm",
               derivation="ring me at 91234567 tomorrow")
    res = core.scan_residual(p)
    previews = " ".join(f["preview"] for f in res["findings"])
    assert "91234567" not in previews
    assert any(f["category"] == "phone" for f in res["findings"])


def test_identity_not_removed_is_reported(tmp_path):
    p = _write(tmp_path / "noremove.dcm", identity_removed="NO",
               derivation="clean text")
    res = core.scan_residual(p)
    assert res["identity_removed"] is False


def test_confident_flag_separates_deterministic_from_probabilistic():
    # A deterministic layer (SG/gazetteer/header/regex) is trusted at min_score;
    # a probabilistic layer (presidio/ner) must clear the higher ner_min_score
    # bar before it counts toward the fail verdict. Every finding stays listed.
    det = {"source": "sg", "score": 0.6}
    ner_low = {"source": "ner", "score": 0.6}
    ner_high = {"source": "ner", "score": 0.9}
    presidio_low = {"source": "presidio", "score": 0.7}
    assert core._finding_confident(det, 0.5, 0.85) is True
    assert core._finding_confident(ner_low, 0.5, 0.85) is False
    assert core._finding_confident(ner_high, 0.5, 0.85) is True
    assert core._finding_confident(presidio_low, 0.5, 0.85) is False


def test_default_ner_bar_rejects_observed_descriptor_noise():
    # Real studies showed Presidio emitting a flat 0.85 for PERSON and NER
    # ~0.86-0.87 on technical descriptors (RescaleType='HU', 'Knee (R)'), while
    # genuine names score >=0.95. The DEFAULT ner_min_score must sit above the
    # observed noise so those descriptors do not fail every study, yet admit a
    # real high-confidence name. This pins that default.
    import inspect
    default = inspect.signature(core.scan_residual).parameters["ner_min_score"].default
    assert default >= 0.90
    presidio_noise = {"source": "presidio", "category": "name", "score": 0.85}
    ner_noise = {"source": "ner", "category": "name", "score": 0.87}
    real_name = {"source": "ner", "category": "name", "score": 0.95}
    assert core._finding_confident(presidio_noise, 0.5, default) is False
    assert core._finding_confident(ner_noise, 0.5, default) is False
    assert core._finding_confident(real_name, 0.5, default) is True


def test_probabilistic_noise_does_not_fail_verdict(tmp_path):
    # A file whose ONLY residual hits come from the ML layers below the
    # ner_min_score bar must PASS (they are still listed for review, not hidden).
    findings = [
        {"source": "ner", "category": "name", "score": 0.55, "location": "metadata"},
        {"source": "presidio", "category": "name", "score": 0.60, "location": "metadata"},
    ]
    passed, n_conf = core._residual_verdict(findings, min_score=0.5, ner_min_score=0.85)
    assert passed is True
    assert n_conf == 0
    # But a real deterministic hit in the same set flips it.
    findings.append({"source": "sg", "category": "nric_fin", "score": 1.0,
                     "location": "metadata"})
    passed, n_conf = core._residual_verdict(findings, min_score=0.5, ner_min_score=0.85)
    assert passed is False
    assert n_conf == 1


def test_scan_result_tags_confident_and_counts(tmp_path):
    # scan_residual must stamp each finding with a `confident` flag and expose a
    # confident count, so the reviewer sees "confirmed vs low-confidence".
    p = _write(tmp_path / "conf.dcm",
               derivation="NRIC S1234567D on file")
    res = core.scan_residual(p)
    assert all("confident" in f for f in res["findings"])
    assert res["counts"].get("confident", 0) >= 1
    assert res["passed"] is False


def test_case_number_format_is_caught_as_confident(tmp_path):
    # A surviving NNN-NN-NNNN case/accession number (SSN-shaped) must be caught
    # deterministically by the default profile's custom_regex, as a CONFIDENT
    # `case_number` hit -- not left to the (now demoted) ML PERSON layer, which
    # in this engine only guesses names and never fires on a bare number.
    p = _write(tmp_path / "case.dcm",
               derivation="Ref 324-58-2995/4 pending review")
    res = core.scan_residual(p)
    assert res["passed"] is False
    assert res["by_category"].get("case_number", 0) >= 1
    hit = next(f for f in res["findings"] if f["category"] == "case_number")
    assert hit["source"] == "custom_regex"
    assert hit["confident"] is True
    assert "324-58-2995" not in hit["preview"]   # still masked in the report


def test_case_number_regex_does_not_match_plain_digit_runs(tmp_path):
    # The rule is specific to the 3-2-4 dashed grouping; a longer plain number
    # (e.g. a device serial) must NOT trip it.
    p = _write(tmp_path / "serial.dcm",
               derivation="Device serial 1234567890 calibrated")
    res = core.scan_residual(p)
    assert res["by_category"].get("case_number", 0) == 0


def test_pixel_scan_degrades_without_ocr(tmp_path):
    # Add pixels; with no Tesseract the pixel scan must not crash and should note.
    ds = pydicom.dcmread(_write(tmp_path / "px.dcm", derivation="clean"))
    arr = np.zeros((8, 8), dtype=np.uint8)
    ds.Rows, ds.Columns = 8, 8
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = arr.tobytes()
    ds["PixelData"].VR = "OB"
    pydicom.dcmwrite(str(tmp_path / "px.dcm"), ds, enforce_file_format=True)
    res = core.scan_residual(str(tmp_path / "px.dcm"), scan_pixels=True)
    # No exception; pixel scan reported (either a note when OCR is absent, or
    # zero boxes when present). Either way the call succeeds.
    assert "pixels" in res["counts"]
