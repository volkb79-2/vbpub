#!/usr/bin/env bash
# F008-A4 -- regenerate assay's committed Go coverage FIXTURES from real
# toolchain output, and derive the statement positions that make them readable.
#
# A-234 recorded that `tests/fixtures/go/hello/hello.out` and
# `tests/fixtures/canary/go/greet/greet_control.out` were hand-authored and
# wrong in BOTH coordinates. A-234's own warning is the reason this script
# emits two artifacts rather than one: swapping in a real profile while the
# expectations are still the naive block expansion replaces a wrong profile
# with a real profile READ as statement truth, which is the exact conflation
# A-O19/A-217 exist to remove. So every run writes
#
#   * the real `go test -coverprofile` bytes for each fixture, and
#   * `fixture-oracle.json` -- the real output of
#     `assay/helpers/go/stmtpos/` over the same source bytes,
#
# and the tests join the two with `assay.statement_attribution` rather than
# reading either alone.
#
# A-042/A-043: this devcontainer has NO Go toolchain and must not acquire one.
# Everything runs inside `tester-unified-go:local`, built from
# `vbpub/tester-unified-go/Dockerfile`:
#
#   docker build -f tester-unified-go/Dockerfile -t tester-unified-go:local \
#       /workspaces/vbpub/tester-unified-go
#
# `--network=none` proves nothing is downloaded: the fixtures import only the
# standard library and `go mod init` is run offline. The tree is piped in with
# `tar` rather than bind-mounted, the pattern `probe-stmtpos.sh` and
# `carve-assets/P27/README.md` already use here.
#
# Usage:  ASSAY=/path/to/assay bash regenerate-fixtures.sh > raw.txt
# The output is delimited plain text; `PROVENANCE.md` records how the run that
# produced the committed bytes was invoked and what it returned.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/workspaces/vbpub}"
ASSAY="${ASSAY:-$REPO_ROOT/assay}"
HELPER="$ASSAY/src/assay/helpers/go/stmtpos"
HELLO="$ASSAY/tests/fixtures/go/hello"
GREET="$ASSAY/tests/fixtures/canary/go/greet"

: "${CGROUP_PARENT_DEV_BACKGROUND:?set by the devcontainer; a spawned container must be placed on the host tier, never left at the Docker unconfined default (AGENTS.md)}"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
mkdir -p "$staging"/{helper,hello,greet,greet-transformed,oracle-src}

cp "$HELPER/stmtpos.go" "$HELPER/go.mod" "$staging/helper/"
cp "$HELLO/hello.go" "$HELLO/doc.go" "$staging/hello/"
cp "$GREET/greet.go" "$staging/greet/"

# The transformed half is the REAL adapter's own pure transform of the
# committed control text (A-105/A-107) -- computed here, never committed as a
# second `.go` file, so the fixture cannot drift from the adapter under test.
PYTHONPATH="$ASSAY/src" python3 - "$GREET/greet.go" "$staging/greet-transformed/greet.go" <<'PY'
import sys
from pathlib import Path

from assay.adapters.go import GoAdapter

control = Path(sys.argv[1]).read_text(encoding="utf-8")
transformed, _ = GoAdapter().inject_uncovered_line(control)
Path(sys.argv[2]).write_text(transformed, encoding="utf-8")
PY

# The two test files. They live here rather than in the fixture directories
# because a committed `_test.go` beside a committed profile is compiled by
# nothing and drifts silently; keeping them in the generator makes the profile
# and the test that produced it one artifact.
cat > "$staging/hello/hello_test.go" <<'EOF'
package hello

import "testing"

func TestGreet(t *testing.T) {
	if Greet("world") == "" {
		t.Fatal("Greet returned empty")
	}
}
EOF

cat > "$staging/greet/greet_test.go" <<'EOF'
package greet

import "testing"

func TestGreet(t *testing.T) {
	if Greet("world") == "" {
		t.Fatal("Greet returned empty")
	}
}
EOF
cp "$staging/greet/greet_test.go" "$staging/greet-transformed/greet_test.go"

# The oracle parses files; it does not compile a package, so the four sources
# can sit flat in one directory under distinct names (the collision pair's own
# trick in `probe-stmtpos.sh`). `greet_transformed.go` is byte-identical to
# `greet-transformed/greet.go`, which is what makes its positions the ones
# `go test` instrumented there.
cp "$staging/hello/hello.go"              "$staging/oracle-src/hello.go"
cp "$staging/hello/doc.go"                "$staging/oracle-src/doc.go"
cp "$staging/greet/greet.go"              "$staging/oracle-src/greet.go"
cp "$staging/greet-transformed/greet.go"  "$staging/oracle-src/greet_transformed.go"

tar -C "$staging" -cf - . | docker run -i --rm --network=none \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e GOPROXY=off -e GOWORK=off -e GOTOOLCHAIN=local -e GOFLAGS=-mod=mod \
  tester-unified-go:local bash -c '
    set -euo pipefail
    mkdir -p /tmp/w && tar -C /tmp/w -xf - && cd /tmp/w
    # `go mod init hello` makes the package import path `hello`, so the
    # profile keys are `hello/hello.go` -- the spelling `git diff` would use
    # for this fixture rooted at tests/fixtures/go/. Same for `greet`.
    ( cd hello && go mod init hello >/dev/null 2>&1 \
        && go test -covermode=set -coverprofile=/tmp/w/hello.out . >/dev/null )
    ( cd greet && go mod init greet >/dev/null 2>&1 \
        && go test -covermode=set -coverprofile=/tmp/w/greet_control.out . >/dev/null )
    ( cd greet-transformed && go mod init greet >/dev/null 2>&1 \
        && go test -covermode=set -coverprofile=/tmp/w/greet_transformed.out . >/dev/null )
    echo "=== go-version ==="
    go version
    echo "=== hello.out ==="
    cat /tmp/w/hello.out
    echo "=== greet_control.out ==="
    cat /tmp/w/greet_control.out
    echo "=== greet_transformed.out ==="
    cat /tmp/w/greet_transformed.out
    echo "=== fixture-oracle.json ==="
    ( cd helper && go run . ../oracle-src/doc.go ../oracle-src/greet.go \
        ../oracle-src/greet_transformed.go ../oracle-src/hello.go )
  '
