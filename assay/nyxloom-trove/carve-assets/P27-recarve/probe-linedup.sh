#!/usr/bin/env bash
# P27 re-carve probe -- the `//line`-directive witness (A-405, BLOCKER 1 of
# adversarial review round 1).
#
# Builds the Go project's OWN canonical duplicate-position fixture --
# `cmd/cover/cover_test.go`'s `lineDupContents`/`lineDupTestContents`, the
# corpus its `TestLineDup` uses -- runs the real `go test -covermode=count
# -coverprofile` over it, and then runs assay's statement-position oracle over
# the same source bytes. Prints the profile, then the oracle JSON.
#
# WHY this fixture and not one of our own. `cmd/cover` says in its own comment
# (go1.25.14 `/usr/local/go/src/cmd/cover/cover.go:1055-1060`) that "positions
# can repeat when there is a line directive that does not specify column
# information and the input has not been passed through gofmt. See issues
# #27530 and #30746. Tests are TestHtmlUnformatted and TestLineDup." So the
# toolchain names the exact corpus that exercises the case, and using anything
# else would be assay guessing at what Go considers the witness. This is the
# same discipline the frozen P27 witnesses keep, one level up.
#
# WHAT it proves, two things at once:
#   1. `dedup` (cover.go:1073-1090, the `for seenPos2[key] { key.p2.Column++ }`
#      loop) is REPLICATED correctly by `helpers/go/stmtpos/stmtpos.go`. REPORT
#      §5 item 4 recorded that as unproven for want of a witness; this is the
#      witness, and the oracle reproduces all nine extents and all nine
#      num_stmts.
#   2. A REAL `go test -coverprofile` artifact carries `Column == 0`. That is
#      `go/token`'s documented behaviour for a position remapped by a `//line
#      file:line` directive with no column, and it is why `dedup` has to exist
#      at all. assay used to refuse the whole artifact for it ("column number 0
#      ... is not positive") -- a guessed fact about `cmd/cover` that
#      `cmd/cover` disproves. A-405 is the disposition.
#
# A-042/A-043: this devcontainer has NO Go toolchain and must not acquire one.
# Everything runs inside `tester-unified-go:local`; `--network=none` proves
# nothing was fetched (the fixture is stdlib-only and its `go.mod` has no
# `require` lines). The tree is tar-piped rather than bind-mounted for the
# reason `probe-stmtpos.sh` records: this devcontainer's `/tmp` is not visible
# to the Docker daemon at the same path.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/workspaces/vbpub}"
ASSAY="${ASSAY:-$REPO_ROOT/assay}"
HELPER="$ASSAY/src/assay/helpers/go/stmtpos"

: "${CGROUP_PARENT_DEV_BACKGROUND:?set by the devcontainer; a spawned container must be placed on the host tier, never left at the Docker unconfined default (AGENTS.md)}"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
mkdir -p "$staging/helper" "$staging/linedup"
cp "$HELPER/stmtpos.go" "$HELPER/go.mod" "$staging/helper/"

# `lineDupContents`, transcribed VERBATIM from
# /usr/local/go/src/cmd/cover/cover_test.go (go1.25.14, lines 475-498) --
# tabs included, leading blank line included. `cat <<'EOF'` is quoted so not
# one byte is expanded.
cat >"$staging/linedup/linedup.go" <<'EOF'

package linedup

var G int

func LineDup(c int) {
	for i := 0; i < c; i++ {
//line ld.go:100
		if i % 2 == 0 {
			G++
		}
		if i % 3 == 0 {
			G++; G++
		}
//line ld.go:100
		if i % 4 == 0 {
			G++; G++; G++
		}
		if i % 5 == 0 {
			G++; G++; G++; G++
		}
	}
}
EOF

# `lineDupTestContents`, same file, lines 501-509.
cat >"$staging/linedup/linedup_test.go" <<'EOF'

package linedup

import "testing"

func TestLineDup(t *testing.T) {
	LineDup(100)
}
EOF

cat >"$staging/linedup/go.mod" <<'EOF'
module linedup

go 1.25
EOF

tar -C "$staging" -cf - . | docker run -i --rm --network=none \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e GOPROXY=off -e GOWORK=off -e GOTOOLCHAIN=local -e GOFLAGS=-mod=mod \
  tester-unified-go:local bash -c '
    set -euo pipefail
    mkdir -p /tmp/w && tar -C /tmp/w -xf - && cd /tmp/w/linedup
    go test -covermode=count -coverprofile=/tmp/w/linedup.out ./... >/dev/null
    echo "=== PROFILE ==="
    cat /tmp/w/linedup.out
    echo "=== ORACLE ==="
    cd /tmp/w/helper && go run . ../linedup/linedup.go
    echo
    echo "=== GO VERSION ==="
    go version'
