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
#      an upgraded host, round-1 review B6);
#   7. install.sh installs and applies the cgprofile control tmpfiles entry,
#      and the shared observer helper provisions only its group and directory
#      in narrow mode; tests run that exact helper against a throwaway root
#      with mocked systemd commands;
#   8. the wizard's propose_memory_min_guaranteed_suggestion() doctest
#      actually runs a non-zero test count, not merely exits 0 -- a file
#      with zero doctests also exits 0 (round-2 review S20(b));
#   9. check.sh's _bytes_of parses legal-systemd non-integer sizes (mdt's
#      own wizard emits half-GiB values routinely, e.g. "4.5G") to the
#      correct byte count, and returns "?" -- never a bash syntax error,
#      never a silent mis-compare -- for anything not byte-comparable
#      (a percentage, garbage) (round-2 review B7).
#
# Extracts install.sh's OWN render() function + RENDER_VARS list, and
# check.sh's OWN _bytes_of function, verbatim (byte range, not a
# hand-copied duplicate) so this test can never silently drift from what
# install.sh/check.sh actually run. Both extractions are guarded on BOTH
# ends (round-1 review B2): if an end-anchor ever stops matching, sed would
# otherwise range to EOF and the "snippet" becomes the rest of the file --
# for install.sh that is root, apt-get, every render() call, the
# /etc/docker/daemon.json merge, modprobe, and every systemctl call. Both
# extractions refuse to `source` anything containing a host-mutating
# statement, not merely "did the start anchor match" (which a
# truncated-but-still-anchored snippet would still pass).
#
# Usage: bash host-setup/tests/test-render.sh   (from anywhere; self-locates)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # host-setup/
INSTALL_SH="$HERE/install.sh"
EXAMPLE="$HERE/host-setup.env.example"
TMP="$(mktemp -d)"
observer_socket_pid=""
cleanup_test_tmp() {
  if [ -n "$observer_socket_pid" ]; then
    kill "$observer_socket_pid" 2>/dev/null || true
    wait "$observer_socket_pid" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup_test_tmp EXIT
export TMPDIR="$TMP"   # nothing this test (or anything it shells out to) does
                        # can write outside its own scratch dir (S14)

# tester-unified owns the gate interpreter and its dependency closure.  The
# cockpit fallback remains useful when this renderer is run by hand on a host
# that has no tester image, but a gate must not silently select system Python.
MDT_TEST_PYTHON="${MDT_TEST_PYTHON:-}"
if [ -z "$MDT_TEST_PYTHON" ]; then
  if [ -x /opt/tester-venv/bin/python ]; then
    MDT_TEST_PYTHON=/opt/tester-venv/bin/python
  else
    MDT_TEST_PYTHON="$(command -v python3)"
  fi
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

[ -f "$INSTALL_SH" ] || fail "install.sh not found at $INSTALL_SH"
[ -f "$EXAMPLE" ] || fail "host-setup.env.example not found at $EXAMPLE"

# --- extract render() + RENDER_VARS verbatim from install.sh ----------------
RENDER_SNIPPET="$TMP/render-snippet.sh"
sed -n '/^RENDER_VARS="/,/WATCHER_INTERVAL"$/p' "$INSTALL_SH" > "$RENDER_SNIPPET"

# Start-anchor guard (unchanged): did sed find RENDER_VARS at all?
grep -q '^RENDER_VARS="' "$RENDER_SNIPPET" \
  || fail "could not extract RENDER_VARS from install.sh -- anchor patterns are stale, update this test"
# End-anchor guard (NEW, B2): a start-anchor match alone does not prove the
# range closed correctly -- if /WATCHER_INTERVAL"$/ ever stops matching,
# sed ranges silently to EOF instead of failing, and the snippet becomes
# most of install.sh. Assert the snippet's OWN last line is the expected
# closing line, not just that the pattern occurs somewhere in the file.
tail -n1 "$RENDER_SNIPPET" | grep -q 'WATCHER_INTERVAL"$' \
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

# The managed daemon's TOML is rendered by the same install.sh helper but is
# deliberately not a systemd unit. Exercise it explicitly so the new
# max-parallelism setting cannot silently remain an unresolved token.
BUILDKIT_TOML_OUT="$TMP/buildkitd.toml"
render "$HERE/buildkitd.toml.in" "$BUILDKIT_TOML_OUT"
grep -qxF "  max-parallelism = $DEV_BUILDKITD_MAX_PARALLELISM" "$BUILDKIT_TOML_OUT" \
  || fail "buildkitd.toml.in did not render DEV_BUILDKITD_MAX_PARALLELISM"
grep -qF 'buildkitd.toml.in' "$INSTALL_SH" \
  || fail "install.sh never renders buildkitd.toml.in"
pass "managed BuildKit TOML renders its configured solver parallelism"

# Rootless BuildKit creates its socket with the mapped user's gid, so the
# directory's group alone is not authoritative. Keep the parent writable only
# while BuildKit binds, then seal it before root changes socket permissions.
BUILDKIT_UNIT_OUT="$TMP/mdt-buildkitd.service"
render "$HERE/units/mdt-buildkitd.service.in" "$BUILDKIT_UNIT_OUT"
grep -qxF 'RuntimeDirectoryMode=01777' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit runtime directory is not sticky"
grep -qxF 'RuntimeDirectoryPreserve=yes' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit service may replace a directory already bind-mounted by a devcontainer"
if grep -Eq \
  '(^|[[:space:]])(rm|rmdir)([[:space:]]+[^[:space:]]+)*[[:space:]]+/run/mdt-buildkitd([[:space:]]|$)' \
  "$BUILDKIT_UNIT_OUT"; then
  fail "managed BuildKit service removes or replaces the bind-mounted runtime directory"
fi
PRESTART_UNIT_LINE="$(grep '^ExecStartPre=/bin/sh -ec ' "$BUILDKIT_UNIT_OUT")"
[[ "$PRESTART_UNIT_LINE" == *'stat -c %%u:%%g "$path")" = 0:0 ] || /bin/chown 0:0 "$path"'* ]] \
  || fail "managed BuildKit service does not repair runtime-directory ownership only when needed"
[[ "$PRESTART_UNIT_LINE" == *'stat -c %%a "$path")" = 1777 ] || /bin/chmod 01777 "$path"'* ]] \
  || fail "managed BuildKit service does not repair runtime-directory mode only when needed"
[[ "$PRESTART_UNIT_LINE" == *'[ ! -L "$path" ]'* ]] \
  || fail "managed BuildKit service does not reject a symlink runtime directory"
if grep -qxF 'ExecStartPre=/bin/chmod 01777 /run/mdt-buildkitd' "$BUILDKIT_UNIT_OUT"; then
  fail "managed BuildKit service changes runtime-directory mode unconditionally"
fi
grep -qxF 'ExecStartPre=/bin/rm -f /run/mdt-buildkitd/buildkitd.sock' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit service does not remove a stale socket before restart"
grep -qF 'ExecStartPost=/bin/sh -ec' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit service has no socket-readiness permission step"
grep -qF '[ ! -L "$socket" ]' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit permission step does not reject symlink substitution"
grep -qF '/bin/chmod 0711 /run/mdt-buildkitd' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit permission step does not lock directory entry changes"
grep -qF 'socket_uid" = "$daemon_uid' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit permission step does not verify socket ownership"
grep -qF 'docker exec mdt-buildkitd stat -c %%u /run/buildkit/buildkitd.sock' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit ownership comparison is not made inside its user namespace"
if grep -qF '/usr/bin/stat -c %%u "$socket"' "$BUILDKIT_UNIT_OUT"; then
  fail "managed BuildKit ownership comparison incorrectly uses the host uid namespace"
fi
grep -qF '/bin/chgrp docker "$socket"' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit socket is not assigned to the existing docker group"
grep -qF '/bin/chmod 0660 "$socket"' "$BUILDKIT_UNIT_OUT" \
  || fail "managed BuildKit socket is not restricted to owner and docker group"
POSTSTART_UNIT_LINE="$(grep '^ExecStartPost=' "$BUILDKIT_UNIT_OUT")"
for format in g a; do
  grep -qF "stat -c %%$format \"\$socket\"" <<<"$POSTSTART_UNIT_LINE" \
    || fail "managed BuildKit stat format %$format is not escaped for systemd"
done
[[ "$POSTSTART_UNIT_LINE" == *'stat -c %%a /run/mdt-buildkitd)" = 711 ] || /bin/chmod 0711 /run/mdt-buildkitd; if [ ! -S "$socket" ] || [ -L "$socket" ]; then'*'docker exec mdt-buildkitd stat -c %%u /run/buildkit/buildkitd.sock'*'/bin/chgrp docker "$socket"'* ]] \
  || fail "managed BuildKit must seal and revalidate its directory before changing socket ownership"
# systemd consumes %% as one literal percent before /bin/sh runs. Any percent
# left after removing those escapes would be interpreted as a unit specifier,
# not passed through as shell text (e.g. %u becomes the manager's user name).
if printf '%s\n' "$PRESTART_UNIT_LINE" "$POSTSTART_UNIT_LINE" | sed 's/%%//g' | grep -q '%'; then
  fail "managed BuildKit post-start command contains an unescaped systemd specifier"
fi
PRESTART_SCRIPT="$(sed -n "s/^ExecStartPre=\/bin\/sh -ec '\(.*\)'$/\1/p" <<<"$PRESTART_UNIT_LINE")"
[ -n "$PRESTART_SCRIPT" ] \
  || fail "could not extract the managed BuildKit pre-start repair command"
printf '%s\n' "$PRESTART_SCRIPT" | sed 's/%%/%/g' | /bin/sh -n \
  || fail "managed BuildKit pre-start repair command does not parse"
POSTSTART_SCRIPT="$(sed -n "s/^ExecStartPost=\/bin\/sh -ec '\(.*\)'$/\1/p" "$BUILDKIT_UNIT_OUT")"
[ -n "$POSTSTART_SCRIPT" ] \
  || fail "could not extract the managed BuildKit post-start command"
printf '%s\n' "$POSTSTART_SCRIPT" | /bin/sh -n \
  || fail "managed BuildKit post-start shell command does not parse"
pass "managed BuildKit socket is validated and restricted to docker-group clients"

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
  "CPUWeight=${DEV_GATES_CPU_WEIGHT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_CPU_WEIGHT= line}" \
  "IOWeight=${DEV_GATES_IO_WEIGHT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_IO_WEIGHT= line}" \
  "ManagedOOMMemoryPressureLimit=${DEV_GATES_OOM_PRESSURE_LIMIT:?not set after sourcing host-setup.env.example -- missing or mistyped DEV_GATES_OOM_PRESSURE_LIMIT= line}" \
  "ManagedOOMMemoryPressure=kill" \
; do
  grep -qxF "$expected" "$GATES_OUT" || fail "dev-gates.slice missing expected line: $expected"
done
# MemorySwapMax is deliberately empty in the shipped example: install.sh
# derives it from this host's swap at install time. Preserve both directions
# of this check — an explicit example value must render, while the auto-
# detected form must drop the directive instead of emitting MemorySwapMax=.
if [ -n "${DEV_GATES_MEMORY_SWAP_MAX:-}" ]; then
  grep -qxF "MemorySwapMax=$DEV_GATES_MEMORY_SWAP_MAX" "$GATES_OUT" \
    || fail "dev-gates.slice missing its explicit DEV_GATES_MEMORY_SWAP_MAX value: $DEV_GATES_MEMORY_SWAP_MAX"
else
  grep -q '^MemorySwapMax=' "$GATES_OUT" \
    && fail "dev-gates.slice rendered MemorySwapMax with the shipped auto-detect value empty"
fi
# ManagedOOMSwap=kill was withdrawn from dev-gates.slice.in (RW-32/D2: an
# oomd swap-usage kill contradicts D-19's "swap is the gates' relief valve,
# MemorySwapMax=32G"). Assert it is ABSENT, the mirror image of the
# presence checks above -- a regression that re-adds it should also go RED.
grep -qx 'ManagedOOMSwap=kill' "$GATES_OUT" \
  && fail "dev-gates.slice has ManagedOOMSwap=kill -- withdrawn by RW-32/D2 (contradicts D-19/D-6, see units/dev-gates.slice.in's own comment)"
pass "dev-gates.slice renders required DEV_GATES_* values and drops auto-detected MemorySwapMax until install time; ManagedOOMSwap=kill stays withdrawn"

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
# Install-DERIVED render variables are not operator keys: an example line would invite a value the
# installer ignores.  IO_SD_DEV (MDT-HS-04) is the whole disk's /dev/<DEVNAME>, computed by install.sh
# from IO_DEV_PATH; the exemption is only valid while install.sh really derives it.
DERIVED_RENDER_VARS="IO_SD_DEV"
grep -qE '^[[:space:]]*IO_SD_DEV=\$\(_mdt_io_systemd_dev ' "$INSTALL_SH" \
  || fail "install.sh no longer derives IO_SD_DEV with _mdt_io_systemd_dev, so the derived-variable exemption in check (6) is invalid"
for v in $(grep -ohE '@[A-Z_][A-Z0-9_]*@' "$HERE"/units/*.in | tr -d '@' | sort -u); do
  case " $DERIVED_RENDER_VARS " in *" $v "*) continue ;; esac
  grep -qE "^${v}=" "$EXAMPLE" || fail "@$v@ has no ${v}= line in host-setup.env.example -- an upgraded host would render it silently dropped/unbounded (B3's failure mode)"
done
pass "every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example"

# --- (7) install.sh installs + applies both cgprofile tmpfiles rules (D-30/M5) --
# round-2 review S20(a): the original assertion was `grep -qF
# 'tmpfiles-cgprofile.conf'`, which would pass just as happily if the only
# remaining mention were a comment -- match the actual `install` line (the
# real installed name, round-2 S15(r2): mdt-cgprofile.conf, mdt- prefixed
# like every other drop-in this host ships) and, for symmetry with
# assertion (5)'s systemctl-start check, the `systemd-tmpfiles --create`
# call too -- excluding comment lines from both so a stale comment alone
# cannot hold this assertion up.
# Materialize the comment-stripped input first. Piping grep into grep -q under
# pipefail lets the successful downstream match close the pipe early, turning
# the upstream grep's SIGPIPE into a false test failure.
INSTALL_NONCOMMENTS="$TMP/install-noncomments.sh"
grep -v '^\s*#' "$INSTALL_SH" > "$INSTALL_NONCOMMENTS"
grep -qE 'install[[:space:]].*units/mdt-cgprofile\.conf.*/etc/tmpfiles\.d/mdt-cgprofile\.conf' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh has no real 'install ... units/mdt-cgprofile.conf ... /etc/tmpfiles.d/mdt-cgprofile.conf' line (D-30/M5) -- a comment mentioning the filename is not enough"
grep -qE 'systemd-tmpfiles[[:space:]]+--create[[:space:]]+/etc/tmpfiles\.d/mdt-cgprofile\.conf' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh never applies the cgprofile tmpfiles.d entry with 'systemd-tmpfiles --create' (D-30/M5)"
grep -qF '|| { echo "ERROR: systemd-tmpfiles --create failed for /etc/tmpfiles.d/mdt-cgprofile.conf"' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh must stop when applying the cgprofile tmpfiles entry fails"
grep -qF 'observer_metadata="$(stat -c '\''%u %g %a'\'' "$observer_directory")"' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh does not inspect the created cgprofile observer directory"
grep -qF '[[ "$observer_metadata" == "0 $observer_gid 751" ]]' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh does not verify exact root/group/mode for the observer directory"
! grep -q 'WARN: systemd-tmpfiles --create failed' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh still treats cgprofile tmpfiles failure as a warning"
grep -qF 'install -D -m 0644 "$HERE/units/mdt-cgprofile.sysusers" "$sysusers_file"' "$INSTALL_NONCOMMENTS" \
  || fail "observer provisioning helper does not install the cgprofile sysusers group entry"
grep -qF 'install -D -m 0644 "$HERE/units/mdt-cgprofile-observer.conf" "$tmpfiles_file"' "$INSTALL_NONCOMMENTS" \
  || fail "observer provisioning helper does not install the observer-only tmpfiles rule"
grep -qF 'systemd-sysusers "$sysusers_file"' "$INSTALL_NONCOMMENTS" \
  || fail "observer provisioning helper does not create the cgprofile group"
grep -qF 'systemd-tmpfiles --create "$tmpfiles_file"' "$INSTALL_NONCOMMENTS" \
  || fail "observer provisioning helper does not apply the observer-only tmpfiles rule"
grep -qF 'd /run/cgprofile/observer 0751 root cgprofile-observer -' "$HERE/units/mdt-cgprofile-observer.conf" \
  || fail "cgprofile tmpfiles entry does not provision the dedicated observer directory"
grep -qF 'g cgprofile-observer - - -' "$HERE/units/mdt-cgprofile.sysusers" \
  || fail "cgprofile sysusers entry does not declare the dedicated observer group"
grep -qF 'provision_cgprofile_observer /' "$INSTALL_NONCOMMENTS" \
  || fail "the full installer does not use the observer provisioning helper"
grep -qF 'if [ "$OBSERVER_ONLY" = 1 ]; then' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh does not dispatch the observer-only workflow"
grep -qF 'ERROR: --observer-only cannot be combined with general host-setup options' "$INSTALL_NONCOMMENTS" \
  || fail "observer-only mode can be combined with general host policy options"
observer_dispatch_line="$(grep -nF 'if [ "$OBSERVER_ONLY" = 1 ]; then' "$INSTALL_NONCOMMENTS" | cut -d: -f1)"
docker_preflight_line="$(grep -nF 'if ! "$DOCKER_BIN" info' "$INSTALL_NONCOMMENTS" | cut -d: -f1)"
[ -n "$observer_dispatch_line" ] && [ -n "$docker_preflight_line" ] \
  && [ "$observer_dispatch_line" -lt "$docker_preflight_line" ] \
  || fail "observer-only mode must complete before the general Docker preflight"
pass "both full and observer-only host workflows install the dedicated cgprofile observer rule"

# Exercise the exact helper body against a throwaway root with mocked systemd
# commands. It must reject an invalid control directory before writing, then
# install only the sysusers and observer tmpfiles files and apply them in order.
OBSERVER_HELPER="$TMP/cgprofile-observer-helper.sh"
sed -n '/^provision_cgprofile_observer() {/,/^}$/p' "$INSTALL_SH" > "$OBSERVER_HELPER"
grep -q '^provision_cgprofile_observer() {' "$OBSERVER_HELPER" \
  || fail "could not extract provision_cgprofile_observer from install.sh"
tail -n1 "$OBSERVER_HELPER" | grep -qx '}' \
  || fail "observer helper extraction did not end at its closing brace"
# The helper is extracted verbatim into this private test directory above.
# shellcheck disable=SC1090
source "$OBSERVER_HELPER"
observer_bad_root="$TMP/observer-missing-control"
mkdir -p "$observer_bad_root/run"
if provision_cgprofile_observer "$observer_bad_root" >/dev/null 2>&1; then
  fail "observer helper accepted a missing control directory"
fi
[ ! -e "$observer_bad_root/etc" ] \
  || fail "observer helper wrote sysusers/tmpfiles files before validating the control directory"

observer_test_root="$TMP/observer-root"
mkdir -p "$observer_test_root/run/cgprofile" "$TMP/mock-bin"
chmod 0770 "$observer_test_root/run/cgprofile"
export observer_test_root
observer_group_gid=1550
observer_group_missing=0
observer_stat_override=""
control_stat_override=""
cat > "$TMP/mock-bin/systemd-sysusers" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "$observer_test_root/etc/sysusers.d/mdt-cgprofile.conf" ] || exit 2
grep -q '^g cgprofile-observer - - -$' "$1" || exit 1
printf 'sysusers\n' >> "$observer_test_root/calls"
EOF
cat > "$TMP/mock-bin/systemd-tmpfiles" <<'EOF'
#!/usr/bin/env bash
[ "$1" = --create ] && [ "$2" = "$observer_test_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf" ] || exit 2
grep -q '^d /run/cgprofile/observer 0751 root cgprofile-observer -$' "$2" || exit 1
mkdir -p "$observer_test_root/run/cgprofile/observer"
chmod 0751 "$observer_test_root/run/cgprofile/observer"
printf 'tmpfiles\n' >> "$observer_test_root/calls"
EOF
chmod 0755 "$TMP/mock-bin/systemd-sysusers" "$TMP/mock-bin/systemd-tmpfiles"
PATH="$TMP/mock-bin:$PATH"
export PATH
getent() {
  case "$*" in
    "group docker") printf 'docker:x:994:\n' ;;
    "group cgprofile-observer")
      [ "$observer_group_missing" = 0 ] || return 2
      printf 'cgprofile-observer:x:%s:\n' "$observer_group_gid" ;;
    *) return 2 ;;
  esac
}
stat() {
  [ "$1" = -c ] && [ "$2" = '%u %g %a' ] || return 2
  case "$3" in
    "$observer_test_root/run/cgprofile")
      printf '%s\n' "${control_stat_override:-0 994 770}" ;;
    "$observer_test_root/run/cgprofile/observer")
      if [ -n "$observer_stat_override" ]; then
        printf '%s\n' "$observer_stat_override"
        return 0
      fi
      [ -d "$3" ] && [ ! -L "$3" ] || return 1
      [ "$(command stat -c '%a' "$3")" = 751 ] || return 1
      printf '0 %s 751\n' "$observer_group_gid" ;;
    *) return 2 ;;
  esac
}
provision_cgprofile_observer "$observer_test_root" \
  || fail "observer helper failed on a valid throwaway host root"
