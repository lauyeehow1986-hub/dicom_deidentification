#' Bulk de-identification: queued, resumable, parallel batches (up to ~2 TB).
#' Phase 5.
#'
#' Scan a root folder into a SQLite manifest, then run it to completion in a
#' background process (callr) so Shiny never blocks. The UI polls the manifest
#' for live progress; restarting the app and pressing Start again resumes where
#' a killed run left off (done files are skipped, stale rows re-queued).

mod_bulk_ui <- function(id) {
  ns <- shiny::NS(id)
  cores <- tryCatch(parallel::detectCores(), error = function(e) 4L)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      shiny::selectInput(ns("project"), "Project (its hashing/signing/roots)",
                         choices = c("(none)" = "")),
      shiny::conditionalPanel(
        condition = sprintf("input['%s'] != ''", ns("project")),
        shiny::passwordInput(ns("proj_pass"), "Keystore passphrase"),
        shiny::p(class = "small text-muted",
                 "Roots, profile, hashing scope, reversibility and signing come "
                 , "from the project. Rebind roots in the Projects tab after a disk swap.")
      ),
      shiny::textInput(ns("input_dir"), "Input root folder",
                       placeholder = "C:/studies/incoming"),
      shiny::textInput(ns("output_dir"), "Output root folder",
                       placeholder = "C:/studies/deid"),
      shiny::textInput(ns("batch_id"), "Batch name", value = "batch1"),
      shiny::selectInput(ns("profile"), "Profile", choices = c("default")),
      shiny::numericInput(ns("workers"), "Parallel workers", value = min(4L, cores),
                          min = 1L, max = max(1L, cores), step = 1L),
      shiny::checkboxInput(ns("reversible"),
                           "Reversible (keep re-identification key)", value = FALSE),
      shiny::conditionalPanel(
        condition = sprintf("input['%s'] == true", ns("reversible")),
        shiny::textInput(ns("keystore"), "Keystore file",
                         placeholder = "C:/keys/batch.keystore"),
        shiny::passwordInput(ns("passphrase"), "Keystore passphrase"),
        shiny::p(class = "small text-muted",
                 "Reversible batches run single-worker to keep the keystore consistent.")
      ),
      shiny::hr(),
      shiny::actionButton(ns("scan"), "Scan & register",
                          class = "btn-outline-secondary", icon = shiny::icon("magnifying-glass")),
      shiny::div(class = "mt-2",
        shiny::actionButton(ns("start"), "Start / Resume", class = "btn-primary",
                            icon = shiny::icon("play")),
        shiny::actionButton(ns("stop"), "Stop", class = "btn-outline-danger",
                            icon = shiny::icon("stop"))
      ),
      shiny::uiOutput(ns("status"))
    ),
    bslib::card(
      bslib::card_header("Progress"),
      bslib::card_body(
        shiny::uiOutput(ns("progressbar")),
        shiny::uiOutput(ns("counts"))
      )
    ),
    bslib::layout_columns(
      col_widths = c(7, 5),
      bslib::card(
        bslib::card_header("Recent files"),
        bslib::card_body(shiny::tableOutput(ns("recent")))
      ),
      bslib::card(
        bslib::card_header("Failures"),
        bslib::card_body(shiny::tableOutput(ns("failures")))
      )
    )
  )
}

