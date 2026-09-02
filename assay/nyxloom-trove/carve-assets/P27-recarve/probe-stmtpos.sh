#!/usr/bin/env bash
# P27 re-carve probe -- run the statement-position oracle over every frozen
# P27 witness and print its JSON to stdout.
#
# This is the command that produced `stmtpos-witness-oracle.json`. It is
# committed rather than described so the claim "the oracle derives {4,6} for
# collision-colA and {4,5} for collision-colB from byte-identical profiles" can
# be re-run by a reviewer instead of believed.
#
# A-042/A-043: this devcontainer has NO Go toolchain and must not acquire one.
# Everything runs inside `tester-unified-go:local`, built from
# `vbpub/tester-unified-go/Dockerfile`:
#
#   docker build -f tester-unified-go/Dockerfile -t tester-unified-go:local \
#       /workspaces/vbpub/tester-unified-go
#
# `--network=none` proves the helper needs no module download: `GOPROXY=off`
# alone would still let a populated module cache hide a dependency, but a
# container with no network at all cannot fetch one, and the helper is
# stdlib-only with a `require`-free `go.mod`.
#
# The tree is piped in with `tar` rather than bind-mounted, because this
# devcontainer's `/tmp` is not visible to the Docker daemon at the same path
# (the pattern `carve-assets/P27/README.md` documents for the original
# witnesses).
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/workspaces/vbpub}"
ASSAY="${ASSAY:-$REPO_ROOT/assay}"
HELPER="$ASSAY/src/assay/helpers/go/stmtpos"
WITNESS="$ASSAY/nyxloom-trove/carve-assets/P27/witness"
FIXTURE="$ASSAY/nyxloom-trove/carve-assets/P27/fixture"

: "${CGROUP_PARENT_DEV_BACKGROUND:?set by the devcontainer; a spawned container must be placed on the host tier, never left at the Docker unconfined default (AGENTS.md)}"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
mkdir -p "$staging/helper" "$staging/witness"
cp "$HELPER/stmtpos.go" "$HELPER/go.mod" "$staging/helper/"
cp "$WITNESS"/*.go "$staging/witness/"
# The two-commit fixture's calc.go under both commits, renamed so they can sit
# in one directory (the collision pair's own trick, one level over).
cp "$FIXTURE/commit1/calc.go" "$staging/witness/calc-commit1.go"
cp "$FIXTURE/commit2/calc.go" "$staging/witness/calc-commit2.go"

tar -C "$staging" -cf - . | docker run -i --rm --network=none \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e GOPROXY=off -e GOWORK=off -e GOTOOLCHAIN=local -e GOFLAGS=-mod=mod \
  tester-unified-go:local bash -c '
    set -euo pipefail
    mkdir -p /tmp/w && tar -C /tmp/w -xf - && cd /tmp/w/helper
    go run . ../witness/*.go'
