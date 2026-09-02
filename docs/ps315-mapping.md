# DICOM PS3.15 Annex E mapping

How this app relates to the **DICOM PS3.15 Annex E — Basic Application Level Confidentiality
Profile** and its options. The machine-readable rules live in
[`inst/profiles/identifier_catalog.yml`](../inst/profiles/identifier_catalog.yml) and
[`inst/profiles/default_profile.yml`](../inst/profiles/default_profile.yml).

## Action codes
| Code | Meaning |
|------|---------|
| D | replace with a non-zero dummy / pseudonym of the same VR |
| Z | replace with a zero-length value |
| X | remove the element |
| K | keep as-is |
| C | clean — retain the element but scrub embedded PHI from its text |
| U | replace UID with an internally-consistent remapped UID |
| H | *(app extension)* deterministic salted SHA-256 pseudonym |
| S | *(app extension)* shift date/time by a per-patient interval-preserving offset |

## Profile options we implement
| PS3.15 option | Profile flag | Behaviour |
|---------------|--------------|-----------|
| Basic profile | (always) | Remove/replace the standard identifying attributes. |
| Clean Descriptors | `clean_descriptors` | Run free-text PHI detectors over descriptions/comments. |
| Clean Pixel Data | `clean_pixel_data` | OCR + redact burned-in text. |
| Retain Longitudinal Temporal (Modified Dates) | `retain_longitudinal_temporal` | Shift dates (S) preserving intervals. |
| Retain Patient Characteristics | `retain_patient_characteristics` | Keep age/sex/size (not direct IDs). |
| Retain Device Identity | `retain_device_identity` | Off by default (remove per-unit serials). |
| Retain Safe Private | `retain_safe_private` | Off by default (strip unknown private tags). |
| Retain UIDs | `retain_uids` | Off by default (remap UIDs, U). |

## Beyond PS3.15 (why the app exists)
PS3.15 defines *tag-level* actions. The hard, real-world PHI this app additionally targets:
- **Nested sequences (SQ)** — recursive application of the above.
- **Private/vendor tags** — configurable strip-unknown vs allowlist.
- **Free-text & structured reports / encapsulated PDF** — NLP PHI detection (Presidio + NER).
- **Burned-in pixel PHI** — OCR + NER + redaction.
- **Names** for Singapore's multiracial + foreigner population — the header-token-scrub +
  gazetteer + NER ensemble.
