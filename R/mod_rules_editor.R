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
