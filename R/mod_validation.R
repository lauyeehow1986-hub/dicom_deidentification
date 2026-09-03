#' Validation & bundle integrity (Phase 7).
#'
#' Two air-gap-friendly self-tests the deployer can run in-app, no shell needed:
#'   * an acceptance self-test that builds the synthetic planted-PHI corpus,
#'     de-identifies it, and checks nothing planted survives (metadata + pixels),
#'     outputs stay valid, the reversibility policy holds, and a rerun reprocesses
#'     nothing; and
#'   * a bundle-integrity check that re-verifies BUNDLE_MANIFEST.json against the
#'     files actually on disk (catches a truncated or tampered copy).

mod_validation_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      bslib::card(
        bslib::card_header("Acceptance self-test"),
        bslib::card_body(
          shiny::radioButtons(ns("mode"), "Reversibility mode",
                              choices = c("Reversible" = "reversible",
                                          "Irreversible" = "irreversible")),
          shiny::actionButton(ns("run"), "Run acceptance self-test",
                              class = "btn-primary",
                              icon = shiny::icon("clipboard-check")),
          shiny::helpText("Runs the full engine over synthetic planted-PHI data.")
        )
      ),
      bslib::card(
        bslib::card_header("Bundle integrity"),
        bslib::card_body(
          shiny::textInput(ns("bundle_dir"), "Bundle folder",
                           placeholder = "C:/dicomdeid-portable"),
          shiny::actionButton(ns("verify"), "Verify bundle",
                              icon = shiny::icon("shield-halved"))
        )
      )
    ),
    bslib::card(
      bslib::card_header("Acceptance report"),
      bslib::card_body(
        shiny::uiOutput(ns("acc_badge")),
        shiny::uiOutput(ns("acc_report"))
      )
    ),
    bslib::card(
      bslib::card_header("Bundle verification"),
      bslib::card_body(shiny::uiOutput(ns("verify_out")))
    )
  )
}

mod_validation_server <- function(id, app_state = NULL) {
  shiny::moduleServer(id, function(input, output, session) {
    rv <- shiny::reactiveValues(acc = NULL, verify = NULL)

    output$acc_report <- shiny::renderUI({
      if (is.null(rv$acc)) return(shiny::p(class = "text-muted",
        "Run the self-test to validate this installation end to end."))
      shiny::markdown(rv$acc$markdown)
    })

    output$acc_badge <- shiny::renderUI({
      if (is.null(rv$acc)) return(NULL)
      ok <- isTRUE(rv$acc$report$passed)
      shiny::div(class = sprintf("alert %s", if (ok) "alert-success" else "alert-danger"),
                 if (ok) "PASS - no planted PHI survived; all checks green."
                 else "FAIL - one or more acceptance checks did not pass (see below).")
    })

    shiny::observeEvent(input$run, {
      if (!engine_available()) {
        rv$acc <- list(markdown = "**Engine unavailable** - the Python venv is not loaded, so the acceptance self-test cannot run. Check `DICOMDEID_VENV`.",
                       report = list(passed = FALSE))
        return()
      }
      shiny::withProgress(message = "Running acceptance self-test...", value = 0.3, {
        res <- tryCatch(acceptance_run(mode = input$mode),
                        error = function(e) list(
                          markdown = sprintf("**Error:** %s", conditionMessage(e)),
                          report = list(passed = FALSE)))
        rv$acc <- res
      })
    })

    output$verify_out <- shiny::renderUI({
      v <- rv$verify
      if (is.null(v)) return(shiny::p(class = "text-muted",
        "Point at a built bundle folder and verify its manifest."))
      if (!is.null(v$error)) return(shiny::div(class = "alert alert-warning", v$error))
      cls <- if (isTRUE(v$ok)) "alert-success" else "alert-danger"
      detail <- if (isTRUE(v$ok))
        sprintf("OK - %d files match the manifest.", v$n_checked)
      else sprintf("MISMATCH - %d checked; missing %d, changed %d, extra %d.",
                   v$n_checked, length(v$missing), length(v$changed), length(v$extra))
      lst <- NULL
      if (!isTRUE(v$ok)) {
        bad <- c(if (length(v$missing)) paste("missing:", paste(v$missing, collapse = ", ")),
                 if (length(v$changed)) paste("changed:", paste(v$changed, collapse = ", ")),
                 if (length(v$extra))   paste("extra:", paste(v$extra, collapse = ", ")))
        lst <- shiny::tags$ul(lapply(bad, shiny::tags$li))
      }
      shiny::tagList(shiny::div(class = sprintf("alert %s", cls), detail), lst)
    })

    shiny::observeEvent(input$verify, {
      dir <- input$bundle_dir
      mp <- file.path(dir, "BUNDLE_MANIFEST.json")
      if (!nzchar(dir) || !dir.exists(dir)) {
        rv$verify <- list(error = "No such folder."); return()
      }
      if (!file.exists(mp)) {
        rv$verify <- list(error = "No BUNDLE_MANIFEST.json in that folder."); return()
      }
      rv$verify <- tryCatch(bundle_verify(dir, mp),
                            error = function(e) list(error = conditionMessage(e)))
    })
  })
}