mod_bulk_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    rv <- shiny::reactiveValues(mpath = NULL, batch = NULL, proc = NULL,
                                running = FALSE, prog = NULL, recent = NULL,
                                fails = NULL, msg = NULL)

    # Offer shipped + workspace profiles, defaulting to the active one.
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

    # Offer projects; selecting one prefills roots/profile/reversibility from it.
    shiny::observe({
      app_state$project
      pids <- vapply(projects_list(), function(p) p$project_id, character(1))
      shiny::updateSelectInput(session, "project", choices = c("(none)" = "", pids),
                               selected = app_state$project %||% "")
    })
    shiny::observeEvent(input$project, {
      if (!nzchar(input$project %||% "")) return()
      p <- tryCatch(project_get(input$project), error = function(e) NULL)
      if (is.null(p)) return()
      shiny::updateTextInput(session, "input_dir", value = p$roots[["in"]] %||% "")
      shiny::updateTextInput(session, "output_dir", value = p$roots[["out"]] %||% "")
      shiny::updateTextInput(session, "batch_id", value = p$project_id)
      shiny::updateSelectInput(session, "profile", selected = p$profile_id %||% "default")
      shiny::updateCheckboxInput(session, "reversible",
                                 value = isTRUE(p$hashing$reversible))
    }, ignoreInit = TRUE)

    # Read the current manifest state into the reactive values feeding the UI.
    refresh_progress <- function() {
      if (is.null(rv$mpath) || !file.exists(rv$mpath)) return(invisible())
      con <- manifest_open(rv$mpath); on.exit(manifest_close(con))
      rv$prog   <- progress_summary(con, rv$batch)
      rv$recent <- manifest_recent(con, rv$batch, n = 12)
      rv$fails  <- manifest_failures(con, rv$batch)
    }

    # --- scan & register ---------------------------------------------------
    shiny::observeEvent(input$scan, {
      shiny::req(input$input_dir, input$output_dir, input$batch_id)
      if (!dir.exists(input$input_dir)) {
        shiny::showNotification("Input folder not found.", type = "error"); return()
      }
      mp <- bulk_manifest_path(input$batch_id)
      con <- manifest_open(mp)
      reg <- tryCatch(
        register_batch(con, input$batch_id, input$input_dir, input$output_dir,
                       profile_id = input$profile,
                       created_by = app_state$user %||% NA_character_),
        error = function(e) { shiny::showNotification(paste("Scan failed:",
          conditionMessage(e)), type = "error"); NULL })
      manifest_close(con)
      shiny::req(reg)
      rv$mpath <- mp; rv$batch <- input$batch_id
      rv$msg <- sprintf("Registered %d new file(s); %d in batch '%s'.",
                        reg$inserted, reg$total, input$batch_id)
      refresh_progress()
      shiny::showNotification(rv$msg, type = "message")
    })

    # --- start / resume ----------------------------------------------------
    shiny::observeEvent(input$start, {
      if (is.null(rv$mpath)) {
        shiny::showNotification("Scan & register a folder first.", type = "warning"); return()
      }
      if (isTRUE(rv$running) && !is.null(rv$proc) && rv$proc$is_alive()) {
        shiny::showNotification("A run is already in progress.", type = "warning"); return()
      }
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured \u2014 see docs/airgap-install.md.",
                                type = "error"); return()
      }
      # Resolve the run's hashing/signing/roots/log \u2014 from the active project
      # when one is selected, otherwise from the manual reversible controls.
      proj <- if (nzchar(input$project %||% ""))
        tryCatch(project_get(input$project), error = function(e) NULL) else NULL
      reversible <- isTRUE(input$reversible)
      ks <- NULL; pw <- NULL; sign_key <- NULL; plog <- NULL; pid <- NULL
      root_in <- input$input_dir %||% ""; root_out <- input$output_dir %||% ""
      signer <- app_state$user %||% NA_character_

      if (!is.null(proj)) {
        ksr <- project_resolve_keystore(proj)
        reversible <- ksr$reversible
        ks <- ksr$path
        pw <- input$proj_pass %||% ""
        if (!nzchar(pw)) {
          shiny::showNotification("Enter the project's keystore passphrase.",
                                  type = "error"); return()
        }
        pid <- proj$project_id
        plog <- project_processed_log(pid)
        sg <- project_resolve_signing(proj)
        if (isTRUE(sg$enabled)) {
          kp <- tryCatch(engine_ensure_keypair(sg$key_dir), error = function(e) NULL)
          if (!is.null(kp)) sign_key <- kp$key_path
        }
      } else if (reversible) {
        if (!nzchar(input$keystore %||% "") || !nzchar(input$passphrase %||% "")) {
          shiny::showNotification("Reversible mode needs a keystore file and passphrase.",
                                  type = "error"); return()
        }
        ks <- input$keystore; pw <- input$passphrase
      }
      workers <- max(1L, as.integer(input$workers %||% 1L))

      src  <- normalizePath("R", mustWork = FALSE)
      venv <- normalizePath(engine_venv_path(), mustWork = FALSE)
      ws   <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))

      rv$proc <- callr::r_bg(
        func = function(mp, src, workers, batch, ks, pw, venv, ws,
                        root_in, root_out, reversible, sign_key, signer, pid, plog) {
          Sys.setenv(DICOMDEID_WORKSPACE = ws, DICOMDEID_VENV = venv,
                     KMP_DUPLICATE_LIB_OK = "TRUE")
          for (f in list.files(src, pattern = "[.]R$", full.names = TRUE)) source(f)
          run_batch(mp, batch_id = batch, workers = workers,
                    keystore_path = ks, passphrase = pw,
                    r_source_dir = src, venv = venv,
                    root_in = root_in, root_out = root_out, reversible = reversible,
                    sign_key_path = sign_key, signer = signer, project_id = pid,
                    processed_log = plog)
        },
        args = list(mp = rv$mpath, src = src, workers = workers, batch = rv$batch,
                    ks = ks, pw = pw, venv = venv, ws = ws,
                    root_in = root_in, root_out = root_out, reversible = reversible,
                    sign_key = sign_key, signer = signer, pid = pid, plog = plog),
        supervise = TRUE)
      rv$running <- TRUE
      rv$msg <- sprintf("Running batch '%s' with %d worker(s)\u2026", rv$batch,
                        if (reversible) 1L else workers)
      shiny::showNotification(rv$msg, type = "message")
    })

    # --- stop --------------------------------------------------------------
    shiny::observeEvent(input$stop, {
      if (!is.null(rv$proc) && rv$proc$is_alive()) rv$proc$kill()
      rv$running <- FALSE
      if (!is.null(rv$mpath)) {
        con <- manifest_open(rv$mpath); n <- reset_stale(con, rv$batch); manifest_close(con)
        rv$msg <- sprintf("Stopped. Re-queued %d in-flight file(s); press Start to resume.", n)
      }
      refresh_progress()
      shiny::showNotification(rv$msg %||% "Stopped.", type = "warning")
    })

    # --- polling: refresh while a run is live, and detect completion -------
    shiny::observe({
      shiny::invalidateLater(1000, session)
      if (is.null(rv$mpath)) return()
      refresh_progress()
      if (isTRUE(rv$running) && !is.null(rv$proc) && !rv$proc$is_alive()) {
        rv$running <- FALSE
        p <- rv$prog
        rv$msg <- sprintf("Batch complete: %d done, %d failed, %d flagged.",
                          p$done %||% 0, p$failed %||% 0, p$flagged %||% 0)
        shiny::showNotification(rv$msg, type = "message", duration = 8)
      }
    })

    # --- outputs -----------------------------------------------------------
    output$status <- shiny::renderUI({
      shiny::tagList(
        if (!is.null(rv$msg)) shiny::p(class = "small mt-2", rv$msg),
        if (isTRUE(rv$running)) shiny::p(class = "small text-primary",
          shiny::icon("spinner", class = "fa-spin"), " running\u2026")
      )
    })

    output$progressbar <- shiny::renderUI({
      p <- rv$prog
      if (is.null(p) || p$total == 0)
        return(shiny::p(class = "text-muted", "No batch registered yet."))
      finished <- p$done + p$failed + p$flagged
      pct <- round(100 * finished / p$total)
      shiny::div(class = "progress", style = "height: 22px;",
        shiny::div(class = "progress-bar", role = "progressbar",
                   style = sprintf("width: %d%%;", pct),
                   sprintf("%d%%  (%d/%d)", pct, finished, p$total)))
    })

    output$counts <- shiny::renderUI({
      p <- rv$prog; if (is.null(p)) return(NULL)
      chip <- function(lbl, n, cls) shiny::span(class = sprintf("badge %s me-1", cls),
                                                sprintf("%s: %d", lbl, n))
      shiny::div(class = "mt-2",
        chip("pending", p$pending, "bg-secondary"),
        chip("in progress", p$in_progress, "bg-info"),
        chip("done", p$done, "bg-success"),
        chip("flagged", p$flagged, "bg-warning text-dark"),
        chip("failed", p$failed, "bg-danger"))
    })

    output$recent <- shiny::renderTable({
      r <- rv$recent
      shiny::validate(shiny::need(!is.null(r) && nrow(r) > 0, "Nothing processed yet."))
      data.frame(File = basename(r$input_path), Status = r$status,
                 Worker = r$worker %||% "", check.names = FALSE)
    }, striped = TRUE, spacing = "xs", width = "100%")

    output$failures <- shiny::renderTable({
      f <- rv$fails
      shiny::validate(shiny::need(!is.null(f) && nrow(f) > 0, "No failures."))
      data.frame(File = basename(f$input_path),
                 Error = trunc_str(f$error, 80), check.names = FALSE)
    }, striped = TRUE, spacing = "xs", width = "100%")
  })
}