control_socket="$observer_test_root/run/cgprofile/ctl.sock"
socket_ready="$TMP/observer-socket-ready"
python3 - "$control_socket" "$socket_ready" >"$TMP/observer-socket.log" 2>&1 <<'PY' &
import socket, sys
listener = socket.socket(socket.AF_UNIX)
listener.bind(sys.argv[1])
listener.listen(1)
open(sys.argv[2], "w", encoding="utf-8").close()
connection, _ = listener.accept()
connection.recv(4)
connection.sendall(b"pong")
connection.close()
listener.close()
PY
observer_socket_pid=$!
for _ in $(seq 1 100); do
  [ -e "$socket_ready" ] && break
  sleep 0.02
done
[ -S "$control_socket" ] || fail "mock daemon did not bind the control socket"
socket_inode_before="$(command stat -c '%i' "$control_socket")"
cmp "$HERE/units/mdt-cgprofile.sysusers" "$observer_test_root/etc/sysusers.d/mdt-cgprofile.conf" \
  || fail "observer helper installed the wrong sysusers source"
cmp "$HERE/units/mdt-cgprofile-observer.conf" "$observer_test_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf" \
  || fail "observer helper installed the wrong tmpfiles source"
[ "$(cat "$observer_test_root/calls")" = $'sysusers\ntmpfiles' ] \
  || fail "observer helper did not create the group before applying its directory rule"
