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
        shiny::uiOutput(ns("learned"))
      )
    )
  )
}

mod_rules_editor_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
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
