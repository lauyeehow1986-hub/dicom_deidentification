#' Append-only, hash-chained audit log (Phase 6 governance).
#'
#' Every de-identification run and every reviewer sign-off is recorded as one
#' JSON line under the writable workspace. Each entry carries the hash of the
#' previous entry, and its own hash covers that link, so any edit, reorder, or
#' deletion of an earlier line breaks the chain and `audit_verify` reports it.
#' Tamper-evident, not tamper-proof: the point is that quiet edits cannot pass
#' unnoticed during governance review.

#' Directory (created on demand) that holds the audit log in the workspace.
audit_dir <- function() {
  ws <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
  d <- file.path(ws, "audit")
  dir.create(d, showWarnings = FALSE, recursive = TRUE)
  d
}

#' Path to the audit log (one JSON object per line).
audit_file <- function() file.path(audit_dir(), "audit_log.jsonl")

# Field separator for the canonical hash payload: a control char (U+001F, unit
# separator) that cannot appear in the JSON-escaped field values, so the payload
# is unambiguous. Built at runtime to avoid embedding a raw control byte.
.AUDIT_SEP <- intToUtf8(31L) #""

#' Canonical hash of one entry: sha256 over its fields plus the previous hash.
#' `details` is hashed as its exact on-disk JSON string so verification is
#' byte-for-byte reproducible (no re-serialisation ordering ambiguity).
.audit_entry_hash <- function(seq, ts, actor, role, action, details_json, prev) {
  payload <- paste(seq, ts, actor, role, action, details_json, prev,
                   sep = .AUDIT_SEP)
  digest::digest(payload, algo = "sha256", serialize = FALSE)
}

#' Append one action to the audit log; returns the written entry (incl. hash).
#'
#' @param action e.g. "deid_run", "residual_scan", "signoff".
#' @param actor  the acting user's identity.
#' @param role   "deidentifier" | "reviewer".
#' @param details a named list of action-specific fields (batch id, decision, …).
audit_append <- function(action, actor, role, details = list(),
                         path = audit_file()) {
  prev <- "GENESIS"
  seq <- 1L
  if (file.exists(path)) {
    lines <- readLines(path, warn = FALSE)
    lines <- lines[nzchar(lines)]
    if (length(lines)) {
      last <- jsonlite::fromJSON(lines[[length(lines)]], simplifyVector = TRUE)
      prev <- last$hash
      seq <- as.integer(last$seq) + 1L
    }
  }
  ts <- format(as.POSIXct(Sys.time()), "%Y-%m-%dT%H:%M:%OS3", tz = "UTC")
  details_json <- jsonlite::toJSON(details, auto_unbox = TRUE, null = "null")
  hash <- .audit_entry_hash(seq, ts, actor, role, action, details_json, prev)

  # Store details as a string field so the hashed bytes and the stored bytes are
  # identical on read-back.
  entry <- list(seq = seq, ts = ts, actor = actor, role = role, action = action,
                details = unclass(details_json), prev_hash = prev, hash = hash)
  line <- jsonlite::toJSON(entry, auto_unbox = TRUE, null = "null")
  dir.create(dirname(path), showWarnings = FALSE, recursive = TRUE)
  cat(line, "\n", file = path, append = TRUE, sep = "")

  entry$details <- details    # hand back the structured details to the caller
  entry
}

#' Read the audit log into a data.frame (empty frame when the log is absent).
audit_read <- function(path = audit_file()) {
  empty <- data.frame(seq = integer(), ts = character(), actor = character(),
                      role = character(), action = character(),
                      details = character(), stringsAsFactors = FALSE)
  if (!file.exists(path)) return(empty)
  lines <- readLines(path, warn = FALSE)
  lines <- lines[nzchar(lines)]
  if (!length(lines)) return(empty)
  rows <- lapply(lines, function(ln) {
    e <- jsonlite::fromJSON(ln, simplifyVector = TRUE)
    data.frame(seq = as.integer(e$seq), ts = e$ts %||% "", actor = e$actor %||% "",
               role = e$role %||% "", action = e$action %||% "",
               details = e$details %||% "", stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

#' Verify the hash chain. Returns `list(ok, n, broken_at)` where `broken_at` is
#' the 1-based sequence of the first entry whose hash or back-link is wrong
#' (NA when the chain is intact).
audit_verify <- function(path = audit_file()) {
  if (!file.exists(path)) return(list(ok = TRUE, n = 0L, broken_at = NA_integer_))
  lines <- readLines(path, warn = FALSE)
  lines <- lines[nzchar(lines)]
  prev <- "GENESIS"
  for (i in seq_along(lines)) {
    e <- jsonlite::fromJSON(lines[[i]], simplifyVector = TRUE)
    h <- .audit_entry_hash(e$seq, e$ts, e$actor, e$role, e$action,
                           e$details, prev)
    if (!identical(e$prev_hash, prev) || !identical(e$hash, h)) {
      return(list(ok = FALSE, n = length(lines), broken_at = i))
    }
    prev <- e$hash
  }
  list(ok = TRUE, n = length(lines), broken_at = NA_integer_)
}
