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
#      in the example -- render()'s own "unset = directive dropped" rule).
#      This asserts only that no MemoryMin= line renders while the var is
#      unset -- it would pass just as happily if the directive were
#      re-pointed at a different (also unset) variable, so it is evidence
#      FOR, not proof of, the RW-30/A1 revert's byte-for-byte claim (that
#      claim rests on `git diff 11ac5d67` showing units/dev.slice.in and
#      units/dev-memory_min_guaranteed.slice.in byte-identical to baseline,
#      established independently, not by this test -- round-1 review S7);
#   4. no rendered unit, and no file under units/, mentions dev-infra or
#      DEV_INFRA_ (RW-30/A1: withdrawn);
#   5. install.sh actually renders (and, for every *.slice template, starts)
#      each units/*.in -- this test renders every template ITSELF in step 1,
#      which does not by itself prove install.sh's own render()/systemctl
#      start calls include it (round-1 review B6: a template that renders
#      correctly but that install.sh forgets to wire in would pass 1-4 and
#      still never reach a real host);
#   6. every @VAR@ placeholder used in any template has a matching `VAR=`
#      assignment line in host-setup.env.example (the general form of B3's
#      failure mode: a key added to a template's RENDER_VARS without ever
#      being added to the example renders as silently dropped/unbounded on
#      an upgraded host, round-1 review B6).
#
# Extracts install.sh's OWN render() function + RENDER_VARS list verbatim
# (byte range, not a hand-copied duplicate) so this test can never silently
# drift from what install.sh actually runs. The extraction is guarded on
# BOTH ends (round-1 review B2): if the RENDER_VARS end-anchor ever stops
# matching, sed would otherwise range to EOF and the "snippet" becomes the
# rest of install.sh -- root, apt-get, every render() call, the
# /etc/docker/daemon.json merge, modprobe, and every systemctl call. This
# test refuses to `source` anything containing a host-mutating statement,
# checked before the `.` on line ~64, not merely "did the start anchor
# match" (which a truncated-but-still-anchored snippet would still pass).
#
# Usage: bash host-setup/tests/test-render.sh   (from anywhere; self-locates)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # host-setup/
INSTALL_SH="$HERE/install.sh"
EXAMPLE="$HERE/host-setup.env.example"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"   # nothing this test (or anything it shells out to) does
                        # can write outside its own scratch dir (S14)

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

[ -f "$INSTALL_SH" ] || fail "install.sh not found at $INSTALL_SH"
[ -f "$EXAMPLE" ] || fail "host-setup.env.example not found at $EXAMPLE"

# --- extract render() + RENDER_VARS verbatim from install.sh ----------------
RENDER_SNIPPET="$TMP/render-snippet.sh"
sed -n '/^RENDER_VARS="/,/SWEEP_INTERVAL"$/p' "$INSTALL_SH" > "$RENDER_SNIPPET"

# Start-anchor guard (unchanged): did sed find RENDER_VARS at all?
grep -q '^RENDER_VARS="' "$RENDER_SNIPPET" \
  || fail "could not extract RENDER_VARS from install.sh -- anchor patterns are stale, update this test"
# End-anchor guard (NEW, B2): a start-anchor match alone does not prove the
# range closed correctly -- if /SWEEP_INTERVAL"$/ ever stops matching,
# sed ranges silently to EOF instead of failing, and the snippet becomes
# most of install.sh. Assert the snippet's OWN last line is the expected
# closing line, not just that the pattern occurs somewhere in the file.
tail -n1 "$RENDER_SNIPPET" | grep -q 'SWEEP_INTERVAL"$' \
  || fail "RENDER_VARS end anchor no longer matches install.sh -- extraction over-ran, update this test's anchor patterns before trusting anything below"

sed -n '/^render() {/,/^}$/p' "$INSTALL_SH" >> "$RENDER_SNIPPET"
grep -q '^render() {' "$RENDER_SNIPPET" \
  || fail "could not extract render() from install.sh -- anchor patterns are stale, update this test"
