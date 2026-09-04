#' Quality-assurance residual scan (Phase 6).
#'
#' Re-runs the PHI detectors on de-identified OUTPUTS and turns the engine's
#' per-file residual reports into the tables the QA tab shows: a per-file verdict
#' (PASS / FLAG) and a per-finding list whose previews are masked so the QA
#' report itself never re-discloses an identifier in clear text.
#'
#' The heavy lifting is the Python `scan_residual`; an injectable `scan_fn` keeps
#' this aggregation unit-testable without a venv.

#' Coerce reticulate's list-of-lists / named-list shapes to plain R.
.as_int <- function(x) if (is.null(x)) 0L else as.integer(x)

#' Scan every path and aggregate. Returns per-file results plus batch rollups.
#'
#' @param paths character vector of output files to scan.
#' @param profile_id profile whose gazetteer / recognisers drive the scan.
#' @param scan_fn function(path) -> residual report; defaults to the engine.
#' @return list(results, files_df, by_category, n_files, n_passed, n_flagged,
#'   total_findings, passed).
residual_scan_paths <- function(paths, profile_id = "default", scan_fn = NULL) {
  scan_fn <- scan_fn %||% function(p) engine_scan_residual(p, profile_id)
  results <- lapply(paths, function(p) {
    r <- scan_fn(p)
    if (is.null(r$path)) r$path <- p
    r
  })

  by_category <- list()
  for (r in results) {
    for (nm in names(r$by_category %||% list())) {
      by_category[[nm]] <- (by_category[[nm]] %||% 0L) + .as_int(r$by_category[[nm]])
    }
  }

  passed_vec <- vapply(results, function(r) isTRUE(r$passed), logical(1))
  files_df <- qa_files_df(results)
  list(
    results = results,
    files_df = files_df,
    by_category = by_category,
    n_files = length(results),
    n_passed = sum(passed_vec),
    n_flagged = sum(!passed_vec),
    total_findings = sum(vapply(results,
                                function(r) .as_int((r$counts %||% list())$total),
                                integer(1))),
    passed = all(passed_vec)
  )
}

#' Per-file verdict table for the QA dashboard.
qa_files_df <- function(results) {
  if (!length(results)) {
    return(data.frame(File = character(), Findings = integer(),
                      Categories = character(), IdentityRemoved = character(),
                      Verdict = character(), stringsAsFactors = FALSE))
  }
  rows <- lapply(results, function(r) {
    cats <- names(r$by_category %||% list())
    data.frame(
      File = basename(r$path %||% ""),
      Findings = .as_int((r$counts %||% list())$total),
      Categories = if (length(cats)) paste(cats, collapse = ", ") else "",
      IdentityRemoved = if (is.null(r$identity_removed) ||
                            (length(r$identity_removed) == 1 && is.na(r$identity_removed))) {
        "n/a"    # NIfTI: residual scan can't attest identity removal (no PatientIdentityRemoved tag)
      } else if (isTRUE(r$identity_removed)) "yes" else "NO",
      Verdict = if (isTRUE(r$passed)) "PASS" else "FLAG",
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, rows)
}

#' Per-finding table (masked previews only) for the QA dashboard.
qa_findings_df <- function(results) {
  empty <- data.frame(File = character(), Location = character(),
                      Field = character(), Category = character(),
                      Source = character(), Score = numeric(),
                      Preview = character(), stringsAsFactors = FALSE)
  rows <- list()
  for (r in results) {
    for (f in (r$findings %||% list())) {
      rows[[length(rows) + 1L]] <- data.frame(
        File = basename(r$path %||% ""),
        Location = f$location %||% "",
        Field = f$keyword %||% "",
        Category = f$category %||% "",
        Source = f$source %||% "",
        Score = as.numeric(f$score %||% NA_real_),
        Preview = f$preview %||% "",
        stringsAsFactors = FALSE
      )
    }
  }
  if (!length(rows)) return(empty)
  do.call(rbind, rows)
}
