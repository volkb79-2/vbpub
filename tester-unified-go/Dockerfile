# tester-unified-go — the shared vbpub gating container for GO projects.
#
# Sibling of `tester-unified`, not a replacement for it. `tester-unified` is a
# Python 3.14 venv closure built from four projects' pyprojects; this is a Go
# toolchain. They are deliberately separate images:
#
#   > "Adding a Go toolchain to tester-unified would rebuild and re-risk the
#   > gate of ciu, cmru, topos and nyxloom to serve a project that still could
#   > not run its own e2e oracles there."
#   -- shared-ramdisk-depot-manager/gate/Dockerfile, which this file is
#      promoted from (its `unit` target, verbatim in substance).
#
# PROVENANCE: srdm built this first and proved it. Everything below was already
# working there; the only changes are de-srdm-ification (a generic run user) so
# a second Go project can use it without inheriting srdm's identity. srdm's
# `gate/Dockerfile` now layers its privileged systemd `e2e` stage ON TOP of
# this image rather than carrying its own copy of the toolchain setup.
# See assay/nyxloom-trove/decisions.md A-043.
#
# COCKPIT DOCTRINE, and the reason Go is NOT in the devcontainer: the gate is
# this image, never the devcontainer. The cockpit has no Go toolchain at all,
# so -- in srdm's own words -- "it cannot even pretend" to produce a ship
# signal. Adding one would manufacture exactly the pathway this doctrine
# exists to close, and would create a toolchain version that can drift from
# the one that ships. `tools/go` (a wrapper around this image) is how a
# cockpit gets Go ergonomics without a cockpit Go.
#
# Build (its own directory is the whole context -- no project sources needed):
#   docker build -f tester-unified-go/Dockerfile -t tester-unified-go:local \
#       /workspaces/vbpub/tester-unified-go
#
# Run placement is NOT this file's business: every container must be placed on
# the host tier by its caller (srdm's tools/cgroup-parent.sh verifies the slice
# is real rather than trusting the name -- a typo'd slice fails OPEN).
#
# THIS IMAGE ALREADY CARRIES A PYTHON INTERPRETER, INHERITED, AND NOTHING BELOW
# INSTALLS IT (assay decision A-402, 2026-09-01). `golang:1.25`'s Debian trixie
# base provides /usr/bin/python3 3.13.5, with no pip and no ensurepip. That is
# recorded here because it is load-bearing for a second consumer and is
# invisible from this file's own contents: assay is stdlib-only by design, its
# floor is `requires-python = ">=3.11"`, and its shipped ZIPAPP therefore runs
# in this image as-is -- which is the only install path there is, pip being
# absent. A Go project can judge its own coverage beside its own toolchain
# without this image growing a Python closure.
#
#   $ docker run --rm --network=none tester-unified-go:local python3 --version
#   Python 3.13.5
#
# Deliberately NOT an `apt-get install python3` layer: adding one for a package
# already present would be ceremony that reads as a safety property, and it
# would also make the image's Python a DIFFERENT interpreter from the one a
# consumer inherits. The check that keeps this honest lives where it can fail:
# assay's `tests/qualification/test_go_r1_real.py` asserts `python3 --version`
# is at or above 3.11 INSIDE this image, so a base-image change that drops or
# downgrades the interpreter goes red instead of silently disarming every Go
# qualification (the outcome asserted, not the mechanism -- assay A-396).

# The Go version is the version that ships. Pin it here and nowhere else, so a
# regenerated coverage profile or a rebuilt binary is reproducible; GOTOOLCHAIN
# below stops the toolchain silently upgrading itself out from under the pin.
FROM golang:1.25

# The gate runs as the uid that owns the host-side bind mount, so build
# artifacts and coverage files it writes are readable outside the container.
# A gate must give its run-uid a FULL identity: a passwd entry (os/user and
# anything calling getpwuid needs one), a group, a writable HOME, and XDG
# dirs. Without them suites fail with errors that look like breakage but are
# pure environment.
#
# 1003 is this estate's cockpit uid; override both ARGs on any host where the
# bind-mount owner differs. A wrong value fails loudly (permission denied on
# the mount), which is why it is a legitimate default rather than a hazard.
# The user NAME is fixed at `gate` rather than being an ARG: it is cosmetic
# (the uid/gid are what the bind mount cares about), and a name ARG would have
# to be re-expanded in the ENV block below, where a mismatch between the two
# would be silent.
ARG RUN_UID=1003
ARG RUN_GID=1003

RUN groupadd -g "${RUN_GID}" gate \
 && useradd -u "${RUN_UID}" -g "${RUN_GID}" -m -d /home/gate -s /bin/bash gate \
 && mkdir -p /home/gate/.config /home/gate/.cache /home/gate/.local/state \
 && chown -R "${RUN_UID}:${RUN_GID}" /home/gate

# Go's build and module caches must be writable by the run-uid, and they must
# NOT live on the bind-mounted worktree: a gate that writes cache into the
# repo pollutes the tree it is meant to be judging.
ENV HOME=/home/gate \
    XDG_CONFIG_HOME=/home/gate/.config \
    XDG_CACHE_HOME=/home/gate/.cache \
    XDG_STATE_HOME=/home/gate/.local/state \
    GOCACHE=/gocache \
    GOMODCACHE=/gomodcache \
    GOFLAGS=-mod=mod \
    GOTOOLCHAIN=local

# Prewarm the standard library into the shared cache so a gate run compiles
# only the project under test. Built as the run-uid so the cache is owned by
# whoever will use it -- a root-owned cache is silently unusable and every run
# pays full rebuild cost while looking fine.
RUN mkdir -p "${GOCACHE}" "${GOMODCACHE}" \
 && chown -R "${RUN_UID}:${RUN_GID}" "${GOCACHE}" "${GOMODCACHE}"
USER ${RUN_UID}:${RUN_GID}
RUN go build std

WORKDIR /src
