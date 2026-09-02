#' Rules & profile editor: customise how each identifier is detected and replaced.
#' Phase 4.

mod_rules_editor_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Rules & profiles", "Phase 4",
    bullets = c(
      "View/clone/edit per-project profiles (per-tag PS3.15 action, regex, gazetteer).",
      "Toggle PS3.15 options (clean descriptors/pixels, retain temporal, private-tag policy).",
      "Configure replacement style (pseudonym format, date-shift range, UID remap)."
    )
  )
}

mod_rules_editor_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 4): edit + persist profile YAML under inst/profiles/ or a project dir.
    invisible(NULL)
  })
}
