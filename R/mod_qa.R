#' Quality-assurance & review workflow (reviewer role).
#' Phase 6.

mod_qa_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "QA / review", "Phase 6",
    bullets = c(
      "Automated residual scan: re-run all detectors on OUTPUTS; flag leftover PHI per category.",
      "Manual sampling review through the image + metadata viewer.",
      "Pass/fail report with per-identifier recall; reviewer sign-off (gated, audited).",
      "De-identifier cannot self-approve — reviewer approval required."
    )
  )
}

mod_qa_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 6): residual scan + sampling review + gated sign-off -> audit log.
    invisible(NULL)
  })
}
