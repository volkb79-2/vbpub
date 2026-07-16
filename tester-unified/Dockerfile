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
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest

USER root

# Copy only the Python projects pip needs to resolve + install from their
# pyprojects. Gate runs execute against the BOUND worktree via PYTHONPATH, so
# these baked copies only seed the dependency closure, not the code under test.
COPY ciu/         /src/ciu/
COPY cmru/        /src/cmru/
COPY groop/       /src/groop/
COPY nyxloom/ /src/nyxloom/

# One COPIES venv on python 3.14 under /opt (a real interpreter binary, so a
# runtime home bind at /home/vscode cannot shadow it). Each install pulls that
# project's deps straight from its pyproject — the closure is the SUM of the
# repo, self-documented by these four lines.
RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir \
      "/src/ciu[ssh,test]" \
      "/src/cmru[test]" \
      "/src/groop[dev]" \
      "/src/nyxloom[test]" \
 && /opt/tester-venv/bin/python -c "import pytest, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko; print('tester-unified closure OK')"

USER 1003
ENV TESTER_VENV=/opt/tester-venv
