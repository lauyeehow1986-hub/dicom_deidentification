#' Rules & profile editor: view/clone/edit per-project profiles and pick the
#' active one the other tabs use. Phase 4.
#'
#' A profile = PS3.15 option toggles + date/reversibility policy + private-tag
#' policy + the layered text-detection config. Shipped profiles are read-only;
#' editing one writes a copy into the writable workspace (engine_profile_save),
#' so the air-gapped package stays pristine.

mod_rules_editor_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 360,
      shiny::selectInput(ns("profile"), "Profile", choices = c("default")),
      shiny::uiOutput(ns("source_badge")),
      shiny::actionButton(ns("activate"), "Use for other tabs",
                          class = "btn-primary btn-sm", icon = shiny::icon("check")),
      shiny::hr(),
      shiny::strong("Save"),
      shiny::actionButton(ns("save"), "Save changes", class = "btn-success btn-sm",
                          icon = shiny::icon("floppy-disk")),
      shiny::div(
        class = "mt-2",
        shiny::textInput(ns("new_id"), "New project id",
                         placeholder = "cardio_ct"),
        shiny::textInput(ns("new_label"), "Label (optional)",
                         placeholder = "Cardiac CT"),
        shiny::actionButton(ns("clone"), "Save as new project",
                            class = "btn-outline-secondary btn-sm",
                            icon = shiny::icon("copy"))
      ),
      shiny::uiOutput(ns("status"))
    ),
    bslib::card(
      bslib::card_header("Profile settings"),
      bslib::card_body(
        bslib::layout_columns(
          col_widths = c(6, 6),
          shiny::div(
            shiny::h6("Dates"),
            shiny::radioButtons(ns("dates_mode"), NULL,
                                choices = c("Remove" = "remove",
                                            "Shift (interval-preserving)" = "shift",
                                            "Keep" = "keep"), selected = "remove"),
            shiny::h6("Reversibility"),
            shiny::radioButtons(ns("rev_mode"), NULL,
                                choices = c("Reversible (keystore)" = "reversible",
                                            "Irreversible" = "irreversible"),
                                selected = "reversible"),
            shiny::h6("Private tags"),
            shiny::selectInput(ns("priv_policy"), NULL,
                               choices = c("Strip unknown" = "strip_unknown",
                                           "Keep allowlisted" = "keep_allowlisted",
                                           "Keep all" = "keep_all"))
          ),
          shiny::div(
            shiny::h6("PS3.15 options"),
            shiny::checkboxInput(ns("clean_descriptors"), "Clean free-text descriptors", TRUE),
            shiny::checkboxInput(ns("clean_pixel_data"), "Clean burned-in pixels", TRUE),
            shiny::checkboxInput(ns("retain_characteristics"), "Retain age/sex/height/weight", TRUE),
            shiny::checkboxInput(ns("retain_device"), "Retain device identity", FALSE),
            shiny::checkboxInput(ns("retain_institution"), "Retain institution identity", FALSE),
            shiny::checkboxInput(ns("retain_uids"), "Retain UIDs (else remap)", FALSE)
          )
        ),
        shiny::hr(),
        shiny::h6("Text PHI detection"),
        bslib::layout_columns(
          col_widths = c(6, 6),
          shiny::div(
            shiny::checkboxInput(ns("td_enabled"), "Enabled", TRUE),
            shiny::checkboxInput(ns("td_header"), "Header-token scrub", TRUE),
            shiny::checkboxInput(ns("td_presidio"), "Presidio (needs spaCy model)", TRUE),
            shiny::checkboxInput(ns("td_ner"), "Transformer NER (needs local model)", FALSE)
          ),
          shiny::div(
            shiny::checkboxInput(ns("td_dates"), "Scrub dates in free text", TRUE),
            shiny::checkboxInput(ns("td_dates_bare"),
                                 "Also flag bare 8-digit dates (may hit numeric IDs)", FALSE),
            shiny::p(class = "small text-muted",
                     paste("Free-text dates only (dd/mm/yyyy, yyyy-mm-dd, ISO date-time,",
                           "\"01 Jan 2024\"). Structured DICOM date tags follow the Dates",
                           "policy above. The bare-8-digit option adds ddmmyyyy / yyyymmdd",
                           "runs — higher recall, more false positives on IDs.")),
            shiny::textInput(ns("td_ner_model"), "NER model directory (local)", ""),
            shiny::p(class = "small text-muted",
                     "Gazetteers and custom-regex rules are grown in the Tagging tab; the read-outs below reflect this project.")
          )
        ),
        shiny::hr(),
        shiny::h6("Teach a site-specific format (opt-in)"),
        shiny::p(class = "small text-muted",
          paste("Paste one or more sample identifiers of the SAME shape (e.g. a",
                "case or accession number). The engine derives a regex that",
                "scrubs matching values at de-id time and flags them in the QA",
                "scan. Samples are used only to derive the pattern — they are",
                "not stored.")),
        bslib::layout_columns(
          col_widths = c(5, 4, 3),
          shiny::textAreaInput(ns("fmt_samples"), "Sample(s), one per line",
                               rows = 2, placeholder = "e.g. 324-58-2995"),
          shiny::textInput(ns("fmt_category"), "Category", value = "case_number"),
          shiny::numericInput(ns("fmt_score"), "Score", value = 0.95,
                              min = 0.5, max = 1, step = 0.05)
        ),
        shiny::div(
          shiny::actionButton(ns("fmt_preview"), "Preview rule",
                              class = "btn-outline-secondary btn-sm",
                              icon = shiny::icon("wand-magic-sparkles")),
          shiny::actionButton(ns("fmt_add"), "Add to this profile",
                              class = "btn-success btn-sm ms-1",
                              icon = shiny::icon("plus"))
        ),
        shiny::uiOutput(ns("fmt_result")),
        shiny::hr(),
        shiny::h6("Load a name list — gazetteer (opt-in)"),
        shiny::p(class = "small text-muted",
          paste("Upload a CSV or text file of names. Pick the column that holds",
                "the names and, if the file has a header row, tick to drop it.",
                "Names are matched whole and case-insensitively — scrubbed at",
                "de-id and flagged in the QA scan. Multi-column CSVs are fine; only",
                "the chosen column is used.")),
        shiny::fileInput(ns("gaz_file"), "Name list (CSV / TSV / TXT)",
                         accept = c(".csv", ".tsv", ".txt"),
                         placeholder = "names.csv"),
        bslib::layout_columns(
          col_widths = c(7, 5),
          shiny::selectInput(ns("gaz_col"), "Name column", choices = character(0)),
          shiny::div(class = "mt-4 pt-2",
            shiny::checkboxInput(ns("gaz_header"),
                                 "First row is a header (drop it)", value = TRUE))
        ),
        shiny::uiOutput(ns("gaz_preview")),
        shiny::actionButton(ns("gaz_add"), "Add names to this profile",
                            class = "btn-success btn-sm",
                            icon = shiny::icon("address-book")),
        shiny::uiOutput(ns("gaz_result")),
        shiny::uiOutput(ns("learned")),
        shiny::uiOutput(ns("remove_rule_ui"))
      )
    )
  )
}

