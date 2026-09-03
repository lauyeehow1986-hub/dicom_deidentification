# Phase 6.5 - Projects: a portable bundle of settings you can copy to another
# study, with global vs project-scoped hashing/signing and rebindable roots.
# Pure R (no venv): the keystore/signing files themselves are created by the
# engine at run time; here we only test the project descriptor + path resolution.

with_ws <- function(code) {
  ws <- tempfile("ws_"); dir.create(ws)
  old <- Sys.getenv("DICOMDEID_WORKSPACE", unset = NA)
  Sys.setenv(DICOMDEID_WORKSPACE = ws)
  on.exit({
    if (is.na(old)) Sys.unsetenv("DICOMDEID_WORKSPACE")
    else Sys.setenv(DICOMDEID_WORKSPACE = old)
  }, add = TRUE)
  force(code)
}

test_that("project_create writes a descriptor that project_get reads back", {
  with_ws({
    p <- project_create("echo2026", label = "NHCS Echo 2026",
                        profile_id = "echo2026", hashing_scope = "project",
                        reversible = TRUE, root_in = "E:/in", root_out = "F:/out",
                        created_by = "yh")
    got <- project_get("echo2026")
    expect_identical(got$project_id, "echo2026")
    expect_identical(got$label, "NHCS Echo 2026")
    expect_identical(got$hashing$scope, "project")
    expect_true(got$hashing$reversible)
    expect_identical(got$roots[["in"]], "E:/in")
    expect_true(file.exists(project_path("echo2026")))
  })
})

test_that("projects_list enumerates created projects", {
  with_ws({
    project_create("a", profile_id = "default")
    project_create("b", profile_id = "default")
    ids <- vapply(projects_list(), function(x) x$project_id, character(1))
    expect_setequal(ids, c("a", "b"))
  })
})

test_that("copy_from inherits policy but starts fresh, overrides win", {
  with_ws({
    project_create("src", profile_id = "srcprof", hashing_scope = "global",
                  reversible = FALSE, signing_scope = "global",
                  root_in = "E:/in", root_out = "F:/out")
    # copy settings into a new project; override only the roots
    dst <- project_create("dst", copy_from = "src",
                         root_in = "G:/in", root_out = "H:/out")
    expect_identical(dst$hashing$scope, "global")   # inherited
    expect_false(dst$hashing$reversible)             # inherited
    expect_identical(dst$signing$scope, "global")    # inherited
    expect_identical(dst$roots[["in"]], "G:/in")          # overridden
    expect_identical(dst$profile_id, "srcprof")      # inherited profile reference
  })
})

test_that("reversible defaults to TRUE but can be turned off per project", {
  with_ws({
    d <- project_create("d", profile_id = "default")
    expect_true(project_get("d")$hashing$reversible)
    irr <- project_create("irr", profile_id = "default", reversible = FALSE)
    expect_false(project_get("irr")$hashing$reversible)
  })
})

test_that("global hashing shares one keystore; project hashing is per-project", {
  with_ws({
    g1 <- project_create("g1", profile_id = "default", hashing_scope = "global")
    g2 <- project_create("g2", profile_id = "default", hashing_scope = "global")
    expect_identical(project_resolve_keystore(g1)$path,
                     project_resolve_keystore(g2)$path)   # same global salt
    p1 <- project_create("p1", profile_id = "default", hashing_scope = "project")
    p2 <- project_create("p2", profile_id = "default", hashing_scope = "project")
    expect_false(identical(project_resolve_keystore(p1)$path,
                           project_resolve_keystore(p2)$path))  # distinct salts
    expect_false(project_resolve_keystore(p1)$reversible == FALSE)  # default TRUE
  })
})

test_that("global signing resolves to one shared key dir; project signing per-project", {
  with_ws({
    g <- project_create("gs", profile_id = "default", signing_scope = "global")
    expect_true(grepl("signing", project_resolve_signing(g)$key_dir))
    expect_true(project_resolve_signing(g)$enabled)
    other <- project_create("gs2", profile_id = "default", signing_scope = "global")
    expect_identical(project_resolve_signing(g)$key_dir,
                     project_resolve_signing(other)$key_dir)
  })
})

test_that("each project has its own hash-chained audit log", {
  with_ws({
    project_create("pa", profile_id = "default")
    project_create("pb", profile_id = "default")
    audit_append("deid_run", "yh", "deidentifier", list(batch_id = "b1"),
                 path = audit_file("pa"))
    audit_append("signoff", "rev", "reviewer", list(decision = "pass"),
                 path = audit_file("pb"))
    expect_true(file.exists(file.path(project_dir("pa"), "audit_log.jsonl")))
    expect_equal(nrow(audit_read(audit_file("pa"))), 1L)
    expect_equal(nrow(audit_read(audit_file("pb"))), 1L)   # separate chain
    expect_true(audit_verify(audit_file("pa"))$ok)
  })
})

test_that("project_rebind_roots updates and persists the input/output roots", {
  with_ws({
    project_create("swap", profile_id = "default",
                  root_in = "E:/in", root_out = "F:/out")
    upd <- project_rebind_roots("swap", "G:/in", "H:/out")
    expect_identical(upd$roots[["in"]], "G:/in")
    expect_identical(project_get("swap")$roots[["out"]], "H:/out")  # persisted
  })
})
