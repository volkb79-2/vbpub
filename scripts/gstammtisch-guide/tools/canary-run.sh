#!/usr/bin/env bash
# canary-run.sh — prove the gate REJECTS known-bad code.
#
# A suite that is green on correct code proves nothing on its own; it also
# has to go red on incorrect code. Each canary below breaks exactly one
# contract some test claims to assert, and that named test must fail. A
# canary that survives is a hollow oracle, and this script fails when one
# does. Pattern matches scripts/cgroup-profiler/tools/canary-run.sh.
#
#   tools/canary-run.sh            run every canary
#   tools/canary-run.sh <name>     run one
#
# Each canary runs against a disposable COPY of the tree, so a crash
# mid-run can never leave a sabotaged source file behind.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
python_bin="${GSTAMMTISCH_PYTHON:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || { echo "canary: no usable python at $python_bin" >&2; exit 2; }

only="${1:-}"
pass=0 fail=0

# name | file | python-replace-expr | test selector that MUST go red
canary() {
  local name="$1" file="$2" find="$3" replace="$4" selector="$5"
  [ -n "$only" ] && [ "$only" != "$name" ] && return 0

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" RETURN

  tar -C "$project_dir" \
      --exclude=__pycache__ --exclude=.pytest_cache \
      -cf - . | tar -C "$work" -xf -

  if ! "$python_bin" - "$work/$file" "$find" "$replace" <<'PY'
import sys, pathlib
path, find, replace = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text()
if find not in text:
    sys.exit(f"canary target not found in {path.name}: {find!r}")
path.write_text(text.replace(find, replace, 1))
PY
  then
    printf '  %-34s BROKEN CANARY (target text not found — the code moved)\n' "$name"
    fail=$((fail + 1))
    return 0
  fi

  local out rc
  set +e
  out="$(cd "$work" && PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m pytest "$selector" \
          -q -x -p no:cacheprovider --basetemp="$work/.pt" 2>&1)"
  rc=$?
  set -e

  if [ "$rc" -ne 0 ]; then
    printf '  %-34s ok (gate rejected it)\n' "$name"
    pass=$((pass + 1))
  else
    printf '  %-34s SURVIVED — %s does not actually assert this\n' "$name" "$selector"
    printf '%s\n' "$out" | tail -5 | sed 's/^/      /'
    fail=$((fail + 1))
  fi
}

echo "gstammtisch-guide gate canaries"

# 1. RCON's req_id is signed on the wire (the auth-failure sentinel is -1)
#    — this is soulmask_rcon.py itself, the reference implementation
#    damon-analysis's rcon_probe.py was fixed to match this session.
#    Unsigned unpacking would make -1 decode as 4294967295.
canary rcon-req-id-unsigned files/usr/local/sbin/soulmask_rcon.py \
  'req_id, pkt_type = struct.unpack("<ii", data[:8])' \
  'req_id, pkt_type = struct.unpack("<II", data[:8])' \
  tests/test_soulmask_rcon.py::test_one_shot_auth_failure_is_reported

# 2. A container restart under an unchanged monitor process must reset the
#    stale RCON connection to the OLD pid's netns — disabling the pid-
#    change check would silently keep polling FPS through a dead netns.
canary poll-fps-ignores-pid-change files/usr/local/sbin/soulmask-monitor.py \
  '        if pid != self.pid:' \
  '        if False:' \
  tests/test_soulmask_monitor.py::test_poll_fps_pid_change_kills_old_process_and_respawns

# 3. A real docker failure (daemon down, permission denied) reading a
#    container's RCON credentials must read as "no value", never crash the
#    monitor loop by letting a non-zero returncode's stdout be parsed
#    anyway.
canary env-of-ignores-docker-failure files/usr/local/sbin/soulmask-monitor.py \
  '    if r.returncode != 0:
        return ""' \
  '    if False:
        return ""' \
  tests/test_soulmask_monitor.py::test_env_of_returns_empty_on_docker_failure

# 4. A per-instance target that is ANY mount other than our own tagged tmpfs
#    must be refused, not stacked on (fstype alone is not identity).
canary pi-foreign-mount-accepted files/usr/local/sbin/soulmask_tmpfs-setup.sh \
  '    if [ "$fs" != "tmpfs" ] || [ "$src" != "$tag" ]; then' \
  '    if false; then' \
  tests/test_soulmask_tmpfs_per_instance.py::test_foreign_tmpfs_is_refused_even_though_the_fstype_matches

# 5. [per-instance-noswap] must really mount noswap (dropping the option would
#    silently serve a swappable mount under a "stays resident" claim).
canary pi-noswap-option-dropped files/usr/local/sbin/soulmask_tmpfs-setup.sh \
  '    [ "$noswap" = "1" ] && opts+=",noswap"' \
  '    :' \
  tests/test_soulmask_tmpfs_per_instance.py::test_noswap_section_mounts_with_noswap

# 6. A busy per-instance mount must fail teardown, not be swallowed.
canary pi-busy-teardown-swallowed files/usr/local/sbin/soulmask_tmpfs-teardown.sh \
  'resolve and re-run"
      PI_BUSY+=("$t"); RC=1' \
  'resolve and re-run"
      PI_BUSY+=("$t"); RC=0' \
  tests/test_soulmask_tmpfs_per_instance.py::test_teardown_refuses_when_a_per_instance_mount_is_busy

# 7. The prune script may only delete regular files named like game backups:
#    without `-type f` a symlink named WS-backup-*.log would be treated (and
#    removed) as a backup.
canary pi-prune-follows-symlinks files/usr/local/sbin/soulmask_logs-prune.sh \
  "find \"\$t\" -maxdepth 1 -type f -name" \
  "find \"\$t\" -maxdepth 1 -name" \
  tests/test_soulmask_tmpfs_per_instance.py::test_prune_keeps_live_log_and_newest_backups_and_touches_nothing_else

# 8. KEEP_BACKUPS=N keeps N backups (an off-by-one would delete the newest).
canary pi-prune-keep-off-by-one files/usr/local/sbin/soulmask_logs-prune.sh \
  'while [ "${#backups[@]}" -gt "$KEEP" ]; do' \
  'while [ "${#backups[@]}" -ge "$KEEP" ]; do' \
  tests/test_soulmask_tmpfs_per_instance.py::test_prune_keeps_live_log_and_newest_backups_and_touches_nothing_else

# 9. A symlink component that stays INSIDE the volume is only caught by the
#    explicit -L check (realpath containment alone would accept it).
canary pi-symlink-component-accepted files/usr/local/sbin/soulmask_tmpfs-setup.sh \
  '    if [ -L "$cur" ]; then' \
  '    if false; then' \
  tests/test_soulmask_tmpfs_per_instance.py::test_symlink_staying_inside_the_volume_is_still_refused

# 10. Teardown must not unmount a per-instance tmpfs while its game container
#     runs (a successful host umount does not stop the game writing).
canary pi-teardown-ignores-running-container files/usr/local/sbin/soulmask_tmpfs-teardown.sh \
  'rc=0; _container_running "$uuid" || rc=$?' \
  'rc=1' \
  tests/test_soulmask_tmpfs_per_instance.py::test_teardown_refuses_a_target_whose_container_is_running

echo
if [ "$fail" -gt 0 ]; then
  echo "canaries: $pass rejected, $fail SURVIVED — the gate is not discriminating"
  exit 1
fi
echo "canaries: $pass rejected, 0 survived"
