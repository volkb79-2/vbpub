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
# Build from the repo root using `tester-unified/build-image.sh`; it supplies
# the exact Run-Gate wheel as a named build context and verifies its version
# and digest during the image build. The repo-root .dockerignore whitelists the
# project sources in this context.
#
# BASE (MDTC-02, T3/T5): tester-base, the minimal gate base (python:3.14-trixie +
# docker client + minisign + shellcheck), NOT the 8 GB MDT cockpit image, whose floating
# `latest` changed the gate environment without a commit. Until the first CMRU release of
# tester-base exists the default is the locally built `tester-base:local`
# (`docker build -t tester-base:local tester-base/`); the controller switches the default
# to the published `ghcr.io/volkb79-2/tester-base@sha256:...` after release. There is no
# other fallback: a missing base is a build failure. The build argument keeps the name
# TESTER_UNIFIED_BASE_IMAGE: build-image.sh and Run-Gate's release preparation (published in
# run-gate 24.0.1) parse this ARG's default and compare the recorded label with the requested
# base, so the name is a published interface. The image's PATH is its own venv first, then
# the base's PATH - nothing of the MDT cockpit PATH is relied on.
ARG TESTER_UNIFIED_BASE_IMAGE=tester-base:local

# Node.js for the suites that execute it (pwmcp's runtime gateway). The official node image,
# major 26 (the cockpit's NODE_MAJOR; the major is the deliberate manual pin), pinned by digest.
# The tag and digest are the ones `cmru versions resolve tester-base` records for target
# `oci.node.26.trixie` under the 14-day age window (tester-base/versions/oci-images.json; the target
# lives in tester-base/cmru.toml because tester-unified has no cmru project); a test asserts equality.
FROM docker.io/library/node:26.9.0-trixie@sha256:fa271c47a5d81dc321f4a45be01362f5b3de7559edc7e76b8c4089be1e50d866 AS node-source

FROM ${TESTER_UNIFIED_BASE_IMAGE}
# Record the base reference this stage was built FROM. build-image.sh and the
# Run-Gate release preparation compare it with the requested base, so a
# renamed or unconsumed build argument cannot silently build the default base.
ARG TESTER_UNIFIED_BASE_IMAGE
LABEL org.vbpub.tester-unified.base-image="${TESTER_UNIFIED_BASE_IMAGE}"
# The identity the base reference resolved to when this image was built (build-image.sh supplies and
# re-reads it): the reference above can be a mutable local tag, the id says which content it was.
ARG TESTER_UNIFIED_BASE_IMAGE_ID
LABEL org.vbpub.tester-unified.base-image-id="${TESTER_UNIFIED_BASE_IMAGE_ID}"

USER root

