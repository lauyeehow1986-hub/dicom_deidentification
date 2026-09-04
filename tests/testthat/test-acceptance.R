# Phase 7 - acceptance runner. The pure core turns raw pipeline results into a
# pass/fail check table + markdown report. No venv/DICOM here: the raw results
# are hand-built, so failure modes can be exercised directly.

good_raw <- function() list(
  deid = list(count = 4L, n_inputs = 4L),
  survivors = list(passed = TRUE, metadata_survivors = list()),
  residual = list(passed = TRUE,
                  summary = list(scanned = 4L, passed = 4L, flagged = 0L),
                  by_category = list()),
  validity = list(ok = TRUE, invalid = character(0)),
  reversibility = list(mode = "reversible", roundtrip_ok = TRUE,
                       crosswalk_present = TRUE),
  resume = list(reprocessed = 0L)
)

test_that("all-good raw yields an overall pass with every check green", {
  rep <- acceptance_checks(good_raw())
  expect_true(rep$passed)
  expect_true(all(rep$checks$passed))
  expect_true(nrow(rep$checks) >= 6)
})

test_that("a surviving planted identifier fails the metadata-clean check", {
  raw <- good_raw()
  raw$survivors <- list(passed = FALSE,
                        metadata_survivors = list(names = list("Tan Wei Ming")))
  rep <- acceptance_checks(raw)
  expect_false(rep$passed)
  row <- rep$checks[grepl("metadata", rep$checks$name, ignore.case = TRUE), ]
  expect_false(row$passed)
  expect_match(row$detail, "Tan Wei Ming")
})

test_that("a flagged residual scan fails the scan check", {
  raw <- good_raw()
  raw$residual <- list(passed = FALSE,
                       summary = list(scanned = 4L, passed = 3L, flagged = 1L),
                       by_category = list(nric_fin = 2L))
  rep <- acceptance_checks(raw)
  expect_false(rep$passed)
  expect_false(rep$checks$passed[grepl("residual", rep$checks$name, ignore.case = TRUE)])
})

test_that("irreversible mode must NOT persist a crosswalk", {
  raw <- good_raw()
  raw$reversibility <- list(mode = "irreversible", roundtrip_ok = NA,
                            crosswalk_present = TRUE)
  rep <- acceptance_checks(raw)
  expect_false(rep$checks$passed[grepl("reversib", rep$checks$name, ignore.case = TRUE)])

  raw$reversibility$crosswalk_present <- FALSE
  rep2 <- acceptance_checks(raw)
  expect_true(rep2$checks$passed[grepl("reversib", rep2$checks$name, ignore.case = TRUE)])
})

test_that("reprocessing on a second run fails the resumable check", {
  raw <- good_raw()
  raw$resume <- list(reprocessed = 2L)
  rep <- acceptance_checks(raw)
  expect_false(rep$checks$passed[grepl("resum", rep$checks$name, ignore.case = TRUE)])
})

test_that("incomplete de-id (fewer outputs than inputs) fails", {
  raw <- good_raw()
  raw$deid <- list(count = 3L, n_inputs = 4L)
  rep <- acceptance_checks(raw)
  expect_false(rep$checks$passed[grepl("de-identif", rep$checks$name, ignore.case = TRUE)])
})

test_that("report renders markdown with an overall verdict and a row per check", {
  rep <- acceptance_checks(good_raw())
  md <- acceptance_report_md(rep, title = "Acceptance")
  expect_true(grepl("PASS", md))
  expect_true(grepl("Acceptance", md))
  # one table row per check
  for (nm in rep$checks$name) expect_true(grepl(nm, md, fixed = TRUE))

  fail <- acceptance_checks(within(good_raw(), {
    survivors <- list(passed = FALSE,
                      metadata_survivors = list(email = list("patient@example.sg")))
  }))
  md2 <- acceptance_report_md(fail)
  expect_true(grepl("FAIL", md2))
  expect_true(grepl("patient@example.sg", md2))
})

test_that("acceptance_checks grades the deface signal", {
  raw <- list(
    deface = list(ok = TRUE, detail = "1 head-MR volume defaced"),
    deid = list(count = 1L, n_inputs = 1L),
    survivors = list(passed = TRUE, metadata_survivors = list()),
    residual = list(passed = TRUE, summary = list(flagged = 0L, scanned = 1L),
                    by_category = list()),
    validity = list(ok = TRUE, invalid = character(0)),
    reversibility = list(mode = "reversible", roundtrip_ok = TRUE,
                         crosswalk_present = TRUE),
    resume = list(reprocessed = 0L))
  rep <- acceptance_checks(raw)
  expect_true("defacing applied or gracefully skipped" %in% rep$checks$name)
  expect_true(rep$passed)
})

test_that("acceptance_checks fails deface when a deface_error occurred", {
  raw <- list(
    deface = list(ok = FALSE, detail = "1 deface record(s), 0 defaced, 1 error(s)"),
    deid = list(count = 1L, n_inputs = 1L),
    survivors = list(passed = TRUE, metadata_survivors = list()),
    residual = list(passed = TRUE, summary = list(flagged = 0L, scanned = 1L),
                    by_category = list()),
    validity = list(ok = TRUE, invalid = character(0)),
    reversibility = list(mode = "reversible", roundtrip_ok = TRUE,
                         crosswalk_present = TRUE),
    resume = list(reprocessed = 0L))
  rep <- acceptance_checks(raw)
  row <- rep$checks[rep$checks$name == "defacing applied or gracefully skipped", ]
  expect_false(row$passed)
  expect_false(rep$passed)
})

test_that(".acc_deface_signal fails (never crashes) on a deface_error-only file", {
  # Python sets exactly one of counts$deface / counts$deface_error. A file with
  # only deface_error must yield ok=FALSE, not crash via $-partial-matching.
  files <- list(list(counts = list(deface_error = "boom")))
  sig <- .acc_deface_signal(files)
  expect_false(sig$ok)
  expect_match(sig$detail, "1 error")
})

test_that(".acc_deface_signal counts records, defaced, and skips", {
  files <- list(
    list(counts = list(deface = list(defaced = TRUE))),
    list(counts = list(deface = list(defaced = FALSE, reason = "no-head-fov"))),
    list(counts = list()))                       # a file that never hit defacing
  sig <- .acc_deface_signal(files)
  expect_true(sig$ok)
  expect_match(sig$detail, "2 deface record\\(s\\), 1 defaced, 0 error")
})

test_that(".acc_deface_signal handles an empty file list", {
  sig <- .acc_deface_signal(list())
  expect_true(sig$ok)
  expect_match(sig$detail, "0 deface record\\(s\\), 0 defaced, 0 error")
})
