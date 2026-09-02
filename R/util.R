#' Load a de-identification profile (YAML) from inst/profiles/.
#'
#' @param profile_id e.g. "default"
#' @return a named list (the parsed profile), or a minimal stub if not found.
load_profile <- function(profile_id = "default") {
  path <- profile_path(paste0(profile_id, "_profile.yml"))
  if (is.null(path) || !file.exists(path)) {
    return(list(profile_id = profile_id, catalog = NULL, options = list()))
  }
  yaml::read_yaml(path)
}

#' Resolve a file under inst/profiles/ whether running from source or installed,
#' and regardless of the current working directory (tests run from tests/testthat).
profile_path <- function(file) {
  root <- app_root()
  if (!is.null(root)) {
    p <- file.path(root, "inst", "profiles", file)
    if (file.exists(p)) return(p)
  }
  # installed-package layout
  p2 <- system.file("profiles", file, package = "dicomdeid")
  if (nzchar(p2)) return(p2)
  NULL
}

#' Find the app/source root by walking up from the working directory (and from
#' this file's own location) until a directory containing DESCRIPTION + inst/ is
#' found. Returns NULL if none is located.
app_root <- function(start = getwd()) {
  candidates <- unique(c(start, tryCatch(dirname(dirname(getwd())), error = function(e) NULL)))
  for (s in candidates) {
    dir <- s
    for (i in seq_len(6)) {
      if (file.exists(file.path(dir, "DESCRIPTION")) &&
          dir.exists(file.path(dir, "inst"))) {
        return(dir)
      }
      parent <- dirname(dir)
      if (identical(parent, dir)) break
      dir <- parent
    }
  }
  NULL
}

#' A consistent placeholder card used by scaffold-stage module tabs.
placeholder_panel <- function(title, phase, bullets = character()) {
  bslib::card(
    bslib::card_header(shiny::tagList(shiny::strong(title),
                                      shiny::span(class = "badge bg-info ms-2", phase))),
    bslib::card_body(
      shiny::p(shiny::em("Scaffold placeholder — implemented in a later phase.")),
      if (length(bullets)) shiny::tags$ul(lapply(bullets, shiny::tags$li))
    )
  )
}
