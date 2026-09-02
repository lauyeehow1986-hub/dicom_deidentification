# Phase 5 - bulk engine: SQLite manifest job queue + resumable, parallel drain.
# The manifest layer is pure SQLite (deterministic); the worker body (`drain`)
# takes an injected de-id function so these tests need no reticulate/DICOM.

# --- helpers ---------------------------------------------------------------
new_manifest <- function() {
  p <- tempfile(fileext = ".sqlite")
  con <- manifest_open(p)
  list(con = con, path = p)
}

make_tree <- function(files) {
  root <- tempfile("in_"); dir.create(root)
  for (f in files) {
    fp <- file.path(root, f)
    dir.create(dirname(fp), showWarnings = FALSE, recursive = TRUE)
    writeLines("x", fp)
  }
  root
}

# --- scanning --------------------------------------------------------------
test_that("manifest_scan_files finds DICOM/NIfTI recursively and skips others", {
  root <- make_tree(c("a.dcm", "sub/b.dcm", "sub/deep/c.nii", "d.nii.gz",
                      "notes.txt", "readme.md"))
  found <- manifest_scan_files(root)
  expect_setequal(basename(found), c("a.dcm", "b.dcm", "c.nii", "d.nii.gz"))
})

# --- registration + idempotency (resumable re-scan) ------------------------
test_that("register_batch inserts pending rows with mirrored output paths", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "sub/b.dcm"))
  out  <- tempfile("out_")
  res <- register_batch(m$con, "batch1", root, out, profile_id = "echo")
  expect_identical(res$inserted, 2L)
  expect_identical(res$total, 2L)
  rows <- DBI::dbGetQuery(m$con, "SELECT * FROM files ORDER BY input_path")
  expect_true(all(rows$status == "pending"))
  # output path mirrors the input's relative location under the output root
  bsub <- rows[basename(rows$input_path) == "b.dcm", ]
  expect_identical(normalizePath(dirname(bsub$output_path), mustWork = FALSE),
                   normalizePath(file.path(out, "sub"),      mustWork = FALSE))
  bat <- DBI::dbGetQuery(m$con, "SELECT * FROM batches WHERE batch_id='batch1'")
  expect_identical(bat$profile_id, "echo")
  expect_identical(bat$total, 2L)
})

test_that("register_batch is idempotent - re-registering adds only new files", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm"))
  out  <- tempfile("out_")
  register_batch(m$con, "b", root, out)
  # add a second file and re-register the same batch
  writeLines("x", file.path(root, "b.dcm"))
  res2 <- register_batch(m$con, "b", root, out)
  expect_identical(res2$inserted, 1L)   # only the new one
  expect_identical(res2$total, 2L)
  n <- DBI::dbGetQuery(m$con, "SELECT COUNT(*) n FROM files")$n
  expect_identical(as.integer(n), 2L)
})

# --- claiming (no double-claim) -------------------------------------------
test_that("claim_next hands each pending row to exactly one worker", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  c1 <- claim_next(m$con, "w1")
  c2 <- claim_next(m$con, "w2")
  c3 <- claim_next(m$con, "w3")
  expect_false(is.null(c1)); expect_false(is.null(c2))
  expect_true(is.null(c3))                       # nothing left
  expect_false(identical(c1$id, c2$id))          # distinct rows
  st <- DBI::dbGetQuery(m$con, "SELECT status FROM files")$status
  expect_true(all(st == "in_progress"))
})

# --- terminal transitions + progress --------------------------------------
test_that("mark_done / mark_failed record terminal state and progress counts", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  r1 <- claim_next(m$con, "w1"); mark_done(m$con, r1$id, residual_count = 0L)
  r2 <- claim_next(m$con, "w1"); mark_failed(m$con, r2$id, "boom")
  prog <- progress_summary(m$con, "b")
  expect_identical(prog$total,   2L)
  expect_identical(prog$done,    1L)
  expect_identical(prog$failed,  1L)
  expect_identical(prog$pending, 0L)
  err <- DBI::dbGetQuery(m$con, "SELECT error FROM files WHERE status='failed'")$error
  expect_identical(err, "boom")
})

# --- crash recovery: stale in_progress -> pending on resume ----------------
test_that("reset_stale re-queues rows a crashed worker left in_progress", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  claim_next(m$con, "w1"); claim_next(m$con, "w1")   # both in_progress, "crash"
  n <- reset_stale(m$con, "b")
  expect_identical(n, 2L)
  expect_identical(progress_summary(m$con, "b")$pending, 2L)
})

