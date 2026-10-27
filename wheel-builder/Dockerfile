# wheel-builder — the single vbpub wheel-build container.
#
# cmru's built-in wheel profile (cmru/src/cmru/handlers.py: cmd_wheel_build) runs
# `python -m build --wheel`. That needs the PyPA `build` frontend package present in
# whatever interpreter invokes it — a repo-root convenience venv used to be pressed
# into that role ad hoc (pip-installing whatever was missing, one gap at a time). This
# image is the dedicated replacement: one purpose-built, stateless place `python -m
# build` runs, so no repo venv needs `build` installed on the fly again.
#
# Stateless tool image: no project source is baked in. The project directory is
# supplied via bind mount at run time (cmd_wheel_build mounts the repo root and runs
# the build against the bind-mounted source), so this image only needs the build
# frontend itself. The wheel bytes depend on each project's `[build-system].requires`,
# which `python -m build`'s isolation installs fresh per invocation AT RUN TIME, from the
# index and outside the age window below (it covers only the two installs baked here):
# exact pins (cmru, ciu, run-gate-project, assay, cli-extended, topos, nyxloom) make that
# resolution reproducible; floors (pwmcp-client `setuptools>=77`, cgroup-profiler
# `setuptools>=68`) do not. Aging the run-time installs too is tester-base backlog TB-08.
#
# Both ciu and cmru declare artifacts = ["wheel"] and fall through the same built-in
# handler — this one image covers both, same as tester-unified covers every
# project's test suite.
#
# Build from the REPO ROOT so the (bind-mounted-at-runtime) project sources would be
# in context if ever needed — today nothing is COPY'd from it, so the build is fast:
#   docker build -f wheel-builder/Dockerfile -t wheel-builder:local /workspaces/vbpub
#
# BASE (MDTC-02, operator decision 2026-10-18): tester-base, the minimal digest-pinned gate base
# (python:3.14-trixie + docker client + minisign + shellcheck), the same base as tester-unified.
# tester-base is built LOCALLY from public upstream inputs only (`docker build -t tester-base:local
# tester-base/`, which cmru/build-initial-standalone.sh does when the tag is absent), so the
# zero-release bootstrap still needs no PUBLISHED vbpub image. Until tester-base has its first CMRU
# release the default is that local tag; the controller then switches it to the published
# `ghcr.io/volkb79-2/tester-base@sha256:...`. No other fallback: a missing base is a build failure.
# A local-only tag is visible to the `docker` driver builder only (BUILDX_BUILDER=default).
ARG WHEEL_BUILDER_BASE_IMAGE=tester-base:local
FROM ${WHEEL_BUILDER_BASE_IMAGE}

LABEL org.vbpub.wheel-builder.kind="python-wheel-builder"

USER root

# setuptools-scm asks Git for the source version inside the builder. Keep this
# image independent of vbpub's published MDT image so it can bootstrap the
# first vbpub wheels from a source-only checkout.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git passwd \
 && rm -rf /var/lib/apt/lists/*

# A dedicated /opt venv, not the base image's /home/vscode/.venv: this container runs
# as uid 1003 (see below), a different user than the image's baked-in vscode/1000, and
# /home/vscode's permissions are that user's to control — same reasoning tester-unified
# already documents for its own /opt/tester-venv ("a real interpreter binary, so a
# runtime home bind cannot shadow it").
# Aged-latest (estate freshness rule): no version is pinned here, and `--uploaded-prior-to` limits both
# installs to releases at least PIP_MIN_RELEASE_AGE old (default 14 days), the vetting buffer against a
# just-shipped regression or supply-chain incident. The age is relative to the build moment: rebuild
# with --no-cache to pick up newer aged releases.
ARG PIP_MIN_RELEASE_AGE=P14D
LABEL org.vbpub.wheel-builder.min-release-age="${PIP_MIN_RELEASE_AGE}"
RUN /usr/local/bin/python3.14 -m venv --copies /opt/wheel-builder-venv \
 && /opt/wheel-builder-venv/bin/pip install --no-cache-dir --uploaded-prior-to "${PIP_MIN_RELEASE_AGE}" --upgrade pip \
 && /opt/wheel-builder-venv/bin/pip install --no-cache-dir --uploaded-prior-to "${PIP_MIN_RELEASE_AGE}" build

# The container runs as uid 1003 (matches this devcontainer's host-remapped vscode
# user, same reasoning as tester-unified/Dockerfile), which the base image has no
# /etc/passwd entry for and no writable HOME. Provide both, so writes to the
# bind-mounted project's dist/ (owned by the host uid) succeed.
RUN if ! getent group 994 >/dev/null; then groupadd --gid 994 docker; fi \
 && if ! getent passwd 1003 >/dev/null; then \
      useradd --uid 1003 --gid 994 --create-home --shell /bin/bash builder; \
    fi \
 && mkdir -p /home/builder/.config /home/builder/.cache /home/builder/.local/state \
 && chown -R 1003:994 /home/builder

USER 1003
ENV HOME=/home/builder \
    XDG_CONFIG_HOME=/home/builder/.config \
    XDG_CACHE_HOME=/home/builder/.cache \
    XDG_STATE_HOME=/home/builder/.local/state
