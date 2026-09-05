#' Quality-assurance & review workflow (Phase 6).
#'
#' Re-runs the PHI detectors on de-identified OUTPUTS, reports what survived per
#' category with a pass/fail verdict, and gates a reviewer sign-off so the person
#' who de-identified a batch can never approve it. Every scan and sign-off is
#' written to the hash-chained audit log.

mod_qa_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      shiny::uiOutput(ns("whoami")),
      shiny::textInput(ns("output_dir"), "Output folder to re-scan",
                       placeholder = "C:/studies/deid"),
      shiny::textInput(ns("batch_id"), "…or a registered batch name",
                       placeholder = "batch1"),
      shiny::selectInput(ns("profile"), "Profile (detectors)", choices = c("default")),
      shiny::actionButton(ns("scan"), "Run residual scan", class = "btn-primary",
                          icon = shiny::icon("magnifying-glass-chart")),
      shiny::uiOutput(ns("status")),
      shiny::hr(),
      bslib::card(
        bslib::card_header("Reviewer sign-off"),
        bslib::card_body(
          shiny::uiOutput(ns("signoff_who")),
          shiny::radioButtons(ns("decision"), "Decision",
                              choices = c("Pass" = "pass", "Fail" = "fail"),
                              inline = TRUE),
          shiny::textAreaInput(ns("note"), "Note", rows = 2,
                               placeholder = "e.g. sampled 20 studies, none carried PHI"),
          shiny::actionButton(ns("signoff"), "Sign off",
                              class = "btn-success", icon = shiny::icon("stamp"))
        )
      )
    ),
    bslib::card(
      bslib::card_header("Residual-PHI verdict"),
      bslib::card_body(
        shiny::uiOutput(ns("verdict")),
        shiny::uiOutput(ns("categories")),
        shiny::uiOutput(ns("notes"))
      )
    ),
    bslib::layout_columns(
      col_widths = c(6, 6),
      bslib::card(
        bslib::card_header("Per-file"),
        bslib::card_body(shiny::tableOutput(ns("files")))
      ),
      bslib::card(
        bslib::card_header(shiny::tagList("Residual findings ",
          shiny::span(class = "small text-muted", "(previews masked)"))),
        bslib::card_body(shiny::tableOutput(ns("findings")))
      )
    ),
    bslib::layout_columns(
      col_widths = c(5, 7),
      bslib::card(
        bslib::card_header("Signatures"),
        bslib::card_body(shiny::tableOutput(ns("signatures")))
      ),
      bslib::card(
        bslib::card_header("De-identified metadata"),
        bslib::card_body(
          shiny::selectInput(ns("meta_file"), "File", choices = character(0),
                             width = "100%"),
          shiny::checkboxInput(ns("mask"), "Mask any flagged values", value = TRUE),
          shiny::tableOutput(ns("metadata"))
        )
      )
    )
  )
}

