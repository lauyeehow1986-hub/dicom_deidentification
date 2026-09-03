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
                            keystore_path = NULL, passphrase = NULL,
                            reversible = TRUE, sign_key_path = NULL,
                            signer = NULL, project_id = NULL,
                            autoredact_pixels = FALSE) {
  call_engine("deid_run", input_path, output_path, profile_id, keystore_path,
              passphrase, reversible, sign_key_path, signer, project_id,
              autoredact_pixels)
}

#' Phase 6.5 integrity + signing + metadata bridges.

#' Streaming SHA-256 of a file (before/after de-id integrity).
engine_file_sha256 <- function(path) call_engine("file_sha256", path)

#' Ensure an Ed25519 signing keypair exists under `dir`; returns key/pub paths.
engine_ensure_keypair <- function(dir) call_engine("ensure_keypair", dir)

#' Verify an output's detached signature; returns list(ok, reason).
engine_verify_output <- function(output_path, pub_path) {
  call_engine("verify_output", output_path, pub_path)
}

#' The de-identified header as reviewer rows (masking any residual-flagged value).
engine_read_metadata <- function(path, profile_id = "default",
                                 mask_flagged = TRUE, min_score = 0.5) {
  call_engine("read_metadata", path, profile_id, mask_flagged, min_score)
}

#' Phase 3 pixel entries (burned-in PHI viewer + redaction).

#' Geometry + OCR-proposed redaction boxes for a DICOM file.
engine_pixel_info <- function(path, profile_id = "default") {
  call_engine("pixel_info", path, profile_id)
}

#' Base64 PNG of one frame, with `boxes` drawn as outlines (for review).
#' @param boxes an R list of lists each with x/y/w/h (and optional frame), or NULL.
engine_pixel_frame_png <- function(path, frame = 0, boxes = NULL, max_side = 640L) {
  call_engine("pixel_frame_png", path, as.integer(frame), boxes, as.integer(max_side))
}

#' Apply redaction boxes + strip audio; write a valid, viewable DICOM.
engine_pixel_redact <- function(input_path, output_path, boxes = NULL,
                                strip_audio = TRUE) {
  call_engine("pixel_redact", input_path, output_path, boxes, strip_audio)
}

#' Phase 4 entries: per-project profiles + the tag-a-miss self-improvement loop.

#' List selectable profiles (shipped defaults + workspace project profiles).
engine_profiles_list <- function() {
  call_engine("profiles_list")
}

#' Load the effective profile for an id (workspace copy wins over shipped).
engine_profile_get <- function(profile_id = "default") {
  call_engine("profile_get", profile_id)
}

#' Persist a profile to the writable workspace (never the shipped package).
engine_profile_save <- function(profile_id, profile) {
  call_engine("profile_save", profile_id, profile)
}

#' Copy an existing profile into the workspace under a new id.
engine_profile_clone <- function(src_id, new_id, label = NULL) {
  call_engine("profile_clone", src_id, new_id, label)
}

#' Capture a missed identifier: always stores a labeled example, and (per `fix`)
#' grows the project gazetteer and/or appends a custom-regex rule.
#' @param fix character vector subset of c("gazetteer", "regex"); empty = store only.
engine_tag_capture <- function(category, value, profile_id = "default",
                               fix = "gazetteer", pattern = NULL,
                               source = NULL, context = NULL, score = 1.0) {
  call_engine("tag_capture", category, value, profile_id,
              as.list(fix), pattern, source, context, score)
}

#' Export captured labeled examples as a training-ready NER dataset (fine-tune hook).
engine_ner_export <- function(out_path = NULL) {
  call_engine("ner_export_examples", out_path)
}

#' Phase 6 QA: re-run the detectors on an OUTPUT and report residual PHI.
#' Returns the per-file residual report (masked previews, per-category counts,
#' pass/fail verdict) as an R list.
engine_scan_residual <- function(path, profile_id = "default",
                                 scan_pixels = TRUE, min_score = 0.5) {
  call_engine("scan_residual", path, profile_id, scan_pixels, min_score)
}

#' Phase 6 QA: residual scan over an OUTPUT folder (or single file), aggregated.
engine_scan_residual_dir <- function(output_path, profile_id = "default",
                                     scan_pixels = TRUE, min_score = 0.5) {
  call_engine("scan_residual_dir", output_path, profile_id, scan_pixels, min_score)
}

#' Phase 7: build the synthetic planted-PHI acceptance corpus under `out_dir`.
engine_build_corpus <- function(out_dir) call_engine("build_corpus", out_dir)

#' Phase 7: literal planted-PHI survivor sweep over de-identified outputs.
engine_check_survivors <- function(out_dir) call_engine("check_survivors", out_dir)

#' Phase 7: reversibility policy (reversible flag + crosswalk size) of a keystore.
engine_keystore_summary <- function(keystore_path, passphrase) {
  call_engine("keystore_summary", keystore_path, passphrase)
}

#' Phase 7: authorised re-identification of a pseudonym (NULL if irreversible).
engine_keystore_reverse <- function(keystore_path, passphrase, pseudonym) {
  call_engine("keystore_reverse", keystore_path, passphrase, pseudonym)
}
