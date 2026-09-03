# Text PHI detection (Phase 2)

Free-text PHI hides in DICOM everywhere the fixed tag rules don't reach: unexpected
text elements, private/vendor text tags, and free text nested inside sequences
(Structured Reports carry it in `ContentSequence > TextValue`). Phase 2 adds a
**layered scanner** that runs over *every* text-VR element the action map doesn't
already cover, recursing into sequences, and scrubs what it finds.

## The layers (strongest-configuration ensemble)

Ordered so the dependency-light, high-precision layers always run on the
air-gapped box, and the heavier statistical layers add recall when their
models are present:

| # | Layer | What it catches | Dependency |
|---|-------|-----------------|------------|
| 1 | **Header-token scrub** | The *this-study* real name/ID tokens (from PatientName, PatientID, physician names, AccessionNumber, OtherPatientIDs) hunted wherever they reappear | none |
| 2 | **Gazetteer** | Names from the user's SG multiracial + foreigner name list (word-boundary, case-insensitive) | a name-list file |
| 3 | **SG recognisers** | NRIC/FIN (**checksum-validated**), SG phone (`+65`/8-digit 3·6·8·9), email | none (regex) |
| 4 | **Presidio** | General PII (PERSON, LOCATION, DATE_TIME, …) + the SG recognisers as Presidio entities | `presidio-analyzer` + a spaCy model |
| 5 | **Transformer NER** | Multilingual person/location (XLM-R class), for names the gazetteer misses | `torch`/`transformers` + a **local** model dir |

Layers 1–3 are pure Python and always available. Layers 4–5 are optional: if the
package or model is missing, the scanner **degrades to layers 1–3** and records why
in `engine notes` — it never hard-fails or hangs. NER is loaded **only from a
bundled local directory** (`text_detection.ner_model`); it never reaches the
network, so a missing model fails fast instead of stalling offline.

Detected spans from all layers are merged (overlaps collapsed to the widest/most
confident) and redacted in one pass.

## NRIC / FIN checksum

`nric_check_letter(prefix, digits)` implements the canonical algorithm — weights
`[2,7,6,5,4,3,2]`, `+4` offset for `T`/`G`, tables `JZIHGFEDCBA` (S/T citizens) and
`XWUTRQPNMLK` (F/G foreigners). A checksum-valid number scores 1.0; a right-shaped
number with a bad check digit (or an M-series FIN, whose scheme is less stable) is
still flagged at a lower score, because in de-identification **recall beats
precision** on identifiers. In Presidio the check digit *validates* the match
(invalid → dropped); the always-on `find_nric_fin` layer remains the recall net.

## Configuration (`text_detection:` in the profile)

```yaml
text_detection:
  enabled: true
  header_token_scrub: true
  gazetteer_file: ""     # path to the SG name list (one name per line)
  use_presidio: true     # needs a spaCy model installed in the venv
  use_ner: true          # multilingual XLM-R
  ner_model: ""          # path to the bundled local NER model DIRECTORY; empty -> off
```

The scanner is built **once per study** and reused across every file, so the
optional models load a single time even for a large batch.

## Encapsulated documents

Embedded PDF/CDA (`EncapsulatedDocument`, `0042,0011`) isn't scrubbed by this
in-place text scanner, so the default profile **removes** it outright (catalog id
`encapsulated_documents`). `DocumentTitle` is a text VR and is scrubbed by the
scanner. An opt-in `encapsulated_pdf.mode: rasterize_redact` profile setting
instead renders, OCR-redacts and flattens the embedded PDF — see
[docs/encapsulated-pdf.md](encapsulated-pdf.md).

## Air-gap runtime models

To light up layers 4–5 on the target box (see [airgap-install.md](airgap-install.md)):
- **Presidio:** install a spaCy model into the venv (e.g. `en_core_web_lg`).
- **NER:** drop the model weights into `inst/models/<name>/` and set
  `text_detection.ner_model` to that directory.
