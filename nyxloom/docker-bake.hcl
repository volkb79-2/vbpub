# nyxloom OCI images — daemon (nyxloomd) + agent-cli bundle.
#
# ONE recipe, TWO drivers (the estate pattern): `ciu` builds + runs these locally
# (build-push.py --build → bake --load), `cmru` builds + pushes them for release
# (build-push.py --push → bake --push → ghcr.io/volkb79-2/*). Keeping a single
# bake file is what stops "works locally, breaks in release" drift.
#
# NYXLOOM_VERSION is resolved on the HOST by build-push.py (git describe
# --match 'nyxloom-v*', prefix stripped — the same tag cmru mints and
# setuptools-scm reads) and injected here. It TAGS both images AND is passed into
# the nyxloomd build as the setuptools-scm pretend-version, so the daemon image
# bakes a correctly-versioned self-contained wheel with no git in the build
# context. Local `ciu`/bare-bake builds default it to 0.0.0-dev.
variable "REGISTRY"         { default = "ghcr.io" }
variable "NAMESPACE"        { default = "volkb79-2" }
variable "NYXLOOM_VERSION"  { default = "0.0.0-dev" }

# agent-cli pinned CLI versions — keep in sync with infra/agent-cli/Dockerfile ARGs.
variable "AGENT_UID"        { default = "1003" }
variable "AGENT_GID"        { default = "1003" }
variable "CODEX_VERSION"    { default = "0.145.0" }
variable "REASONIX_VERSION" { default = "1.17.12" }
variable "OPENCODE_VERSION" { default = "1.18.1" }

# The daemon: mdt base + a self-contained /opt venv with the nyxloom wheel. The
# build context is the nyxloom package root so the Dockerfile's stage-1 can build
# the wheel from src/ + pyproject.toml.
target "nyxloomd" {
  context    = "."
  dockerfile = "nyxloomd/Dockerfile"
  args = {
    NYXLOOM_VERSION = "${NYXLOOM_VERSION}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/nyxloomd:${NYXLOOM_VERSION}",
    "${REGISTRY}/${NAMESPACE}/nyxloomd:latest",
  ]
}

# The agent-cli bundle (claude/codex/reasonix/opencode). BUILT + made available so
# the daemon can `docker run` it per-task with tailored mounts (worktree, agent
# auth volume, cgroup slice) — it is NOT run as a long-lived service.
target "nyxloom-agent-cli" {
  context    = "infra/agent-cli"
  dockerfile = "Dockerfile"
  args = {
    AGENT_UID        = "${AGENT_UID}"
    AGENT_GID        = "${AGENT_GID}"
    CODEX_VERSION    = "${CODEX_VERSION}"
    REASONIX_VERSION = "${REASONIX_VERSION}"
    OPENCODE_VERSION = "${OPENCODE_VERSION}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/nyxloom-agent-cli:${NYXLOOM_VERSION}",
    "${REGISTRY}/${NAMESPACE}/nyxloom-agent-cli:latest",
  ]
}

group "all" { targets = ["nyxloomd", "nyxloom-agent-cli"] }
