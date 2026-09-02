#' Bulk de-identification engine (Phase 5): drive a manifest batch to
#' completion, optionally across several parallel worker processes.
#'
#' The queue/worker mechanics live in job_manager.R (pure SQLite, unit-tested).
#' This file adds (1) the single-file adapter that calls the reticulate engine
#' and (2) `run_batch`, which resumes any stale rows and then drains the queue
#' either in-process (serial) or across `mirai` daemons (parallel).

#' Directory under the writable workspace where batch manifests live.
bulk_run_dir <- function() {
  ws <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
  d <- file.path(ws, "run")
  dir.create(d, showWarnings = FALSE, recursive = TRUE)
  d
}

#' Manifest path for a named batch inside the workspace.
bulk_manifest_path <- function(batch_id) {
  safe <- gsub("[^A-Za-z0-9_-]+", "_", batch_id)
  file.path(bulk_run_dir(), paste0("manifest_", safe, ".sqlite"))
}

#' Effective worker count: a reversible batch (keystore in play) is forced to a
#' single worker so parallel processes never race on the one encrypted keystore.
bulk_effective_workers <- function(workers, keystore_path = NULL) {
  workers <- max(1L, as.integer(workers))
  if (!is.null(keystore_path) && nzchar(keystore_path) && workers > 1L) 1L
  else workers
}

#' Single-file de-id used by bulk workers: wraps the engine and returns its
#' report (the manifest reads `residual_count` from it, when present).
bulk_deid_file <- function(input_path, output_path, profile_id = "default",
                           keystore_path = NULL, passphrase = NULL) {
  engine_deid_run(input_path, output_path, profile_id, keystore_path, passphrase)
}

#' Run a batch to completion. Resumable and idempotent: stale `in_progress`
#' rows (from a killed run) are re-queued first, and rows already `done` are
#' skipped.
#'
#' @param manifest_path SQLite manifest created by `register_batch`.
#' @param workers number of parallel processes; <= 1 runs in-process.
#' @param deid_fn injectable worker fn (tests pass a fake); default is the real
#'   engine adapter. Parallel runs always use the engine adapter in each daemon.
#' @param r_source_dir directory of app R sources each daemon must load.
#' @param venv absolute path to the relocatable venv for daemons (defaults to
#'   the one this process resolved), so a fresh worker process finds the engine.
#' @param on_progress optional callback given the live `progress_summary` after
#'   each file (serial path only).
#' @return the final `progress_summary` for the batch.
run_batch <- function(manifest_path, batch_id = NULL, workers = 1L,
                      keystore_path = NULL, passphrase = NULL,
                      deid_fn = NULL, r_source_dir = "R",
                      venv = engine_venv_path(), on_progress = NULL) {
  con <- manifest_open(manifest_path)
  on.exit(manifest_close(con), add = TRUE)
  reset_stale(con, batch_id)

  # A reversible run writes one shared encrypted keystore; parallel processes
  # would race on it, so reversible batches are forced single-worker.
  eff <- bulk_effective_workers(workers, keystore_path)
  if (eff < workers)
    message("Reversible batch -> running single-worker to keep the keystore consistent.")
  workers <- eff

  # Serial path: tests inject a fake fn; real serial runs use the engine here.
  if (!is.null(deid_fn) || workers <= 1L) {
    fn <- deid_fn %||% bulk_deid_file
    drain(con, deid_fn = fn, worker = "w1", batch_id = batch_id,
          keystore_path = keystore_path, passphrase = passphrase,
          on_progress = on_progress)
    return(progress_summary(con, batch_id))
  }

  # Parallel path: N mirai daemons, each a self-contained drain over the shared
  # WAL database. Each daemon loads the app sources and points reticulate at the
  # same venv so it can import the engine in its own process.
  src  <- normalizePath(r_source_dir, mustWork = TRUE)
  venv <- normalizePath(venv, mustWork = FALSE)
  mirai::daemons(as.integer(workers))
  on.exit(mirai::daemons(0), add = TRUE)
  tasks <- lapply(seq_len(workers), function(i) {
    mirai::mirai({
      Sys.setenv(DICOMDEID_VENV = VENV, KMP_DUPLICATE_LIB_OK = "TRUE")
      for (f in list.files(SRC, pattern = "\\.R$", full.names = TRUE)) source(f)
      con <- manifest_open(MP)
      on.exit(manifest_close(con))
      drain(con, deid_fn = bulk_deid_file, worker = WK, batch_id = BID,
            keystore_path = KS, passphrase = PW)
    }, SRC = src, MP = manifest_path, WK = sprintf("w%d", i), BID = batch_id,
       KS = keystore_path, PW = passphrase, VENV = venv)
  })
  lapply(tasks, mirai::call_mirai)   # block until every daemon finishes
  progress_summary(con, batch_id)
}
