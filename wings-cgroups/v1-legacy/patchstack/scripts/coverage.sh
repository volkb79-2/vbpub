#!/usr/bin/env bash
# coverage.sh [pterodactyl] — assay R1 changed-line coverage over the patch
# surface (base..HEAD, i.e. the 9-patch series against the upstream tag).
#
# Runs standalone, and is also what `COVERAGE=1 test.sh` calls.
#
# Everything happens in one golang container: this devcontainer has no Go
# toolchain, and assay's Go adapter needs a REAL `go` on PATH on the machine
# that judges (it re-derives statement positions from the source with a Go
# program it ships — see assay docs/CONSUMERS.md, Go point 1). assay itself is
# a stdlib-only zipapp, and golang:1.24 already carries /usr/bin/python3
# 3.13.5, so nothing is installed: the .pyz is verified on the host, copied in,
# and run with the interpreter that is already there.
#
# Rigor is R0+R1. Assay caps Go at R1; R2/R3 are UNSUPPORTED for Go.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }

# judge.base in assay.toml is the upstream tag this series sits on. Only the
# pterodactyl target has one; pelican tracks a moving `main`.
[[ "$TARGET" == "pterodactyl" ]] || {
    echo "coverage.sh: only the pterodactyl target has an assay lane (its judge.base is the fixed tag $PTERODACTYL_REF)" >&2
    exit 2
}

# --- locate and verify the judge -------------------------------------------
# vbpub-internal: consume assay's own release output rather than vendoring a
# copy of the .pyz here. Override with ASSAY_DIST=<dir> to pin another build.
VBPUB_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"
ASSAY_DIST="${ASSAY_DIST:-$VBPUB_ROOT/assay/artifacts/assay-v6.0.0/dist}"
ASSAY_PYZ="$(ls "$ASSAY_DIST"/assay-*.pyz 2>/dev/null | head -1)"
[[ -n "$ASSAY_PYZ" && -f "$ASSAY_PYZ.sha256" ]] || {
    echo "coverage.sh: no assay zipapp + .sha256 under $ASSAY_DIST" >&2
    exit 1
}
PYZ_NAME="$(basename "$ASSAY_PYZ")"
echo "=== verifying judge: $PYZ_NAME (host side) ==="
( cd "$ASSAY_DIST" && sha256sum -c "$PYZ_NAME.sha256" )

# --- stage the judge + the lane file into the disposable clone --------------
# The clone is recreated by clone.sh and rewritten by apply.sh/rebase.sh, so
# both of these are copies of committed originals and both are removed again
# on exit. Nothing that matters is allowed to live only in build/.
STAGE_DIR="$SRC_DIR/.assay-judge"
cleanup() { rm -rf "$STAGE_DIR" "$SRC_DIR/assay.toml"; }
trap cleanup EXIT
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp "$ASSAY_PYZ" "$ASSAY_PYZ.sha256" "$STAGE_DIR/"
cp "$STACK_DIR/assay/assay.toml" "$SRC_DIR/assay.toml"

OUT_DIR="$BUILD_DIR/assay-$TARGET"
mkdir -p "$OUT_DIR"

# --- run ------------------------------------------------------------------
# .git rides along: assay resolves base..HEAD and materialises its snapshot
# from the repository. `go mod download` pre-warms the shared module cache so
# the lane itself can run with GOPROXY=off.
CMD='
set -e
mv /src/.assay-judge /opt/assay-judge
echo "=== verifying judge: '"$PYZ_NAME"' (container side) ==="
cd /opt/assay-judge && sha256sum -c "'"$PYZ_NAME"'.sha256"
cd /src
# NB: ownership of /src has to match the container user; see the
# --no-same-owner note in common.sh. A `safe.directory` exception does NOT
# work here — assay runs git with GIT_CONFIG_GLOBAL=/dev/null.
#
# clone.sh clones --depth 50, and assay refuses a shallow repository outright
# ("refusing a grafted or shallow source whose reachable history is
# redefined"). Deleting .git/shallow would only hide the graft, so the copy is
# genuinely completed instead. Again: container-local, the host clone stays
# shallow and untouched.
if [ -f /src/.git/shallow ]; then
    echo "=== unshallowing the container-local copy (assay refuses shallow) ==="
    git fetch --unshallow --quiet origin
fi

# An R1+ lane refuses NO_MEASUREMENT/DIRTY_TREE on a WHOLE-TREE dirty check,
# not just on the source roots, so the copied-in assay.toml would take the lane
# down on its own. There is no way to exempt it: assay unions
# `git status --porcelain` with `git ls-files --others
# --exclude-per-directory=.gitignore` specifically so that .git/info/exclude
# and every non-committed ignore source is powerless (assay A-177/A-290). A
# lane file therefore has to be COMMITTED in the repository it judges — and it
# has to sit at the Go module root, which here IS the disposable clone.
#
# So it is committed HERE, inside the throwaway container copy, on top of the
# patch-stack tip. The real clone on the host is never touched, and the commit
# adds no line under judge.source_roots, so the measured surface is identical
# to the patch tip printed below.
echo "=== committing the lane file into the container-local copy ==="
git rev-parse HEAD | sed "s/^/patch-stack tip: /"
git -c user.name=wings-cgroups -c user.email=wings-cgroups@localhost \
    -c commit.gpgSign=false add -- assay.toml
git -c user.name=wings-cgroups -c user.email=wings-cgroups@localhost \
    -c commit.gpgSign=false commit -q -m "chore(assay): lane file (container-local, not pushed)"
git rev-parse HEAD | sed "s/^/judged commit: /"
export GOMODCACHE=/go/pkg/mod
echo "=== go mod download (warms the cache; the lane runs GOPROXY=off) ==="
go mod download
echo "=== assay run coverage ==="
set +e
# Verdict lands OUTSIDE /src: writing it into the judged tree would dirty the
# tree that assay re-checks after the command.
python3 /opt/assay-judge/'"$PYZ_NAME"' run coverage \
    --file assay.toml \
    --verdict-json /tmp/verdict.json
rc=$?
set -e
echo "=== BEGIN verdict.json ==="
cat /tmp/verdict.json 2>/dev/null || echo "(no verdict written)"
echo "=== END verdict.json ==="
exit $rc
'

echo "=== assay R1 changed-line coverage ($TARGET, base=$REF) ==="
set +e
go_in_container "$SRC_DIR" --with-git --cpus 3 "$CMD" 2>&1 | tee "$OUT_DIR/run.log"
rc="${PIPESTATUS[0]}"
set -e

# Split the verdict back out of the transcript so it survives as CI evidence.
sed -n '/^=== BEGIN verdict.json ===$/,/^=== END verdict.json ===$/p' "$OUT_DIR/run.log" \
    | sed '1d;$d' > "$OUT_DIR/verdict.json"
echo "coverage.sh: transcript $OUT_DIR/run.log, verdict $OUT_DIR/verdict.json"
[[ "$rc" -eq 0 ]] && echo "coverage.sh: PASS ($TARGET)" || echo "coverage.sh: assay exit $rc ($TARGET)" >&2
exit "$rc"
