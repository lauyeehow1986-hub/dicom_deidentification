test_that("default profile loads and references the catalog", {
  p <- load_profile("default")
  expect_type(p, "list")
  expect_identical(p$profile_id, "default")
  expect_true(!is.null(p$options))
})

test_that("identifier catalog parses and covers the 15 SingHealth categories", {
  path <- profile_path("identifier_catalog.yml")
  expect_true(!is.null(path) && file.exists(path))
  cat <- yaml::read_yaml(path)
  ids <- vapply(cat$categories, function(c) c$id, character(1))
  # 15 direct-identifier categories (uid_remap is a separate integrity section).
  expect_gte(length(ids), 15)
  expect_true(all(c("names", "national_id", "mrn", "linked_dates") %in% ids))
})

test_that("engine bridge degrades gracefully when the venv is absent", {
  # Force the 'no venv' path; the app must still be constructible.
  old <- Sys.getenv("DICOMDEID_VENV", unset = NA)
  on.exit({
    if (is.na(old)) Sys.unsetenv("DICOMDEID_VENV") else Sys.setenv(DICOMDEID_VENV = old)
    if (exists("handle", envir = .engine_env)) rm("handle", envir = .engine_env)
  }, add = TRUE)

  Sys.setenv(DICOMDEID_VENV = file.path(tempdir(), "no_such_venv"))
  if (exists("handle", envir = .engine_env)) rm("handle", envir = .engine_env)  # clear cache
  h <- engine_handle()
  expect_false(h$available)
  expect_type(h$reason, "character")
})

test_that("top-level UI builds without error", {
  expect_error(app_ui(), NA)
})
