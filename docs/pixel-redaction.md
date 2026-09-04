# Pixel / burned-in PHI (Phase 3)

PHI is often *burned into the pixels* — a patient-name banner on an echo frame, an
annotation on a secondary capture. Phase 3 adds decoding, review, and redaction of
image data, producing valid, viewable DICOM.

## Pipeline

```
decode frames (mono/RGB, single/multiframe, JPEG2000/JPEG-LS/RLE via pylibjpeg/gdcm)
  -> OCR each frame (optional; Tesseract) -> scan words with the Phase-2 text scanner
  -> propose redaction boxes; reviewer adds/edits manual boxes
  -> black out boxes (per-frame or across frames), strip audio/waveform
  -> write valid DICOM (decompress -> redact -> Explicit VR Little Endian)
```

Everything except the OCR step needs no external binary. OCR (`ocr_phi_boxes`)
degrades gracefully when the **Tesseract** binary is absent — it returns no boxes
and a note, exactly like the Presidio/NER layers — so manual-box redaction always
works. On the air-gapped box, drop a portable Tesseract binary on PATH to enable
auto-proposals.

## Why decompress on write

A compressed input (JPEG2000 lossy/lossless, JPEG-LS, RLE) is decoded, redacted,
and written back as **uncompressed** Explicit VR Little Endian. This guarantees a
valid, viewable object without needing a matching re-encoder for every transfer
syntax. The output is larger; re-encoding back to the original syntax is a possible
later optimisation. The change is reported (`decompressed: true`).

## The Pixels tab

`R/mod_pixels.R` drives the engine entries:

- `pixel_info(path)` — geometry + OCR-proposed boxes.
- `pixel_frame_png(path, frame, boxes)` — a base64 PNG of a frame with boxes drawn
  as red outlines, for review before applying.
- `pixel_redact(input, output, boxes)` — apply boxes + strip audio; write valid DICOM.

**Order matters:** the Pixels tab redacts pixels and strips audio only — it does
**not** de-identify metadata. Run the **Interactive** metadata de-id first, then load
its output into the Pixels tab for burned-in review. The QA phase re-scans outputs
for any residual PHI in metadata *and* pixels.

## Audio / waveform

`strip_waveforms` removes `WaveformSequence` (voice/ECG is biometric PHI). The
metadata pass also removes it via the catalog (`biometrics` -> X); Phase 3 fixed the
walker so an explicit X/Z on a sequence removes the whole sequence instead of
recursing into it.

## NIfTI burned-in text

Burned-in-text OCR now runs on NIfTI volumes too (previously header-only). Each
in-plane slice (along the volume's shortest axis) is OCR'd with the same SG-aware
scanner as DICOM pixels. The interactive flow proposes boxes for confirmation;
bulk/acceptance runs auto-redact. Degrades to a noted skip without Tesseract.

The residual-PHI QA scan is likewise NIfTI-aware: it re-checks NIfTI outputs'
header fields and (when Tesseract is present) OCR-scans their slices, so a
surviving burned-in identifier is caught by the same pass/fail report as DICOM.
