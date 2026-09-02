#' Encrypted keystore & crosswalk management (reversible pseudonymisation).
#' Phase 1 (create/use) + Phase 6 (governed access).

mod_keystore_ui <- function(id) {
  ns <- shiny::NS(id)
  placeholder_panel(
    "Keystore & crosswalk", "Phase 1 & 6",
    bullets = c(
      "Encrypted store (AEAD/Argon2) of orig<->pseudonym crosswalk, salts, date offsets.",
      "Reversible mode persists it; irreversible mode never writes a key.",
      "Look up / export the crosswalk for authorised re-identification (audited)."
    )
  )
}

mod_keystore_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    # TODO(Phase 1): open/create encrypted keystore; reuse shinyEncrypt AEAD pattern.
    invisible(NULL)
  })
}
