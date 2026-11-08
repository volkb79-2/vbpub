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
#   7. retired cgprofile/topos provisioning and old BuildKit environment
#      writers stay out of install.sh, while endpoint publication/check wiring
#      remains present;
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
trap 'rm -rf "$TMP"' EXIT
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
# Templates owned by mdt_tree_cache.py (dev-cache.slice, tree mounts, GC units): install.sh's render()
# does not know their keys; the live install renders them with mdt_tree_cache.py, so this test does too
# (the same code path, below) instead of pushing them through the bash renderer.
OWNED_TEMPLATES="$("$MDT_TEST_PYTHON" "$HERE/mdt_tree_cache.py" templates)" \
  || fail "mdt_tree_cache.py templates failed"
[ -n "$OWNED_TEMPLATES" ] || fail "mdt_tree_cache.py reports no owned templates"
rendered_any=0
for tmpl in "$HERE"/units/*.in; do
  [ -e "$tmpl" ] || continue
  if printf '%s\n' "$OWNED_TEMPLATES" | grep -qxF "$(basename "$tmpl")"; then
    continue
  fi
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

# The tree-cache-owned templates, rendered by the exact code the installer runs
# (mdt_tree_cache.py render), from the shipped example, as dedicated-dev. No host
# mutation: scratch systemd dir and capacity json under $TMP.
TC_SYSD="$TMP/tree-cache-systemd"; mkdir -p "$TC_SYSD"
"$MDT_TEST_PYTHON" "$HERE/mdt_tree_cache.py" render --config "$EXAMPLE" --role dedicated-dev --gid 1 \
  --units-dir "$HERE/units" --systemd-dir "$TC_SYSD" --capacity-json "$TMP/tree-cache-capacity.json" >/dev/null \
  || fail "mdt_tree_cache.py render failed on the shipped example"
for tmpl in $OWNED_TEMPLATES; do
  case "$tmpl" in
    mdt-tree-area.mount.in) ;;  # one unit per tree root, name derived from the path
    *) [ -f "$TC_SYSD/${tmpl%.in}" ] || fail "mdt_tree_cache.py did not render ${tmpl%.in}" ;;
  esac
done
[ -n "$(ls "$TC_SYSD")" ] || fail "mdt_tree_cache.py rendered nothing"
if grep -HE '@[A-Za-z_][A-Za-z0-9_]*@' "$TC_SYSD"/*; then
  fail "a tree-cache unit rendered with an unresolved placeholder (see above)"
fi
grep -qxF 'MemoryZSwapWriteback=yes' "$TC_SYSD/dev-cache.slice" \
  || fail "dev-cache.slice did not render DEV_CACHE_ZSWAP_WRITEBACK=yes"
# DEV_CACHE_MEMORY_LOW ships empty: no MemoryLow line at all (not an empty or literal one)
! grep -q '^MemoryLow=' "$TC_SYSD/dev-cache.slice" \
  || fail "dev-cache.slice renders MemoryLow= although DEV_CACHE_MEMORY_LOW ships empty"
pass "tree-cache-owned units render via mdt_tree_cache.py from the shipped example, no unresolved placeholders"

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

# The Docker API socket is a child of a directory bind-mounted by devcontainers.
# Socket restarts may replace that child, but must not replace its parent inode.
DOCKER_API_SOCKET_UNIT="$HERE/units/docker-api-socket.conf"
grep -qxF 'ExecStartPre=/bin/mkdir -p /run/docker-api' "$DOCKER_API_SOCKET_UNIT" \
  || fail "Docker API socket unit does not idempotently ensure its directory"
grep -qxF 'ExecStartPre=/bin/chown root:docker /run/docker-api' "$DOCKER_API_SOCKET_UNIT" \
  || fail "Docker API socket unit does not repair directory ownership in place"
grep -qxF 'ExecStartPre=/bin/chmod 0750 /run/docker-api' "$DOCKER_API_SOCKET_UNIT" \
  || fail "Docker API socket unit does not repair directory mode in place"
DOCKER_API_DIRECTORY_REMOVAL='(^|[=;/[:space:]'"'"'"])(rm|rmdir|mv)([[:space:]]+-[^[:space:]]+)*[[:space:]]+['"'"'"]?/run/docker-api/?['"'"'"]?([[:space:];'"'"'"]|$)'
if grep -v '^[[:space:]]*#' "$DOCKER_API_SOCKET_UNIT" | grep -Eq "$DOCKER_API_DIRECTORY_REMOVAL"; then
  fail "Docker API socket unit removes or replaces the directory that devcontainers bind-mount"
fi
grep -qF 'umount -l /run/docker-api/docker.sock' "$DOCKER_API_SOCKET_UNIT" \
  || fail "Docker API socket unit does not confine stale-mount cleanup to the socket entry"
grep -qF '"source=/run/docker-api,target=/run/docker-api,type=bind"' "$HERE/../templates/devcontainer.json" \
  || fail "MDT devcontainer no longer bind-mounts the stable Docker API directory"
pass "Docker API socket restart preserves the directory bind mount and replaces only its socket entry"

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
INSTALL_NONCOMMENTS_EARLY="$TMP/install-noncomments-early.sh"
grep -v '^\s*#' "$INSTALL_SH" > "$INSTALL_NONCOMMENTS_EARLY"
JOINED_INSTALL="$(sed ':a;N;$!ba;s/\\\n/ /g' "$INSTALL_SH")"   # collapse `\`-continued
                                                                # lines to one logical
                                                                # line each, so a name on
                                                                # a continuation (e.g. the
                                                                # systemctl start list)
                                                                # is still found
for tmpl in "$HERE"/units/*.in; do
  base="$(basename "${tmpl%.in}")"
  if printf '%s\n' "$OWNED_TEMPLATES" | grep -qxF "$(basename "$tmpl")"; then
    # rendered and started by mdt_tree_cache.py (render, then activate after daemon-reload)
    grep -qE 'python3 "\$HERE/mdt_tree_cache\.py" render .*--units-dir "\$HERE/units"' "$INSTALL_NONCOMMENTS_EARLY" \
      || fail "install.sh never runs mdt_tree_cache.py render for $base"
    grep -qE 'python3 "\$HERE/mdt_tree_cache\.py" activate' "$INSTALL_NONCOMMENTS_EARLY" \
      || fail "install.sh never runs mdt_tree_cache.py activate for $base"
    continue
  fi
  grep -qF "units/$(basename "$tmpl")" "$INSTALL_SH" \
    || fail "install.sh never renders units/$(basename "$tmpl")"
  case "$base" in
    *.slice)
      grep -qE "systemctl start\b.*\b${base//./\\.}\b" <<<"$JOINED_INSTALL" \
        || fail "install.sh never starts $base (not in any systemctl start ... line, incl. continuations)" ;;
  esac
done
pass "install.sh renders every units/*.in, and starts every *.slice"

# --- (6) every @VAR@ placeholder used in any template has an example line --
# Install-DERIVED render variables are not operator keys: an example line would invite a value the
# installer ignores.  IO_SD_DEV (MDT-HS-04) is the whole disk's /dev/<DEVNAME>, computed by install.sh
# from IO_DEV_PATH; the exemption is only valid while install.sh really derives it.
DERIVED_RENDER_VARS="IO_SD_DEV"
grep -qE 'IO_SD_DEV=\$\(_mdt_io_systemd_dev ' "$INSTALL_SH" \
  || fail "install.sh no longer derives IO_SD_DEV with _mdt_io_systemd_dev, so the derived-variable exemption in check (6) is invalid"
# The tree-cache-owned templates use python-derived tokens (TREE_*, resolved from the plan) next to a few
# env keys; mdt_tree_cache.py _render() refuses any token it was not given, and the render above proved
# the example drives all of them. Here: the env keys it consumes must be in the example too.
for v in DEV_CACHE_MEMORY_LOW DEV_CACHE_ZSWAP_WRITEBACK MDT_TREE_CACHE_GC_INTERVAL; do
  grep -qE "^#? ?${v}=" "$EXAMPLE" || fail "${v}= is not documented in host-setup.env.example"
done
NONOWNED_TEMPLATES=()
for tmpl in "$HERE"/units/*.in; do
  printf '%s\n' "$OWNED_TEMPLATES" | grep -qxF "$(basename "$tmpl")" || NONOWNED_TEMPLATES+=("$tmpl")
done
for v in $(grep -ohE '@[A-Z_][A-Z0-9_]*@' "${NONOWNED_TEMPLATES[@]}" | tr -d '@' | sort -u); do
  case " $DERIVED_RENDER_VARS " in *" $v "*) continue ;; esac
  grep -qE "^${v}=" "$EXAMPLE" || fail "@$v@ has no ${v}= line in host-setup.env.example -- an upgraded host would render it silently dropped/unbounded (B3's failure mode)"
done
pass "every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example"

# --- (7) retired host provisioning is gone; endpoint ownership is explicit --
INSTALL_NONCOMMENTS="$TMP/install-noncomments.sh"
grep -v '^\s*#' "$INSTALL_SH" > "$INSTALL_NONCOMMENTS"
! grep -Eq -- '--observer-only|--cgprofile-only|provision_cgprofile_observer|mdt-cgprofile-install\.sh' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh retains retired cgprofile provisioning or options"
! grep -Eq 'systemd-tmpfiles.*mdt-(cgprofile|topos)|mdt-topos-service|/etc/environment.mdt-buildkit' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh retains retired cgprofile/topos or environment-block installation"
grep -qF 'publish_host_setup_endpoints "$HERE" publish /' "$INSTALL_NONCOMMENTS" \
  || fail "install.sh does not publish the mdt-host-setup endpoint fragment"
grep -qF 'check_host_setup_endpoint_record' "$HERE/scripts/check.sh" \
  || fail "check.sh has no mdt-host-setup endpoint-record line"
grep -qF 'endpoints check --json' "$HERE/scripts/check.sh" \
  || fail "check.sh does not use augeaswrap's endpoint check"
pass "retired cgprofile/topos provisioning and environment writers are removed; endpoint publish/check wiring remains"

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
SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-host-endpoints.sh" "$HERE/scripts/mdt-dev-governance-reconcile.sh" "$HERE/scripts/mdt-container-io-events-watcher.sh" "$HERE/scripts/check.sh" "$HERE/scripts/docker-buildx-shim.sh" "$HERE/scripts/mdt-container-io-caps.lib.sh" "$HERE/scripts/mdt-cgroup-io-limits.sh" "$HERE/tests/vm-governance-acceptance.sh" "$HERE/tests/vm-writeback-acceptance.sh" "$HERE/tests/vm-iocost-acceptance.sh" "$HERE/tests/vm-upgrade-acceptance.sh" "$HERE/tests/vm-install-with-diagnostics.sh" "$HERE/tests/vm-dump-diagnostics.sh" "$HERE/scripts/mdt-container-memory-inotify-watcher.py" "$HERE/scripts/mdt-slice-memory-min-low-audit.py" "$HERE/scripts/mdt-buildkit-guard.py" "$HERE/../scripts/mdt_buildkit_builder.py" "$HERE/mdt-host-setup-wizard.py" "${BASH_SOURCE[0]}")
for s in "${SCRIPTS[@]}"; do
  case "$s" in
    *.py) "$MDT_TEST_PYTHON" -m py_compile "$s" || fail "py_compile failed: $s" ;;
    *)    bash -n "$s" || fail "bash -n failed: $s" ;;
  esac
done
pass "bash -n / py_compile clean on every touched script"

SHELL_SCRIPTS=("$INSTALL_SH" "$HERE/scripts/mdt-host-endpoints.sh" "$HERE/scripts/mdt-dev-governance-reconcile.sh" "$HERE/scripts/mdt-container-io-events-watcher.sh" "$HERE/scripts/check.sh" "$HERE/scripts/docker-buildx-shim.sh" "$HERE/scripts/mdt-container-io-caps.lib.sh" "$HERE/scripts/mdt-cgroup-io-limits.sh" "$HERE/tests/vm-governance-acceptance.sh" "$HERE/tests/vm-writeback-acceptance.sh" "$HERE/tests/vm-iocost-acceptance.sh" "$HERE/tests/vm-upgrade-acceptance.sh" "$HERE/tests/vm-install-with-diagnostics.sh" "$HERE/tests/vm-dump-diagnostics.sh" "${BASH_SOURCE[0]}")
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
