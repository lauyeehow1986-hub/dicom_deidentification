"""Phase 7 - synthetic acceptance corpus with PLANTED PHI.

`corpus.build_corpus` emits *real* DICOM/NIfTI fixtures carrying known
identifiers in known locations (structured tags, free-text, private tags,
nested sequences, and burned into the pixels) across every encoding
(single-frame, multiframe/cine, RGB, JPEG2000, NIfTI). The acceptance test
then de-identifies the corpus and asserts none of these values survive - so
the fixtures are only useful if the PHI is genuinely present *before* de-id,
which is what this test guards.
"""
import numpy as np
import pydicom
import nibabel as nib

from deid_engine import corpus


def _read_dcm(path):
    return pydicom.dcmread(path)


def test_build_corpus_writes_manifest_and_all_encodings(tmp_path):
    out = tmp_path / "synthetic"
    man = corpus.build_corpus(str(out))

    # planted manifest is written for the QA scan to check against
    assert (out / "planted_manifest.json").is_file()
    assert man["planted"] == corpus.PLANTED

    encs = {f["encoding"] for f in man["fixtures"]}
    assert {"single_frame", "multiframe_cine", "rgb", "jpeg2000", "nifti"} <= encs

    # every declared fixture actually exists on disk
    for f in man["fixtures"]:
        assert (out / f["rel"]).is_file(), f["rel"]


def test_dicom_fixtures_are_valid_and_readback(tmp_path):
    out = tmp_path / "synthetic"
    man = corpus.build_corpus(str(out))
    for f in man["fixtures"]:
        if f.get("sidecar") or f["rel"].endswith(".json"):
            # a BIDS/dcm2niix JSON sidecar - valid JSON object, not a DICOM file
            import json as _json
            with open(out / f["rel"], encoding="utf-8") as fh:
                assert isinstance(_json.load(fh), dict)
            continue
        if f["encoding"] in ("nifti", "nifti2", "nifti_pair",
                             "nifti_head_mr", "nifti_burned_in"):
            img = nib.load(str(out / f["rel"]))
            assert img.get_fdata().size > 0
            continue
        if f.get("encapsulated_pdf"):
            # an embedded-PDF instance carries no PixelData; it must still be a
            # valid Encapsulated PDF Storage object that reads back with its doc.
            ds = _read_dcm(str(out / f["rel"]))
            assert str(ds.SOPClassUID) == "1.2.840.10008.5.1.4.1.1.104.1"
            assert "EncapsulatedDocument" in ds
            continue
        ds = _read_dcm(str(out / f["rel"]))
        # pixels decode (incl. the compressed one) back to a real array
        assert ds.pixel_array.size > 0


def test_planted_identifiers_present_before_deid(tmp_path):
    out = tmp_path / "synthetic"
    man = corpus.build_corpus(str(out))
    by_enc = {f["encoding"]: f for f in man["fixtures"]}

    sf = _read_dcm(str(out / by_enc["single_frame"]["rel"]))
    # structured name tag carries a planted name
    assert str(sf.PatientName).replace("^", " ").strip() in [
        n for n in corpus.PLANTED["names"]
    ] or any(n.split()[0] in str(sf.PatientName) for n in corpus.PLANTED["names"])
    # a national id lands in PatientID
    assert sf.PatientID in (corpus.PLANTED["nric_fin"] + corpus.PLANTED["mrn"])
    # free-text tag carries an email or phone
    free = " ".join(
        str(sf.get(t, "")) for t in ("ImageComments", "StudyDescription",
                                     "SeriesDescription", "PatientComments")
    )
    assert any(e in free for e in corpus.PLANTED["email"]) or \
        any(p in free for p in corpus.PLANTED["phone"])


def test_private_tag_and_nested_sequence_carry_phi(tmp_path):
    out = tmp_path / "synthetic"
    man = corpus.build_corpus(str(out))
    sf = _read_dcm(str(out / [f for f in man["fixtures"]
                             if f["encoding"] == "single_frame"][0]["rel"]))

    # a private tag block exists and holds a planted value
    blob = sf.filename and ""  # noqa
    private_values = []
    for elem in sf:
        if elem.tag.is_private and elem.VR in ("LO", "SH", "UT", "ST", "LT", "PN", "UN"):
            private_values.append(str(elem.value))
    assert any(any(pv and (nm.split()[0] in pv or pv in nm) for nm in corpus.PLANTED["names"])
               or any(x in pv for x in corpus.PLANTED["nric_fin"])
               for pv in private_values), "no planted value in any private tag"

    # a nested sequence carries a planted identifier one level deep
    seq_text = []
    for elem in sf:
        if elem.VR == "SQ":
            for item in elem.value:
                for sub in item:
                    seq_text.append(str(sub.value))
    joined = " ".join(seq_text)
    assert any(n.split()[0] in joined for n in corpus.PLANTED["names"]) or \
        any(x in joined for x in corpus.PLANTED["nric_fin"]), \
        "no planted value in any nested sequence"


def test_burned_in_pixels_have_nonbackground_text(tmp_path):
    out = tmp_path / "synthetic"
    man = corpus.build_corpus(str(out))
    # the RGB fixture is drawn on a black ground with light burned-in text
    rgb = _read_dcm(str(out / [f for f in man["fixtures"]
                             if f["encoding"] == "rgb"][0]["rel"]))
    arr = rgb.pixel_array
    assert arr.max() > arr.min(), "burned-in text region is flat"
    # the fixture manifest flags which encodings carry burned-in text
    assert any(f.get("burned_in") for f in man["fixtures"])


def test_corpus_has_head_mr_and_burned_in_nifti(tmp_path):
    man = corpus.build_corpus(str(tmp_path))
    encs = {f["encoding"] for f in man["fixtures"]}
    assert "nifti_head_mr" in encs        # head-inclusive MR volume (deface target)
    assert "nifti_burned_in" in encs      # burned-in-text NIfTI (OCR target)
    head = next(f for f in man["fixtures"] if f["encoding"] == "nifti_head_mr")
    img = nib.load(str(tmp_path / head["rel"]))
    assert min(img.shape) >= 16           # deep enough for the FOV gate
