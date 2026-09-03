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

# --------------------------------------------------------------------------- #
# Credential store (Phase 6). shinymanager is not available on the air-gapped   #
# box, so we keep a light, dependency-free salted-hash store. It is enough to    #
# separate the two roles; harden with an OS/AD login at deployment if required.  #
# --------------------------------------------------------------------------- #

#' Salted SHA-256 of a password (never store the password itself).
.pw_hash <- function(password, salt) {
  digest::digest(paste0(salt, password), algo = "sha256", serialize = FALSE)
}

#' Build a user record with a random salt and a hashed password.
make_user <- function(name, password, role = "deidentifier") {
  role <- match.arg(role, ROLES)
  salt <- paste(format(as.hexmode(as.integer(openssl::rand_bytes(16)))),
                collapse = "")
  list(name = name, role = role, salt = salt,
       hash = .pw_hash(password, salt))
}

#' Verify a login against a store (a list of `make_user` records).
#' Returns the user's role on success, or NULL on any failure.
verify_user <- function(store, name, password) {
  for (u in store) {
    if (identical(u$name, name) &&
        identical(.pw_hash(password, u$salt), u$hash)) {
      return(u$role)
    }
  }
  NULL
}

# --------------------------------------------------------------------------- #
# The QA sign-off gate: a reviewer (never the de-identifier) approves a batch.  #
# --------------------------------------------------------------------------- #

#' Can `reviewer` (with `reviewer_role`) sign off a batch de-identified by
#' `deidentifier`? Enforces the segregation-of-duties rule: only a reviewer, and
#' never the person who ran the de-identification, may approve.
#' Returns `list(ok, reason)`.
can_sign_off <- function(deidentifier, reviewer, reviewer_role) {
  if (!can_review(reviewer_role))
    return(list(ok = FALSE, reason = "Only a reviewer can sign off."))
  if (is.null(reviewer) || !nzchar(trimws(reviewer)))
    return(list(ok = FALSE, reason = "Reviewer identity is required."))
  if (identical(tolower(trimws(reviewer)), tolower(trimws(deidentifier %||% ""))))
    return(list(ok = FALSE,
                reason = "The de-identifier cannot sign off their own batch."))
  list(ok = TRUE, reason = "")
}

#' Record a reviewer sign-off in the audit log after enforcing `can_sign_off`.
#' Errors (writing nothing) if the gate refuses. `decision` is "pass" | "fail".
record_signoff <- function(batch_id, deidentifier, reviewer, reviewer_role,
                           decision, note = "", audit_path = audit_file()) {
  gate <- can_sign_off(deidentifier, reviewer, reviewer_role)
  if (!isTRUE(gate$ok)) stop(gate$reason, call. = FALSE)
  if (!decision %in% c("pass", "fail"))
    stop("decision must be 'pass' or 'fail'.", call. = FALSE)
  audit_append("signoff", actor = reviewer, role = reviewer_role,
               details = list(batch_id = batch_id, deidentifier = deidentifier,
                              decision = decision, note = note),
               path = audit_path)
}
