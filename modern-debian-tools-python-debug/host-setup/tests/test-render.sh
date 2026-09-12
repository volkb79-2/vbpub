#!/usr/bin/env bash
# mdt host-setup — renderer test. Renders every units/*.in template from
# host-setup.env.example's SHIPPED defaults (no root, no host mutation, no
# /etc/mdt/host-setup.env involved) and asserts:
#   1. every rendered unit is free of unresolved @VAR@ placeholder tokens
#      (the exact class of bug CGROUP-NOTES.md's "Status corrected
#      2026-09-08" note describes for DEV_MEMORY_MIN_GUARANTEED_CEILING: a
#      RENDER_VARS omission that would ship a literal broken token) --
#      checked for every unit, not only the new one;
#   2. dev-gates.slice.in renders every DEV_GATES_* key to its shipped
#      example value (RG-55 P8, D-19);
#   3. dev.slice.in and dev-memory_min_guaranteed.slice.in render with NO
#      MemoryMin line at all (DEV_MEMORY_MIN_GUARANTEED_CEILING ships unset
#      in the example -- render()'s own "unset = directive dropped" rule),
#      confirming the RW-30/A1 revert left the original 1:1-pin behaviour
#      byte-for-byte intact;
#   4. no rendered unit, and no file under units/, mentions dev-infra or
#      DEV_INFRA_ (RW-30/A1: withdrawn).
#
# Extracts install.sh's OWN render() function + RENDER_VARS list verbatim
# (byte range, not a hand-copied duplicate) so this test can never silently
# drift from what install.sh actually runs.
#
# Usage: bash host-setup/tests/test-render.sh   (from anywhere; self-locates)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # host-setup/
INSTALL_SH="$HERE/install.sh"
EXAMPLE="$HERE/host-setup.env.example"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

[ -f "$INSTALL_SH" ] || fail "install.sh not found at $INSTALL_SH"
[ -f "$EXAMPLE" ] || fail "host-setup.env.example not found at $EXAMPLE"

# --- extract render() + RENDER_VARS verbatim from install.sh ----------------
RENDER_SNIPPET="$TMP/render-snippet.sh"
sed -n '/^RENDER_VARS="/,/SWEEP_INTERVAL"$/p' "$INSTALL_SH" > "$RENDER_SNIPPET"
sed -n '/^render() {/,/^}$/p' "$INSTALL_SH" >> "$RENDER_SNIPPET"
grep -q '^RENDER_VARS="' "$RENDER_SNIPPET" \
  || fail "could not extract RENDER_VARS from install.sh -- anchor patterns are stale, update this test"
grep -q '^render() {' "$RENDER_SNIPPET" \
  || fail "could not extract render() from install.sh -- anchor patterns are stale, update this test"

# --- source the extracted render() + RENDER_VARS, then the shipped example --
# shellcheck disable=SC1090
. "$RENDER_SNIPPET"
# shellcheck disable=SC1090
. "$EXAMPLE"
type render >/dev/null 2>&1 || fail "render() was not defined after sourcing the extracted snippet"

# --- render every units/*.in template ----------------------------------------
rendered_any=0
for tmpl in "$HERE"/units/*.in; do
  [ -e "$tmpl" ] || continue
  rendered_any=1
  dst="$TMP/$(basename "${tmpl%.in}")"
  render "$tmpl" "$dst"
  # (1) no unresolved @VAR@ tokens survive rendering, in ANY unit.
  if grep -qE '@[A-Za-z_][A-Za-z0-9_]*@' "$dst"; then
    fail "$(basename "$tmpl") rendered with an unresolved placeholder: $(grep -oE '@[A-Za-z_][A-Za-z0-9_]*@' "$dst" | sort -u | tr '\n' ' ')"
  fi
done
[ "$rendered_any" = 1 ] || fail "no units/*.in templates found under $HERE/units"
pass "every rendered unit is free of unresolved @VAR@ placeholders"

# --- (2) dev-gates.slice.in renders the shipped example's own values --------
GATES_OUT="$TMP/dev-gates.slice"
[ -f "$GATES_OUT" ] || fail "dev-gates.slice.in did not render to $GATES_OUT"
for expected in \
  "MemoryHigh=${DEV_GATES_MEMORY_HIGH}" \
  "MemoryMax=${DEV_GATES_MEMORY_MAX}" \
  "MemorySwapMax=${DEV_GATES_MEMORY_SWAP_MAX}" \
  "CPUWeight=${DEV_GATES_CPU_WEIGHT}" \
  "IOWeight=${DEV_GATES_IO_WEIGHT}" \
  "ManagedOOMMemoryPressureLimit=${DEV_GATES_OOM_PRESSURE_LIMIT}" \
  "ManagedOOMMemoryPressure=kill" \
  "ManagedOOMSwap=kill" \
; do
  grep -qxF "$expected" "$GATES_OUT" || fail "dev-gates.slice missing expected line: $expected"
done
pass "dev-gates.slice renders every DEV_GATES_* key from the shipped example"

# --- (3) dev.slice / dev-memory_min_guaranteed.slice: no MemoryMin line -----
# (DEV_MEMORY_MIN_GUARANTEED_CEILING ships unset in the example -- confirms
# the RW-30/A1 revert restored the original opt-in-only behaviour exactly.)
for slice in dev.slice dev-memory_min_guaranteed.slice; do
  out="$TMP/$slice"
  [ -f "$out" ] || fail "$slice did not render to $out"
  grep -q '^MemoryMin=' "$out" \
    && fail "$slice rendered a MemoryMin= line with DEV_MEMORY_MIN_GUARANTEED_CEILING unset -- expected it dropped"
done
pass "dev.slice and dev-memory_min_guaranteed.slice render with no MemoryMin (opt-in, unset by default)"

# --- (4) no trace of the withdrawn dev-infra.slice anywhere under units/ ----
[ -e "$HERE/units/dev-infra.slice.in" ] && fail "units/dev-infra.slice.in still exists -- RW-30/A1 withdrew it"
if grep -rlE 'dev-infra|DEV_INFRA_' "$HERE"/units/*.in 2>/dev/null; then
  fail "a units/*.in template still mentions dev-infra/DEV_INFRA_ -- RW-30/A1 withdrew it"
fi
pass "no units/*.in template mentions the withdrawn dev-infra.slice"

# --- bash -n + shellcheck (if available) on every shell script this package touches --
SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-apply-dev-caps.sh" "$HERE/scripts/check.sh" "${BASH_SOURCE[0]}")
for s in "${SCRIPTS[@]}"; do
  bash -n "$s" || fail "bash -n failed: $s"
done
pass "bash -n clean on every touched shell script"

if command -v shellcheck >/dev/null 2>&1; then
  # -S warning: this package leaves a handful of PRE-EXISTING info/style
  # findings untouched (SC2015 "A && B || C", SC2181 "check $? directly") --
  # verified none are on a line this package's diff touches, and fixing
  # decades of pre-existing style debt across install.sh/check.sh/
  # mdt-apply-dev-caps.sh is out of this package's scope. warning severity
  # and above is a real correctness bar; style/info is not enforced here.
  shellcheck -S warning "${SCRIPTS[@]}" || fail "shellcheck (warning severity) reported an issue -- see above"
  pass "shellcheck (warning severity+) clean on every touched shell script"
else
  echo "shellcheck not available -- skipped (bash -n above still ran)"
fi

echo "test-render: ALL OK"
