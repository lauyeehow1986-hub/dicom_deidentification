#' Bulk-job manifest (SQLite) — the backbone of resumable, parallel batch runs.
#'
#' Every input file becomes a manifest row whose `status` moves
#' pending -> in_progress -> done | failed | flagged. A batch is resumable
#' because restarting simply skips rows already `done`. Parallel workers
#' (mirai/future) claim `pending` rows transactionally.
#'
#' Scaffold stage: schema + open/init only. Worker pool + claiming logic land in
#' Phase 5.

MANIFEST_SCHEMA <- "
CREATE TABLE IF NOT EXISTS files (
  id            INTEGER PRIMARY KEY,
  batch_id      TEXT NOT NULL,
  input_path    TEXT NOT NULL,
  output_path   TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  modality      TEXT,
  transfer_syntax TEXT,
  worker        TEXT,
  started_at    TEXT,
  finished_at   TEXT,
  residual_count INTEGER,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE TABLE IF NOT EXISTS batches (
  batch_id   TEXT PRIMARY KEY,
  profile_id TEXT,
  created_at TEXT,
  created_by TEXT,
  total      INTEGER
);
"

#' Open (creating if needed) a manifest database.
manifest_open <- function(path = file.path("run", "manifest.sqlite")) {
  dir.create(dirname(path), showWarnings = FALSE, recursive = TRUE)
  con <- DBI::dbConnect(RSQLite::SQLite(), path)
  for (stmt in strsplit(MANIFEST_SCHEMA, ";\\s*")[[1]]) {
    stmt <- trimws(stmt)
    if (nzchar(stmt)) DBI::dbExecute(con, stmt)
  }
  con
}

manifest_close <- function(con) DBI::dbDisconnect(con)

# TODO(Phase 5): register_batch(), claim_next(), mark_done(), mark_failed(),
# progress_summary(), and the mirai/future worker pool that drives them.
