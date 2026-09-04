# engine_deid_run resolves call_engine lexically in the global environment
# (tests/testthat.R sources R/*.R there). Swap that binding to capture the
# forwarded args, and restore it on exit. No package load / pkgload needed,
# consistent with the rest of this suite.
test_that("engine_deid_run forwards pdf_mode/pdf_dpi to the engine", {
  called <- NULL
  orig <- call_engine
  assign("call_engine",
         function(fn, ...) { called <<- list(fn = fn, args = list(...)); list(count = 0) },
         envir = globalenv())
  on.exit(assign("call_engine", orig, envir = globalenv()), add = TRUE)

  engine_deid_run("in", "out", profile_id = "default",
                  pdf_mode = "rasterize_redact", pdf_dpi = 120)

  expect_equal(called$fn, "deid_run")
  # positional order forwarded to call_engine (after fn): in, out, profile_id,
  # keystore_path, passphrase, reversible, sign_key_path, signer, project_id,
  # autoredact_pixels, pdf_mode, pdf_dpi, deface
  expect_equal(length(called$args), 13L)
  expect_equal(called$args[[11]], "rasterize_redact")  # pdf_mode
  expect_equal(called$args[[12]], 120)                 # pdf_dpi
})
