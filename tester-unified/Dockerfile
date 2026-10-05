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
COPY ciu/         /src/ciu/
COPY cmru/        /src/cmru/
COPY assay/       /src/assay/
COPY topos/       /src/topos/
COPY nyxloom/ /src/nyxloom/
# Estate-internal libraries, at the path cmru's pyproject expects relative to
# /src/cmru (`../libraries/worktree/src`, `../libraries/cli-extended/src`).
# They are installed ONLY from these copied sources, never from an index.
COPY libraries/ /src/libraries/
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
# INDEX POLICY (BG-05): the closure goes to a PyPI-default pip, so the generator
# REFUSES any estate-internal name (cli-extended, worktree, cmru, assay, ciu,
# ...): `cli-extended` is unclaimed on PyPI and `worktree` is an unrelated PyPI
# project, i.e. a dependency-confusion path into an image that is handed the
# host Docker socket. Estate-internal packages (cli-extended, cmru) are instead
# built OFFLINE from the COPYed sources above, in a throwaway venv holding only
# their pinned build backends (`--no-index --no-deps --no-build-isolation`),
# and installed from those local wheels. The cli-extended wheel carries a local
# version segment (+tester.unified) that no PyPI release can have.
RUN /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      > /tmp/tester-requirements.txt \
 && /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      --build-requires cmru/pyproject.toml libraries/cli-extended/pyproject.toml \
      > /tmp/internal-build-requirements.txt
RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir -r /tmp/tester-requirements.txt \
 && /usr/local/bin/python3.14 -m venv /tmp/internal-build-venv \
 && /tmp/internal-build-venv/bin/pip install --no-cache-dir -r /tmp/internal-build-requirements.txt \
 && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=0.0.0+tester.unified \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified \
    /tmp/internal-build-venv/bin/pip wheel --no-index --no-deps --no-build-isolation \
      -w /tmp/internal-wheels /src/libraries/cli-extended /src/cmru \
 && /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/internal-wheels/*.whl \
 && rm -rf /tmp/internal-build-venv /tmp/internal-wheels /tmp/internal-build-requirements.txt \
 && /opt/tester-venv/bin/python -c "import build, pytest, pytest_cov, xdist, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko, coverage, structlog; print('tester-unified closure OK')" \
 && /opt/tester-venv/bin/python -c "import pandas, plotly, matplotlib, scipy, numpy, ruptures; print('cgroup-profiler report tier OK')"

# Build-time assertion (BG-05): the estate-internal imports resolve from THIS
# image's locally built wheels, never from an index. A PyPI release can never
# carry the +tester.unified local version segment.
RUN /opt/tester-venv/bin/python - <<'PY'
import importlib.metadata as metadata
from pathlib import Path

import cli_extended
import worktree

site = "/opt/tester-venv/"
for module in (cli_extended, worktree):
    location = str(Path(module.__file__).resolve())
    assert location.startswith(site), f"{module.__name__} imports from {location}, not {site}"
for dist in ("cli-extended", "cmru"):
    version = metadata.version(dist)
    assert version.endswith("+tester.unified"), (
        f"{dist} {version} was not built from the COPYed source in this image"
    )
print("estate-internal packages are local builds:", metadata.version("cli-extended"))
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
