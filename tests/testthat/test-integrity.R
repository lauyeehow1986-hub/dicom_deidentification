# Phase 6.5 - manifest integrity: relative paths so a batch resumes after a
# portable-disk swap, and before/after checksums recorded per file.

test_that("processed_log append/read round-trips one line per file", {
  lg <- tempfile(fileext = ".jsonl")
  processed_log_append(lg, list(rel_in = "a/x.dcm", input_sha256 = strrep("a", 64),
                                output_sha256 = strrep("b", 64), status = "done"))
  processed_log_append(lg, list(rel_in = "b/y.dcm", status = "flagged",
                                residual_count = 2L))
  df <- processed_log_read(lg)
  expect_equal(nrow(df), 2L)
  expect_setequal(df$rel_in, c("a/x.dcm", "b/y.dcm"))
  expect_identical(df$status[df$rel_in == "b/y.dcm"], "flagged")
})

test_that("signature_status_df reports OK / FAIL per output with signer metadata", {
  d <- tempfile("out_"); dir.create(d)
  good <- file.path(d, "good.dcm"); writeLines("x", good)
  writeLines(jsonlite::toJSON(list(signed_by = "yh", signed_at = "2026-09-03T00:00:00",
                                   sha256 = strrep("a", 64)), auto_unbox = TRUE),
             paste0(good, ".sig.json"))
  bad <- file.path(d, "bad.dcm"); writeLines("y", bad)  # no/failed signature
  fake_verify <- function(out, pub) {
    if (grepl("good", out)) list(ok = TRUE, reason = "verified")
    else list(ok = FALSE, reason = "hash mismatch")
  }
  df <- signature_status_df(c(good, bad), pub_path = "pub.pem", verify_fn = fake_verify)
  expect_identical(df$Verify[df$File == "good.dcm"], "OK")
  expect_true(grepl("FAIL", df$Verify[df$File == "bad.dcm"]))
  expect_identical(df$SignedBy[df$File == "good.dcm"], "yh")
})

test_that("register_batch stores paths relative to the roots", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out",
                 files = c("E:/in/a/x.dcm", "E:/in/b/y.dcm"))
  rows <- DBI::dbGetQuery(con, "SELECT rel_in, rel_out, output_path FROM files ORDER BY rel_in")
  expect_setequal(rows$rel_in, c("a/x.dcm", "b/y.dcm"))
  expect_true(all(grepl("^F:/out/", rows$output_path)))
})

test_that("re-registering after a disk swap adds no duplicate rows", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out",
                 files = c("E:/in/a/x.dcm", "E:/in/b/y.dcm"))
  # a done file must survive the swap
  row <- claim_next(con, "w1", "b1"); mark_done(con, row$id)
  # same study, new drive letters, same relative layout
  res <- register_batch(con, "b1", "G:/in", "H:/out",
                        files = c("G:/in/a/x.dcm", "G:/in/b/y.dcm"))
  expect_identical(res$inserted, 0L)                 # matched on rel_in
  expect_identical(progress_summary(con, "b1")$done, 1L)
})

test_that("claim resolves the input against the CURRENT roots after a rebind", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out", files = c("E:/in/a/x.dcm"))
  # disk swapped to G:/ ; claim with the new root
  row <- claim_next(con, "w1", "b1", root_in = "G:/in", root_out = "H:/out")
  expect_identical(row$input_path, "G:/in/a/x.dcm")   # not the old E:/ path
  expect_identical(row$output_path, "H:/out/a/x.dcm")
})

test_that("mark_done records before/after checksums", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out", files = c("E:/in/a/x.dcm"))
  row <- claim_next(con, "w1", "b1")
  mark_done(con, row$id, input_sha256 = strrep("a", 64),
            output_sha256 = strrep("b", 64))
  got <- DBI::dbGetQuery(con, "SELECT input_sha256, output_sha256, status FROM files")
  expect_identical(got$input_sha256, strrep("a", 64))
  expect_identical(got$output_sha256, strrep("b", 64))
  expect_identical(got$status, "done")
})

test_that("drain writes a processing-log line per file when a log is given", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out", files = c("E:/in/a/x.dcm"))
  lg <- tempfile(fileext = ".jsonl")
  fake_deid <- function(input_path, output_path, ...)
    list(residual_count = 0L, input_sha256 = strrep("1", 64),
         output_sha256 = strrep("2", 64), signature_id = "sig1")
  drain(con, fake_deid, worker = "w1", batch_id = "b1",
        root_in = "G:/in", root_out = "H:/out", processed_log = lg)
  df <- processed_log_read(lg)
  expect_equal(nrow(df), 1L)
  expect_identical(df$rel_in, "a/x.dcm")
  expect_identical(df$output_sha256, strrep("2", 64))
  expect_identical(df$status, "done")
})

test_that("drain resolves current roots and threads checksums from the report", {
  con <- manifest_open(tempfile(fileext = ".sqlite")); on.exit(manifest_close(con))
  register_batch(con, "b1", "E:/in", "F:/out", files = c("E:/in/a/x.dcm"))
  seen <- new.env()
  fake_deid <- function(input_path, output_path, ...) {
    seen$in_path <- input_path                         # capture what drain passed
    list(residual_count = 0L,
         input_sha256 = strrep("1", 64), output_sha256 = strrep("2", 64))
  }
  drain(con, fake_deid, worker = "w1", batch_id = "b1",
        root_in = "G:/in", root_out = "H:/out")
  expect_identical(seen$in_path, "G:/in/a/x.dcm")       # resolved to new drive
  got <- DBI::dbGetQuery(con, "SELECT input_sha256, output_sha256, status FROM files")
  expect_identical(got$output_sha256, strrep("2", 64))
  expect_identical(got$status, "done")
})