mod_qa_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    rv <- shiny::reactiveValues(res = NULL, proc = NULL, running = FALSE,
                                msg = NULL, deidentifier = NA_character_,
                                batch = NULL)

    # Mirror the profile picker used elsewhere.
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

    output$whoami <- shiny::renderUI({
      shiny::p(class = "small text-muted",
        "Acting as ", shiny::strong(app_state$user %||% "(unnamed)"),
        " \u2014 role: ", shiny::strong(app_state$role %||% "deidentifier"), ".")
    })

    # Resolve the batch de-identifier for the sign-off gate.
    resolve_deidentifier <- function() {
      b <- input$batch_id
      if (is.null(b) || !nzchar(b)) return(NA_character_)
      mp <- bulk_manifest_path(b)
      if (!file.exists(mp)) return(NA_character_)
      con <- manifest_open(mp); on.exit(manifest_close(con))
      batch_created_by(con, b)
    }

    # --- run the residual scan in the background (Shiny stays responsive) ----
    shiny::observeEvent(input$scan, {
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured \u2014 see docs/airgap-install.md.",
                                type = "error"); return()
      }
      batch <- if (nzchar(input$batch_id %||% "")) input$batch_id else NULL
      paths <- NULL
      if (!is.null(batch)) {
        mp <- bulk_manifest_path(batch)
        if (file.exists(mp)) {
          con <- manifest_open(mp)
          paths <- manifest_output_paths(con, batch)
          manifest_close(con)
        }
      }
      if (is.null(paths) && nzchar(input$output_dir %||% "")) {
        if (!dir.exists(input$output_dir)) {
          shiny::showNotification("Output folder not found.", type = "error"); return()
        }
        paths <- manifest_scan_files(input$output_dir)
      }
      if (is.null(paths) || !length(paths)) {
        shiny::showNotification("Nothing to scan: give an output folder or a registered batch.",
                                type = "warning"); return()
      }
      rv$batch <- batch
      rv$deidentifier <- resolve_deidentifier()

      profile <- input$profile %||% "default"
      src  <- normalizePath("R", mustWork = FALSE)
      venv <- normalizePath(engine_venv_path(), mustWork = FALSE)
      ws   <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
      mpath <- if (!is.null(batch)) bulk_manifest_path(batch) else NA_character_

      rv$proc <- callr::r_bg(
        func = function(paths, profile, src, venv, ws, mpath) {
          Sys.setenv(DICOMDEID_WORKSPACE = ws, DICOMDEID_VENV = venv,
                     KMP_DUPLICATE_LIB_OK = "TRUE")
          for (f in list.files(src, pattern = "[.]R$", full.names = TRUE)) source(f)
          res <- residual_scan_paths(paths, profile_id = profile)
          # Annotate the manifest (flag rows with residual PHI) when a batch is known.
          if (!is.na(mpath) && file.exists(mpath)) {
            con <- manifest_open(mpath); on.exit(manifest_close(con))
            for (r in res$results)
              set_residual(con, r$path, (r$counts %||% list())$total %||% 0L)
          }
          res
        },
        args = list(paths = paths, profile = profile, src = src, venv = venv,
                    ws = ws, mpath = mpath),
        supervise = TRUE)
      rv$running <- TRUE
      rv$msg <- sprintf("Scanning %d output file(s)\u2026", length(paths))
      shiny::showNotification(rv$msg, type = "message")
    })

    # --- poll for completion ----------------------------------------------
    shiny::observe({
      shiny::invalidateLater(1000, session)
      if (!isTRUE(rv$running) || is.null(rv$proc)) return()
      if (rv$proc$is_alive()) return()
      rv$running <- FALSE
      res <- tryCatch(rv$proc$get_result(), error = function(e) {
        rv$msg <- paste("Scan failed:", conditionMessage(e)); NULL })
      if (is.null(res)) { shiny::showNotification(rv$msg, type = "error"); return() }
      rv$res <- res
      rv$msg <- sprintf("Scanned %d file(s): %d passed, %d flagged.",
                        res$n_files, res$n_passed, res$n_flagged)
      audit_append("residual_scan", actor = app_state$user %||% "(unnamed)",
                   role = app_state$role %||% "deidentifier",
                   details = list(batch_id = rv$batch %||% "(folder)",
                                  scanned = res$n_files, flagged = res$n_flagged,
                                  passed = res$passed))
      shiny::showNotification(rv$msg, type = "message", duration = 8)
    })

    # --- sign-off (gated + audited) ---------------------------------------
    output$signoff_who <- shiny::renderUI({
      d <- rv$deidentifier
      shiny::p(class = "small",
        "Batch de-identifier: ",
        shiny::strong(if (is.na(d) || !nzchar(d %||% "")) "(unknown)" else d),
        shiny::br(),
        shiny::span(class = "text-muted",
          "A reviewer other than the de-identifier must sign off."))
    })

    shiny::observeEvent(input$signoff, {
      role <- app_state$role %||% "deidentifier"
      user <- app_state$user %||% ""
      gate <- can_sign_off(rv$deidentifier, user, role)
      if (!isTRUE(gate$ok)) { shiny::showNotification(gate$reason, type = "error"); return() }
      if (is.null(rv$res)) {
        shiny::showNotification("Run a residual scan before signing off.", type = "warning"); return()
      }
      e <- tryCatch(
        record_signoff(batch_id = rv$batch %||% (input$output_dir %||% "(folder)"),
                       deidentifier = rv$deidentifier, reviewer = user,
                       reviewer_role = role, decision = input$decision,
                       note = input$note %||% ""),
        error = function(err) { shiny::showNotification(conditionMessage(err),
          type = "error"); NULL })
      shiny::req(e)
      shiny::showNotification(sprintf("Signed off (%s) by %s. Recorded in the audit log.",
                                      input$decision, user), type = "message", duration = 8)
    })

    # --- outputs ----------------------------------------------------------
    output$status <- shiny::renderUI({
      shiny::tagList(
        if (!is.null(rv$msg)) shiny::p(class = "small mt-2", rv$msg),
        if (isTRUE(rv$running)) shiny::p(class = "small text-primary",
          shiny::icon("spinner", class = "fa-spin"), " scanning\u2026"))
    })

    output$verdict <- shiny::renderUI({
      res <- rv$res
      if (is.null(res)) return(shiny::p(class = "text-muted", "No scan run yet."))
      ok <- isTRUE(res$passed)
      shiny::div(
        shiny::span(class = sprintf("badge %s fs-6",
                     if (ok) "bg-success" else "bg-danger"),
                    if (ok) "PASS \u2014 no residual PHI" else "FLAGGED \u2014 residual PHI found"),
        shiny::span(class = "ms-3 text-muted",
          sprintf(paste("%d scanned \u00b7 %d passed \u00b7 %d flagged \u00b7",
                        "%d confirmed hit(s), %d low-confidence for review"),
                  res$n_files, res$n_passed, res$n_flagged,
                  res$total_confident %||% res$total_findings,
                  max(0L, (res$total_findings %||% 0L) - (res$total_confident %||% 0L)))))
    })

    output$categories <- shiny::renderUI({
      res <- rv$res; if (is.null(res) || !length(res$by_category)) return(NULL)
      chips <- lapply(names(res$by_category), function(nm)
        shiny::span(class = "badge bg-warning text-dark me-1",
                    sprintf("%s: %d", nm, res$by_category[[nm]])))
      shiny::div(class = "mt-2", chips)
    })

    output$notes <- shiny::renderUI({
      res <- rv$res; if (is.null(res)) return(NULL)
      notes <- unique(unlist(lapply(res$results, function(r) r$notes)))
      notes <- notes[nzchar(notes)]
      if (!length(notes)) return(NULL)
      shiny::div(class = "small text-muted mt-2",
        shiny::strong("Detector notes: "),
        shiny::tags$ul(lapply(notes, shiny::tags$li)))
    })

    output$files <- shiny::renderTable({
      res <- rv$res
      shiny::validate(shiny::need(!is.null(res) && nrow(res$files_df) > 0, "No scan run yet."))
      res$files_df
    }, striped = TRUE, spacing = "xs", width = "100%")

    output$findings <- shiny::renderTable({
      res <- rv$res
      shiny::validate(shiny::need(!is.null(res), "No scan run yet."))
      fd <- qa_findings_df(res$results)
      shiny::validate(shiny::need(nrow(fd) > 0, "No residual findings \u2014 clean."))
      fd
    }, striped = TRUE, spacing = "xs", width = "100%")

    # --- signatures + metadata review -------------------------------------
    scanned_paths <- shiny::reactive({
      res <- rv$res
      if (is.null(res)) character(0)
      else vapply(res$results, function(r) r$path %||% "", character(1))
    })

    shiny::observeEvent(rv$res, {
      paths <- scanned_paths()
      shiny::updateSelectInput(session, "meta_file",
        choices = stats::setNames(paths, basename(paths)),
        selected = if (length(paths)) paths[1] else NULL)
    })

    output$signatures <- shiny::renderTable({
      paths <- scanned_paths()
      shiny::validate(shiny::need(length(paths) > 0, "Run a scan to list outputs."))
      p <- app_state$project_obj
      shiny::validate(shiny::need(!is.null(p), "Select a project (Projects tab) to verify signatures."))
      sg <- project_resolve_signing(p)
      shiny::validate(shiny::need(isTRUE(sg$enabled), "Signing is off for this project."))
      shiny::validate(shiny::need(isTRUE(app_state$engine$available), "Engine not configured."))
      kp <- tryCatch(engine_ensure_keypair(sg$key_dir), error = function(e) NULL)
      shiny::validate(shiny::need(!is.null(kp), "No signing key available."))
      signature_status_df(paths, pub_path = kp$pub_path,
                          verify_fn = function(out, pub) engine_verify_output(out, pub))
    }, striped = TRUE, spacing = "xs", width = "100%")

    output$metadata <- shiny::renderTable({
      f <- input$meta_file
      shiny::validate(shiny::need(!is.null(f) && nzchar(f), "Pick a file."))
      shiny::validate(shiny::need(isTRUE(app_state$engine$available), "Engine not configured."))
      md <- tryCatch(engine_read_metadata(f, profile_id = input$profile %||% "default",
                                          mask_flagged = isTRUE(input$mask)),
                     error = function(e) NULL)
      shiny::validate(shiny::need(!is.null(md), "Could not read metadata."))
      do.call(rbind, lapply(md$rows, function(r) data.frame(
        Tag = r$tag %||% "", Keyword = r$keyword %||% "", VR = r$vr %||% "",
        Value = trunc_str(as.character(r$value %||% ""), 80),
        Flagged = if (isTRUE(r$flagged)) "yes" else "", stringsAsFactors = FALSE)))
    }, striped = TRUE, spacing = "xs", width = "100%")
  })
}
