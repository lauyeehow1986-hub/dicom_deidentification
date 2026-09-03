#' Projects tab (Phase 6.5): create/clone a project, choose its hashing and
#' signing policy, set and rebind the input/output roots after a disk swap, and
#' make it the app's active context (profile + keystore + manifest + audit log).

mod_projects_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 400,
      shiny::selectInput(ns("select"), "Active project", choices = character(0)),
      shiny::uiOutput(ns("active_note")),
      shiny::hr(),
      bslib::card(
        bslib::card_header("New / clone project"),
        bslib::card_body(
          shiny::textInput(ns("pid"), "Project id",
                           placeholder = "nhcs_echo_2026"),
          shiny::textInput(ns("label"), "Label", placeholder = "NHCS Echo 2026"),
          shiny::selectInput(ns("copy_from"), "Copy settings from",
                             choices = c("(none)" = "")),
          shiny::selectInput(ns("profile"), "Profile", choices = c("default")),
          shiny::radioButtons(ns("hscope"), "Patient hashing",
            choices = c("Project-scoped (unlinkable across projects)" = "project",
                        "Global (same patient hashes the same everywhere)" = "global"),
            selected = "project"),
          shiny::checkboxInput(ns("reversible"),
            "Reversible (keep a re-identification crosswalk)", value = TRUE),
          shiny::checkboxInput(ns("signing"), "Sign outputs (Ed25519)", value = TRUE),
          shiny::radioButtons(ns("sscope"), "Signing key",
            choices = c("Global (one key, verifiable everywhere)" = "global",
                        "Project-scoped" = "project"),
            selected = "global"),
          shiny::textInput(ns("root_in"), "Input root folder",
                           placeholder = "E:/incoming"),
          shiny::textInput(ns("root_out"), "Output root folder",
                           placeholder = "F:/deid"),
          shiny::actionButton(ns("create"), "Create / update project",
                              class = "btn-primary", icon = shiny::icon("folder-plus"))
        )
      ),
      shiny::hr(),
      bslib::card(
        bslib::card_header("Portable disk swapped? Rebind roots"),
        bslib::card_body(
          shiny::textInput(ns("new_in"), "New input root", placeholder = "G:/incoming"),
          shiny::textInput(ns("new_out"), "New output root", placeholder = "H:/deid"),
          shiny::actionButton(ns("rebind"), "Rebind & keep resuming",
                              class = "btn-outline-secondary",
                              icon = shiny::icon("arrows-rotate"))
        )
      ),
      shiny::uiOutput(ns("status"))
    ),
    bslib::card(
      bslib::card_header("Active project settings"),
      bslib::card_body(shiny::uiOutput(ns("settings")))
    )
  )
}

mod_projects_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    rv <- shiny::reactiveValues(msg = NULL, tick = 0L)

    profile_ids <- function() {
      if (isTRUE(app_state$engine$available)) {
        tryCatch(vapply(engine_profiles_list(), function(p) p$id, character(1)),
                 error = function(e) "default")
      } else "default"
    }

    # Populate the pickers from disk (and refresh after create/rebind).
    refresh_lists <- function(select = NULL) {
      ids <- vapply(projects_list(), function(p) p$project_id, character(1))
      shiny::updateSelectInput(session, "select", choices = ids,
                               selected = select %||% app_state$project %||%
                                 (if (length(ids)) ids[1] else NULL))
      shiny::updateSelectInput(session, "copy_from",
                               choices = c("(none)" = "", ids))
      pids <- profile_ids(); if (!length(pids)) pids <- "default"
      shiny::updateSelectInput(session, "profile", choices = pids,
                               selected = input$profile %||% pids[1])
    }
    shiny::observe({ app_state$profiles_version; rv$tick; refresh_lists() })

    # Selecting a project makes it the app-wide active context.
    shiny::observeEvent(input$select, {
      shiny::req(nzchar(input$select %||% ""))
      p <- tryCatch(project_get(input$select), error = function(e) NULL)
      if (is.null(p)) return()
      app_state$project <- p$project_id
      app_state$project_obj <- p
      app_state$profile_id <- p$profile_id %||% "default"
    }, ignoreInit = TRUE)

    output$active_note <- shiny::renderUI({
      if (is.null(app_state$project))
        shiny::p(class = "small text-muted", "No project selected yet.")
      else shiny::p(class = "small",
        "Active: ", shiny::strong(app_state$project),
        " \u2014 other tabs (Bulk, QA) use its settings.")
    })

    shiny::observeEvent(input$create, {
      shiny::req(nzchar(input$pid %||% ""))
      cf <- if (nzchar(input$copy_from %||% "")) input$copy_from else NULL
      p <- tryCatch(
        project_create(input$pid, label = input$label %||% input$pid,
                       profile_id = input$profile, copy_from = cf,
                       hashing_scope = input$hscope,
                       reversible = isTRUE(input$reversible),
                       signing_enabled = isTRUE(input$signing),
                       signing_scope = input$sscope,
                       root_in = input$root_in %||% "",
                       root_out = input$root_out %||% "",
                       created_by = app_state$user %||% NA_character_),
        error = function(e) { shiny::showNotification(conditionMessage(e),
          type = "error"); NULL })
      shiny::req(p)
      rv$msg <- sprintf("Project '%s' saved.", p$project_id)
      rv$tick <- rv$tick + 1L
      refresh_lists(select = p$project_id)
      app_state$project <- p$project_id; app_state$project_obj <- p
      app_state$profile_id <- p$profile_id %||% "default"
      shiny::showNotification(rv$msg, type = "message")
    })

    shiny::observeEvent(input$rebind, {
      shiny::req(app_state$project)
      p <- tryCatch(project_rebind_roots(app_state$project, input$new_in %||% "",
                                         input$new_out %||% ""),
                    error = function(e) { shiny::showNotification(conditionMessage(e),
                      type = "error"); NULL })
      shiny::req(p)
      app_state$project_obj <- p
      rv$msg <- sprintf("Rebound '%s' -> in=%s out=%s. Resume in Bulk to continue.",
                        p$project_id, p$roots[["in"]], p$roots[["out"]])
      shiny::showNotification(rv$msg, type = "message", duration = 8)
    })

    output$status <- shiny::renderUI({
      if (is.null(rv$msg)) NULL else shiny::p(class = "small mt-2", rv$msg)
    })

    output$settings <- shiny::renderUI({
      p <- app_state$project_obj
      if (is.null(p)) return(shiny::p(class = "text-muted",
        "Create or select a project to see its settings."))
      ks <- project_resolve_keystore(p); sg <- project_resolve_signing(p)
      row <- function(k, v) shiny::tags$tr(shiny::tags$th(k, class = "pe-3 text-muted"),
                                           shiny::tags$td(v))
      shiny::tags$table(class = "table table-sm",
        shiny::tags$tbody(
          row("Project", p$project_id),
          row("Profile", p$profile_id %||% "default"),
          row("Hashing", sprintf("%s \u00b7 %s", ks$scope,
                if (ks$reversible) "reversible" else "irreversible")),
          row("Keystore", ks$path),
          row("Signing", if (sg$enabled) sprintf("on \u00b7 %s key", sg$scope) else "off"),
          row("Signing key dir", sg$key_dir),
          row("Input root", p$roots[["in"]] %||% ""),
          row("Output root", p$roots[["out"]] %||% ""),
          row("Manifest", project_manifest_path(p$project_id)),
          row("Audit log", audit_file(p$project_id))))
    })
  })
}
