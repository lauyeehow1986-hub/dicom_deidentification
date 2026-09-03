# Encapsulated-PDF de-identification

DICOM can embed a PDF report in `EncapsulatedDocument (0042,0011)` (SOP Class
Encapsulated PDF Storage `1.2.840.10008.5.1.4.1.1.104.1`). PHI can hide in the
PDF's text layer *and* in its images.

## Modes (profile `encapsulated_pdf.mode`)

- `remove` (default) — strip the whole embedded PDF. Safe, needs no OCR.
- `rasterize_redact` — render each page (`pypdfium2`), OCR-redact PHI regions with
  the shared layered scanner, and rebuild a **flattened image-only PDF**. Flattening
  removes the text layer entirely, so hidden/selectable text PHI cannot survive even
  if OCR missed it visually. Needs the bundled Tesseract; **degrades to `remove`**
  when Tesseract is absent (never keeps an un-scanned PDF).

`dpi` (default 150) controls render resolution; raise it if OCR misses small text.

## Trade-off

A redacted PDF becomes a picture: no selectable/searchable text, larger file. This
is the safety/utility trade for an airtight guarantee. `remove` stays the default;
opt in per project/profile.

## Dependency

`pypdfium2` (Apache-2.0 / BSD-3, self-contained wheel — no system binary, no admin,
offline once staged). Bundled by `build_venv.ps1` via pyproject.toml.
