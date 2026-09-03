# Projects, integrity & signing (Phase 6.5)

A de-identification effort is more than one folder run once. The same rules,
the same patient-hashing, and the same reviewer sign-off have to follow a study
across weeks and across **portable disks** — SingHealth cardiovascular batches
are large enough that the input and output folders move between hard drives
mid-run. This slice makes that first-class:

1. **Projects** — a named, portable bundle of settings you can copy to another
   study and rebind to a new disk.
2. **Dual-scope hashing** — the same NRIC can hash *identically everywhere*
   (global) or *differently per study* (project), your choice per project.
3. **Disk-swap-safe resume** — swap the drive, rebind the folders, keep going.
4. **Integrity** — before/after checksums and a per-file processing log.
5. **Signing** — an Ed25519 sidecar signature per output for tamper-evident
   assurance, verifiable by a reviewer who holds only the public key.
6. **Metadata review** — the reviewer inspects the de-identified header itself,
   not just the residual-scan verdict.

## 1. Project — a portable bundle

A **project** lives entirely in the writable workspace, outside git and outside
the shipped package, so it copies onto a portable disk as a folder:

```
$DICOMDEID_WORKSPACE/
  projects/<project_id>/
    project.json          # the settings (below)
    manifest.sqlite       # this project's resumable job queue
    keystore.json         # only when hashing scope = project
    processed_log.jsonl   # append-only per-file processing log
    audit_log.jsonl       # this project's hash-chained audit log
    signing/pipeline_ed25519.key + .pub   # only when signing scope = project
  keystores/global.json                   # portable global salt (shared, linkable)
  signing/global_ed25519.key + .pub       # portable global signing key
```

`project.json` is the copyable descriptor:

```json
{
  "project_id": "nhcs_echo_2026",
  "label": "NHCS Echo 2026",
  "profile_id": "nhcs_echo_2026",
  "hashing":  { "scope": "global",  "reversible": true },
  "signing":  { "enabled": true, "scope": "global" },
  "roots":    { "in": "E:/incoming", "out": "F:/deid" },
  "created_by": "yh", "created_at": "2026-09-03T..."
}
```

**Copy settings to a new project.** `project_create(new_id, copy_from=old_id)`
clones the source profile (reusing the Phase 4 `profile_clone`) and copies the
hashing/signing policy, but starts a **fresh manifest, log, and audit chain**
and leaves the roots for you to point at the new disk. Two projects that both
use `scope: global` stay linkable automatically.

## 2. Dual-scope hashing

Pseudonyms are `salted_sha256(value, salt)`; the salt lives in a keystore file.
So "global vs project" is simply *which keystore a project resolves to*:

- **global** → `keystores/global.json` — one portable salt shared by every
  project that opts in. The same NRIC → the same pseudonym across projects,
  machines, and disk swaps. Enables longitudinal / cross-project linkage. This
  file links patients, so it is guarded like the crosswalk.
- **project** → `projects/<id>/keystore.json` — a salt unique to the project.
  The same NRIC → a *different* pseudonym in another project.

Persistence is decoupled from reversibility. A new keystore mode makes the salt
**persist even when irreversible**, so "same NRIC, same hash regardless of when
we run it" holds in both modes:

- **reversible** (default) — persist salt **and** the encrypted orig↔pseudonym
  crosswalk; authorised staff can re-identify (research follow-up/recontact).
- **deterministic-irreversible** — persist **only the salt**; `record()` is a
  no-op, no crosswalk is ever written, re-identification is impossible, but
  hashes stay stable over time.

Both are per-project selectable. `deid_run` gains an explicit `reversible` flag
and always receives a keystore path (the project resolves it); the old
"no path ⇒ random ephemeral salt" behaviour is retired because it broke
cross-run stability.

## 3. Disk-swap-safe resume

Today the manifest resumes on the **absolute** `input_path` with a unique index
on `(batch_id, input_path)` — a new drive letter after a disk swap looks like a
brand-new file, so everything re-processes. Fix:

- Store paths **relative to the project roots**: new `rel_in` / `rel_out`
  columns; the unique identity becomes `(batch_id, rel_in)`.
