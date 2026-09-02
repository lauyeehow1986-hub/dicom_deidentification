#' Self-improvement tagging loop: flag identifiers the pipeline missed, in
#' metadata OR pixels, and feed them back into the rules/gazetteer/model.
#' Phase 4.

mod_tagging_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Tagging & self-improvement", "Phase 4",
    bullets = c(
      "Flag a missed value in a tag or a drawn pixel region; label its category.",
      "Choose the fix: add to gazetteer, add a regex/tag rule, or store a labeled example.",
      "Persisted rules apply to all future files; labeled examples feed later NER fine-tuning."
    )
  )
}

mod_tagging_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 4): capture flag -> append to gazetteer/regex + labeled-example store.
    invisible(NULL)
  })
}
