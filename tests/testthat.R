library(testthat)
# Scaffold-stage tests source the app modules directly (no install step).
for (f in list.files("R", pattern = "\\.R$", full.names = TRUE)) source(f)
test_check_dir <- "testthat"
testthat::test_dir(file.path("tests", test_check_dir))
