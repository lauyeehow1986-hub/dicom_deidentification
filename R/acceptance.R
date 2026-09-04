#' Phase 7 - acceptance / validation runner.
#'
#' Runs the whole de-identification pipeline over the synthetic planted-PHI
#' corpus and turns the results into a pass/fail check table + a markdown report.
#' The pure core (`acceptance_checks`, `acceptance_report_md`) is separated from
#' the engine orchestration (`acceptance_run`) so the verdict logic is unit-
#' tested without a venv, and the live run is a thin wrapper.

# --- helpers ---------------------------------------------------------------

# Deface acceptance signal: a file carrying a `deface` record must be defaced or
# a recognised graceful skip; a `deface_error` means the step blew up -> fail.
# Uses exact-match `[[ ]]` accessors: `$` partial-matches `deface` to
# `deface_error` and would crash on the error-only path this must grade.
.acc_deface_signal <- function(files) {
  deface_recs <- Filter(Negate(is.null),
                        lapply(files, function(f) f$counts[["deface"]]))
  deface_errs <- sum(vapply(files,
                            function(f) !is.null(f$counts[["deface_error"]]),
                            logical(1)))
  deface_applied <- sum(vapply(deface_recs,
                               function(d) isTRUE(d[["defaced"]]), logical(1)))
  list(ok = deface_errs == 0,
       detail = sprintf("%d deface record(s), %d defaced, %d error(s)",
                        length(deface_recs), deface_applied, deface_errs))
}

.acc_survivor_str <- function(metadata_survivors) {
  if (length(metadata_survivors) == 0) return("")
  parts <- vapply(names(metadata_survivors), function(cat) {
    vals <- unlist(metadata_survivors[[cat]], use.names = FALSE)
    sprintf("%s: %s", cat, paste(vals, collapse = ", "))
  }, character(1))
  paste(parts, collapse = "; ")
}

#' Turn raw pipeline results into the acceptance check table.
#'
#' @param raw list with elements `deid`, `survivors`, `residual`, `validity`,
#'   `reversibility`, `resume` (see `acceptance_run`).
#' @return list(checks = data.frame(name, passed, detail), passed = logical)
#' @export
acceptance_checks <- function(raw) {
  checks <- list()
  add <- function(name, passed, detail) {
    checks[[length(checks) + 1L]] <<- data.frame(
      name = name, passed = isTRUE(passed), detail = detail,
      stringsAsFactors = FALSE)
  }

  # 1. de-identification ran over every input
  d <- raw$deid
  add("de-identification ran",
      !is.null(d) && d$count > 0 && d$count == d$n_inputs,
      sprintf("%d of %d inputs produced output",
              d$count %||% 0L, d$n_inputs %||% 0L))

  # 2. metadata clean - no planted identifier survives literally
  s <- raw$survivors
  surv <- .acc_survivor_str(s$metadata_survivors %||% list())
  add("metadata clean (no planted PHI)", isTRUE(s$passed),
      if (nzchar(surv)) sprintf("survivors -> %s", surv)
      else "0 planted identifiers survived in output metadata")

  # 3. residual detector scan (metadata + pixels) passes
  r <- raw$residual
  cats <- r$by_category %||% list()
  catstr <- if (length(cats))
    paste(sprintf("%s=%d", names(cats), unlist(cats)), collapse = ", ") else "none"
  add("residual detector scan", isTRUE(r$passed),
      sprintf("flagged %d of %d files; categories: %s",
              r$summary$flagged %||% 0L, r$summary$scanned %||% 0L, catstr))

  # 4. outputs are valid, re-readable objects
  v <- raw$validity
  bad <- v$invalid %||% character(0)
  add("outputs are valid", isTRUE(v$ok),
      if (length(bad)) sprintf("%d invalid: %s", length(bad),
                               paste(basename(bad), collapse = ", "))
      else "all outputs re-read as valid")

  # 5. reversibility policy honoured
  rv <- raw$reversibility
  if (identical(rv$mode, "irreversible")) {
    add("reversibility policy (irreversible)", !isTRUE(rv$crosswalk_present),
        if (isTRUE(rv$crosswalk_present))
          "a crosswalk was persisted in irreversible mode (must not be)"
        else "no crosswalk persisted, as required")
  } else {
    add("reversibility policy (reversible)", isTRUE(rv$roundtrip_ok),
        if (isTRUE(rv$roundtrip_ok)) "crosswalk round-trips pseudonym -> original"
        else "crosswalk did not round-trip")
  }

  # 6. resumable - a second run reprocesses nothing
  rs <- raw$resume
  add("resumable (no reprocessing)", (rs$reprocessed %||% 0L) == 0L,
      sprintf("second run reprocessed %d files", rs$reprocessed %||% 0L))

  # 7. defacing ran or degraded gracefully (never silently errored). Absence of
  # a `deface` signal (older raw payloads, or no head-inclusive MR fixtures in
  # this raw) is treated as a vacuous pass, not a failure.
  dfc <- raw$deface
  add("defacing applied or gracefully skipped", isTRUE(dfc$ok %||% TRUE),
      dfc$detail %||% "no head-inclusive MR fixtures encountered")

  df <- do.call(rbind, checks)
  list(checks = df, passed = all(df$passed))
}

