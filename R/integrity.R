#' Integrity records (Phase 6.5): the per-file processing log and the
#' signature-verification view for the reviewer.

#' Append one processing record (a named list) as a JSON line. Used to log every
#' file the bulk engine processes: relative paths, before/after checksums,
#' status, residual count, and the signature id.
processed_log_append <- function(path, record) {
  dir.create(dirname(path), showWarnings = FALSE, recursive = TRUE)
  rec <- record
  if (is.null(rec$ts)) rec$ts <- format(Sys.time(), "%Y-%m-%dT%H:%M:%S")
  line <- jsonlite::toJSON(rec, auto_unbox = TRUE, null = "null")
  cat(line, "\n", file = path, sep = "", append = TRUE)
  invisible(rec)
}

#' Read a processing log into a data.frame (one row per file). Ragged records are
#' unioned over their fields; missing cells come back as NA.
processed_log_read <- function(path) {
  if (!file.exists(path)) return(data.frame())
  lines <- readLines(path, warn = FALSE)
  lines <- lines[nzchar(trimws(lines))]
  if (!length(lines)) return(data.frame())
  recs <- lapply(lines, function(l)
    tryCatch(as.list(jsonlite::fromJSON(l, simplifyVector = TRUE)),
             error = function(e) NULL))
  recs <- Filter(Negate(is.null), recs)
  cols <- unique(unlist(lapply(recs, names)))
  out <- lapply(cols, function(cn) vapply(recs, function(r) {
    v <- r[[cn]]
    if (is.null(v) || length(v) == 0) NA_character_ else as.character(v[[1]])
  }, character(1)))
  names(out) <- cols
  as.data.frame(out, stringsAsFactors = FALSE)
}

#' Build the reviewer's signature table: for each output, verify its detached
#' signature and surface the signer/time. `verify_fn(output, pub)` returns
#' `list(ok, reason)`; it defaults to the reticulate engine and is injected in
#' tests. Reads signer metadata from the sidecar `<output>.sig.json`.
signature_status_df <- function(output_paths, pub_path, verify_fn = NULL) {
  verify_fn <- verify_fn %||% function(out, pub) engine_verify_output(out, pub)
  rows <- lapply(output_paths, function(out) {
    sig_path <- paste0(out, ".sig.json")
    meta <- if (file.exists(sig_path))
      tryCatch(jsonlite::fromJSON(sig_path, simplifyVector = TRUE),
               error = function(e) list()) else list()
    res <- tryCatch(verify_fn(out, pub_path),
                    error = function(e) list(ok = FALSE, reason = conditionMessage(e)))
    verdict <- if (isTRUE(res$ok)) "OK" else paste0("FAIL: ", res$reason %||% "invalid")
    data.frame(File = basename(out),
               SignedBy = meta$signed_by %||% "",
               SignedAt = meta$signed_at %||% "",
               Verify = verdict, stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}