tail -n1 "$RENDER_SNIPPET" | grep -qx '}' \
  || fail "render() end anchor no longer matches install.sh -- extraction over-ran, update this test's anchor patterns before trusting anything below"

# Fail-closed content guard (NEW, B2): even with both anchors intact, refuse
# to `source` a snippet that contains anything host-mutating. This is the
# actual safety net -- the anchor guards above only catch a stale PATTERN,
# not a snippet that happens to still start/end right but grew to include
# real commands in between.
if grep -nE '^(systemctl|apt-get|install |mkdir |modprobe|udevadm|python3 )|/etc/systemd/system|/etc/docker' "$RENDER_SNIPPET"; then
  fail "extracted snippet contains a host-mutating statement (see above) -- refusing to source it"
fi

# --- source the extracted render() + RENDER_VARS, then the shipped example --
# shellcheck disable=SC1090
. "$RENDER_SNIPPET"
# shellcheck disable=SC1090
. "$EXAMPLE"
type render >/dev/null 2>&1 || fail "render() was not defined after sourcing the extracted snippet"
pass "render()/RENDER_VARS extracted from install.sh and guarded (anchors + host-mutation scan)"

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
# ${VAR:?msg} rather than a bare ${VAR}: under `set -u` a mistyped/missing key
# in host-setup.env.example (the example is what was just sourced, not this
# script) must fail with a message that points at the EXAMPLE, not one that
# reads like an internal crash in this test (round-1 review S8).
GATES_OUT="$TMP/dev-gates.slice"
[ -f "$GATES_OUT" ] || fail "dev-gates.slice.in did not render to $GATES_OUT"
for expected in \
  "MemoryHigh=${DEV_GATES_MEMORY_HIGH:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_MEMORY_HIGH= line}" \
  "MemoryMax=${DEV_GATES_MEMORY_MAX:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_MEMORY_MAX= line}" \
  "MemorySwapMax=${DEV_GATES_MEMORY_SWAP_MAX:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_MEMORY_SWAP_MAX= line}" \
  "CPUWeight=${DEV_GATES_CPU_WEIGHT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_CPU_WEIGHT= line}" \
  "IOWeight=${DEV_GATES_IO_WEIGHT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_IO_WEIGHT= line}" \
  "ManagedOOMMemoryPressureLimit=${DEV_GATES_OOM_PRESSURE_LIMIT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_OOM_PRESSURE_LIMIT= line}" \
  "ManagedOOMMemoryPressure=kill" \
; do
  grep -qxF "$expected" "$GATES_OUT" || fail "dev-gates.slice missing expected line: $expected"
done
# ManagedOOMSwap=kill was withdrawn from dev-gates.slice.in (RW-32/D2: an
# oomd swap-usage kill contradicts D-19's "swap is the gates' relief valve,
# MemorySwapMax=32G"). Assert it is ABSENT, the mirror image of the
# presence checks above -- a regression that re-adds it should also go RED.
grep -qx 'ManagedOOMSwap=kill' "$GATES_OUT" \
  && fail "dev-gates.slice has ManagedOOMSwap=kill -- withdrawn by RW-32/D2 (contradicts D-19/D-6, see units/dev-gates.slice.in's own comment)"
pass "dev-gates.slice renders every DEV_GATES_* key from the shipped example, and ManagedOOMSwap=kill stays withdrawn"

# --- (3) dev.slice / dev-memory_min_guaranteed.slice: no MemoryMin line -----
# (DEV_MEMORY_MIN_GUARANTEED_CEILING ships unset in the example -- evidence
# FOR, not proof of, the RW-30/A1 revert's byte-for-byte claim; see the
# file header comment above, S7.)
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

