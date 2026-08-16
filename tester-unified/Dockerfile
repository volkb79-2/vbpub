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

# Copy only the Python projects pip needs to resolve + install from their
# pyprojects. Gate runs execute against the BOUND worktree via PYTHONPATH, so
# these baked copies only seed the dependency closure, not the code under test.
COPY ciu/         /src/ciu/
COPY cmru/        /src/cmru/
COPY topos/       /src/topos/
COPY nyxloom/ /src/nyxloom/
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
# copied sources, provides the code under test.  CMRU is the sole exception:
# its installed console script is the canonical gate entry point, so install it
# explicitly with a build-only SCM identity after its dependencies are present.
RUN /usr/local/bin/python3.14 - <<'PY' > /tmp/tester-requirements.txt
import re
import tomllib
from pathlib import Path

projects = (
    (Path("/src/ciu/pyproject.toml"), ("ssh", "test")),
    (Path("/src/cmru/pyproject.toml"), ("test",)),
    (Path("/src/topos/pyproject.toml"), ("dev",)),
    (Path("/src/nyxloom/pyproject.toml"), ("test",)),
    (Path("/src/cgroup-profiler/pyproject.toml"), ("test",)),
)
def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def own_extra_requirements(project, extras):
    """Expand only this project's composite extras.

    `topos[dev]` and `cgroup-profiler[test]` deliberately reuse their own
    `zstandard`/`report` extras.  Handing those self-references to pip would
    make it look for an unrelated package index project instead of reading the
    copied pyproject that is the closure's source of truth.
    """
    pending = list(extras)
    seen = set()
    while pending:
        extra = pending.pop()
        if extra in seen:
            continue
        seen.add(extra)
        for requirement in project.get("optional-dependencies", {})[extra]:
            match = re.match(r"^([A-Za-z0-9_.-]+)\[([^]]+)\]$", requirement)
            if match and normalized(match.group(1)) == normalized(project["name"]):
                pending.extend(item.strip() for item in match.group(2).split(","))
            else:
                yield requirement


for pyproject, extras in projects:
    with pyproject.open("rb") as handle:
        document = tomllib.load(handle)
    project = document["project"]
    for requirement in project.get("dependencies", ()):
        print(requirement)
    for requirement in own_extra_requirements(project, extras):
        print(requirement)
print("build")
PY
RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir -r /tmp/tester-requirements.txt \
 && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0 \
    /opt/tester-venv/bin/pip install --no-cache-dir --no-deps /src/cmru \
 && /opt/tester-venv/bin/python -c "import build, pytest, pytest_cov, xdist, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko, coverage, structlog; print('tester-unified closure OK')" \
 && /opt/tester-venv/bin/python -c "import pandas, plotly, matplotlib, scipy, numpy, ruptures; print('cgroup-profiler report tier OK')"

# The container runs as uid 1003 (to match the host-owned bind mounts), which
# the base image has no /etc/passwd entry for and no writable HOME. Provide
# BOTH: a passwd entry (pwd.getpwuid(1003) — topos daemon-status calls it) AND a
# writable HOME with XDG dirs, so suites that read/write $HOME/.config or
# $HOME/.cache (e.g. topos config/record) don't hit PermissionError on the
# image's root-owned /home/vscode. A gate must give the run-uid a real HOME.
RUN echo "tester:x:1003:994:tester:/home/tester:/bin/bash" >> /etc/passwd \
 && echo "docker:x:994:tester" >> /etc/group \
 && mkdir -p /home/tester/.config /home/tester/.cache /home/tester/.local/state \
 && chown -R 1003:994 /home/tester

USER 1003
ENV HOME=/home/tester \
    XDG_CONFIG_HOME=/home/tester/.config \
    XDG_CACHE_HOME=/home/tester/.cache \
    XDG_STATE_HOME=/home/tester/.local/state \
    TESTER_VENV=/opt/tester-venv
