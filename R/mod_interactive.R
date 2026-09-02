#' Interactive de-identification of a single folder/study, with before/after review.
#' Phase 1 (metadata core) + Phase 3 (pixel viewer/redaction).

mod_interactive_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Interactive de-identification", "Phase 1 & 3",
    bullets = c(
      "Pick an input folder (DICOM/NIfTI) and a profile; run de-identification.",
      "Before/after metadata diff table (per-tag action, original vs replacement).",
      "Frame/cine/RGB image viewer with auto-proposed + manual redaction boxes.",
      "Write valid, viewable DICOM (incl. compressed) to an output folder."
    )
  )
}

mod_interactive_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 1/3): folder pick -> call_engine('deidentify_study', ...) ->
    # metadata diff + viewer.
    invisible(NULL)
  })
}
