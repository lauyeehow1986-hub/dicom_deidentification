#' Interactive de-identification of a single folder, with before/after review.
#' Phase 1 (metadata core). Phase 3 adds the pixel viewer + manual redaction.

mod_interactive_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      shiny::textInput(ns("input_dir"), "Input folder (DICOM)",
                       placeholder = "C:/path/to/study"),
      shiny::textInput(ns("output_dir"), "Output folder",
                       placeholder = "C:/path/to/output"),
      shiny::selectInput(ns("profile"), "Profile", choices = c("default")),
      shiny::checkboxInput(ns("reversible"),
                           "Reversible (keep re-identification key)", value = FALSE),
      shiny::conditionalPanel(
        condition = sprintf("input['%s'] == true", ns("reversible")),
        shiny::textInput(ns("keystore"), "Keystore file",
                         placeholder = "C:/keys/study.keystore"),
        shiny::passwordInput(ns("passphrase"), "Keystore passphrase")
      ),
      shiny::actionButton(ns("run"), "De-identify", class = "btn-primary",
                          icon = shiny::icon("user-shield")),
      shiny::hr(),
      shiny::uiOutput(ns("summary"))
    ),
    bslib::card(
      bslib::card_header("Before / after \u2014 first file"),
      bslib::card_body(shiny::tableOutput(ns("diff")))
    )
  )
}

mod_interactive_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    result <- shiny::reactiveVal(NULL)

    # Offer every profile (shipped + workspace projects), defaulting to the one
    # made active in the Rules & Profiles tab.
    shiny::observe({
      app_state$profiles_version
      ids <- if (isTRUE(app_state$engine$available)) {
        tryCatch(vapply(engine_profiles_list(), function(p) p$id, character(1)),
                 error = function(e) "default")
      } else "default"
      if (!length(ids)) ids <- "default"
      shiny::updateSelectInput(session, "profile", choices = ids,
                               selected = app_state$profile_id %||% ids[1])
    })

    shiny::observeEvent(input$run, {
      shiny::req(input$input_dir, input$output_dir)
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured \u2014 see docs/airgap-install.md.",
                                type = "error"); return()
      }
      if (isTRUE(input$reversible) && (!nzchar(input$keystore %||% "") ||
                                       !nzchar(input$passphrase %||% ""))) {
        shiny::showNotification("Reversible mode needs a keystore file and passphrase.",
                                type = "error"); return()
      }
      ks <- if (isTRUE(input$reversible)) input$keystore else NULL
      pw <- if (isTRUE(input$reversible)) input$passphrase else NULL

      out <- tryCatch(
        engine_deid_run(input$input_dir, input$output_dir, input$profile, ks, pw),
        error = function(e) {
          shiny::showNotification(paste("Error:", conditionMessage(e)), type = "error")
          NULL
        }
      )
      result(out)
      if (!is.null(out)) {
        shiny::showNotification(sprintf("Processed %d file(s).", out$count %||% 0),
                                type = "message")
      }
    })

    output$diff <- shiny::renderTable({
      r <- result(); shiny::req(r)
      shiny::validate(shiny::need(length(r$files) > 0, "No DICOM files found."))
      records_to_df(r$files[[1]]$records)
    }, striped = TRUE, spacing = "xs", width = "100%")

    output$summary <- shiny::renderUI({
      r <- result()
      if (is.null(r)) {
        return(shiny::p(class = "text-muted small",
                        "Choose a folder and profile, then run."))
      }
      shiny::tagList(
        shiny::p(shiny::strong(sprintf("%d file(s) processed", r$count %||% 0))),
        shiny::p(class = "small",
                 if (isTRUE(r$reversible))
                   "Reversible \u2014 crosswalk saved to the keystore."
                 else "Irreversible \u2014 no re-identification key was kept.")
      )
    })
  })
}
