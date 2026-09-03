#' Top-level server: wires each module and reports engine status.
#'
#' Role gating (de-identifier vs reviewer) is applied here in a later phase via
#' auth.R / shinymanager; at scaffold stage every tab is visible.
app_server <- function(input, output, session) {

  # The writable workspace (per-project profiles, grown gazetteers, labeled
  # examples) lives next to the app unless the air-gapped box overrides it.
  if (!nzchar(Sys.getenv("DICOMDEID_WORKSPACE"))) {
    root <- app_root() %||% getwd()
    Sys.setenv(DICOMDEID_WORKSPACE = file.path(root, "workspace"))
  }

  # Shared application state passed to modules (profile, engine handle, role, ...).
  app_state <- shiny::reactiveValues(
    role       = "deidentifier",  # deidentifier | reviewer  (from the navbar control)
    user       = "",              # acting user's name (recorded in the audit log)
    profile_id = "default",       # active profile the workflow tabs use
    profile    = load_profile("default"),
    profiles_version = 0L,         # bumped to refresh profile lists across tabs
    project    = NULL,            # active project id (Phase 6.5) or NULL
    project_obj = NULL,          # the active project descriptor (list)
    engine     = engine_handle()  # lazy; NULL-safe when the venv isn't built yet
  )

  # The navbar "acting as" control drives role (QA sign-off gate) + audit actor.
  shiny::observeEvent(input$acting_role, app_state$role <- input$acting_role,
                      ignoreInit = TRUE)
  shiny::observeEvent(input$acting_user, app_state$user <- input$acting_user,
                      ignoreInit = TRUE)

  mod_projects_server("projects", app_state)
  mod_interactive_server("interactive", app_state)
  mod_pixels_server("pixels", app_state)
  mod_rules_editor_server("rules", app_state)
  mod_tagging_server("tagging", app_state)
  mod_bulk_server("bulk", app_state)
  mod_qa_server("qa", app_state)
  mod_keystore_server("keystore", app_state)
  mod_audit_server("audit", app_state)
  mod_validation_server("validation", app_state)

  output$engine_status <- shiny::renderUI({
    ok <- isTRUE(app_state$engine$available)
    shiny::span(
      class = if (ok) "badge bg-success" else "badge bg-secondary",
      if (ok) "engine: ready" else "engine: not configured"
    )
  })
}
