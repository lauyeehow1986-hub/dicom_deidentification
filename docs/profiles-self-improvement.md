# Profiles & self-improvement (Phase 4)

De-identification is never "done once" — every study team hits an identifier the
pipeline missed. Phase 4 makes the tool *learnable*: the reviewer keeps
per-project profiles and feeds misses straight back into detection, without
touching code or the shipped package.

## The writable workspace

The shipped package (`inst/profiles`, `inst/gazetteers`) is read-only on the
air-gapped box. Everything the reviewer *creates* lives in a separate **workspace**
(`inst/python/deid_engine/workspace.py`):

```
$DICOMDEID_WORKSPACE            (default: ./workspace next to the app)
  profiles/<id>_profile.yml     per-project profiles
  gazetteers/<id>_custom.txt    names/values grown by tagging misses
  labeled_examples.jsonl        every captured miss (for a later NER fine-tune)
  ner_export/train.jsonl        the exported training set
```

The workspace is **git-ignored** — it can contain identifiers captured from real
studies, so it must never be committed. The app sets `$DICOMDEID_WORKSPACE`
alongside itself at startup; override it to point at a shared project location.

## Per-project profiles

A profile = PS3.15 option toggles + date/reversibility policy + private-tag
policy + the layered text-detection config. The **Rules & Profiles** tab lets the
reviewer:

- pick a profile and see every setting;
- edit it and **Save** — a shipped default is *copied* into the workspace on
  first edit (`profile_get` resolves a workspace copy ahead of the shipped one),
  so the package stays pristine;
- **Save as new project** to clone under a new id (e.g. `cardio_ct`, `echo_us`);
- **Use for other tabs** to make it the active profile the Interactive / Pixels /
  QA tabs run.

Engine surface: `profiles_list`, `profile_get`, `profile_save`, `profile_clone`.

## Tag-a-miss self-improvement loop

The **Tagging** tab captures a value the pipeline missed (in a metadata tag or
burned into pixels). Every capture:

1. **always** appends a labeled example to `labeled_examples.jsonl`; and
2. per the chosen fix, either
   - **adds it to the project gazetteer** (`gazetteers/<id>_custom.txt`, wired
     into the profile's `text_detection.extra_gazetteer_files`), and/or
   - **appends a custom-regex rule** (`text_detection.custom_regex`), a compiled
     extra layer in the scanner alongside the SG recognisers.

Because the fix edits the *project profile*, the next run with that profile
catches the value. The loop is closed and deterministic: tag `Dr Muthusamy` once,
and every later study de-identified under that project scrubs it.

Engine surface: `tag_capture(category, value, profile_id, fix, pattern, …)`.

## NER fine-tuning hook

**Export NER training set** turns the accumulated labeled examples into a
training-ready JSON-lines file (`{"text", "entities": [[start, end, LABEL]]}`),
the shape spaCy / transformers fine-tuning expects. This is a **hook, not a
trainer** — `ner_export_examples` prepares the data; an offline `spacy train` /
transformers fine-tune runs separately on the air-gapped box, and the app then
consumes the resulting model directory via `text_detection.ner_model`.

## Degradation

No new runtime dependency. The custom-regex layer is pure `re`; a broken pattern
is skipped with a note rather than failing the scan. A profile with no learned
rules behaves exactly as the shipped default.