# Keep host-facing tools explicit so the zero-release build can use the
# upstream Python base (explicit --base-image) without a published vbpub image. Docker is a
# client only; the launcher supplies the host socket when a lane needs it.
#
# Install only what the base lacks. Docker's `docker-ce-cli` comes with tester-base and the MDT cockpit
# image; Debian's `docker-cli` owns the same /usr/bin/docker, so installing it over them makes dpkg refuse
# the overwrite or, if apt resolves the clash, silently swaps the gate image's Docker client. It is
# therefore installed only when `docker` is absent from PATH (the upstream Python base), decided by the
# capability it provides, not by a package name. Compose is a separate step below.
RUN set -eu \
 && packages="ca-certificates curl git jq openssh-client util-linux" \
 && if ! command -v docker >/dev/null 2>&1; then packages="$packages docker-cli"; fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends $packages \
 && rm -rf /var/lib/apt/lists/* \
 && docker --version \
 && git --version \
 && nsenter --version

# Docker Compose (INT-01 D-006): real `docker compose` is needed by the suites (ciu has 16 test files that
# drive it, plus topos, nyxloom, cmru and run-gate-project). tester-base keeps the plugin out (T2), so on it
# this step installs Docker's OWN `docker-compose-plugin` package at an exact version, never Debian's
# `docker-compose` (an unpinned, differently-built 2.26.1). The package is fetched from Docker's apt pool
# by exact version and checked against a pinned sha256 per architecture, so it needs neither the base's
# apt source nor a network-resolved "latest"; it only runs when `docker compose version` does not already
# answer (the MDT cockpit base ships the plugin). Pin evidence (freshness rule, >= 14 days at adoption):
# docker/compose v5.6.0 was published 2026-10-02T16:07:06Z (`gh api repos/docker/compose/releases/tags/v5.6.0`),
# 17 days before this pin was taken on 2026-10-19; Docker's apt index serves it as 5.6.0-1~debian.13~trixie
# (`apt-cache madison docker-compose-plugin` on tester-base). The sha256s are of the .deb files downloaded
# from the URLs below. A bump is a reviewed edit of these three lines.
ARG DOCKER_COMPOSE_PLUGIN_VERSION=5.6.0-1~debian.13~trixie
ARG DOCKER_COMPOSE_PLUGIN_SHA256_AMD64=500327ad3ca6c52f6d142225c7e6830c46a27ff0348a2b2291f0a143d99872ac
ARG DOCKER_COMPOSE_PLUGIN_SHA256_ARM64=f7b51c2e48afe340074285b6a10f72601e49c7a669100e6ff8b903c257cfc213
# (`;` not `&&` between the commands: POSIX ignores `set -e` inside a compound command that is part of an
# AND-OR list, which would let a failed checksum fall through to dpkg.)
RUN set -eu; \
    if ! docker compose version >/dev/null 2>&1; then \
      case "$(dpkg --print-architecture)" in \
        amd64) arch=amd64; sum="${DOCKER_COMPOSE_PLUGIN_SHA256_AMD64}" ;; \
        arm64) arch=arm64; sum="${DOCKER_COMPOSE_PLUGIN_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture" >&2; exit 1 ;; \
      esac; \
      curl -fsSL -o /tmp/docker-compose-plugin.deb "https://download.docker.com/linux/debian/dists/trixie/pool/stable/${arch}/docker-compose-plugin_${DOCKER_COMPOSE_PLUGIN_VERSION}_${arch}.deb"; \
      echo "${sum}  /tmp/docker-compose-plugin.deb" | sha256sum -c -; \
      dpkg -i /tmp/docker-compose-plugin.deb; \
      rm -f /tmp/docker-compose-plugin.deb; \
    fi; \
    docker compose version

# Behaviour the old MDT cockpit base supplied implicitly, now explicit (MDTC-02 T8 skip-diff):
#  * `zsh` (D-003): mdt-cockpit/scripts/test_path_hook.py tests the login hook under zsh. The test
#    FAILS (MDT_REQUIRE_SHELLS=1 in the smoke lane) when zsh is absent instead of silently not
#    collecting its cases. It is a userland package this image's suites need, so it lives here and
#    not in the minimal tester-base (T2).
RUN apt-get update \
 && apt-get install -y --no-install-recommends zsh \
 && rm -rf /var/lib/apt/lists/*
#  * `systemctl` (INT-01 finding): host-placement gate scripts (scripts/cgroup-profiler/tools/gate.sh
#    placement probe, shared-ramdisk-depot-manager/tools/cgroup-parent.sh) run /bin/systemctl INSIDE
#    this image against the host's system bus, and the old cockpit base carried the binary. Without it
#    the cgprofile r0-r1 probe exits 127. This is the real client from Debian's `systemd` package, not
#    a stub (MDTC-02 D-001 forbids a fake one; topos handles an unreachable systemd itself).
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd \
 && rm -rf /var/lib/apt/lists/* \
 && test "$(dpkg -S /usr/bin/systemctl | cut -d: -f1)" = systemd \
 && /bin/systemctl --version
#  * Node.js + npm + npx (D-004) from the official node image (stage `node-source` above,
#    digest-pinned and age-checked by `cmru versions`), not Debian's nodejs, which is too old.
#    pwmcp's runtime-gateway tests execute the node binary. The links mirror the official image's.
#    corepack is NOT copied: the node 26 image no longer ships it (measured: `corepack: not found`).
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version && npx --version
# (These userland additions sit BEFORE the source-built CMRU wheel on purpose: the cmru Dockerfile
# oracle forbids any layer that could rewrite the installed wheel from following its install.)

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

# Copy dependency metadata first. The generator reads only these pyprojects, so
# this layer stays stable when source files change.
COPY tester-unified/gen-requirements.py /usr/local/lib/tester-unified/gen-requirements.py
COPY tester-unified/fetch-cli-extended.py /usr/local/lib/tester-unified/fetch-cli-extended.py
COPY ciu/pyproject.toml /src/ciu/pyproject.toml
COPY cmru/pyproject.toml /src/cmru/pyproject.toml
COPY assay/pyproject.toml /src/assay/pyproject.toml
COPY topos/pyproject.toml /src/topos/pyproject.toml
COPY nyxloom/pyproject.toml /src/nyxloom/pyproject.toml
COPY scripts/cgroup-profiler/pyproject.toml /src/cgroup-profiler/pyproject.toml
COPY run-gate-project/pyproject.toml /src/run-gate-project/pyproject.toml
# ZERO-RELEASE INPUT: empty (a .gitkeep) unless the operator dropped a locally
# built cli_extended-*.whl here for CLI_EXTENDED_RESOLVE=local (see below).
COPY tester-unified/local-wheel/ /tmp/cli-extended-local/

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
#   latest  the `cli-extended-latest/latest.json` pointer (opt-in). The caller must
#           also pass the PUBLISHED digest as CLI_EXTENDED_WHEEL_SHA256 (build-image.sh
#           requires --cli-extended-wheel-sha256): a pointer naming other bytes, or
#           downloaded bytes that hash differently, fail the build, so no mode installs
#           unverified bytes.
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
#           PEP 440 local version (a stable release has none). Never a fallback: pinned stays
#           the default and fails by itself when the pinned release is missing.
ARG CLI_EXTENDED_RESOLVE=pinned
ARG CLI_EXTENDED_POINTER_URL=https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json
ARG CLI_EXTENDED_WHEEL_URL=https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.3.0/cli_extended-0.3.0-py3-none-any.whl
ARG CLI_EXTENDED_WHEEL_SHA256=a0f80f1b614bcaf81fac235aa3a7f42f20eccac06f82311a6d8d5a725eea5f50
# CLI_EXTENDED_WHEEL_SHA256 is, in EVERY mode, the digest the installed wheel must
# have: the repository pin (pinned), the supplied digest (local) or the published
# digest the caller names (latest, which refuses to resolve without it). The label
# records it, and the install RUN below verifies the actual wheel file against it.
LABEL org.vbpub.cli-extended.wheel-sha256="${CLI_EXTENDED_WHEEL_SHA256}"
# Exact identity of the Run-Gate wheel supplied through the named build context.
# The image installs those exact wheel bytes; the version and digest are
# independently checked before installation and against installed metadata.
ARG RUN_GATE_TESTER_VERSION
ARG RUN_GATE_TESTER_WHEEL_SHA256
RUN test -n "${RUN_GATE_TESTER_VERSION}" \
 && test -n "${RUN_GATE_TESTER_WHEEL_SHA256}" \
    || { echo "Run-Gate version and wheel SHA-256 are required; use tester-unified/build-image.sh" >&2; exit 1; }
LABEL org.vbpub.run-gate.version="${RUN_GATE_TESTER_VERSION}" \
      org.vbpub.run-gate.wheel-sha256="${RUN_GATE_TESTER_WHEEL_SHA256}"
RUN printf '{"version":"%s","sha256":"%s"}\n' \
      "${RUN_GATE_TESTER_VERSION}" "${RUN_GATE_TESTER_WHEEL_SHA256}" \
      > /etc/tester-unified-run-gate-wheel.json \
 && chmod 0444 /etc/tester-unified-run-gate-wheel.json
RUN /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      > /tmp/tester-requirements.txt \
 && /usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py --root /src \
      --build-requires cmru/pyproject.toml \
      > /tmp/internal-build-requirements.txt \
 && case "${CLI_EXTENDED_RESOLVE}" in \
      pinned) set -- --wheel-url "${CLI_EXTENDED_WHEEL_URL}" --sha256 "${CLI_EXTENDED_WHEEL_SHA256}" ;; \
      latest) set -- --pointer-url "${CLI_EXTENDED_POINTER_URL}" --expect-sha256 "${CLI_EXTENDED_WHEEL_SHA256}" ;; \
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
    fi \
 && set -- /tmp/cli-extended-release/cli_extended-*.whl \
 && [ "$#" -eq 1 ] && [ -f "$1" ] \
 && echo "${CLI_EXTENDED_WHEEL_SHA256}  $1" | sha256sum -c -

# `venv` preserves existing contents. Start with empty environments so files
# or `.pth` configuration inherited from a changed base image cannot affect
# dependency installation or the offline internal wheel builds.
RUN rm -rf /opt/tester-venv /tmp/internal-build-venv

RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir -r /tmp/tester-requirements.txt \
 && /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/cli-extended-release/*.whl \
 && /usr/local/bin/python3.14 -m venv /tmp/internal-build-venv \
 && /tmp/internal-build-venv/bin/pip install --no-cache-dir -r /tmp/internal-build-requirements.txt

# Start the source-build roots empty. A base image or earlier metadata layer
# must not contribute files that setuptools could discover beside the copied
# candidate sources.
RUN rm -rf /src/cmru /src/libraries/worktree /src/run-gate-project

# Keep Run-Gate's metadata for dependency closure generation. Its code is not
# rebuilt from this mutable context: the named context supplies the exact wheel
# whose version and digest the caller also installs into the cockpit.
COPY run-gate-project/pyproject.toml /src/run-gate-project/pyproject.toml
COPY --from=run-gate-wheel /run_gate-*.whl /tmp/run-gate-wheels/
RUN set -eu; \
    set -- /tmp/run-gate-wheels/run_gate-*.whl; \
    if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then \
      echo "named run-gate-wheel context must contain exactly one run_gate-*.whl" >&2; exit 1; \
    fi; \
    printf '%s  %s\n' "${RUN_GATE_TESTER_WHEEL_SHA256}" "$1" | sha256sum -c -; \
    /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "$1"; \
    rm -rf /tmp/run-gate-wheels /src/run-gate-project

# Build tooling is ready before package sources enter the image. These copies
# stay untouched until the single-command offline CMRU wheel build below; gate
# runs still execute against the BOUND worktree via PYTHONPATH.
COPY ciu/ /src/ciu/
COPY cmru/ /src/cmru/
COPY assay/ /src/assay/
COPY topos/ /src/topos/
COPY nyxloom/ /src/nyxloom/
# The one estate-internal library cmru still packages itself, at the path cmru's
# pyproject expects relative to /src/cmru (`../libraries/worktree/src`). It is
# built ONLY from this copied source, never from an index. cli-extended is NOT
# copied: it is a real wheel dependency of cmru (KI-51 / CX-D1) and is installed
# from its RELEASE below.
COPY libraries/worktree/ /src/libraries/worktree/
# scripts/cgroup-profiler adds the report tier (pandas/plotly/matplotlib/
# scipy/ruptures) that no other project here pulls in. It is NOT a fork
# candidate the way srdm was: its suite uses fake cgroup trees, injected
# clocks and stubbed sysfs by design, so it needs no Go toolchain, no
# privilege, no systemd and no Docker socket — only this closure.
COPY scripts/cgroup-profiler/ /src/cgroup-profiler/

RUN rm -rf /tmp/internal-wheels \
 && mkdir -p /tmp/internal-wheels \
 && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified \
    /tmp/internal-build-venv/bin/pip wheel --no-index --no-deps --no-build-isolation \
      -w /tmp/internal-wheels /src/cmru
RUN /opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/internal-wheels/*.whl \
 && rm -rf /tmp/internal-build-venv /tmp/internal-wheels /tmp/internal-build-requirements.txt /tmp/cli-extended-release \
 && /opt/tester-venv/bin/python -c "import build, cmru, run_gate, pytest, pytest_cov, xdist, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko, coverage, structlog, markdown_it, linkify_it; print('tester-unified closure OK')" \
 && /opt/tester-venv/bin/python -c "import pandas, plotly, matplotlib, scipy, numpy, ruptures; print('cgroup-profiler report tier OK')"

# Build-time assertion (BG-05): `worktree` and cmru resolve from THIS image's
# locally built cmru wheel (a PyPI release can never carry the +tester.unified
# local version segment), and cli_extended matches the selected resolver mode:
# a stable published wheel by default, or an explicitly supplied local wheel.
# cmru's metadata must require cli-extended.
RUN /opt/tester-venv/bin/python - <<'PY'
import importlib.metadata as metadata
import os
import subprocess
import sys
from pathlib import Path

import cli_extended
import cmru
import run_gate
import worktree

site = "/opt/tester-venv/"
for module in (cli_extended, cmru, run_gate, worktree):
    location = str(Path(module.__file__).resolve())
    assert location.startswith(site), f"{module.__name__} imports from {location}, not {site}"
version = metadata.version("cmru")
assert version.endswith("+tester.unified"), (
    f"cmru {version} was not built from the COPYed source in this image"
)
run_gate_version = metadata.version("run-gate")
expected_run_gate_version = (
    os.environ["RUN_GATE_TESTER_VERSION"]
)
assert run_gate_version == expected_run_gate_version, (
    f"Run-Gate wheel {run_gate_version} does not match the requested image version "
    f"{expected_run_gate_version}"
)
run_gate_location = str(Path(run_gate.__file__).resolve())
assert run_gate_location.startswith(site), (
    f"run_gate imports from {run_gate_location}, not {site}"
)
entrypoint = Path(sys.executable).with_name("run-gate")
reported = subprocess.run([str(entrypoint), "--version"], check=True,
                          capture_output=True, text=True).stdout
assert reported == f"run-gate {run_gate_version}\n", reported
released = metadata.version("cli-extended")
if os.environ["CLI_EXTENDED_RESOLVE"] == "local":
    # Zero-release mode: the operator-built wheel (sha256-verified above) carries a
    # PEP 440 local version segment so it cannot be mistaken for a stable release.
    assert "+" in released, f"cli-extended {released} has no local version segment"
else:
    assert "+" not in released, f"cli-extended {released} is a local build, not the release"
assert any(r.startswith("cli-extended") for r in metadata.requires("cmru") or ()), (
    "cmru's wheel does not declare cli-extended as a dependency"
)
print("cmru local, Run-Gate installed:", run_gate_version,
      "; cli-extended identity:", released)
PY

# The container runs as uid 1003 (to match the host-owned bind mounts), which
# the base image has no /etc/passwd entry for and no writable HOME. Provide
# BOTH: a passwd entry (pwd.getpwuid(1003) — topos daemon-status calls it) AND a
# writable HOME with XDG dirs, so suites that read/write $HOME/.config or
# $HOME/.cache (e.g. topos config/record) don't hit PermissionError on a
# root-owned or absent home. A gate must give the run-uid a real HOME.
RUN if getent group 1003 >/dev/null; then \
      test "$(getent group 1003 | cut -d: -f1)" != docker; \
    else \
      groupadd --gid 1003 tester; \
    fi \
 && ! getent passwd 1003 >/dev/null \
 && ! getent passwd tester >/dev/null \
 && echo "tester:x:1003:1003:tester:/home/tester:/bin/bash" >> /etc/passwd \
 && mkdir -p /home/tester/.config /home/tester/.cache /home/tester/.local/state \
 && chown -R 1003:1003 /home/tester \
 && chown -R 1003:1003 /opt/tester-venv

USER 1003
ENV PATH=/opt/tester-venv/bin:${PATH} \
    HOME=/home/tester \
    XDG_CONFIG_HOME=/home/tester/.config \
    XDG_CACHE_HOME=/home/tester/.cache \
    XDG_STATE_HOME=/home/tester/.local/state \
    TESTER_VENV=/opt/tester-venv

# The installed CLI must remain executable after switching to the unprivileged
# gate identity, with PATH resolving to the exact wheel installed above.
RUN run-gate --version
