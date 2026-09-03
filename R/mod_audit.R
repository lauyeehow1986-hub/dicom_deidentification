#' Audit log viewer (Phase 6): the append-only, hash-chained record of every
#' de-identification run and reviewer sign-off. Verify the chain and export for
#' governance. The store itself lives in the workspace (see audit.R).

mod_audit_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 320,
      shiny::p(class = "small text-muted",
        "Every de-identification run and reviewer sign-off is appended here and ",
        "hash-chained, so a later edit or deletion breaks the chain."),
      shiny::actionButton(ns("verify"), "Verify integrity",
                          class = "btn-outline-primary", icon = shiny::icon("shield-halved")),
      shiny::uiOutput(ns("integrity")),
      shiny::hr(),
      shiny::downloadButton(ns("export"), "Export CSV", class = "btn-outline-secondary"),
      shiny::actionButton(ns("refresh"), "Refresh", class = "btn-link btn-sm")
    ),
    bslib::card(
      bslib::card_header("Audit log"),
      bslib::card_body(shiny::tableOutput(ns("log")))
    )
  )
}

mod_audit_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    rv <- shiny::reactiveValues(tick = 0L, integrity = NULL)

    read_log <- shiny::reactive({
      rv$tick               # re-read on refresh / after actions elsewhere
      shiny::invalidateLater(3000, session)
      audit_read()
    })

    shiny::observeEvent(input$refresh, rv$tick <- rv$tick + 1L)

    shiny::observeEvent(input$verify, {
      rv$integrity <- audit_verify()
    })

    output$integrity <- shiny::renderUI({
      v <- rv$integrity; if (is.null(v)) return(NULL)
      if (isTRUE(v$ok)) {
        shiny::div(class = "alert alert-success py-1 small mt-2",
          shiny::icon("check"), sprintf(" Chain intact (%d entr%s).",
            v$n, if (v$n == 1) "y" else "ies"))
      } else {
        shiny::div(class = "alert alert-danger py-1 small mt-2",
          shiny::icon("triangle-exclamation"),
          sprintf(" Chain broken at entry %d — the log was altered.", v$broken_at))
      }
    })

    output$log <- shiny::renderTable({
      df <- read_log()
      shiny::validate(shiny::need(nrow(df) > 0, "No audit entries yet."))
      df[order(df$seq), c("seq", "ts", "actor", "role", "action", "details")]
    }, striped = TRUE, spacing = "xs", width = "100%")

    output$export <- shiny::downloadHandler(
      filename = function() sprintf("audit_log_%s.csv", format(Sys.Date())),
      content = function(file) utils::write.csv(audit_read(), file, row.names = FALSE)
    )
  })
}