[ ! -e "$observer_test_root/etc/tmpfiles.d/mdt-cgprofile.conf" ] \
  || fail "observer-only helper rewrote the shared control-socket tmpfiles rule"
provision_cgprofile_observer "$observer_test_root" \
  || fail "observer helper failed on an idempotent second run"
socket_inode_after="$(command stat -c '%i' "$control_socket")"
[ "$socket_inode_before" = "$socket_inode_after" ] \
  || fail "observer provisioning replaced the existing control-socket inode"
python3 - "$control_socket" <<'PY' \
  || fail "the existing control socket did not accept a client after observer provisioning"
import socket, sys
client = socket.socket(socket.AF_UNIX)
client.connect(sys.argv[1])
client.sendall(b"ping")
assert client.recv(4) == b"pong"
client.close()
PY
wait "$observer_socket_pid" \
  || fail "mock control-socket listener failed"
observer_socket_pid=""
pass "observer-only setup is idempotent and preserves the live control socket"

observer_collision_root="$TMP/observer-docker-gid-collision"
mkdir -p "$observer_collision_root/run/cgprofile"
chmod 0770 "$observer_collision_root/run/cgprofile"
observer_test_root="$observer_collision_root"
observer_group_gid=994
if provision_cgprofile_observer "$observer_collision_root" >"$TMP/observer-collision.out" 2>&1; then
  fail "observer helper accepted cgprofile-observer with the Docker GID"
