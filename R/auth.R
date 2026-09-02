#' Two-role access control: "deidentifier" and "reviewer".
#'
#' The QA gate requires that the person who de-identifies a study cannot also
#' sign it off — a reviewer must approve. Scaffold stage defines the role model
#' and predicates; shinymanager-based login + the enforced sign-off gate land in
#' Phase 6.

ROLES <- c("deidentifier", "reviewer")

#' Can this role run de-identification?
can_deidentify <- function(role) role %in% c("deidentifier")

#' Can this role approve/sign-off QA?
can_review <- function(role) role %in% c("reviewer")

#' Guard a reactive/observer so only permitted roles proceed; otherwise notify.
require_role <- function(app_state, predicate, session = shiny::getDefaultReactiveDomain()) {
  ok <- predicate(app_state$role)
  if (!ok && !is.null(session)) {
    shiny::showNotification("Your role is not permitted to perform this action.",
                            type = "error")
  }
  ok
}

# TODO(Phase 6): shinymanager credential store (encrypted), login UI wrap,
# per-action enforcement, and audit-logged reviewer sign-off.
