#' Top-level UI: a bslib navbar shell with one tab per workflow module.
#'
#' Each tab is a Shiny module (mod_*.R). At scaffold stage the tabs render
#' placeholders describing what the phase will build.
app_ui <- function() {
  bslib::page_navbar(
    title = "DICOM De-Identification",
    id = "main_nav",
    theme = bslib::bs_theme(version = 5, preset = "cosmo"),
    header = disclaimer_banner(),

    bslib::nav_panel("Interactive",  mod_interactive_ui("interactive")),
    bslib::nav_panel("Pixels",       mod_pixels_ui("pixels")),
    bslib::nav_panel("Rules & Profiles", mod_rules_editor_ui("rules")),
    bslib::nav_panel("Tagging",      mod_tagging_ui("tagging")),
    bslib::nav_panel("Bulk",         mod_bulk_ui("bulk")),
    bslib::nav_panel("QA / Review",  mod_qa_ui("qa")),
    bslib::nav_panel("Keystore",     mod_keystore_ui("keystore")),
    bslib::nav_panel("Audit",        mod_audit_ui("audit")),

    bslib::nav_spacer(),
    bslib::nav_item(identity_control()),
    bslib::nav_item(shiny::uiOutput("engine_status", inline = TRUE))
  )
}

#' Compact "acting as" control (name + role) in the navbar. Role drives the QA
#' sign-off gate; the name is recorded as the actor in the audit log. A production
#' deployment can replace this with an OS/AD or shinymanager login.
identity_control <- function() {
  shiny::div(
    class = "d-flex align-items-center gap-2 me-2",
    shiny::tags$span(class = "small text-muted", "acting as"),
    shiny::div(class = "mb-0",
      shiny::textInput("acting_user", label = NULL, placeholder = "your name",
                       width = "120px")),
    shiny::div(class = "mb-0",
      shiny::selectInput("acting_role", label = NULL, width = "150px",
                         choices = c("De-identifier" = "deidentifier",
                                     "Reviewer" = "reviewer")))
  )
}

#' Persistent non-diagnostic disclaimer shown on every tab.
disclaimer_banner <- function() {
  shiny::div(
    class = "alert alert-warning m-0 rounded-0 py-1 small text-center",
    shiny::strong("Research / educational de-identification tool. "),
    "Not for diagnosis or clinical decision-making. Never load real patient data into a shared or networked instance."
  )
}
