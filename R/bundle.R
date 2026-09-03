#' Phase 7 - reproducible bundle manifest + integrity verifier.
#'
#' The air-gapped app ships as a copy-over folder (portable R + app + relocatable
#' Python venv + models + optional binaries). These functions checksum every
#' bundled file into `BUNDLE_MANIFEST.json` on the build machine, and re-verify
#' it on the target so tampering or a truncated copy is caught before de-id runs.
#' Pure R (digest) so they run on the locked-down box without the engine.

# Paths never worth hashing (rebuildable caches / VCS / the manifest itself).
.BUNDLE_EXCLUDE <- c("__pycache__", "[.]git(/|$)", "[.]pyc$", "[.]Rproj[.]user",
                     "BUNDLE_MANIFEST[.]json$")

.bundle_excluded <- function(rel) {
  any(vapply(.BUNDLE_EXCLUDE, function(p) grepl(p, rel), logical(1)))
}

#' Checksum every file under `dir` into a data.frame(path, sha256, bytes).
#' Paths are relative, forward-slashed, and sorted for a reproducible manifest.
#' @export
bundle_manifest <- function(dir) {
  dir <- normalizePath(dir, winslash = "/", mustWork = TRUE)
  files <- list.files(dir, recursive = TRUE, all.files = TRUE,
                      no.. = TRUE, full.names = TRUE)
  files <- files[!dir.exists(files)]  # files only
  full <- normalizePath(files, winslash = "/")
  rel <- substring(full, nchar(paste0(dir, "/")) + 1L)
  keep <- !vapply(rel, .bundle_excluded, logical(1))
  files <- files[keep]; rel <- rel[keep]
  ord <- order(rel)
  files <- files[ord]; rel <- rel[ord]
  data.frame(
    path = rel,
    sha256 = vapply(files, function(f) digest::digest(f, algo = "sha256",
                                                      file = TRUE), character(1)),
    bytes = file.size(files),
    row.names = NULL, stringsAsFactors = FALSE)
}

#' Write BUNDLE_MANIFEST.json under `dir`; returns its path.
#' @export
bundle_write_manifest <- function(dir, path = file.path(dir, "BUNDLE_MANIFEST.json"),
                                  meta = list()) {
  m <- bundle_manifest(dir)
  doc <- c(list(
    version = 1L,
    created = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
    root = basename(normalizePath(dir, winslash = "/")),
    n_files = nrow(m),
    total_bytes = sum(m$bytes),
    files = lapply(seq_len(nrow(m)), function(i)
      list(path = m$path[i], sha256 = m$sha256[i], bytes = m$bytes[i]))), meta)
  writeLines(jsonlite::toJSON(doc, auto_unbox = TRUE, pretty = TRUE), path)
  path
}

#' Verify a directory against a written manifest.
#' @return list(ok, missing, changed, extra, n_checked)
#' @export
bundle_verify <- function(dir, manifest_path) {
  doc <- jsonlite::fromJSON(manifest_path, simplifyVector = FALSE)
  want <- doc$files
  want_path <- vapply(want, function(f) f$path, character(1))
  want_sha  <- vapply(want, function(f) f$sha256, character(1))
  cur <- bundle_manifest(dir)

  missing <- setdiff(want_path, cur$path)
  extra   <- setdiff(cur$path, want_path)
  common  <- intersect(want_path, cur$path)
  changed <- character(0)
  for (p in common) {
    if (!identical(cur$sha256[cur$path == p], want_sha[want_path == p]))
      changed <- c(changed, p)
  }
  list(ok = length(missing) == 0 && length(changed) == 0 && length(extra) == 0,
       missing = missing, changed = changed, extra = extra,
       n_checked = length(common))
}

#' The components a complete portable bundle must carry.
#'
#' `secret = TRUE` marks parts that link patients or can sign as the pipeline
#' (the global keystore + Ed25519 signing keys): they travel with an authorised
#' bundle but must NEVER be committed to git. Used by the build script to check
#' completeness and by the air-gap doc.
#' @export
bundle_components <- function() {
  data.frame(
    component = c(
      "R app (R/, app.R, DESCRIPTION)",
      "Identifier catalog + profiles (inst/profiles)",
      "Gazetteers (inst/gazetteers)",
      "Relocatable Python venv (inst/python/.venv)",
      "Transformer NER model (inst/models)",
      "Tesseract OCR binary (portable)",
      "Global keystore (workspace/keystores/global.json)",
      "Ed25519 signing keys (workspace/signing/)",
      "Optional dcmtk binaries"),
    required = c(TRUE, TRUE, TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE),
    secret   = c(FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, TRUE, TRUE, FALSE),
    note = c(
      "UI + orchestration + QA",
      "PS3.15 actions + the 15 SingHealth categories",
      "patient-name dictionaries (may itself be sensitive)",
      "the DICOM/PHI engine; built by build_venv.ps1",
      "optional NER layer; deterministic layers work without it",
      "needed by presidio-image-redactor for burned-in text",
      "cross-project pseudonym linkage; guard like the crosswalk",
      "verify de-identified outputs; public key ships, private stays secret",
      "fallback decoders for edge transfer syntaxes"),
    stringsAsFactors = FALSE)
}