# --- (5) install.sh actually renders + (for *.slice) starts each template --
# (round-1 review B6: rendering a template correctly here does not prove
# install.sh's own render()/systemctl start calls include it.)
JOINED_INSTALL="$(sed ':a;N;$!ba;s/\\\n/ /g' "$INSTALL_SH")"   # collapse `\`-continued
                                                                # lines to one logical
                                                                # line each, so a name on
                                                                # a continuation (e.g. the
                                                                # systemctl start list)
                                                                # is still found
for tmpl in "$HERE"/units/*.in; do
  base="$(basename "${tmpl%.in}")"
  grep -qF "units/$(basename "$tmpl")" "$INSTALL_SH" \
    || fail "install.sh never renders units/$(basename "$tmpl")"
  case "$base" in
    *.slice)
      printf '%s\n' "$JOINED_INSTALL" \
        | grep -qE "systemctl start\b.*\b${base//./\\.}\b" \
        || fail "install.sh never starts $base (not in any systemctl start ... line, incl. continuations)" ;;
  esac
done
pass "install.sh renders every units/*.in, and starts every *.slice"

# --- (6) every @VAR@ placeholder used in any template has an example line --
for v in $(grep -ohE '@[A-Z_][A-Z0-9_]*@' "$HERE"/units/*.in | tr -d '@' | sort -u); do
  grep -qE "^${v}=" "$EXAMPLE" || fail "@$v@ has no ${v}= line in host-setup.env.example -- an upgraded host would render it silently dropped/unbounded (B3's failure mode)"
done
pass "every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example"

# --- (7) install.sh installs the cgprofile tmpfiles.d entry (D-30/M5) ------
grep -qF 'tmpfiles-cgprofile.conf' "$INSTALL_SH" \
  || fail "install.sh does not install the cgprofile tmpfiles.d entry (D-30/M5, units/tmpfiles-cgprofile.conf)"
pass "install.sh installs the cgprofile tmpfiles.d entry"

# --- (8) the wizard's earmark-sum doctest actually runs (round-1 review S2) --
# propose_memory_min_guaranteed_suggestion()'s own doctest demonstrates the
# leftover/suggested-ceiling BEFORE vs AFTER dev-gates.slice's MemoryHigh
# enters the earmark sum -- run it for real, not just py_compile the file.
python3 -m doctest "$HERE/mdt-host-setup-wizard.py" \
  || fail "mdt-host-setup-wizard.py doctest failed (propose_memory_min_guaranteed_suggestion's before/after earmark-sum demonstration, S2)"
pass "wizard's earmark-sum doctest (before/after dev-gates.slice's MemoryHigh) passes"

# --- bash -n + shellcheck (if available) on every shell script this package touches --
SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-apply-dev-caps.sh" "$HERE/scripts/check.sh" "$HERE/scripts/mdt-dev-cap-watcher.py" "${BASH_SOURCE[0]}")
for s in "${SCRIPTS[@]}"; do
  case "$s" in
    *.py) python3 -m py_compile "$s" || fail "py_compile failed: $s" ;;
    *)    bash -n "$s" || fail "bash -n failed: $s" ;;
  esac
done
pass "bash -n / py_compile clean on every touched script"

SHELL_SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-apply-dev-caps.sh" "$HERE/scripts/check.sh" "${BASH_SOURCE[0]}")
if command -v shellcheck >/dev/null 2>&1; then
  # -S warning: this package leaves a handful of PRE-EXISTING info/style
  # findings untouched (SC2015 "A && B || C", SC2181 "check $? directly") --
  # verified none are on a line this package's diff touches, and fixing
  # decades of pre-existing style debt across install.sh/check.sh/
  # mdt-apply-dev-caps.sh is out of this package's scope. warning severity
  # and above is a real correctness bar; style/info is not enforced here.
  shellcheck -S warning "${SHELL_SCRIPTS[@]}" || fail "shellcheck (warning severity) reported an issue -- see above"
  pass "shellcheck (warning severity+) clean on every touched shell script"
else
  echo "shellcheck not available -- skipped (bash -n above still ran)"
fi

echo "test-render: ALL OK"