#' Render an acceptance report as markdown.
#' @export
acceptance_report_md <- function(report, title = "Acceptance report",
                                 meta = list()) {
  verdict <- if (isTRUE(report$passed)) "PASS" else "FAIL"
  lines <- c(
    sprintf("# %s - %s", title, verdict),
    "",
    sprintf("Generated: %s", format(Sys.time(), "%Y-%m-%d %H:%M:%S")),
    "")
  if (length(meta)) {
    lines <- c(lines, vapply(names(meta),
                             function(k) sprintf("- **%s:** %s", k, meta[[k]]),
                             character(1)), "")
  }
  lines <- c(lines, "| Check | Result | Detail |", "|---|---|---|")
  for (i in seq_len(nrow(report$checks))) {
    row <- report$checks[i, ]
    lines <- c(lines, sprintf("| %s | %s | %s |", row$name,
                              if (row$passed) "PASS" else "FAIL", row$detail))
  }
  lines <- c(lines, "",
             sprintf("**Overall: %s** (%d of %d checks passed)", verdict,
                     sum(report$checks$passed), nrow(report$checks)))
  paste(lines, collapse = "\n")
}

# --- live orchestration ----------------------------------------------------

.acc_meta_value <- function(md, keyword) {
  hit <- Filter(function(r) identical(r$keyword, keyword), md$rows)
  if (length(hit)) hit[[1]]$value else NA_character_
}