fi
grep -q 'cgprofile-observer GID must differ from the docker group GID' "$TMP/observer-collision.out" \
  || fail "observer helper did not explain the Docker GID collision"
[ ! -e "$observer_collision_root/etc" ] \
  || fail "observer helper wrote unit files before rejecting an existing Docker-GID alias"
[ ! -e "$observer_collision_root/run/cgprofile/observer" ] \
  || fail "observer helper created a Docker-group-accessible directory before rejecting the collision"
[ ! -e "$observer_collision_root/calls" ] \
  || fail "observer helper invoked systemd before rejecting an existing Docker-GID alias"
pass "observer-only setup refuses an observer GID that grants Docker-group access"

observer_group_gid=1550
observer_bad_control="$TMP/observer-bad-control"
mkdir -p "$observer_bad_control/run/cgprofile"
chmod 0770 "$observer_bad_control/run/cgprofile"
observer_test_root="$observer_bad_control"
control_stat_override="0 994 700"
if provision_cgprofile_observer "$observer_bad_control" >"$TMP/observer-control-drift.out" 2>&1; then
  fail "observer helper accepted a drifted control directory mode"
fi
grep -q 'must be root:docker 0770; found 0 994 700' "$TMP/observer-control-drift.out" \
  || fail "observer helper did not report the control-directory mode it found"
