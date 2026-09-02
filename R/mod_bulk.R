#' Bulk de-identification: queued, resumable, parallel batches (up to ~2 TB).
#' Phase 5.

mod_bulk_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Bulk de-identification", "Phase 5",
    bullets = c(
      "Point at a root folder; build a SQLite manifest of every study/file.",
      "Launch a parallel worker pool (mirai/future); Shiny polls live progress.",
      "Resumable: restarting skips completed files; failures are retryable.",
      "Streams large batches without loading everything into memory."
    )
  )
}

mod_bulk_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 5): manifest_open() -> register_batch() -> worker pool -> progress.
    invisible(NULL)
  })
}
