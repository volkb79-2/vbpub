# tester-unified — the single vbpub gating container.
#
# Cockpit doctrine: the gating suite NEVER runs in the devcontainer (its base
# pins are not a ship signal). dstdns has its `test-runner`; this is the vbpub
# equivalent — one overarching container for every vbpub Python project.
#
# SINGLE SOURCE OF TRUTH: the test closure is DERIVED from each project's own
# pyproject.toml (its [project.dependencies] + declared test/dev extra), never
# duplicated in a hand-maintained requirements file. Change a dependency in a
# project's pyproject and it flows to the gate on the next image rebuild — no
# second list to sync.
#
# Runtime = python 3.14 (matches the nyxloomd controller image). The one test
# that relied on the pre-3.14 multiprocessing "fork" default is now explicitly
# fork-scoped, so 3.14 is green.
#
# Build from the REPO ROOT so the project sources are in context:
#   docker build -f tester-unified/Dockerfile -t tester-unified:local /workspaces/vbpub
# (see the repo-root .dockerignore, which whitelists only these projects.)
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest

USER root

# git's detached auto-maintenance (`git gc --auto` / `git maintenance run
# --auto` after a commit) forks a daemon that is orphaned to PID 1. A gate
# makes thousands of commits; 2026-10-05 left 19,108 zombies and exhausted the
# host pids ceiling (KI-52). Make maintenance run in the foreground for every
# git invocation in this image. (Callers that set GIT_CONFIG_NOSYSTEM=1, e.g.
# assay's hermetic git environment, bypass this file and must set the same keys
# themselves; the launchers also run the gate under `docker run --init`.)
RUN git config --system maintenance.autoDetach false \
 && git config --system gc.autoDetach false \
 && test "$(git config --system --get maintenance.autoDetach)" = false \
 && test "$(git config --system --get gc.autoDetach)" = false

# Copy only the Python projects pip needs to resolve + install from their
# pyprojects. Gate runs execute against the BOUND worktree via PYTHONPATH, so
# these baked copies only seed the dependency closure, not the code under test.
COPY tester-unified/gen-requirements.py /usr/local/lib/tester-unified/gen-requirements.py
COPY tester-unified/fetch-cli-extended.py /usr/local/lib/tester-unified/fetch-cli-extended.py
COPY ciu/         /src/ciu/
COPY cmru/        /src/cmru/
COPY assay/       /src/assay/
COPY topos/       /src/topos/
COPY nyxloom/ /src/nyxloom/
# The one estate-internal library cmru still packages itself, at the path cmru's
# pyproject expects relative to /src/cmru (`../libraries/worktree/src`). It is
# built ONLY from this copied source, never from an index. cli-extended is NOT
# copied: it is a real wheel dependency of cmru (KI-51 / CX-D1) and is installed
# from its RELEASE below.
COPY libraries/worktree/ /src/libraries/worktree/
# ZERO-RELEASE INPUT: empty (a .gitkeep) unless the operator dropped a locally
# built cli_extended-*.whl here for CLI_EXTENDED_RESOLVE=local (see below).
COPY tester-unified/local-wheel/ /tmp/cli-extended-local/
# scripts/cgroup-profiler adds the report tier (pandas/plotly/matplotlib/
# scipy/ruptures) that no other project here pulls in. It is NOT a fork
# candidate the way srdm was: its suite uses fake cgroup trees, injected
# clocks and stubbed sysfs by design, so it needs no Go toolchain, no
# privilege, no systemd and no Docker socket — only this closure.
COPY scripts/cgroup-profiler/ /src/cgroup-profiler/