[ ! -e "$observer_bad_control/etc" ] \
  || fail "observer helper wrote unit files before refusing control-directory drift"
control_stat_override=""

observer_bad_owner="$TMP/observer-bad-owner"
mkdir -p "$observer_bad_owner/run/cgprofile"
chmod 0770 "$observer_bad_owner/run/cgprofile"
observer_test_root="$observer_bad_owner"
control_stat_override="1003 994 770"
if provision_cgprofile_observer "$observer_bad_owner" >"$TMP/observer-control-owner.out" 2>&1; then
  fail "observer helper accepted a control directory with the wrong owner"
fi
grep -q 'must be root:docker 0770; found 1003 994 770' "$TMP/observer-control-owner.out" \
  || fail "observer helper did not report the control-directory owner it found"
[ ! -e "$observer_bad_owner/etc" ] \
  || fail "observer helper wrote unit files before refusing control-directory owner drift"
control_stat_override=""

refuse_observer_path() {
  local test_root="$1" expected="$2" output="$3"
  observer_test_root="$test_root"
  observer_stat_override=""
  control_stat_override=""
  if provision_cgprofile_observer "$test_root" >"$output" 2>&1; then
    fail "observer helper accepted an invalid pre-existing observer path: $expected"
  fi
  grep -qF "$expected" "$output" \
    || fail "observer helper did not explain the refusal for $expected"
  [ ! -e "$test_root/etc" ] \
    || fail "observer helper wrote unit files before refusing $expected"
  [ ! -e "$test_root/calls" ] \
    || fail "observer helper invoked systemd before refusing $expected"
}

observer_bad_file="$TMP/observer-file-path"
mkdir -p "$observer_bad_file/run/cgprofile"
chmod 0770 "$observer_bad_file/run/cgprofile"
printf 'not a directory\n' > "$observer_bad_file/run/cgprofile/observer"
refuse_observer_path "$observer_bad_file" \
  "refusing non-directory at $observer_bad_file/run/cgprofile/observer" \
  "$TMP/observer-file-path.out"

observer_bad_symlink="$TMP/observer-symlink-path"
mkdir -p "$observer_bad_symlink/run/cgprofile"
chmod 0770 "$observer_bad_symlink/run/cgprofile"
ln -s "$TMP/observer-target" "$observer_bad_symlink/run/cgprofile/observer"
refuse_observer_path "$observer_bad_symlink" \
  "refusing symlink at $observer_bad_symlink/run/cgprofile/observer" \
  "$TMP/observer-symlink-path.out"

observer_bad_directory="$TMP/observer-drifted-directory"
mkdir -p "$observer_bad_directory/run/cgprofile/observer"
chmod 0770 "$observer_bad_directory/run/cgprofile"
chmod 0700 "$observer_bad_directory/run/cgprofile/observer"
observer_test_root="$observer_bad_directory"
observer_stat_override="0 1550 700"
if provision_cgprofile_observer "$observer_bad_directory" >"$TMP/observer-directory-drift.out" 2>&1; then
  fail "observer helper silently re-owned or re-moded an existing observer directory"
fi
grep -qF "refusing drifted $observer_bad_directory/run/cgprofile/observer: found 0 1550 700; expected 0 1550 751" "$TMP/observer-directory-drift.out" \
  || fail "observer helper did not report the pre-existing observer-directory metadata"
[ ! -e "$observer_bad_directory/etc" ] \
  || fail "observer helper wrote unit files before refusing observer-directory drift"
observer_stat_override=""

observer_without_group="$TMP/observer-directory-without-group"
mkdir -p "$observer_without_group/run/cgprofile/observer"
chmod 0770 "$observer_without_group/run/cgprofile"
observer_test_root="$observer_without_group"
observer_group_missing=1
if provision_cgprofile_observer "$observer_without_group" >"$TMP/observer-directory-without-group.out" 2>&1; then
  fail "observer helper accepted a pre-existing directory with no observer group identity"
