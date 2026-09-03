# Phase 7 - reproducible bundle manifest + verifier. Pure R (digest), so no
# venv/DICOM: a temp tree stands in for a staged bundle.

make_tree <- function() {
  root <- tempfile("bundle_"); dir.create(root)
  writeLines("app", file.path(root, "app.R"))
  dir.create(file.path(root, "R"))
  writeLines("code", file.path(root, "R", "acceptance.R"))
  dir.create(file.path(root, "inst", "python", "__pycache__"), recursive = TRUE)
  writeLines("x", file.path(root, "inst", "python", "core.py"))
  writeLines("junk", file.path(root, "inst", "python", "__pycache__", "core.pyc"))
  root
}

test_that("bundle_manifest checksums every file, skipping caches", {
  root <- make_tree()
  m <- bundle_manifest(root)
  expect_true(all(c("path", "sha256", "bytes") %in% names(m)))
  # forward-slash relative paths, sorted, no __pycache__
  expect_true("inst/python/core.py" %in% m$path)
  expect_false(any(grepl("__pycache__", m$path)))
  # sha256 matches an independent digest of the file
  row <- m[m$path == "app.R", ]
  expect_identical(row$sha256,
                   digest::digest(file.path(root, "app.R"), algo = "sha256",
                                  file = TRUE))
  expect_gt(row$bytes, 0)
})

test_that("bundle_write_manifest + bundle_verify round-trip clean", {
  root <- make_tree()
  mp <- bundle_write_manifest(root)
  expect_true(file.exists(mp))
  v <- bundle_verify(root, mp)
  expect_true(v$ok)
  expect_length(v$missing, 0)
  expect_length(v$changed, 0)
  expect_length(v$extra, 0)
})

test_that("bundle_verify detects a changed file", {
  root <- make_tree()
  mp <- bundle_write_manifest(root)
  writeLines("tampered", file.path(root, "R", "acceptance.R"))
  v <- bundle_verify(root, mp)
  expect_false(v$ok)
  expect_true("R/acceptance.R" %in% v$changed)
})

test_that("bundle_verify detects a missing and an extra file", {
  root <- make_tree()
  mp <- bundle_write_manifest(root)
  file.remove(file.path(root, "app.R"))
  writeLines("new", file.path(root, "R", "extra.R"))
  v <- bundle_verify(root, mp)
  expect_false(v$ok)
  expect_true("app.R" %in% v$missing)
  expect_true("R/extra.R" %in% v$extra)
})

test_that("bundle_components lists required parts and flags secrets", {
  comp <- bundle_components()
  expect_true(all(c("component", "required", "secret") %in% names(comp)))
  # the two portable runtimes are required, non-secret
  expect_true(any(grepl("venv", comp$component, ignore.case = TRUE) & comp$required))
  # the global keystore + signing keys are flagged as secrets
  secrets <- comp$component[comp$secret]
  expect_true(any(grepl("keystore", secrets, ignore.case = TRUE)))
  expect_true(any(grepl("sign", secrets, ignore.case = TRUE)))
})
