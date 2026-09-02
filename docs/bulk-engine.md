# Bulk engine (Phase 5)

Interactive one-folder de-identification does not scale to the real workload:
cardiovascular batches reach **~2 TB** across thousands of studies, a run can be
interrupted (a killed app, a rebooted laptop), and single-threaded processing
wastes a multi-core box. Phase 5 adds a **queued, resumable, parallel** engine.

## The manifest (SQLite)

Every input file becomes a row in a SQLite **manifest** whose `status` moves

```
pending -> in_progress -> done | flagged | failed
```

`flagged` is reserved for outputs a residual scan (Phase 6) finds still carry
PHI; today the engine records `done`. The manifest is the single source of
truth, so the batch is **resumable**: restarting simply skips rows already
`done`, and `reset_stale()` re-queues any row a crashed worker left
`in_progress`. Manifests live in the writable workspace at
`$DICOMDEID_WORKSPACE/run/manifest_<batch>.sqlite` — never in the package, and
never committed (they name real input paths).

Engine surface (`R/job_manager.R`):
`manifest_open`, `manifest_scan_files`, `register_batch`, `claim_next`,
`mark_done`, `mark_failed`, `reset_stale`, `progress_summary`,
`manifest_recent`, `manifest_failures`.

### No double-claim

Workers claim work with a single atomic statement:

```sql
UPDATE files SET status='in_progress', worker=?, started_at=?
WHERE id = (SELECT id FROM files WHERE status='pending' ORDER BY id LIMIT 1)
RETURNING id, input_path, output_path, batch_id;
```

SQLite serialises writers, so two workers running this concurrently can never
grab the same row — the second re-evaluates the subquery and picks the next
pending file. The database opens in **WAL** mode with a 30 s busy timeout so
several worker processes contend cleanly.

## Workers

`drain()` is the worker body: claim -> de-identify -> mark, looping until the
queue empties. It takes an **injected** de-id function, which keeps the queue
logic unit-testable with a fake and lets each parallel worker call the
reticulate engine in its own process.

`run_batch()` drives a batch to completion:

- **serial** (`workers <= 1`) runs `drain` in-process;
- **parallel** spins `workers` **`mirai`** daemons, each a self-contained
  `drain` over the shared WAL database. Each daemon loads the app sources and
  points reticulate at the same relocatable venv, so it can import the engine
  independently.

Before running, `run_batch` re-queues stale rows (resume) and de-conflicts the
worker count: a **reversible** batch writes one shared encrypted keystore, so it
is forced **single-worker** (`bulk_effective_workers`) to avoid a write race.

## The dashboard (Shiny)

The **Bulk** tab (`R/mod_bulk.R`) never blocks: pressing **Start / Resume**
launches `run_batch` in a background **`callr`** process, and the UI polls the
manifest once a second for a live progress bar, per-status counts, a recent-files
table, and a failures table. **Stop** kills the background process and
re-queues in-flight rows; **Start** then resumes. Restarting the whole app and
re-scanning the same batch id picks up exactly where it left off.

## Verification

- Unit tests (`tests/testthat/test-bulk.R`): scanning, idempotent registration,
  no-double-claim, terminal transitions, progress counts, crash recovery, the
  `drain` happy/failure/resume paths, `run_batch` resume, dashboard readers, and
  the reversible worker cap.
- Acceptance smoke: copies of an open-source DICOM processed across 2-4 `mirai`
  workers after a simulated crash -> every output a valid, reloadable,
  pseudonymised DICOM; a second run does zero work (resumable). Driven both from
  a script and live through the Bulk tab.