mod_rules_editor_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    ns <- session$ns
    current <- shiny::reactiveVal(NULL)   # the loaded profile (a list)
    status  <- shiny::reactiveVal(NULL)

    profiles <- shiny::reactive({
      app_state$profiles_version
      if (!isTRUE(app_state$engine$available)) return(list())
      tryCatch(engine_profiles_list(), error = function(e) list())
    })

    # Keep the profile dropdown's choices in sync with the workspace, preserving
    # the user's current selection (only defaulting when it has gone away).
    want_select <- shiny::reactiveVal(NULL)  # set to force a selection (e.g. after clone)
    shiny::observe({
      ps <- profiles()
      ids <- vapply(ps, function(p) p$id, character(1))
      if (!length(ids)) ids <- "default"
      forced <- shiny::isolate(want_select())
      keep <- shiny::isolate(input$profile)
      sel <- if (!is.null(forced) && forced %in% ids) forced
             else if (!is.null(keep) && keep %in% ids) keep
             else app_state$profile_id %||% ids[1]
      shiny::updateSelectInput(session, "profile", choices = ids, selected = sel)
      if (!is.null(forced)) want_select(NULL)
    })

    load_into_form <- function(prof) {
      opt <- prof$options %||% list()
      td  <- prof$text_detection %||% list()
      shiny::updateRadioButtons(session, "dates_mode",
                                selected = (prof$dates %||% list())$mode %||% "remove")
      shiny::updateRadioButtons(session, "rev_mode",
                                selected = (prof$reversibility %||% list())$mode %||% "reversible")
      shiny::updateSelectInput(session, "priv_policy",
                               selected = (prof$private_tags %||% list())$policy %||% "strip_unknown")
      set_chk <- function(inp, v, default)
        shiny::updateCheckboxInput(session, inp, value = isTRUE(v %||% default))
      set_chk("clean_descriptors", opt$clean_descriptors, TRUE)
      set_chk("clean_pixel_data", opt$clean_pixel_data, TRUE)
      set_chk("retain_characteristics", opt$retain_patient_characteristics, TRUE)
      set_chk("retain_device", opt$retain_device_identity, FALSE)
      set_chk("retain_institution", opt$retain_institution_identity, FALSE)
      set_chk("retain_uids", opt$retain_uids, FALSE)
      set_chk("td_enabled", td$enabled, TRUE)
      set_chk("td_header", td$header_token_scrub, TRUE)
      set_chk("td_presidio", td$use_presidio, TRUE)
      set_chk("td_ner", td$use_ner, FALSE)
      set_chk("td_dates", td$detect_dates, TRUE)
      set_chk("td_dates_bare", td$dates_include_bare, FALSE)
      shiny::updateTextInput(session, "td_ner_model", value = td$ner_model %||% "")
    }

    shiny::observeEvent(input$profile, {
      shiny::req(input$profile)
      if (!isTRUE(app_state$engine$available)) return()
      prof <- tryCatch(engine_profile_get(input$profile), error = function(e) NULL)
      shiny::req(prof)
      current(prof)
      load_into_form(prof)
    }, ignoreInit = FALSE)

    output$source_badge <- shiny::renderUI({
      prof <- current(); if (is.null(prof)) return(NULL)
      src <- prof$`_source` %||% "shipped"
      active <- identical(input$profile, app_state$profile_id)
      shiny::tagList(
        shiny::span(class = if (src == "workspace") "badge bg-info" else "badge bg-secondary",
                    sprintf("%s profile", src)),
        if (active) shiny::span(class = "badge bg-primary ms-1", "active on other tabs")
      )
    })

    output$learned <- shiny::renderUI({
      prof <- current(); if (is.null(prof)) return(NULL)
      td <- prof$text_detection %||% list()
      rx <- td$custom_regex %||% list()
      gz <- td$extra_gazetteer_files %||% list()
      if (!length(rx) && !length(gz))
        return(shiny::p(class = "small text-muted", "No learned rules yet for this project."))
      shiny::tagList(
        shiny::hr(),
        shiny::h6("Learned from tagging"),
        if (length(gz)) shiny::p(class = "small",
                                 sprintf("%d custom gazetteer file(s).", length(gz))),
        if (length(rx)) shiny::tags$ul(class = "small",
          lapply(rx, function(r) shiny::tags$li(sprintf("%s: %s",
                                                        r$category %||% "?", r$pattern %||% ""))))
      )
    })

    # --- opt-in: teach a site-specific format ------------------------------
    .fmt_samples <- function() {
      raw <- input$fmt_samples %||% ""
      s <- trimws(unlist(strsplit(raw, "[\r\n,]+")))
      s[nzchar(s)]
    }

    shiny::observeEvent(input$fmt_preview, {
      if (!isTRUE(app_state$engine$available)) return()
      s <- .fmt_samples()
      if (!length(s)) {
        shiny::showNotification("Enter at least one sample.", type = "warning"); return()
      }
      pat <- tryCatch(engine_derive_pattern(s), error = function(e) {
        shiny::showNotification(conditionMessage(e), type = "error"); NULL })
      shiny::req(pat)
      output$fmt_result <- shiny::renderUI(shiny::div(
        class = "small mt-2",
        shiny::strong("Derived pattern: "), shiny::tags$code(pat), shiny::br(),
        shiny::span(class = "text-muted",
                    sprintf("Matches all %d sample(s). Nothing added yet — click 'Add'.",
                            length(s)))))
    })

    shiny::observeEvent(input$fmt_add, {
      shiny::req(input$profile); if (!isTRUE(app_state$engine$available)) return()
      s <- .fmt_samples()
      if (!length(s)) {
        shiny::showNotification("Enter at least one sample.", type = "warning"); return()
      }
      cat <- gsub("[^A-Za-z0-9_]+", "_", trimws(input$fmt_category %||% "case_number"))
      if (!nzchar(cat)) cat <- "case_number"
      res <- tryCatch(
        engine_add_pattern_rule(input$profile, cat, s, score = input$fmt_score %||% 0.95),
        error = function(e) { shiny::showNotification(conditionMessage(e), type = "error"); NULL })
      shiny::req(res)
      current(engine_profile_get(input$profile))
      app_state$profiles_version <- (app_state$profiles_version %||% 0L) + 1L
      if (identical(input$profile, app_state$profile_id)) app_state$profile <- current()
      output$fmt_result <- shiny::renderUI(shiny::div(
        class = "small text-success mt-2",
        sprintf("Added %s rule to '%s': ", cat, input$profile),
        shiny::tags$code(res$pattern)))
      shiny::showNotification("Rule added to this profile.", type = "message")
    })

    # --- opt-in: load a name-list gazetteer from an uploaded CSV/TXT --------
    # Delimiter-sniffing reader: a name-per-line TXT becomes one column; a CSV/TSV
    # keeps its columns so the user can pick which one holds the names.
    read_name_table <- function(path) {
      lines <- tryCatch(readLines(path, n = 100, warn = FALSE, encoding = "UTF-8"),
                        error = function(e) character(0))
      nonblank <- lines[nzchar(trimws(lines))]
      if (!length(nonblank)) return(NULL)
      first <- nonblank[1]
      sep <- if (grepl("\t", first, fixed = TRUE)) "\t"
             else if (grepl(",", first, fixed = TRUE)) ","
             else if (grepl(";", first, fixed = TRUE)) ";"
             else NA_character_
      if (is.na(sep)) {
        all_lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
        return(data.frame(V1 = all_lines, stringsAsFactors = FALSE))
      }
      tryCatch(
        utils::read.table(path, header = FALSE, sep = sep, quote = "\"",
                          stringsAsFactors = FALSE, colClasses = "character",
                          fill = TRUE, comment.char = "", check.names = FALSE,
                          encoding = "UTF-8"),
        error = function(e) NULL)
    }

    gaz_raw <- shiny::reactiveVal(NULL)  # data.frame, read with header = FALSE

    shiny::observeEvent(input$gaz_file, {
      shiny::req(input$gaz_file$datapath)
      tbl <- read_name_table(input$gaz_file$datapath)
      if (is.null(tbl) || !nrow(tbl)) {
        shiny::showNotification("Could not read any rows from that file.",
                                type = "warning")
        gaz_raw(NULL); return()
      }
      gaz_raw(tbl)
      output$gaz_result <- shiny::renderUI(NULL)  # clear a prior success message
    })

    # Apply the header toggle: when ticked, row 1 supplies the column labels and
    # is dropped from the data; otherwise columns are generic and all rows kept.
    gaz_parsed <- shiny::reactive({
      tbl <- gaz_raw(); shiny::req(tbl)
      if (isTRUE(input$gaz_header) && nrow(tbl) >= 1) {
        cn <- trimws(as.character(unlist(tbl[1, ])))
        body <- tbl[-1, , drop = FALSE]
      } else {
        cn <- character(ncol(tbl))
        body <- tbl
      }
      blank <- is.na(cn) | !nzchar(cn)
      cn[blank] <- paste("Column", which(blank))
      names(body) <- make.unique(cn)
      list(cols = names(body), body = body)
    })

    shiny::observeEvent(gaz_parsed(), {
      cols <- gaz_parsed()$cols
      keep <- shiny::isolate(input$gaz_col)
      sel <- if (!is.null(keep) && keep %in% cols) keep else cols[1]
      shiny::updateSelectInput(session, "gaz_col", choices = cols, selected = sel)
    })

    gaz_names <- shiny::reactive({
      p <- gaz_parsed(); col <- input$gaz_col
      shiny::req(col %in% p$cols)
      v <- trimws(as.character(p$body[[col]]))
      v <- v[nzchar(v) & !startsWith(v, "#")]
      unique(v)
    })

    output$gaz_preview <- shiny::renderUI({
      if (is.null(gaz_raw())) return(NULL)
      nm <- tryCatch(gaz_names(), error = function(e) character(0))
      if (!length(nm))
        return(shiny::p(class = "small text-warning",
                        "No names in the selected column (check the header toggle)."))
      shown <- utils::head(nm, 8)
      shiny::div(class = "small mt-1",
        shiny::strong(sprintf("%d name(s) ready. ", length(nm))),
        shiny::span(class = "text-muted",
                    paste0("Preview: ", paste(shown, collapse = ", "),
                           if (length(nm) > length(shown)) " ..." else "")))
    })

    shiny::observeEvent(input$gaz_add, {
      shiny::req(input$profile); if (!isTRUE(app_state$engine$available)) return()
      nm <- tryCatch(gaz_names(), error = function(e) character(0))
      if (!length(nm)) {
        shiny::showNotification("No names to add — upload a file and pick a column.",
                                type = "warning"); return()
      }
      res <- tryCatch(engine_add_gazetteer_names(input$profile, nm),
        error = function(e) {
          shiny::showNotification(conditionMessage(e), type = "error"); NULL })
      shiny::req(res)
      current(engine_profile_get(input$profile))
      app_state$profiles_version <- (app_state$profiles_version %||% 0L) + 1L
      if (identical(input$profile, app_state$profile_id)) app_state$profile <- current()
      output$gaz_result <- shiny::renderUI(shiny::div(
        class = "small text-success mt-2",
        sprintf("Added %d new name(s) to '%s' (%d skipped as duplicates; %d in the list now).",
                res$added %||% 0L, input$profile, res$skipped %||% 0L, res$total %||% 0L)))
      shiny::showNotification(
        sprintf("Gazetteer updated: %d added, %d total.",
                res$added %||% 0L, res$total %||% 0L), type = "message")
    })

    output$remove_rule_ui <- shiny::renderUI({
      prof <- current(); if (is.null(prof)) return(NULL)
      rx <- (prof$text_detection %||% list())$custom_regex %||% list()
      if (!length(rx)) return(NULL)
      choices <- vapply(rx, function(r) r$pattern %||% "", character(1))
      names(choices) <- vapply(rx, function(r)
        sprintf("%s: %s", r$category %||% "?", r$pattern %||% ""), character(1))
      shiny::div(class = "mt-2",
        shiny::selectInput(ns("rm_rule_sel"), "Remove a rule", choices = choices),
        shiny::actionButton(ns("rm_rule"), "Remove selected rule",
                            class = "btn-outline-danger btn-sm",
                            icon = shiny::icon("trash")))
    })

    shiny::observeEvent(input$rm_rule, {
      shiny::req(input$profile, input$rm_rule_sel)
      if (!isTRUE(app_state$engine$available)) return()
      res <- tryCatch(
        engine_remove_custom_rule(input$profile, pattern = input$rm_rule_sel),
        error = function(e) { shiny::showNotification(conditionMessage(e), type = "error"); NULL })
      shiny::req(res)
      current(engine_profile_get(input$profile))
      app_state$profiles_version <- (app_state$profiles_version %||% 0L) + 1L
      if (identical(input$profile, app_state$profile_id)) app_state$profile <- current()
      shiny::showNotification(sprintf("Removed %d rule(s).", res$removed %||% 0L),
                              type = "message")
    })

    # Collect the form back into the loaded profile, preserving fields we don't edit.
    collect <- function() {
      prof <- current() %||% list()
      prof$`_source` <- NULL
      prof$dates <- prof$dates %||% list()
      prof$dates$mode <- input$dates_mode
      prof$reversibility <- prof$reversibility %||% list()
      prof$reversibility$mode <- input$rev_mode
      prof$private_tags <- prof$private_tags %||% list()
      prof$private_tags$policy <- input$priv_policy
      prof$options <- prof$options %||% list()
      prof$options$clean_descriptors <- isTRUE(input$clean_descriptors)
      prof$options$clean_pixel_data <- isTRUE(input$clean_pixel_data)
      prof$options$retain_patient_characteristics <- isTRUE(input$retain_characteristics)
      prof$options$retain_device_identity <- isTRUE(input$retain_device)
      prof$options$retain_institution_identity <- isTRUE(input$retain_institution)
      prof$options$retain_uids <- isTRUE(input$retain_uids)
      prof$text_detection <- prof$text_detection %||% list()
      prof$text_detection$enabled <- isTRUE(input$td_enabled)
      prof$text_detection$header_token_scrub <- isTRUE(input$td_header)
      prof$text_detection$use_presidio <- isTRUE(input$td_presidio)
      prof$text_detection$use_ner <- isTRUE(input$td_ner)
      prof$text_detection$detect_dates <- isTRUE(input$td_dates)
      prof$text_detection$dates_include_bare <- isTRUE(input$td_dates_bare)
      prof$text_detection$ner_model <- input$td_ner_model
      prof
    }

    do_save <- function(profile_id, prof) {
      out <- tryCatch(engine_profile_save(profile_id, prof), error = function(e) {
        shiny::showNotification(paste("Save failed:", conditionMessage(e)), type = "error")
        NULL
      })
      if (!is.null(out)) {
        app_state$profiles_version <- (app_state$profiles_version %||% 0L) + 1L
        status(sprintf("Saved '%s' to the workspace.", profile_id))
        shiny::showNotification("Profile saved.", type = "message")
      }
      out
    }

    shiny::observeEvent(input$save, {
      shiny::req(input$profile); if (!isTRUE(app_state$engine$available)) return()
      out <- do_save(input$profile, collect())
      if (!is.null(out)) {
        current(engine_profile_get(input$profile))
        if (identical(input$profile, app_state$profile_id))
          app_state$profile <- current()
      }
    })

    shiny::observeEvent(input$clone, {
      shiny::req(input$profile, input$new_id); if (!isTRUE(app_state$engine$available)) return()
      new_id <- gsub("[^A-Za-z0-9_]+", "_", trimws(input$new_id))
      if (!nzchar(new_id)) {
        shiny::showNotification("Enter a new project id.", type = "warning"); return()
      }
      cl <- tryCatch(engine_profile_clone(input$profile, new_id,
                                          label = input$new_label %||% NULL),
                     error = function(e) {
                       shiny::showNotification(paste("Clone failed:", conditionMessage(e)),
                                               type = "error"); NULL })
      shiny::req(cl)
      prof <- collect(); prof$profile_id <- new_id
      if (nzchar(input$new_label %||% "")) prof$label <- input$new_label
      do_save(new_id, prof)
      want_select(new_id)  # switch the editor to the freshly created project
    })

    shiny::observeEvent(input$activate, {
      shiny::req(input$profile)
      app_state$profile_id <- input$profile
      app_state$profile <- current() %||% engine_profile_get(input$profile)
      status(sprintf("'%s' is now the active profile for the other tabs.", input$profile))
      shiny::showNotification(sprintf("Active profile: %s", input$profile), type = "message")
    })

    output$status <- shiny::renderUI({
      s <- status(); if (is.null(s)) return(NULL)
      shiny::p(class = "small text-success mt-2", s)
    })
  })
}
