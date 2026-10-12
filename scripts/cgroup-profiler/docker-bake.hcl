// cgprofile daemon image — docker-bake.hcl (pwmcp shape: variable/target/
// group, buildx bake driving build-push.py's --build/--push).
//
// Bake automatically imports matching variables from the process environment.
// Keep its defaults literal so a bare `docker buildx bake` cannot turn an
// ambient CGPROFILE_VERSION into release identity. build-push.py supplies
// validated per-target --set overrides after checking the exact CMRU tag and
// clean source tree.

// Shared build definition: concrete output names live only in the two
// leaf targets below so a release push cannot publish the local alias.
target "cgprofile" {
  context    = "."
  dockerfile = "Dockerfile"
  args = {
    CGPROFILE_VERSION = "0.0.0-dev"
    GIT_REVISION       = "unknown"
  }
  // The Dockerfile's own DAMON-support COPY needs the sibling
  // scripts/damon-analysis/lib/damon_analysis.py, which the "." context
  // above cannot see (it is outside scripts/cgroup-profiler/) — a second,
  // named build context, exactly what `COPY --from=damon_analysis` in the
  // Dockerfile expects. Without this the image builds fine but DAMON is
  // unconditionally "unavailable" inside every container regardless of
  // the host kernel (found live during RG-55 P1 acceptance).
  contexts = {
    damon_analysis = "../damon-analysis"
  }
}

target "cgprofile-local" {
  inherits = ["cgprofile"]
  tags = ["cgprofile:local"]
}

target "cgprofile-release" {
  inherits = ["cgprofile"]
  // Bare Bake invocations remain development builds. Only build-push.py may
  // replace this tag after resolving and validating the exact release tag.
  tags = ["ghcr.io/volkb79-2/cgprofile:0.0.0-dev"]
}

group "all" {
  targets = ["cgprofile-local"]
}