#' Run the full acceptance suite against the synthetic corpus (live engine).
#'
#' Builds the planted-PHI corpus, de-identifies it, and gathers the raw results
#' the pure `acceptance_checks` grades: no planted identifier survives (literal
#' + detector scan), outputs are valid, the reversibility policy is honoured,
#' and a second bulk pass reprocesses nothing. Writes `acceptance_report.md`.
#'
#' @param work_dir scratch directory (created if absent).
#' @param mode "reversible" or "irreversible".
#' @param passphrase keystore passphrase for the run.
#' @return list(report, raw, work_dir, report_path)
#' @export
acceptance_run <- function(work_dir = tempfile("acc_"),
                           profile_id = "default",
                           mode = c("reversible", "irreversible"),
                           passphrase = "acceptance") {
  mode <- match.arg(mode)
  dir.create(work_dir, showWarnings = FALSE, recursive = TRUE)
  corpus_dir <- file.path(work_dir, "corpus")
  out_dir    <- file.path(work_dir, "output")
  ks_path    <- if (mode == "reversible") file.path(work_dir, "keystore.json") else NULL

  man <- engine_build_corpus(corpus_dir)
  n_inputs <- length(man$fixtures)
  # Completeness is measured against actual input FILES (the corpus also writes a
  # ground-truth manifest, and de-id now processes JSON sidecars), so a silently
  # dropped file is still caught. `n_inputs` (fixtures) stays for the report meta.
  n_input_files <- length(list.files(corpus_dir, recursive = TRUE))

  # Auto-apply burned-in-pixel redaction for the acceptance run (simulating a
  # reviewer confirming every OCR-proposed box), so the residual scan validates
  # that planted pixel PHI is actually removed - not just that it would be
  # flagged for manual review. Production keeps human confirmation by default.
  rep <- engine_deid_run(corpus_dir, out_dir, profile_id = profile_id,
                         keystore_path = ks_path, passphrase = passphrase,
                         reversible = (mode == "reversible"),
                         autoredact_pixels = TRUE,
                         pdf_mode = "rasterize_redact",
                         deface = TRUE)
  outputs <- Filter(nzchar, vapply(rep$files, function(f) f$output %||% "",
                                   character(1)))

  # validity: DICOM outputs must re-read as valid headers; NIfTI + de-identified
  # JSON sidecars must exist and (for JSON) parse.
  invalid <- character(0)
  for (o in outputs) {
    if (grepl("[.]nii([.]gz)?$", o, ignore.case = TRUE)) {
      if (!file.exists(o) || file.size(o) == 0) invalid <- c(invalid, o)
    } else if (grepl("[.]json$", o, ignore.case = TRUE)) {
      ok <- tryCatch({ jsonlite::fromJSON(o); TRUE }, error = function(e) FALSE)
      if (!ok) invalid <- c(invalid, o)
    } else {
      ok <- tryCatch({ md <- engine_read_metadata(o, profile_id); length(md$rows) > 0 },
                     error = function(e) FALSE)
      if (!ok) invalid <- c(invalid, o)
    }
  }

  survivors <- engine_check_survivors(out_dir)
  scan <- engine_scan_residual_dir(out_dir, profile_id)

  # reversibility policy
  if (mode == "reversible") {
    summ <- engine_keystore_summary(ks_path, passphrase)
    sf <- file.path(out_dir, "single_frame.dcm")
    pid <- if (file.exists(sf))
      .acc_meta_value(engine_read_metadata(sf, profile_id), "PatientID") else NA
    orig <- tryCatch(engine_keystore_reverse(ks_path, passphrase, pid),
                     error = function(e) NULL)
    reversibility <- list(mode = mode,
                          roundtrip_ok = !is.null(orig) && !is.na(orig) && nzchar(orig),
                          crosswalk_present = isTRUE(summ$n_crosswalk > 0))
  } else {
    reversibility <- list(mode = mode, roundtrip_ok = NA, crosswalk_present = FALSE)
  }

  # resumability: two bulk passes over the corpus; the second must add nothing
  reprocessed <- tryCatch(.acc_resume_probe(work_dir, corpus_dir, profile_id),
                          error = function(e) NA_integer_)

  raw <- list(
    deid = list(count = rep$count %||% length(outputs), n_inputs = n_input_files),
    survivors = survivors,
    residual = list(passed = isTRUE(scan$passed),
                    summary = scan$summary, by_category = scan$by_category),
    validity = list(ok = length(invalid) == 0, invalid = invalid),
    reversibility = reversibility,
    resume = list(reprocessed = reprocessed),
    deface = .acc_deface_signal(rep$files))

  report <- acceptance_checks(raw)
  md <- acceptance_report_md(report, title = "DICOM de-identification acceptance",
                             meta = list(mode = mode, corpus = corpus_dir,
                                         fixtures = n_inputs, profile = profile_id))
  report_path <- file.path(work_dir, "acceptance_report.md")
  writeLines(md, report_path)
  list(report = report, raw = raw, work_dir = work_dir, report_path = report_path,
       markdown = md)
}

.acc_resume_probe <- function(work_dir, corpus_dir, profile_id) {
  mp   <- file.path(work_dir, "resume.sqlite")
  outd <- file.path(work_dir, "resume_out")
  plog <- file.path(work_dir, "resume_log.jsonl")
  con <- manifest_open(mp)
  register_batch(con, "acc", corpus_dir, outd, profile_id = profile_id,
                 created_by = "acceptance")
  manifest_close(con)
  run_batch(mp, "acc", workers = 1L, root_in = corpus_dir, root_out = outd,
            processed_log = plog)
  n1 <- nrow(processed_log_read(plog))
  run_batch(mp, "acc", workers = 1L, root_in = corpus_dir, root_out = outd,
            processed_log = plog)
  n2 <- nrow(processed_log_read(plog))
  as.integer(n2 - n1)
}