# --- the worker body: drain (serial, injected engine fn) -------------------
test_that("drain processes every pending row via the injected de-id fn", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "sub/b.dcm"))
  out  <- tempfile("out_")
  register_batch(m$con, "b", root, out, profile_id = "echo")
  seen <- new.env(); seen$paths <- character()
  fake <- function(input_path, output_path, profile_id, keystore_path, passphrase) {
    seen$paths <- c(seen$paths, input_path)
    expect_identical(profile_id, "echo")          # profile threaded through
    list(count = 1L, residual_count = 0L)
  }
  n <- drain(m$con, deid_fn = fake, worker = "w1")
  expect_identical(n, 2L)                          # processed 2
  expect_identical(length(seen$paths), 2L)
  prog <- progress_summary(m$con, "b")
  expect_identical(prog$done, 2L)
  expect_identical(prog$pending, 0L)
})

test_that("drain marks a row failed when the de-id fn throws, and continues", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm", "c.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  boom_on <- basename(DBI::dbGetQuery(m$con,
    "SELECT input_path FROM files ORDER BY input_path LIMIT 1")$input_path)
  fake <- function(input_path, output_path, profile_id, keystore_path, passphrase) {
    if (basename(input_path) == boom_on) stop("kaboom")
    list(count = 1L)
  }
  drain(m$con, deid_fn = fake, worker = "w1")
  prog <- progress_summary(m$con, "b")
  expect_identical(prog$failed, 1L)                # the one that threw
  expect_identical(prog$done, 2L)                  # the rest still processed
})

test_that("drain is resumable - a second pass does no work once all are done", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  fake <- function(...) list(count = 1L)
  first  <- drain(m$con, deid_fn = fake, worker = "w1")
  second <- drain(m$con, deid_fn = fake, worker = "w1")
  expect_identical(first,  2L)
  expect_identical(second, 0L)                     # nothing left to do
})

# --- run_batch orchestration (serial path, injected fn) --------------------
test_that("run_batch resumes stale rows then drains to completion", {
  p <- tempfile(fileext = ".sqlite")
  con <- manifest_open(p)
  root <- make_tree(c("a.dcm", "b.dcm", "c.dcm"))
  register_batch(con, "b", root, tempfile("out_"), profile_id = "echo")
  # simulate a crash: one row stuck in_progress, one already done
  r <- claim_next(con, "dead"); # left in_progress
  d <- claim_next(con, "dead"); mark_done(con, d$id, residual_count = 0L)
  manifest_close(con)

  fake <- function(input_path, output_path, profile_id, keystore_path, passphrase)
    list(count = 1L, residual_count = 0L)
  prog <- run_batch(p, batch_id = "b", workers = 1L, deid_fn = fake)
  expect_identical(prog$total, 3L)
  expect_identical(prog$done, 3L)          # stale requeued + remaining processed
  expect_identical(prog$pending, 0L)
  expect_identical(prog$in_progress, 0L)
})

# --- dashboard readers -----------------------------------------------------
test_that("manifest_recent and manifest_failures feed the progress dashboard", {
  m <- new_manifest(); on.exit(manifest_close(m$con))
  root <- make_tree(c("a.dcm", "b.dcm", "c.dcm"))
  register_batch(m$con, "b", root, tempfile("out_"))
  r1 <- claim_next(m$con, "w1"); mark_done(m$con, r1$id, residual_count = 0L)
  r2 <- claim_next(m$con, "w1"); mark_failed(m$con, r2$id, "bad transfer syntax")
  rec <- manifest_recent(m$con, "b", n = 10)
  expect_true(all(c("input_path", "status", "worker") %in% names(rec)))
  expect_true(nrow(rec) >= 2)
  fails <- manifest_failures(m$con, "b")
  expect_identical(nrow(fails), 1L)
  expect_identical(fails$error, "bad transfer syntax")
})

# --- reversible batches are forced single-worker (keystore safety) ---------
test_that("bulk_effective_workers caps reversible runs at one worker", {
  expect_identical(bulk_effective_workers(4L, NULL), 4L)          # irreversible: as asked
  expect_identical(bulk_effective_workers(4L, ""), 4L)           # empty path == none
  expect_identical(bulk_effective_workers(4L, "k.keystore"), 1L) # reversible: serialised
  expect_identical(bulk_effective_workers(1L, "k.keystore"), 1L)
  expect_identical(bulk_effective_workers(0L, NULL), 1L)          # floor at 1
})
