# Phase 6 - QA & governance: residual-scan aggregation, the two-role sign-off
# gate, and the hash-chained audit log. Pure R (an injected fake stands in for
# the reticulate residual scanner), so these need no venv/DICOM.

# --- helpers ---------------------------------------------------------------
with_ws <- function(code) {
  ws <- tempfile("ws_"); dir.create(ws)
  old <- Sys.getenv("DICOMDEID_WORKSPACE", unset = NA)
  Sys.setenv(DICOMDEID_WORKSPACE = ws)
  on.exit({
    if (is.na(old)) Sys.unsetenv("DICOMDEID_WORKSPACE")
    else Sys.setenv(DICOMDEID_WORKSPACE = old)
  }, add = TRUE)
  force(code)
}

fake_scan <- function(path) {
  # A tiny stand-in for engine scan_residual: flags files whose name says "leak".
  if (grepl("leak", basename(path))) {
    list(path = path, passed = FALSE,
         findings = list(list(location = "metadata", tag = 0x00082111,
                              keyword = "DerivationDescription", category = "nric_fin",
                              source = "sg", score = 1.0, preview = "S•••••••D")),
         by_category = list(nric_fin = 1L),
         counts = list(total = 1L, metadata = 1L, pixels = 0L),
         identity_removed = TRUE, notes = list())
  } else {
    list(path = path, passed = TRUE, findings = list(), by_category = list(),
         counts = list(total = 0L, metadata = 0L, pixels = 0L),
         identity_removed = TRUE, notes = list())
  }
}

# --- audit log: hash-chained, append-only ----------------------------------
test_that("audit_append writes a hash-chained entry and audit_read returns it", {
  with_ws({
    e1 <- audit_append("deid_run", actor = "alice", role = "deidentifier",
                       details = list(batch_id = "b1"))
    e2 <- audit_append("signoff", actor = "bob", role = "reviewer",
                       details = list(batch_id = "b1", decision = "pass"))
    expect_identical(e1$seq, 1L)
    expect_identical(e2$seq, 2L)
    expect_identical(e2$prev_hash, e1$hash)   # chained
    df <- audit_read()
    expect_equal(nrow(df), 2L)
    expect_identical(df$action, c("deid_run", "signoff"))
  })
})

test_that("audit_verify accepts an intact chain and rejects tampering", {
  with_ws({
    audit_append("deid_run", "alice", "deidentifier", list(batch_id = "b1"))
    audit_append("signoff", "bob", "reviewer", list(batch_id = "b1"))
    expect_true(audit_verify()$ok)

    # Tamper with the first line's actor without fixing the hash chain.
    path <- audit_file()
    lines <- readLines(path)
    lines[[1]] <- sub("\"actor\":\"alice\"", "\"actor\":\"mallory\"", lines[[1]])
    writeLines(lines, path)
    v <- audit_verify()
    expect_false(v$ok)
    expect_identical(v$broken_at, 1L)
  })
})

# --- two-role sign-off gate ------------------------------------------------
test_that("the de-identifier cannot sign off their own batch", {
  g <- can_sign_off(deidentifier = "alice", reviewer = "alice",
                    reviewer_role = "reviewer")
  expect_false(g$ok)
  expect_match(g$reason, "cannot sign off", ignore.case = TRUE)
})

test_that("a non-reviewer cannot sign off", {
  g <- can_sign_off(deidentifier = "alice", reviewer = "carol",
                    reviewer_role = "deidentifier")
  expect_false(g$ok)
})

test_that("a distinct reviewer can sign off and it is audited", {
  with_ws({
    g <- can_sign_off("alice", "bob", "reviewer")
    expect_true(g$ok)
    e <- record_signoff(batch_id = "b1", deidentifier = "alice",
                        reviewer = "bob", reviewer_role = "reviewer",
                        decision = "pass", note = "sampled 20, clean")
    expect_identical(e$action, "signoff")
    df <- audit_read()
    expect_true("signoff" %in% df$action)
  })
})

test_that("record_signoff refuses self-approval (no audit entry written)", {
  with_ws({
    expect_error(
      record_signoff("b1", deidentifier = "alice", reviewer = "alice",
                     reviewer_role = "reviewer", decision = "pass"),
      "own batch")
    expect_false(file.exists(audit_file()))
  })
})

# --- credential store ------------------------------------------------------
test_that("verify_user returns the role on a correct password and NULL otherwise", {
  store <- list(make_user("bob", "s3cr3t-pw", "reviewer"))
  expect_identical(verify_user(store, "bob", "s3cr3t-pw"), "reviewer")
  expect_null(verify_user(store, "bob", "wrong"))
  expect_null(verify_user(store, "nobody", "s3cr3t-pw"))
})

# --- residual-scan aggregation ---------------------------------------------
test_that("residual_scan_paths aggregates per-file verdicts and categories", {
  paths <- c("/out/clean_a.dcm", "/out/leak_b.dcm", "/out/clean_c.dcm")
  res <- residual_scan_paths(paths, scan_fn = fake_scan)
  expect_identical(res$n_files, 3L)
  expect_identical(res$n_flagged, 1L)
  expect_identical(res$n_passed, 2L)
  expect_false(res$passed)                       # batch fails if any file flags
  expect_identical(res$by_category[["nric_fin"]], 1L)
  fdf <- res$files_df
  expect_equal(nrow(fdf), 3L)
  expect_true(all(c("File", "Findings", "Verdict") %in% names(fdf)))
})

test_that("qa_findings_df masks previews and never carries a raw identifier", {
  res <- residual_scan_paths(c("/out/leak_b.dcm"), scan_fn = fake_scan)
  fd <- qa_findings_df(res$results)
  expect_true(nrow(fd) >= 1)
  expect_true(all(c("File", "Category", "Preview") %in% names(fd)))
  expect_false(any(grepl("S1234567D", fd$Preview, fixed = TRUE)))
})

# --- manifest QA flagging --------------------------------------------------
test_that("set_residual flags a manifest row when residual PHI is found", {
  p <- tempfile(fileext = ".sqlite")
  con <- manifest_open(p); on.exit(manifest_close(con))
  root <- tempfile("in_"); dir.create(root); writeLines("x", file.path(root, "a.dcm"))
  out <- tempfile("out_")
  register_batch(con, "b1", root, out)
  row <- DBI::dbGetQuery(con, "SELECT output_path FROM files LIMIT 1")
  set_residual(con, row$output_path[[1]], residual_count = 2L)
  st <- DBI::dbGetQuery(con, "SELECT status, residual_count FROM files LIMIT 1")
  expect_identical(st$status, "flagged")
  expect_identical(as.integer(st$residual_count), 2L)

  set_residual(con, row$output_path[[1]], residual_count = 0L)
  st2 <- DBI::dbGetQuery(con, "SELECT status FROM files LIMIT 1")
  expect_identical(st2$status, "done")
})
