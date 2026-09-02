#' Self-improvement tagging loop: flag identifiers the pipeline missed, in
#' metadata OR pixels, and feed them back into the rules/gazetteer/model.
#' Phase 4.
#'
#' A capture ALWAYS records a labeled example (for a later NER fine-tune); the
#' chosen fix additionally grows the active project's gazetteer and/or appends a
#' custom-regex rule (engine_tag_capture -> the writable workspace). The NER
#' export button turns the accumulated labeled examples into a training-ready
#' dataset (the fine-tuning hook).

#' Category choices read from the shipped identifier catalog (id -> label).
tagging_categories <- function() {
  path <- profile_path("identifier_catalog.yml")
  fallback <- c("Name" = "name", "National ID (NRIC/FIN)" = "national_id",
                "Phone" = "telephone", "Email" = "email", "Address" = "address",
                "Other" = "other")
  if (is.null(path) || !file.exists(path)) return(fallback)
  cat_yaml <- tryCatch(yaml::read_yaml(path), error = function(e) NULL)
  cats <- cat_yaml$categories
  if (is.null(cats) || !length(cats)) return(fallback)
  ids <- vapply(cats, function(c) c$id %||% "", character(1))
  labs <- vapply(cats, function(c) c$label %||% c$id %||% "", character(1))
  choices <- stats::setNames(ids, labs)
  choices[nzchar(ids)]
}

mod_tagging_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      shiny::p(class = "small text-muted",
               "Flag a value the pipeline missed (in a tag or burned-in pixels). ",
               "It is stored as a labeled example and, if you choose, added to this ",
               "project's gazetteer or as a regex rule."),
      shiny::uiOutput(ns("active_profile")),
      shiny::selectInput(ns("category"), "Identifier category",
                         choices = tagging_categories()),
      shiny::textInput(ns("value"), "The missed value",
                       placeholder = "Dr Muthusamy"),
      shiny::textInput(ns("context"), "Context (optional)",
                       placeholder = "Reported by Dr Muthusamy for the study"),
      shiny::textInput(ns("source"), "Where seen (optional)",
                       placeholder = "SeriesDescription / pixel banner"),
      shiny::checkboxGroupInput(ns("fix"), "Feed back as",
                                choices = c("Add to gazetteer" = "gazetteer",
                                            "Add a regex rule" = "regex"),
                                selected = "gazetteer"),
      shiny::conditionalPanel(
        condition = sprintf("input['%s'].includes('regex')", ns("fix")),
        shiny::textInput(ns("pattern"), "Regex pattern",
                         placeholder = "\\bMRN\\d{6,}\\b")
      ),
      shiny::actionButton(ns("capture"), "Capture", class = "btn-primary",
                          icon = shiny::icon("tag")),
      shiny::hr(),
      shiny::actionButton(ns("export"), "Export NER training set",
                          class = "btn-outline-secondary btn-sm",
                          icon = shiny::icon("download")),
      shiny::uiOutput(ns("export_note"))
    ),
    bslib::card(
      bslib::card_header("Captured this session"),
      bslib::card_body(shiny::tableOutput(ns("captured")))
    )
  )
}

mod_tagging_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    captured <- shiny::reactiveVal(
      data.frame(Category = character(), Value = character(), Profile = character(),
                 `Fed back as` = character(), check.names = FALSE,
                 stringsAsFactors = FALSE))
    export_note <- shiny::reactiveVal(NULL)

    output$active_profile <- shiny::renderUI({
      pid <- app_state$profile_id %||% "default"
      shiny::p(shiny::strong("Active project: "),
               shiny::span(class = "badge bg-primary", pid))
    })

    shiny::observeEvent(input$capture, {
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured.", type = "error"); return()
      }
      shiny::req(input$value)
      if ("regex" %in% input$fix && !nzchar(input$pattern %||% "")) {
        shiny::showNotification("A regex fix needs a pattern.", type = "warning"); return()
      }
      pid <- app_state$profile_id %||% "default"
      res <- tryCatch(
        engine_tag_capture(
          category = input$category, value = input$value, profile_id = pid,
          fix = input$fix %||% character(0),
          pattern = if (nzchar(input$pattern %||% "")) input$pattern else NULL,
          source = if (nzchar(input$source %||% "")) input$source else NULL,
          context = if (nzchar(input$context %||% "")) input$context else NULL),
        error = function(e) {
          shiny::showNotification(paste("Capture failed:", conditionMessage(e)),
                                  type = "error"); NULL })
      shiny::req(res)
      fed <- if (length(input$fix)) paste(input$fix, collapse = ", ") else "labeled example only"
      row <- data.frame(Category = input$category, Value = input$value, Profile = pid,
                        `Fed back as` = fed, check.names = FALSE, stringsAsFactors = FALSE)
      captured(rbind(row, captured()))
      # a new gazetteer/regex changes the project profile -> refresh other tabs
      if (length(input$fix)) {
        app_state$profiles_version <- (app_state$profiles_version %||% 0L) + 1L
        if (identical(pid, app_state$profile_id))
          app_state$profile <- tryCatch(engine_profile_get(pid), error = function(e) app_state$profile)
      }
      shiny::updateTextInput(session, "value", value = "")
      shiny::updateTextInput(session, "context", value = "")
      shiny::showNotification("Captured.", type = "message")
    })

    output$captured <- shiny::renderTable({
      df <- captured(); if (!nrow(df)) return(NULL)
      df
    }, striped = TRUE, spacing = "xs")

    shiny::observeEvent(input$export, {
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured.", type = "error"); return()
      }
      out <- tryCatch(engine_ner_export(), error = function(e) {
        shiny::showNotification(paste("Export failed:", conditionMessage(e)), type = "error")
        NULL })
      shiny::req(out)
      export_note(out)
      shiny::showNotification(sprintf("Wrote %d example(s).", out$count), type = "message")
    })

    output$export_note <- shiny::renderUI({
      o <- export_note(); if (is.null(o)) return(NULL)
      shiny::tagList(
        shiny::p(class = "small text-success mt-2",
                 sprintf("%d example(s) -> %s", o$count, o$path)),
        shiny::p(class = "small text-muted", o$note))
    })
  })
}
