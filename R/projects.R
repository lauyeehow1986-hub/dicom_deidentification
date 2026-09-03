#' Projects (Phase 6.5) — a portable bundle of de-identification settings.
#'
#' A project ties together a de-id profile, a hashing policy (global vs
#' project-scoped salt; reversible vs deterministic-irreversible), a signing
#' policy, the input/output roots, its own resumable manifest, processing log,
#' and hash-chained audit log. Everything lives under the writable workspace so
#' the whole folder copies onto a portable disk and rebinds to a new drive.
#'
#' Pure R: the keystore.json / signing keypair files are created by the Python
#' engine at run time; this module owns the descriptor (project.json) and path
#' resolution only.

#' Root of the projects tree in the workspace (created on demand).
projects_root <- function() {
  ws <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
  d <- file.path(ws, "projects")
  dir.create(d, showWarnings = FALSE, recursive = TRUE)
  d
}

#' The folder holding one project's bundle.
project_dir <- function(project_id) {
  d <- file.path(projects_root(), project_id)
  dir.create(d, showWarnings = FALSE, recursive = TRUE)
  d
}

#' Path to a project's descriptor.
project_path <- function(project_id) file.path(project_dir(project_id), "project.json")

#' Workspace-level shared stores (used when a project opts into global scope).
keystores_dir <- function() {
  ws <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
  d <- file.path(ws, "keystores"); dir.create(d, showWarnings = FALSE, recursive = TRUE); d
}
global_keystore_path <- function() file.path(keystores_dir(), "global.json")
global_signing_dir <- function() {
  ws <- Sys.getenv("DICOMDEID_WORKSPACE", file.path(getwd(), "workspace"))
  file.path(ws, "signing")
}

# Per-project bundle members.
project_keystore_path  <- function(project_id) file.path(project_dir(project_id), "keystore.json")
project_signing_dir    <- function(project_id) file.path(project_dir(project_id), "signing")
project_manifest_path  <- function(project_id) file.path(project_dir(project_id), "manifest.sqlite")
project_processed_log  <- function(project_id) file.path(project_dir(project_id), "processed_log.jsonl")

#' Persist a project descriptor (a named list) to its project.json.
project_save <- function(project) {
  path <- project_path(project$project_id)
  writeLines(jsonlite::toJSON(project, auto_unbox = TRUE, pretty = TRUE, null = "null"),
             path)
  invisible(project)
}

#' Read a project descriptor. Errors if the project does not exist.
project_get <- function(project_id) {
  path <- project_path(project_id)
  if (!file.exists(path)) stop(sprintf("no such project: %s", project_id))
  p <- jsonlite::fromJSON(path, simplifyVector = TRUE, simplifyDataFrame = FALSE)
  p$project_id <- project_id
  p
}

#' Create (or overwrite) a project. When `copy_from` names an existing project,
#' its hashing/signing policy and profile reference are inherited unless
#' overridden here; the new project always starts with a fresh manifest, log,
#' and audit chain (i.e. only the *settings* are copied, never the data).
project_create <- function(project_id, label = NULL, profile_id = NULL,
                           copy_from = NULL, hashing_scope = "project",
                           reversible = TRUE, signing_enabled = TRUE,
                           signing_scope = "global", root_in = "", root_out = "",
                           created_by = NA_character_) {
  base <- if (!is.null(copy_from)) project_get(copy_from) else NULL
  hashing <- list(
    scope      = if (!is.null(base) && missing_arg("hashing_scope")) base$hashing$scope else hashing_scope,
    reversible = if (!is.null(base) && missing_arg("reversible")) base$hashing$reversible else reversible)
  signing <- list(
    enabled = if (!is.null(base) && missing_arg("signing_enabled")) base$signing$enabled else signing_enabled,
    scope   = if (!is.null(base) && missing_arg("signing_scope")) base$signing$scope else signing_scope)
  project <- list(
    project_id = project_id,
    label      = label %||% (if (!is.null(base)) base$label else project_id),
    profile_id = profile_id %||% (if (!is.null(base)) base$profile_id else "default"),
    hashing    = hashing,
    signing    = signing,
    roots      = list(`in` = root_in, out = root_out),
    created_by = created_by,
    created_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"))
  project_save(project)
  project
}

# TRUE when the caller left argument `name` at its default in project_create.
missing_arg <- function(name) {
  cl <- sys.call(-1L)
  !(name %in% names(cl))
}

#' Every project as a small summary list, for the picker.
projects_list <- function() {
  dirs <- list.dirs(projects_root(), recursive = FALSE, full.names = TRUE)
  out <- list()
  for (d in dirs) {
    pid <- basename(d)
    if (file.exists(file.path(d, "project.json"))) {
      p <- tryCatch(project_get(pid), error = function(e) NULL)
      if (!is.null(p))
        out[[length(out) + 1L]] <- list(project_id = pid,
                                         label = p$label %||% pid,
                                         profile_id = p$profile_id %||% "default")
    }
  }
  out
}

#' Update a project's input/output roots (used after a portable-disk swap) and
#' persist. The manifest is untouched — it stores paths relative to these roots.
project_rebind_roots <- function(project_id, root_in, root_out) {
  p <- project_get(project_id)
  p$roots <- list(`in` = root_in, out = root_out)
  project_save(p)
  p
}

#' Resolve which keystore file (salt) a project uses, and its reversibility.
#' scope="global" -> one portable shared salt; scope="project" -> its own.
project_resolve_keystore <- function(project) {
  scope <- project$hashing$scope %||% "project"
  path <- if (identical(scope, "global")) global_keystore_path()
          else project_keystore_path(project$project_id)
  list(path = path, reversible = isTRUE(project$hashing$reversible), scope = scope)
}

#' Resolve the signing key directory and whether signing is enabled.
project_resolve_signing <- function(project) {
  enabled <- isTRUE(project$signing$enabled)
  scope <- project$signing$scope %||% "global"
  key_dir <- if (identical(scope, "global")) global_signing_dir()
             else project_signing_dir(project$project_id)
  list(enabled = enabled, scope = scope, key_dir = key_dir)
}
