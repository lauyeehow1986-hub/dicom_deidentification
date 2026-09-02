#' Thin bridge to the Python de-identification engine (inst/python/deid_engine).
#'
#' All reticulate use is confined to this file so the Shiny modules stay pure R.
#' The bridge degrades gracefully: if the relocatable venv isn't built yet, the
#' app still launches and the modules show an "engine not configured" state.

.engine_env <- new.env(parent = emptyenv())

#' Path to the relocatable Python venv (built by inst/python/build_venv.R).
engine_venv_path <- function() {
  # override for the air-gapped box via env var; default is alongside the engine
  Sys.getenv("DICOMDEID_VENV", file.path("inst", "python", ".venv"))
}

#' Return a (cached) handle describing engine availability.
#'
#' @return list(available = logical, module = <python module or NULL>, reason = chr)
engine_handle <- function() {
  if (!is.null(.engine_env$handle)) return(.engine_env$handle)

  handle <- tryCatch({
    venv <- engine_venv_path()
    if (!dir.exists(venv)) {
      list(available = FALSE, module = NULL,
           reason = paste0("venv not found at ", venv, " (see docs/airgap-install.md)"))
    } else {
      reticulate::use_virtualenv(venv, required = TRUE)
      mod <- reticulate::import("deid_engine", delay_load = TRUE)
      list(available = TRUE, module = mod, reason = "ok")
    }
  }, error = function(e) {
    list(available = FALSE, module = NULL, reason = conditionMessage(e))
  })

  .engine_env$handle <- handle
  handle
}

#' Convenience predicate.
engine_available <- function() isTRUE(engine_handle()$available)

#' Call a function on the engine module, erroring clearly if unavailable.
#' @keywords internal
call_engine <- function(fn, ...) {
  h <- engine_handle()
  if (!h$available) stop("Python engine unavailable: ", h$reason, call. = FALSE)
  do.call(h$module[[fn]], list(...))
}

#' De-identify a folder/file via the engine (Phase 1 high-level entry).
#'
#' @param keystore_path NULL -> irreversible (ephemeral); a path -> reversible,
#'   creating or opening an encrypted keystore protected by `passphrase`.
#' @return the engine report (per-file records + counts) as an R list.
engine_deid_run <- function(input_path, output_path, profile_id = "default",
                            keystore_path = NULL, passphrase = NULL) {
  call_engine("deid_run", input_path, output_path, profile_id, keystore_path, passphrase)
}