fi
grep -qF "refusing pre-existing $observer_without_group/run/cgprofile/observer because cgprofile-observer does not exist" "$TMP/observer-directory-without-group.out" \
  || fail "observer helper did not explain missing group identity for a pre-existing path"
[ ! -e "$observer_without_group/etc" ] \
  || fail "observer helper wrote unit files before refusing a path with no group"
observer_group_missing=0
pass "observer-only provisioning validates its parent and changes only the group and observer directory"

CHECK_SH="$HERE/scripts/check.sh"
CHECK_OBSERVER_HELPER="$TMP/check-observer-helper.sh"
sed -n '/^check_cgprofile_observer() {/,/^}$/p' "$CHECK_SH" > "$CHECK_OBSERVER_HELPER"
grep -q '^check_cgprofile_observer() {' "$CHECK_OBSERVER_HELPER" \
  || fail "could not extract the observer host-check implementation"
tail -n1 "$CHECK_OBSERVER_HELPER" | grep -qx '}' \
  || fail "observer host-check extraction did not end at its closing brace"

observer_check_root="$TMP/observer-health-check"
mkdir -p "$observer_check_root/run/cgprofile/observer" \
  "$observer_check_root/etc/tmpfiles.d" "$observer_check_root/etc/sysusers.d" \
  "$observer_check_root/bin"
chmod 0751 "$observer_check_root/run/cgprofile/observer"
cp "$HERE/units/mdt-cgprofile-observer.conf" \
  "$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf"
cp "$HERE/units/mdt-cgprofile.sysusers" \
  "$observer_check_root/etc/sysusers.d/mdt-cgprofile.conf"
cat > "$observer_check_root/bin/systemd-tmpfiles" <<'EOF'
#!/bin/sh
if [ "$1" != --cat-config ] || [ "$2" != --no-pager ]; then
  exit 64
