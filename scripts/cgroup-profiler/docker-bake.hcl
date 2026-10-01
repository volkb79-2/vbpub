// cgprofile daemon image — docker-bake.hcl (pwmcp shape: variable/target/
// group, buildx bake driving build-push.py's --build/--push).
//
// Unlike pwmcp's own docker-bake.hcl (a Playwright-version-coordinated
// release, several pins resolved by CMRU's prepare phase into cmru.vars),
// cgprofile has exactly one externally-resolved coordinate — its own
// release version — set via `scm` versioning (cmru.toml). CGPROFILE_VERSION
// is read from the process environment automatically (buildx bake's normal
// variable-from-env behavior) when the release pipeline sets it; a plain
// local `--build` with nothing exported gets this file's own default below.

variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// Set by cmru at release time (scm strategy); a bare local build never
// needs this to be real, only present.
variable "CGPROFILE_VERSION" {
  default = "0.0.0-dev"
}

// Set by build-push.py from `git rev-parse HEAD` — the OCI
// `org.opencontainers.image.revision` label the Dockerfile already wires
// to this build arg.
variable "GIT_REVISION" {
  default = "unknown"
}

// Shared build definition: concrete output names live only in the two
// leaf targets below so a release push cannot publish the local alias.
target "cgprofile" {
  context    = "."
  dockerfile = "Dockerfile"
  args = {
    CGPROFILE_VERSION = "${CGPROFILE_VERSION}"
    GIT_REVISION       = "${GIT_REVISION}"
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
  tags = ["${REGISTRY}/${NAMESPACE}/cgprofile:${CGPROFILE_VERSION}"]
}

group "all" {
  targets = ["cgprofile-local"]
}
