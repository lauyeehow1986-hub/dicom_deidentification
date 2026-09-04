# engine_deid_run resolves call_engine lexically in the global environment
# (tests/testthat.R sources R/*.R there). Swap that binding to capture the
# forwarded args, and restore it on exit. No package load / pkgload needed,
# consistent with the rest of this suite.
test_that("engine_deid_run forwards deface = TRUE as the last positional arg", {
  called <- NULL
  orig <- call_engine
  assign("call_engine",
         function(fn, ...) { called <<- list(fn = fn, args = list(...)); list(count = 0) },
         envir = globalenv())
  on.exit(assign("call_engine", orig, envir = globalenv()), add = TRUE)

  engine_deid_run("in", "out", profile_id = "default", deface = TRUE)

  expect_equal(called$fn, "deid_run")
  # deface is the last positional arg
  n <- length(called$args)
  expect_equal(called$args[[n]], TRUE)
})

test_that("engine_deid_run defaults deface to FALSE as the last positional arg", {
  called <- NULL
  orig <- call_engine
  assign("call_engine",
         function(fn, ...) { called <<- list(fn = fn, args = list(...)); list(count = 0) },
         envir = globalenv())
  on.exit(assign("call_engine", orig, envir = globalenv()), add = TRUE)

  engine_deid_run("in", "out", profile_id = "default")

  expect_equal(called$fn, "deid_run")
  # deface is the last positional arg
  n <- length(called$args)
  expect_equal(called$args[[n]], FALSE)
})