# One COPIES venv on python 3.14 under /opt (a real interpreter binary, so a
# runtime home bind at /home/vscode cannot shadow it).  The build context omits
# `.git`, therefore installing every copied project as a package would make
# setuptools-scm fabricate or fail on a version before it could provide its
# test dependencies.  Derive the dependency closure straight from each
# project's `pyproject.toml` instead: the bind-mounted worktree, not these
# copied sources, provides the code under test.
#
# INDEX POLICY (BG-05, D8): the closure goes to a PyPI-default pip, so the
# generator REFUSES any estate-internal name (worktree, cmru, assay, ciu, ...):
# `worktree` is an unrelated PyPI project and `cli-extended` is unclaimed on
# PyPI, i.e. a dependency-confusion path into an image that is handed the host
# Docker socket. cmru declares `cli-extended>=0.3.0`; the generator SKIPS that
# one line (no cli-extended requirement ever reaches pip), and the image installs
# the RELEASED cli-extended wheel instead: fetch-cli-extended.py downloads the
# pinned release asset (default; or `cli-extended-latest/latest.json` as an opt-in),
# REQUIRES its sha256 and verifies it, and the wheel goes in with
# `--no-index --no-deps` BEFORE cmru. cmru itself is built OFFLINE from the
# COPYed source, in a throwaway venv holding only its pinned build backends
# (`--no-index --no-deps --no-build-isolation`), and installed from that local
# wheel, whose local version segment (+tester.unified) no PyPI release can have.
# PINNED BY DEFAULT (D-3): the two WHEEL args below name the released 0.3.0 asset
# and its sha256 IN THIS REPOSITORY, so the digest is an independent pin and not
# something `latest.json` can vouch for over the same TLS channel. Because the pin
# is build-arg text, a changed pin is a changed RUN layer: no stale cache. TO BUMP:
# release cli-extended, copy `version`/`url`/`sha256` from
# cli-extended-latest/latest.json, update the three defaults (and cmru's floor in
# cmru/pyproject.toml plus --min-version below if the floor moves), rebuild.
# `latest.json` mode is an explicit opt-in: --build-arg CLI_EXTENDED_RESOLVE=latest
# (still sha256-verified, but the digest then comes from the pointer; add
# --no-cache, the unchanged RUN text would otherwise reuse the cached wheel).
# THREE MODES (CLI_EXTENDED_RESOLVE):
#   pinned  (DEFAULT) the released asset named by the two pin args above.
#   latest  the `cli-extended-latest/latest.json` pointer (opt-in).
#   local   ZERO-RELEASE path: no release exists to fetch, so install a LOCALLY BUILT
#           wheel from the build context instead. Build it with cmru's own wheel
#           builder (`CMRU_BOOTSTRAP_CLI_EXTENDED=source` in cmru/build-initial-
#           standalone.sh logs its sha256, or `cmru handler wheel-build` in the
#           library), copy exactly one cli_extended-*.whl into
#           tester-unified/local-wheel/, and pass
#           --build-arg CLI_EXTENDED_RESOLVE=local
#           --build-arg CLI_EXTENDED_WHEEL_SHA256=<its sha256>   (the existing arg).
#           The digest is verified with sha256sum, the wheel goes in the same
#           `--no-index --no-deps` way, and the post-install assertion requires a
#           `+local` version (a release has none). Never a fallback: pinned stays
#           the default and fails by itself when the pinned release is missing.
ARG CLI_EXTENDED_RESOLVE=pinned
ARG CLI_EXTENDED_POINTER_URL=https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json
ARG CLI_EXTENDED_WHEEL_URL=https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.3.0/cli_extended-0.3.0-py3-none-any.whl
ARG CLI_EXTENDED_WHEEL_SHA256=a0f80f1b614bcaf81fac235aa3a7f42f20eccac06f82311a6d8d5a725eea5f50
RUN /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      > /tmp/tester-requirements.txt \
 && /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      --build-requires cmru/pyproject.toml \
      > /tmp/internal-build-requirements.txt \
 && case "${CLI_EXTENDED_RESOLVE}" in \
      pinned) set -- --wheel-url "${CLI_EXTENDED_WHEEL_URL}" --sha256 "${CLI_EXTENDED_WHEEL_SHA256}" ;; \
      latest) set -- --pointer-url "${CLI_EXTENDED_POINTER_URL}" ;; \
      local) set -- ;; \
      *) echo "CLI_EXTENDED_RESOLVE must be pinned, latest or local" >&2; exit 1 ;; \
    esac \
 && if [ "${CLI_EXTENDED_RESOLVE}" = local ]; then \
      set -- /tmp/cli-extended-local/cli_extended-*.whl; \
      if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then echo "CLI_EXTENDED_RESOLVE=local needs exactly one cli_extended-*.whl in tester-unified/local-wheel" >&2; exit 1; fi; \
      echo "${CLI_EXTENDED_WHEEL_SHA256}  $1" | sha256sum -c - \
        || { echo "local wheel does not match --build-arg CLI_EXTENDED_WHEEL_SHA256" >&2; exit 1; }; \
      mkdir -p /tmp/cli-extended-release && cp "$1" /tmp/cli-extended-release/; \
    else \
      /usr/local/bin/python3.14 /usr/local/lib/tester-unified/fetch-cli-extended.py \
        --dest /tmp/cli-extended-release --min-version 0.3.0 "$@"; \
    fi
RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir -r /tmp/tester-requirements.txt \
 && /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/cli-extended-release/*.whl \
 && /usr/local/bin/python3.14 -m venv /tmp/internal-build-venv \
 && /tmp/internal-build-venv/bin/pip install --no-cache-dir -r /tmp/internal-build-requirements.txt \
 && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified \
    /tmp/internal-build-venv/bin/pip wheel --no-index --no-deps --no-build-isolation \
      -w /tmp/internal-wheels /src/cmru \
 && /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/internal-wheels/*.whl \
 && rm -rf /tmp/internal-build-venv /tmp/internal-wheels /tmp/internal-build-requirements.txt /tmp/cli-extended-release \
 && /opt/tester-venv/bin/python -c "import build, pytest, pytest_cov, xdist, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko, coverage, structlog; print('tester-unified closure OK')" \
 && /opt/tester-venv/bin/python -c "import pandas, plotly, matplotlib, scipy, numpy, ruptures; print('cgroup-profiler report tier OK')"

# Build-time assertion (BG-05): `worktree` and cmru resolve from THIS image's
# locally built cmru wheel (a PyPI release can never carry the +tester.unified
# local version segment), and cli_extended is the RELEASED wheel, not a local
# build and not an index install; cmru's metadata must require it.
RUN /opt/tester-venv/bin/python - <<'PY'
import importlib.metadata as metadata
import os
from pathlib import Path

import cli_extended
import worktree

site = "/opt/tester-venv/"
for module in (cli_extended, worktree):
    location = str(Path(module.__file__).resolve())
    assert location.startswith(site), f"{module.__name__} imports from {location}, not {site}"
version = metadata.version("cmru")
assert version.endswith("+tester.unified"), (
    f"cmru {version} was not built from the COPYed source in this image"
)
released = metadata.version("cli-extended")
if os.environ["CLI_EXTENDED_RESOLVE"] == "local":
    # Zero-release mode: the operator-built wheel (sha256-verified above) carries a
    # +local version segment so it can never be mistaken for, or equal, a release.
    assert "+" in released, f"cli-extended {released} has no +local segment: not a local build"
else:
    assert "+" not in released, f"cli-extended {released} is a local build, not the release"
assert any(r.startswith("cli-extended") for r in metadata.requires("cmru") or ()), (
    "cmru's wheel does not declare cli-extended as a dependency"
)
print("cmru is a local build; cli-extended is the released wheel:", released)
PY

# The container runs as uid 1003 (to match the host-owned bind mounts), which
# the base image has no /etc/passwd entry for and no writable HOME. Provide
# BOTH: a passwd entry (pwd.getpwuid(1003) — topos daemon-status calls it) AND a
# writable HOME with XDG dirs, so suites that read/write $HOME/.config or
# $HOME/.cache (e.g. topos config/record) don't hit PermissionError on the
# image's root-owned /home/vscode. A gate must give the run-uid a real HOME.
RUN echo "tester:x:1003:994:tester:/home/tester:/bin/bash" >> /etc/passwd \
 && echo "docker:x:994:tester" >> /etc/group \
 && mkdir -p /home/tester/.config /home/tester/.cache /home/tester/.local/state \
 && chown -R 1003:994 /home/tester \
 && chown -R 1003:994 /opt/tester-venv

USER 1003
ENV HOME=/home/tester \
    XDG_CONFIG_HOME=/home/tester/.config \
    XDG_CACHE_HOME=/home/tester/.cache \
    XDG_STATE_HOME=/home/tester/.local/state \
    TESTER_VENV=/opt/tester-venv
