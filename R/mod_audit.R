#' Audit log: an append-only record of every de-identification and review action.
#' Phase 6.

mod_audit_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Audit log", "Phase 6",
    bullets = c(
      "Append-only log: who ran what profile on which batch, and every reviewer sign-off.",
      "Filter/search; export for governance.",
      "Tamper-evident (hash-chained entries)."
    )
  )
}

mod_audit_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 6): append-only, hash-chained audit store + viewer.
    invisible(NULL)
  })
}
