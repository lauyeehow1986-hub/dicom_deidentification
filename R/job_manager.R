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
  rel_in        TEXT,
  rel_out       TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  modality      TEXT,
  transfer_syntax TEXT,
  worker        TEXT,
  started_at    TEXT,
  finished_at   TEXT,
  residual_count INTEGER,
  input_sha256  TEXT,
  output_sha256 TEXT,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
-- Resume identity is the path RELATIVE to the project roots, so a batch keeps
-- resuming after a portable-disk swap changes the absolute drive/mount.
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_batch_relin ON files(batch_id, rel_in);
CREATE TABLE IF NOT EXISTS batches (
  batch_id   TEXT PRIMARY KEY,
  profile_id TEXT,
  created_at TEXT,
  created_by TEXT,
  total      INTEGER
);
"

# Columns added after the first schema shipped; ALTER them into any pre-existing
# manifest so an in-flight batch upgrades in place.
.MANIFEST_ADDED_COLS <- c(rel_in = "TEXT", rel_out = "TEXT",
                          input_sha256 = "TEXT", output_sha256 = "TEXT")

.manifest_migrate <- function(con) {
  have <- DBI::dbGetQuery(con, "PRAGMA table_info(files)")$name
  for (col in names(.MANIFEST_ADDED_COLS)) {
    if (!(col %in% have)) {
      DBI::dbExecute(con, sprintf("ALTER TABLE files ADD COLUMN %s %s",
                                  col, .MANIFEST_ADDED_COLS[[col]]))
    }
  }
  DBI::dbExecute(con,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_batch_relin ON files(batch_id, rel_in)")
}

# File extensions we treat as processable volumes/images.
MANIFEST_EXTS <- c("dcm", "dicom", "ima", "nii", "nii.gz")

#' Open (creating if needed) a manifest database.
#'
#' WAL journalling + a generous busy timeout let several parallel workers claim
#' rows from the same file concurrently without tripping over SQLITE_BUSY.
manifest_open <- function(path = file.path("run", "manifest.sqlite")) {
  dir.create(dirname(path), showWarnings = FALSE, recursive = TRUE)
  con <- DBI::dbConnect(RSQLite::SQLite(), path)
  DBI::dbExecute(con, "PRAGMA journal_mode=WAL;")
  DBI::dbExecute(con, "PRAGMA busy_timeout=30000;")
  DBI::dbExecute(con, "PRAGMA synchronous=NORMAL;")
  for (stmt in strsplit(MANIFEST_SCHEMA, ";\\s*")[[1]]) {
    stmt <- trimws(stmt)
    # skip SQL comment-only fragments produced by the split
    if (nzchar(stmt) && !all(grepl("^--", strsplit(stmt, "\n")[[1]])))
      DBI::dbExecute(con, stmt)
  }
  .manifest_migrate(con)
  con
}

manifest_close <- function(con) DBI::dbDisconnect(con)

# --------------------------------------------------------------------------- #
# Phase 5 - manifest job queue                                                #
# --------------------------------------------------------------------------- #

.now <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%S")

#' Recursively list the DICOM/NIfTI files under a root folder.
manifest_scan_files <- function(root) {
  pat <- paste0("\\.(", paste(gsub("\\.", "\\\\.", MANIFEST_EXTS), collapse = "|"),
                ")$")
  files <- list.files(root, pattern = pat, recursive = TRUE,
                      full.names = TRUE, ignore.case = TRUE)
  # `.nii.gz` also matches the `.gz`-agnostic set above via the alternation.
  normalizePath(files, winslash = "/", mustWork = FALSE)
}

# Relative path of `path` beneath `root` (falls back to the basename).
.rel_under <- function(root, path) {
  root <- normalizePath(root, winslash = "/", mustWork = FALSE)
  path <- normalizePath(path, winslash = "/", mustWork = FALSE)
  if (startsWith(path, paste0(root, "/"))) substring(path, nchar(root) + 2L)
  else basename(path)
}

#' Register (or top up) a batch: scan `root_in`, insert a pending row per file
#' with an output path mirroring its location under `root_out`. Idempotent -
#' re-registering the same batch adds only files not already present, so a run
#' can be resumed or a growing folder re-scanned safely.
register_batch <- function(con, batch_id, root_in, root_out, files = NULL,
                           profile_id = "default", created_by = NA_character_) {
  if (is.null(files)) files <- manifest_scan_files(root_in)
  inserted <- 0L
  if (length(files)) {
    rels <- vapply(files, function(f) .rel_under(root_in, f), character(1))
    out_paths <- vapply(rels, function(r) file.path(root_out, r), character(1))
    DBI::dbWithTransaction(con, {
      rs <- DBI::dbSendStatement(con,
        "INSERT OR IGNORE INTO files
           (batch_id, input_path, output_path, rel_in, rel_out, status)
         VALUES (?, ?, ?, ?, ?, 'pending')")
      DBI::dbBind(rs, list(rep(batch_id, length(files)), unname(files),
                           unname(out_paths), unname(rels), unname(rels)))
      inserted <- DBI::dbGetRowsAffected(rs)
      DBI::dbClearResult(rs)
    })
  }
  total <- DBI::dbGetQuery(con,
    "SELECT COUNT(*) n FROM files WHERE batch_id = ?", params = list(batch_id))$n
  total <- as.integer(total)
  DBI::dbExecute(con,
    "INSERT INTO batches (batch_id, profile_id, created_at, created_by, total)
     VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(batch_id) DO UPDATE SET total = excluded.total,
       profile_id = excluded.profile_id",
    params = list(batch_id, profile_id, .now(), created_by, total))
  list(batch_id = batch_id, inserted = as.integer(inserted), total = total)
}

#' Transactionally claim one pending row for `worker`. Returns the row (as a
#' one-row list) or NULL when the queue is drained. BEGIN IMMEDIATE serialises
#' the select-then-update so two workers never grab the same file.
claim_next <- function(con, worker, batch_id = NULL,
                       root_in = NULL, root_out = NULL) {
  # A single UPDATE...RETURNING is atomic: SQLite serialises writers, so a
  # second worker running this concurrently re-evaluates the subquery and picks
  # the *next* pending row rather than re-claiming the same one.
  sel <- "SELECT id FROM files WHERE status='pending'"
  if (is.null(batch_id)) {
    sub <- paste(sel, "ORDER BY id LIMIT 1")
    params <- list(worker, .now())
  } else {
    sub <- paste(sel, "AND batch_id=? ORDER BY id LIMIT 1")
    params <- list(worker, .now(), batch_id)
  }
  row <- DBI::dbGetQuery(con, paste0(
    "UPDATE files SET status='in_progress', worker=?, started_at=?
     WHERE id = (", sub, ")
     RETURNING id, input_path, output_path, rel_in, rel_out, batch_id"),
    params = params)
  if (!nrow(row)) return(NULL)
  row <- as.list(row)
  # Resolve absolute paths against the CURRENT project roots, so a batch resumes
  # correctly after a portable-disk swap changed the drive letter / mount.
  .rel_ok <- function(x) length(x) && !is.na(x[[1]]) && nzchar(x[[1]])
  if (!is.null(root_in) && .rel_ok(row$rel_in))
    row$input_path <- file.path(root_in, row$rel_in)
  if (!is.null(root_out) && .rel_ok(row$rel_out))
    row$output_path <- file.path(root_out, row$rel_out)
  row
}

#' Mark a claimed row finished successfully.
mark_done <- function(con, id, output_path = NULL, residual_count = NA_integer_,
                      modality = NULL, transfer_syntax = NULL,
                      input_sha256 = NULL, output_sha256 = NULL) {
  rc <- if (length(residual_count)) residual_count[[1]] else NA_integer_
  status <- if (!is.na(rc) && rc > 0) "flagged" else "done"
  nn <- function(x) if (is.null(x) || !length(x)) NA else x[[1]]
  DBI::dbExecute(con,
    "UPDATE files SET status=?, finished_at=?, residual_count=?,
       output_path=COALESCE(?, output_path),
       modality=COALESCE(?, modality),
       transfer_syntax=COALESCE(?, transfer_syntax),
       input_sha256=COALESCE(?, input_sha256),
       output_sha256=COALESCE(?, output_sha256) WHERE id=?",
    params = list(status, .now(), nn(rc), nn(output_path),
                  nn(modality), nn(transfer_syntax),
                  nn(input_sha256), nn(output_sha256), id))
  invisible(status)
}

#' Mark a claimed row failed, recording the error message.
mark_failed <- function(con, id, error) {
  DBI::dbExecute(con,
    "UPDATE files SET status='failed', finished_at=?, error=? WHERE id=?",
    params = list(.now(), as.character(error), id))
  invisible("failed")
}

#' Re-queue rows a crashed worker left mid-flight (in_progress -> pending).
reset_stale <- function(con, batch_id = NULL) {
  if (is.null(batch_id)) {
    n <- DBI::dbExecute(con,
      "UPDATE files SET status='pending', worker=NULL, started_at=NULL
       WHERE status='in_progress'")
  } else {
    n <- DBI::dbExecute(con,
      "UPDATE files SET status='pending', worker=NULL, started_at=NULL
       WHERE status='in_progress' AND batch_id=?", params = list(batch_id))
  }
  as.integer(n)
}

#' Count rows by status for a batch (or all batches when batch_id is NULL).
progress_summary <- function(con, batch_id = NULL) {
  if (is.null(batch_id)) {
    q <- DBI::dbGetQuery(con, "SELECT status, COUNT(*) n FROM files GROUP BY status")
  } else {
    q <- DBI::dbGetQuery(con,
      "SELECT status, COUNT(*) n FROM files WHERE batch_id=? GROUP BY status",
      params = list(batch_id))
  }
  get <- function(s) { v <- q$n[q$status == s]; if (length(v)) as.integer(v) else 0L }
  list(
    total    = sum(as.integer(q$n)),
    pending  = get("pending"),
    in_progress = get("in_progress"),
    done     = get("done"),
    failed   = get("failed"),
    flagged  = get("flagged")
  )
}

#' Most-recently-touched rows, for the live dashboard table.
manifest_recent <- function(con, batch_id = NULL, n = 15) {
  base <- "SELECT input_path, output_path, status, worker, error, finished_at
           FROM files"
  ord <- "ORDER BY COALESCE(finished_at, started_at, '') DESC, id DESC LIMIT ?"
  if (is.null(batch_id)) {
    DBI::dbGetQuery(con, paste(base, ord), params = list(as.integer(n)))
  } else {
    DBI::dbGetQuery(con, paste(base, "WHERE batch_id=?", ord),
                    params = list(batch_id, as.integer(n)))
  }
}

#' All failed rows (input + error), for the retry/triage table.
manifest_failures <- function(con, batch_id = NULL) {
  if (is.null(batch_id)) {
    DBI::dbGetQuery(con, "SELECT input_path, error FROM files WHERE status='failed'")
  } else {
    DBI::dbGetQuery(con,
      "SELECT input_path, error FROM files WHERE status='failed' AND batch_id=?",
      params = list(batch_id))
  }
}

#' The de-id profile registered for a batch (falls back to "default").
batch_profile <- function(con, batch_id) {
  v <- DBI::dbGetQuery(con, "SELECT profile_id FROM batches WHERE batch_id=?",
                       params = list(batch_id))$profile_id
  if (length(v) && !is.na(v) && nzchar(v)) v else "default"
}

#' The worker body: claim -> de-identify -> mark, until the queue drains.
#'
#' `deid_fn(input_path, output_path, profile_id, keystore_path, passphrase)` does
#' the actual work and returns the engine report (a list; an optional
#' `residual_count` flags residual PHI). Injected so it can be unit-tested with a
#' fake, and so each parallel worker can call the reticulate engine in its own
#' process. Returns the number of rows this worker processed.
drain <- function(con, deid_fn, worker = "w1", batch_id = NULL,
                  profile_id = NULL, keystore_path = NULL,
                  passphrase = NULL, on_progress = NULL,
                  root_in = NULL, root_out = NULL, processed_log = NULL) {
  processed <- 0L
  repeat {
    row <- claim_next(con, worker, batch_id, root_in = root_in, root_out = root_out)
    if (is.null(row)) break
    pid <- profile_id %||% batch_profile(con, row$batch_id)
    if (!is.null(row$output_path) && !is.na(row$output_path))
      dir.create(dirname(row$output_path), showWarnings = FALSE, recursive = TRUE)
    ok <- tryCatch({
      rep <- deid_fn(row$input_path, row$output_path, pid,
                     keystore_path, passphrase)
      rc <- suppressWarnings(as.integer(rep$residual_count %||% NA))
      status <- mark_done(con, row$id, output_path = row$output_path,
                          residual_count = rc,
                          input_sha256 = rep$input_sha256 %||% NULL,
                          output_sha256 = rep$output_sha256 %||% NULL)
      if (!is.null(processed_log))
        processed_log_append(processed_log, list(
          rel_in = row$rel_in %||% NA, rel_out = row$rel_out %||% NA,
          input_sha256 = rep$input_sha256 %||% NA,
          output_sha256 = rep$output_sha256 %||% NA,
          residual_count = rc, status = status,
          signature_id = rep$signature_id %||% NA))
      TRUE
    }, error = function(e) {
      mark_failed(con, row$id, conditionMessage(e))
      if (!is.null(processed_log))
        processed_log_append(processed_log, list(
          rel_in = row$rel_in %||% NA, status = "failed",
          error = conditionMessage(e)))
      FALSE
    })
    processed <- processed + 1L
    if (is.function(on_progress)) on_progress(progress_summary(con, batch_id))
  }
  processed
}

#' Who registered/ran a batch (the de-identifier), for the QA sign-off gate.
batch_created_by <- function(con, batch_id) {
  r <- DBI::dbGetQuery(con, "SELECT created_by FROM batches WHERE batch_id = ?",
                       params = list(batch_id))
  if (nrow(r) && !is.na(r$created_by[[1]]) && nzchar(r$created_by[[1]]))
    r$created_by[[1]] else NA_character_
}

#' Output paths recorded for a batch (or the whole manifest), for QA re-scanning.
manifest_output_paths <- function(con, batch_id = NULL) {
  if (is.null(batch_id)) {
    DBI::dbGetQuery(con,
      "SELECT output_path FROM files WHERE output_path IS NOT NULL")$output_path
  } else {
    DBI::dbGetQuery(con,
      "SELECT output_path FROM files WHERE batch_id = ? AND output_path IS NOT NULL",
      params = list(batch_id))$output_path
  }
}

#' Phase 6: record a QA residual scan on an output row. A non-zero residual count
#' moves the row to `flagged`; zero (re)marks it `done`. Matched by output_path
#' so QA can annotate a batch after the fact.
set_residual <- function(con, output_path, residual_count) {
  rc <- suppressWarnings(as.integer(residual_count))
  status <- if (!is.na(rc) && rc > 0L) "flagged" else "done"
  DBI::dbExecute(
    con,
    "UPDATE files SET status = ?, residual_count = ?, finished_at = ? WHERE output_path = ?",
    params = list(status, rc, .now(), output_path))
}
