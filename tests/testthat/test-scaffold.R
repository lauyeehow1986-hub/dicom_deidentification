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

test_that("records_to_df surfaces text-scan detections in a Detected column", {
  recs <- list(
    list(tag = 0x00082111L, keyword = "DerivationDescription", action = "C",
         original = "NRIC S1234567D", result = "NRIC ",
         categories = list("nric_fin", "phone")),
    list(tag = 0x00100010L, keyword = "PatientName", action = "D",
         original = "Tan Wei Ming", result = "ANON^AB12")
  )
  df <- records_to_df(recs)
  expect_true("Detected" %in% names(df))
  expect_identical(df$Detected[df$Field == "DerivationDescription"], "nric_fin, phone")
  expect_identical(df$Detected[df$Field == "PatientName"], "")  # no detections -> blank
})

test_that("tagging categories are read from the identifier catalog", {
  ch <- tagging_categories()
  expect_true(length(ch) >= 15)
  expect_true(all(c("names", "national_id", "telephone") %in% unname(ch)))
  expect_false(any(!nzchar(unname(ch))))  # no empty ids
})

test_that("Phase 4 module UIs build without error", {
  expect_error(mod_rules_editor_ui("rules"), NA)
  expect_error(mod_tagging_ui("tagging"), NA)
})

test_that("top-level UI builds without error", {
  expect_error(app_ui(), NA)
})