- Resolve absolute paths against the project's **current** roots at claim time.
- `project_rebind_roots(project, new_in, new_out)` updates `project.json`; the
  manifest is untouched because it already stores relative paths. After
  rebinding `E:/` → `G:/`, `done` rows stay done and `pending` rows resume.
- Back-compatible: rows without `rel_in` fall back to the stored absolute path.

## 4. Integrity — checksums + processing log

- Manifest gains `input_sha256` and `output_sha256`. The worker streams the
  input to hash it *before* de-id and hashes the written output *after*
  (chunked, safe for ~2 TB throughput).
- before ≠ after **by design** — de-id changes bytes. The point is provenance
  and tamper-evidence: the pair is recorded, signed (§5), and chained into the
  audit log.
- `processed_log.jsonl` (append-only) gets one line per file:
  `{ts, rel_in, rel_out, input_sha256, output_sha256, modality,
    transfer_syntax, status, residual_count, signature_id}`. This is the
  human-readable "what was processed, and did it change as expected" record.

## 5. Signing — Ed25519 sidecar

`signing.py` (built on the existing `cryptography` dependency):

- `ensure_keypair(dir)` — generate `pipeline_ed25519.key` (private, PEM) and
  `.pub` if absent.
- `sign_output(output_path, meta, key_path)` — write `<output>.sig.json`:
  `{v, file, sha256, deid_method, profile_id, project_id, signed_by,
    signed_at, sig}`, where `sig` is Ed25519 over the canonical JSON of the
  payload. A per-batch `batch_manifest.sig.json` signs the list of
  `(rel, sha256)`.
- `verify_output(output_path, pub_path)` — recompute the SHA-256, verify the
  signature → `{ok, reason}`. A file verifies only when its bytes still match
  the signed hash **and** the signature checks out.

The **public key ships in the bundle**, so a reviewer on any machine can verify
without the private key and cannot forge. Signing scope (global/project)
follows the project setting.

## 6. Reviewer metadata review

`read_metadata(path)` returns the de-identified header as searchable rows
`[{tag, keyword, vr, value}]`, recursing one level into sequences, masking any
value the residual scan flagged (reusing `_mask_preview`). The **QA / Review**
tab gains a **Metadata** panel: pick a file → inspect its de-identified tags
alongside the residual verdict and the signature status, so the reviewer
eyeballs the real header before signing off.

## 7. UI & wiring

- New `mod_projects.R` — create / clone / select a project; edit hashing scope,
  reversibility, and signing; set and **rebind** the input/output roots
  ("disk swapped → rebind"). The selected project is the app's active context.
- `mod_bulk`, `mod_interactive`, and `mod_qa` read the **active project's**
  profile, keystore, roots, and manifest instead of ad-hoc inputs.
- Audit stays **per-project**: each project has its own hash-chained
  `audit_log.jsonl`, so the audit travels with the bundle. Entries carry the
  `project_id`.

## 8. Bundle (Phase 7)

The portable bundle includes `keystores/global.json` and
`signing/global_ed25519.*` so global hashing and signature verification work on
the target air-gapped workstation. These files link patients and can sign as
the pipeline — they are handled as secrets, guarded like the crosswalk, and
never committed to git.

## Verification (test-first)

- **Engine (pytest):** salt-only irreversible keystore (stable hash, empty
  crosswalk); global vs project salt → identical / different pseudonym for the
  same NRIC; `file_sha256`; sign → verify round-trip, tampered bytes → FAIL,
  wrong key → FAIL; `read_metadata` masks flagged values and recurses a
  sequence.
- **R (testthat):** `project_create` / clone copies settings + starts a fresh
  manifest; `project_rebind_roots` → resume matches on the relative path after a
  simulated drive change; manifest checksum columns populate; processing-log
  append/read; signature-verify table; per-project audit tagging.
- **End-to-end:** de-identify `image-00000.dcm` under a project, swap the output
  root, resume without re-processing, verify the signature, and view the
  de-identified metadata in the review tab.