fi
for file in "$CGPROFILE_TEST_TMPFILES_DIR"/*.conf; do
  [ -f "$file" ] || continue
  cat "$file"
done
EOF
cat > "$observer_check_root/bin/systemd-sysusers" <<'EOF'
#!/bin/sh
if [ "$1" != --cat-config ] || [ "$2" != --no-pager ]; then
  exit 64
fi
for file in "$CGPROFILE_TEST_SYSUSERS_DIR"/*.conf; do
  [ -f "$file" ] || continue
  cat "$file"
done
EOF
chmod 0755 "$observer_check_root/bin/systemd-tmpfiles" \
  "$observer_check_root/bin/systemd-sysusers"
observer_test_root="$observer_check_root"
observer_group_gid=1550
observer_stat_override=""
check_health_run() {
  local expected_failures="$1" output="$2"
  (
    check_failures=0
    check_output="$output"
    ok() { :; }
    info() { :; }
    fail() {
      printf 'FAIL %s\n' "$*" >> "$check_output"
      check_failures=$((check_failures + 1))
    }
    # shellcheck disable=SC1090
    . "$CHECK_OBSERVER_HELPER"
    export CGPROFILE_DIR="$observer_check_root/run/cgprofile"
    export CGPROFILE_OBSERVER_TMPFILES_FILE="$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf"
    export CGPROFILE_OBSERVER_SYSUSERS_FILE="$observer_check_root/etc/sysusers.d/mdt-cgprofile.conf"
    export CGPROFILE_TEST_TMPFILES_DIR="$observer_check_root/etc/tmpfiles.d"
    export CGPROFILE_TEST_SYSUSERS_DIR="$observer_check_root/etc/sysusers.d"
    export PATH="$observer_check_root/bin:$PATH"
    check_cgprofile_observer
    [ "$check_failures" = "$expected_failures" ]
  )
}
: > "$TMP/observer-health-ok.out"
check_health_run 0 "$TMP/observer-health-ok.out" \
  || fail "host-check rejected a valid observer installation"

observer_stat_override="0 1550 700"
: > "$TMP/observer-health-mode.out"
check_health_run 1 "$TMP/observer-health-mode.out" \
  || fail "host-check did not reject an observer directory mode mismatch"
grep -q 'expected 0 1550 751' "$TMP/observer-health-mode.out" \
  || fail "host-check did not report the observer directory's expected metadata"
observer_stat_override=""

mv "$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf" \
  "$observer_check_root/etc/tmpfiles.d/observer-rule.missing"
: > "$TMP/observer-health-rule.out"
check_health_run 1 "$TMP/observer-health-rule.out" \
  || fail "host-check accepted a missing observer tmpfiles rule"
grep -q 'observer-only' "$TMP/observer-health-rule.out" \
  || fail "host-check did not prescribe the observer-only provisioning workflow"
mv "$observer_check_root/etc/tmpfiles.d/observer-rule.missing" \
  "$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf"

printf '%s\n' 'd /run/cgprofile 0770 root docker -' \
  >> "$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf"
: > "$TMP/observer-health-extra-tmpfiles.out"
check_health_run 1 "$TMP/observer-health-extra-tmpfiles.out" \
  || fail "host-check accepted an extra active tmpfiles directive"
grep -q 'exactly the required observer tmpfiles rule' "$TMP/observer-health-extra-tmpfiles.out" \
  || fail "host-check did not explain rejection of extra tmpfiles directives"
cp "$HERE/units/mdt-cgprofile-observer.conf" \
  "$observer_check_root/etc/tmpfiles.d/mdt-cgprofile-observer.conf"

printf '%s\n' 'd /run/cgprofile/observer 0700 root root -' \
  > "$observer_check_root/etc/tmpfiles.d/competing-observer.conf"
: > "$TMP/observer-health-competing-tmpfiles.out"
check_health_run 1 "$TMP/observer-health-competing-tmpfiles.out" \
  || fail "host-check accepted a competing tmpfiles rule from another active file"
grep -q 'effective tmpfiles rules' "$TMP/observer-health-competing-tmpfiles.out" \
  || fail "host-check did not name a competing effective tmpfiles rule"
rm "$observer_check_root/etc/tmpfiles.d/competing-observer.conf"

printf '%s\n' 'u unexpected-user - "Unexpected user" /nonexistent' \
  >> "$observer_check_root/etc/sysusers.d/mdt-cgprofile.conf"
: > "$TMP/observer-health-extra-sysusers.out"
check_health_run 1 "$TMP/observer-health-extra-sysusers.out" \
  || fail "host-check accepted an extra active sysusers directive"
grep -q 'exactly the required observer group rule' "$TMP/observer-health-extra-sysusers.out" \
  || fail "host-check did not explain rejection of extra sysusers directives"
cp "$HERE/units/mdt-cgprofile.sysusers" \
  "$observer_check_root/etc/sysusers.d/mdt-cgprofile.conf"

printf '%s\n' 'g cgprofile-observer 2000 - -' \
  > "$observer_check_root/etc/sysusers.d/competing-observer.conf"
: > "$TMP/observer-health-competing-sysusers.out"
check_health_run 1 "$TMP/observer-health-competing-sysusers.out" \
  || fail "host-check accepted a competing sysusers rule from another active file"
grep -q 'effective sysusers rules' "$TMP/observer-health-competing-sysusers.out" \
  || fail "host-check did not name a competing effective sysusers rule"
rm "$observer_check_root/etc/sysusers.d/competing-observer.conf"

observer_group_gid=994
: > "$TMP/observer-health-gid.out"
check_health_run 1 "$TMP/observer-health-gid.out" \
  || fail "host-check accepted an observer GID shared with Docker"
grep -q 'aliases docker' "$TMP/observer-health-gid.out" \
  || fail "host-check did not explain the observer/Docker GID collision"
observer_group_gid=1550
pass "host-check validates observer ownership, group isolation, and installed rule declarations"

# The optional DAMON_STAT policy is applied only from the operator's explicit
# config choice and uses the documented kernel module parameter.
grep -q '^DAMON_STAT_DISABLE_DEFAULT="yes"$' "$EXAMPLE" \
  || fail "host-setup.env.example must default to disabling DAMON_STAT (operator decision 2026-10-20)"
grep -qF 'install -m 0644 "$HERE/units/mdt-damon-stat.conf" "$DAMON_STAT_TMPFILES"' "$HERE/scripts/mdt-container-io-caps.lib.sh" \
  || fail "install.sh does not install the DAMON_STAT tmpfiles policy"
grep -qF 'systemd-tmpfiles --create "$DAMON_STAT_TMPFILES"' "$HERE/scripts/mdt-container-io-caps.lib.sh" \
  || fail "install.sh does not apply the selected DAMON_STAT policy"
grep -qF 'case "${DAMON_STAT_DISABLE_DEFAULT:-}" in' "$HERE/scripts/check.sh" \
  || fail "check.sh invents a DAMON_STAT policy when the configured value is absent"
grep -qF 'DAMON_STAT_DISABLE_DEFAULT must be yes or no' "$HERE/scripts/check.sh" \
  || fail "check.sh accepts an unknown DAMON_STAT policy as the kernel default"
grep -qF 'DAMON_STAT policy is missing from $CONF' "$HERE/scripts/check.sh" \
  || fail "check.sh certifies an absent DAMON_STAT policy"
grep -qFx 'w /sys/module/damon_stat/parameters/enabled - - - - N' "$HERE/units/mdt-damon-stat.conf" \
  || fail "DAMON_STAT tmpfiles entry does not disable the kernel parameter"
pass "DAMON_STAT is explicit and persistent, and health checks reject missing or unknown policy"

# --- (8) the wizard's earmark-sum doctest actually runs (round-1 review S2) --
# propose_memory_min_guaranteed_suggestion()'s own doctest demonstrates the
# leftover/suggested-ceiling BEFORE vs AFTER dev-gates.slice's MemoryHigh
# enters the earmark sum -- run it for real, not just py_compile the file.
# round-2 review S20(b): `python3 -m doctest FILE` exits 0 on a file with
# ZERO doctests too -- a deleted doctest would not go red. Run with -v and
# require a non-zero "N passed" count, so an empty doctest run itself fails
# this assertion.
DOCTEST_OUT="$($MDT_TEST_PYTHON -m doctest -v "$HERE/mdt-host-setup-wizard.py" 2>&1)" \
  || fail "mdt-host-setup-wizard.py doctest failed (propose_memory_min_guaranteed_suggestion's before/after earmark-sum demonstration, S2):
$DOCTEST_OUT"
printf '%s\n' "$DOCTEST_OUT" | grep -qE '^[1-9][0-9]* passed' \
  || fail "mdt-host-setup-wizard.py doctest reported 0 tests (would pass vacuously on a deleted doctest, S20(b)):
$DOCTEST_OUT"
pass "wizard's earmark-sum doctest (before/after dev-gates.slice's MemoryHigh) passes, and actually ran a non-zero test count"

# --- (9) check.sh's _bytes_of parses legal-systemd non-integer sizes (round-2 review B7) --
# B7: the original _bytes_of used bash integer arithmetic, which is a
# SYNTAX ERROR on any legal-systemd non-integer mantissa ("4.5G" -- and
# mdt's own wizard, kib_to_size_str(), rounds to the nearest half-GiB and
# emits exactly this form routinely), and silently mis-handled a
# percentage ("50%", also legal systemd syntax) by comparing it verbatim
# against a byte count -- both cases hard-FAILed a CORRECTLY configured
# host. Extracts _bytes_of out of check.sh verbatim (same guarded-sed
# extraction style as render()/RENDER_VARS above, anchors included) so
# this test can never silently drift from what check.sh actually runs.
CHECK_SH="$HERE/scripts/check.sh"
BYTES_SNIPPET="$TMP/bytes-of-snippet.sh"
sed -n '/^_bytes_of() {/,/^}$/p' "$CHECK_SH" > "$BYTES_SNIPPET"
grep -q '^_bytes_of() {' "$BYTES_SNIPPET" \
  || fail "could not extract _bytes_of from check.sh -- anchor pattern is stale, update this test"
tail -n1 "$BYTES_SNIPPET" | grep -qx '}' \
  || fail "_bytes_of() end anchor no longer matches check.sh -- extraction over-ran, update this test's anchor patterns before trusting anything below"
if grep -nE '^(systemctl|apt-get|install |mkdir |modprobe|udevadm|python3 )|/etc/systemd/system|/etc/docker' "$BYTES_SNIPPET"; then
  fail "extracted _bytes_of snippet contains a host-mutating statement (see above) -- refusing to source it"
fi
(
  # shellcheck disable=SC1090
  . "$BYTES_SNIPPET"
  check() { # check <input> <expected>
    got="$(_bytes_of "$1")"
    [ "$got" = "$2" ] || { echo "FAIL: _bytes_of '$1' -> '$got', want '$2'" >&2; exit 1; }
  }
  check "6G" "6442450944"                 # sanity: existing integer form unaffected
  check "4.5G" "4831838208"                # B7's own regression case: the exact
                                            # kernel byte value the round-2 review
                                            # cited for a CORRECTLY configured host
  check "50%" "?"                          # B7: not byte-comparable -> "?", never
                                            # a bash syntax error, never compared
                                            # verbatim against a byte count
  check "bogus" "?"                        # confirmed real behaviour, round 3: an
                                            # unparseable string takes the SAME "?"
                                            # path as a percentage (the awk regex
                                            # cannot distinguish "valid-but-not-a-
                                            # size" from "garbage", and B7's own
                                            # prescription does not ask it to) --
                                            # so this warns, it does not fail; see
                                            # the LOG for why this differs from a
                                            # literal reading of "bogus -> FAIL"
  check "max" "max"
  check "" ""
) || fail "_bytes_of regression case failed (B7) -- see stderr above"
pass "check.sh's _bytes_of parses legal-systemd non-integer sizes (4.5G) to the correct byte count and returns ? (never a bash syntax error, never a silent mis-compare) for non-byte-comparable forms (50%, bogus)"

# --- mdt-buildkitd.service must bound a cold pull in its start job -----------
# ExecStartPre pulls the image and ExecStartPost waits for the socket inside one start
# job; systemd's 90 s default timed out in the TCG guest (vm-system, 2026-10-19).
grep -qE '^TimeoutStartSec=10min$' "$HERE/units/mdt-buildkitd.service.in" \
  || fail "units/mdt-buildkitd.service.in lost TimeoutStartSec=10min (a cold image pull exceeds the 90 s default)"
pass "mdt-buildkitd.service carries TimeoutStartSec=10min"

# --- focused BuildKit/config/wizard identity tests --------------------------
GOVERNANCE_TEST_OUT="$TMP/buildkit-governance-tests.log"
if "$MDT_TEST_PYTHON" "$HERE/tests/test_buildkit_governance.py" >"$GOVERNANCE_TEST_OUT" 2>&1; then
  GOV_RC=0
else
  GOV_RC=$?
fi
if [ "$GOV_RC" -ne 0 ]; then
  tail -n 240 "$GOVERNANCE_TEST_OUT"
  fail "focused BuildKit governance tests failed"
fi
echo "GOVERNANCE_TEST_EXIT=$GOV_RC"
pass "focused BuildKit guard identity, policy, builder idempotency, memory constraints, and template invariants"

# --- bash -n + shellcheck (if available) on every shell script this package touches --
SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-dev-governance-reconcile.sh" "$HERE/scripts/mdt-container-io-events-watcher.sh" "$HERE/scripts/check.sh" "$HERE/scripts/mdt-container-io-caps.lib.sh" "$HERE/scripts/mdt-cgroup-io-limits.sh" "$HERE/tests/vm-governance-acceptance.sh" "$HERE/tests/vm-upgrade-acceptance.sh" "$HERE/tests/vm-install-with-diagnostics.sh" "$HERE/tests/vm-dump-diagnostics.sh" "$HERE/scripts/mdt-container-memory-inotify-watcher.py" "$HERE/scripts/mdt-slice-memory-min-low-audit.py" "$HERE/scripts/mdt-buildkit-guard.py" "$HERE/../scripts/mdt_buildkit_builder.py" "$HERE/mdt-host-setup-wizard.py" "${BASH_SOURCE[0]}")
for s in "${SCRIPTS[@]}"; do
  case "$s" in
    *.py) "$MDT_TEST_PYTHON" -m py_compile "$s" || fail "py_compile failed: $s" ;;
    *)    bash -n "$s" || fail "bash -n failed: $s" ;;
  esac
done
pass "bash -n / py_compile clean on every touched script"

SHELL_SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-dev-governance-reconcile.sh" "$HERE/scripts/mdt-container-io-events-watcher.sh" "$HERE/scripts/check.sh" "$HERE/scripts/mdt-container-io-caps.lib.sh" "$HERE/scripts/mdt-cgroup-io-limits.sh" "$HERE/tests/vm-governance-acceptance.sh" "$HERE/tests/vm-upgrade-acceptance.sh" "$HERE/tests/vm-install-with-diagnostics.sh" "$HERE/tests/vm-dump-diagnostics.sh" "${BASH_SOURCE[0]}")
if command -v shellcheck >/dev/null 2>&1; then
  # -S warning: this package leaves a handful of PRE-EXISTING info/style
  # findings untouched (SC2015 "A && B || C", SC2181 "check $? directly") --
  # verified none are on a line this package's diff touches, and fixing
  # decades of pre-existing style debt across install.sh/check.sh/
  # mdt-dev-governance-reconcile.sh is out of this package's scope. warning severity
  # and above is a real correctness bar; style/info is not enforced here.
  shellcheck -S warning "${SHELL_SCRIPTS[@]}" || fail "shellcheck (warning severity) reported an issue -- see above"
  pass "shellcheck (warning severity+) clean on every touched shell script"
else
  echo "shellcheck not available -- skipped (bash -n above still ran)"
fi

echo "test-render: ALL OK"
