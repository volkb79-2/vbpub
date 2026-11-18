#!/usr/bin/env python3
"""Create hand mutations in a disposable test tree."""
from pathlib import Path
import shutil
import sys


repo = Path(__file__).resolve().parents[3]
root = Path(sys.argv[1]).resolve()
controller = root / "mutants/scripts/controller"
runner = root / "mutants/scripts/netcup/runner"
controller.mkdir(parents=True)
runner.mkdir(parents=True)
shutil.copy2(repo / "scripts/controller/lib.sh", controller / "lib.sh")
shutil.copy2(repo / "scripts/controller/opendesign-accounts.py", controller / "opendesign-accounts.py")
shutil.copy2(repo / "scripts/controller/pi-live.py", controller / "pi-live.py")
shutil.copy2(repo / "scripts/controller/job-status.sh", controller / "job-status.sh")
# A mutant wrapper must be judged on the change it carries, not on a missing
# sibling file: the continuation prompt is read from SCRIPT_DIR.
shutil.copy2(repo / "scripts/controller/continue-empty-completion.md", controller / "continue-empty-completion.md")


def account_helper_mutant(name: str, before: str, after: str) -> Path:
    source = repo / "scripts/controller/opendesign-accounts.py"
    original = source.read_text(encoding="utf-8")
    count = original.count(before)
    if count != 1:
        raise SystemExit(f"{source}: expected one mutation target, found {count}: {before!r}")
    variant = controller / name
    variant.mkdir(parents=True)
    for filename in ("lib.sh", "codex-impl.sh", "pi-impl.sh", "resume-usage-limited.sh", "finisher-generic.md"):
        shutil.copy2(repo / "scripts/controller" / filename, variant / filename)
    target = variant / "opendesign-accounts.py"
    target.write_text(original.replace(before, after, 1), encoding="utf-8")
    target.chmod(0o755)
    return variant


def mutant(source: str, name: str, before: str, after: str, destination: Path = controller) -> Path:
    original = (repo / source).read_text(encoding="utf-8")
    count = original.count(before)
    if count != 1:
        raise SystemExit(f"{source}: expected one mutation target, found {count}: {before!r}")
    target = destination / name
    target.write_text(original.replace(before, after, 1), encoding="utf-8")
    target.chmod(0o755)
    return target


mutant("scripts/controller/ctl-status.sh", "ctl-status-infra.sh",
       "if (( streak >= 2 || flag_newer == 1 )); then",
       "if (( streak >= 3 || flag_newer == 1 )); then")
mutant("scripts/controller/ctl-status.sh", "ctl-status-flag.sh",
       "${flag_mtime:-0} -gt $pass_epoch", "${flag_mtime:-0} -lt $pass_epoch")
mutant("scripts/controller/job-check.sh", "job-check-empty-final.sh",
       '    elif not text:', '    elif False:')
mutant("scripts/controller/job-check.sh", "job-check-tool-call-case.sh",
       "text[-600:], re.M | re.I", "text[-600:], re.M")
mutant("scripts/controller/job-check.sh", "job-check-report-path.sh",
       'if [[ $report != /* ]]; then', 'if [[ $report == /* ]]; then')
mutant("scripts/controller/job-check.sh", "job-check-gate-binding.sh",
       "sys.exit(0 if bound and passed else 1)", "sys.exit(0)")
mutant("scripts/controller/job-check.sh", "job-check-gate-final-verdict.sh",
       'passed = final_verdict == "PASS"', 'passed = final_verdict in ("PASS", "FAIL")')
mutant("scripts/controller/job-check.sh", "job-check-report-head.sh",
       'if (( worktree_exists )) && [[ -n $head_full ]] && ! grep -Fq -- "$head_full" "$report" && ! grep -Fq -- "$head_short" "$report"; then',
       'if False; then')
mutant("scripts/controller/job-check.sh", "job-check-reasoning-end.sh",
       'elif turn_state == "completed" and turn_last_item == "reasoning":', 'elif False:')
mutant("scripts/controller/job-check.sh", "job-check-usage-limit.sh",
       'usage_limited = (\n    (turn_state == "failed" and usage_reset is not None)\n    or (pi_detected and pi_stop_reason == "error" and pi_usage_limited)\n)',
       'usage_limited = False')
mutant("scripts/controller/job-check.sh", "job-check-usage-retry-binding.sh",
       '    elif event_type == "turn.failed":\n        turn_state = "failed"\n        usage_reset = sys.argv[2] or None',
       '    elif event_type == "turn.failed":\n        turn_state = "failed"\n        usage_reset = None')
mutant("scripts/controller/job-check.sh", "job-check-killed-run.sh",
       'elif turn_state != "completed":', 'elif False:')
mutant("scripts/controller/job-check.sh", "job-check-mid-wait-trailing.sh",
       '        if pending_wait:', '        if False:')
mutant("scripts/controller/job-check.sh", "job-check-mid-wait-resolution.sh",
       "            pending_wait = None", "            pending_wait = pending_wait")
mutant("scripts/controller/hx.sh", "hx-identity.sh",
       'if ! controller_host_api_check "$host" >/dev/null; then', 'if false; then')
mutant("scripts/controller/release-from-wt.sh", "release-archive.sh",
       'if [[ $archive_mode == auto && $branch == rel-* ]] || [[ $archive_mode == on ]]; then',
       'if [[ $archive_mode == off ]]; then')
mutant("scripts/controller/release-from-wt.sh", "release-clean.sh",
       'if [[ -n $(git -C "$worktree" status --porcelain 2>/dev/null) ]]; then', 'if false; then')
mutant("scripts/controller/push-image.sh", "push-image-status.sh",
       'for rc in "${statuses[@]}"; do (( rc == 0 )) || exit 1; done', ': # deliberately ignore pipeline exit statuses')

# skills-sync.sh: five ways the ownership/staleness contract can be weakened.
# Each turns a state the script must tell apart into its neighbour: a foreign
# stamp becomes ours, a local edit is clobbered, a stale source reads as
# current, a dangling link is adopted as absent, and `check` stops being able to
# fail.
mutant("scripts/controller/skills-sync.sh", "skills-stamp-ownership.sh",
       '[[ $tool == "$OWNER" ]] || { printf \'unmanaged\\n\'; return 0; }',
       '[[ -n $tool ]] || { printf \'unmanaged\\n\'; return 0; }')
mutant("scripts/controller/skills-sync.sh", "skills-modified-clobber.sh",
       'if ((overwrite_modified)); then', 'if true; then')
mutant("scripts/controller/skills-sync.sh", "skills-stale-detection.sh",
       '[[ $stamped == "$want" ]] || { printf \'stale\\n\'; return 0; }',
       ': # a moved source reads as current')
# A DANGLING symlink must not read as `absent`: that branch is the one that
# calls install_one, which rm -rf's and recreates the directory. Losing the
# `-L` half turns "refuse an unmanaged link" into "replace it".
mutant("scripts/controller/skills-sync.sh", "skills-dangling-symlink.sh",
       'if [[ ! -e $dest && ! -L $dest ]]; then', 'if [[ ! -e $dest ]]; then')
# `--help` dropping the header's own facts: a fixed `sed -n '3,14p'` range lost
# the first description line (and would lose more as the header grows).
mutant("scripts/controller/skills-sync.sh", "skills-usage-range.sh",
       'usage() { awk \'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }\' "${BASH_SOURCE[0]}"; }',
       'usage() { sed -n \'3,14p\' "${BASH_SOURCE[0]}" | sed \'s/^# \\{0,1\\}//\'; }')
# Reviewer additions: `--dest` arity (the test helper puts it last, which hid
# `--dest DIR --dry-run` being rejected), a failed copy reported as success, and
# a write below a `synced` directory.
mutant("scripts/controller/skills-sync.sh", "skills-dest-arity.sh",
       '[[ $# -ge 1 && $1 != -* ]] ||', '[[ $# == 1 ]] ||')
mutant("scripts/controller/skills-sync.sh", "skills-install-failure.sh",
       'echo "ERROR cannot stage $name in $base" >&2; failed=1; return 1',
       'echo "ERROR cannot stage $name in $base" >&2; return 1')
mutant("scripts/controller/skills-sync.sh", "skills-synced-guard.sh",
       '      */synced/*)', '      */synced-never/*)')
# `--dry-run` that writes anyway (only visible on a tree that needs work).
mutant("scripts/controller/skills-sync.sh", "skills-dry-run-writes.sh",
       '  if ((dry_run)); then\n    printf \'would', '  if ((dry_run > 5)); then\n    printf \'would')
mutant("scripts/controller/skills-sync.sh", "skills-check-exit.sh",
       '  check) ((failed)) && { echo "FAIL $failed target(s) are not current" >&2; exit 1; }; exit 0 ;;',
       '  check) exit 0 ;;')

shutil.copy2(repo / "scripts/controller/lib.sh", root / "mutants/scripts/controller/lib.sh")
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-threshold.sh",
       'if (( consec_infra >= MAX_CONSEC_INFRA )); then',
       'if (( consec_infra > MAX_CONSEC_INFRA )); then', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-fallback.sh",
       "write_auto_pause || { log 'pause metadata write failed; touching empty flag'; touch \"$BASE/host-sat-queue.disabled.$HOST\"; }",
       "write_auto_pause || log 'metadata write failed'", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-probe.sh",
       '[[ -n $nproc && -n $load1 && -n $memsome && -n $iofull && -n $memavail && -n $swapused && -n $oomkill && -n $rootfree ]] || {',
       '[[ -n $nproc ]] || {', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-stop-alias.sh",
       'if ! controller_host_scalar "$host" guard >/dev/null; then',
       'if false; then', runner)
# CTL-12: the remote-gate cache. The observable defects: a publish that rewrites
# the cache file in place (a reader can see a half-written tool), a copy trusted
# by name instead of verified by content, a refresh lock that is not held, an
# ABSENCE reported as a refresh failure (the two refusals collapse again), and an
# unrecorded identity (a reader cannot know which revision ran).
#
# The per-invocation staging NAME is deliberately not mutated into a shared name:
# the serialised refresh lock already makes two stagers mutually exclusive inside
# one cache dir, so such a mutant is equivalent on observable behaviour and could
# not be killed honestly. The unique name is still required (a crashed invocation
# must not leave a file another one publishes), which the leftover-staging case
# checks.
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-inplace-publish.sh",
       'if ! mv -f -T -- "$stage" "$published" 2>/dev/null; then',
       'if ! cp -- "$stage" "$published" 2>/dev/null; then', runner)
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-name-only-validity.sh",
       'published_is_valid() {',
       'published_is_valid() {\n  [[ -s $1 ]] && return 0', runner)
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-stray-repo.sh",
       'repo=${REMOTE_GATE_REPO:-}',
       'repo=${repo:-${REMOTE_GATE_REPO:-}}', runner)
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-lock-ignored.sh",
       'if ! flock -w "$lock_wait" "$refresh_fd"; then',
       'if false; then', runner)
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-absent-as-refresh.sh",
       '  absent_refusal "$commit"',
       '  refresh_refusal "cannot read blob $commit:$tool from $repo"', runner)
mutant("scripts/netcup/runner/remote-gate-cache.sh", "remote-gate-cache-identity-missing.sh",
       "  printf 'cache=%s\\nref=%s\\ncommit=%s\\nidentity=blob:%s\\n' \\\n    \"$published\" \"$ref\" \"$commit\" \"$blob\"",
       "  printf 'cache=%s\\nref=%s\\ncommit=%s\\n' \\\n    \"$published\" \"$ref\" \"$commit\"", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-start-config.sh",
       'host_text=$(queue_hosts) || exit 2', "host_text=$(queue_hosts) || host_text=''", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-stop-config.sh",
       'host_text=$(controller_host_list) || exit 2', "host_text=$(controller_host_list) || host_text=''", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-controller-lib.sh",
       'if [[ -f $REPO_ROOT/scripts/controller/lib.sh ]]; then', 'if [[ -f $SCRIPT_DIR/lib.sh ]]; then', runner)

# CTL-19: a name with no `.done` is only RUNNING when the owner record says so.
mutant("scripts/controller/job-check.sh", "job-check-dead-owner-running.sh",
       '  if [[ -n $dead_reason ]]; then',
       '  if false; then')
mutant("scripts/controller/job-check.sh", "job-check-owner-pid-alive-only.sh",
       '      if [[ $owner_ticks =~ ^[0-9]+$ && $live_ticks =~ ^[0-9]+$ && $owner_ticks != "$live_ticks" ]]; then',
       '      if false; then')
mutant("scripts/controller/job-check.sh", "job-check-owner-boot.sh",
       '    if [[ -n $owner_boot && -n $current_boot && $owner_boot != "$current_boot" ]]; then',
       '    if false; then')
mutant("scripts/controller/job-check.sh", "job-check-owner-liveness.sh",
       '    elif ! kill -0 "$owner_pid" 2>/dev/null; then',
       '    elif false; then')
mutant("scripts/controller/job-check.sh", "job-check-run-traversal.sh",
       '[[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || { echo "invalid run name" >&2; exit 2; }',
       ': # removed run-name traversal guard')
# A name that no wrapper ever owned is a distinct state, and the line says so
# instead of borrowing RUNNING's claim about a process.
mutant("scripts/controller/job-check.sh", "job-check-missing-done-owner-record.sh",
       "      printf 'owner record: none (%s absent",
       "      : printf 'owner record: none (%s absent")
# CTL-19: the Codex wrapper owns its run name and states its outcome on every
# exit path, including a resume whose child fails.
mutant("scripts/controller/codex-impl.sh", "codex-run-name-claim.sh",
       'controller_run_claim "$name" || exit 2',
       ': # name ownership not claimed')
mutant("scripts/controller/codex-impl.sh", "codex-resume-stale-marker.sh",
       '  rm -f -- "$out/$name.done"\n  codex_launched=1',
       '  codex_launched=1')
# CTL-14 (blocker 2): the Codex child inherits the wrapper's ownership
# descriptor, and a SIGKILLed wrapper then strands its run name while the agent
# (or a daemon the agent left) keeps the flock. Both launch paths must close it.
def codex_wrapper_with_lib_mutant(name: str, before: str, after: str) -> Path:
    """A codex-impl.sh mutant with its own lib.sh copy: the wrapper sources
    lib.sh from its own directory, so a lib mutation must ship beside it or the
    real library is loaded and the mutation is never exercised."""
    variant = controller / name
    variant.mkdir(parents=True, exist_ok=True)
    for sibling in ("codex-impl.sh", "opendesign-accounts.py", "opendesign-models.py"):
        source = repo / "scripts/controller" / sibling
        if source.is_file():
            shutil.copy2(source, variant / sibling)
    shutil.copy2(repo / "scripts/controller/continue-empty-completion.md", variant / "continue-empty-completion.md")
    mutant("scripts/controller/lib.sh", "lib.sh", before, after, variant)
    return variant / "codex-impl.sh"


codex_wrapper_with_lib_mutant(
    "codex-run-name-child-close",
    '  eval "exec $fd>&-" 2>/dev/null || :\n  return 0\n}\n\ncontroller_run_owner_pid',
    '  :\n  return 0\n}\n\ncontroller_run_owner_pid',
)
# CTL-22: EVERY Codex exec site closes the ownership descriptor, and the resume
# and auto-resume sites are the ROUTINE ones. A shared-function mutant (the
# `codex-run-name-child-close` lib mutant) cannot tell the three call sites
# apart, so one mutant per site: deleting the call on that line alone must make
# its own SIGKILL case fail.
mutant("scripts/controller/codex-impl.sh", "codex-newlaunch-child-close.sh",
       '  if (controller_close_ownership_fds && \\\n'
       '    nice codex exec -C "$worktree"',
       '  if (\\\n'
       '    nice codex exec -C "$worktree"')
mutant("scripts/controller/codex-impl.sh", "codex-resume-child-close.sh",
       '  if (cd -- "$worktree" && controller_close_ownership_fds && \\\n'
       '    nice codex exec resume -m "$model" -c "model_reasoning_effort=\\"$effort\\"" \\\n'
       '    --skip-git-repo-check --json "$session_id"',
       '  if (cd -- "$worktree" && \\\n'
       '    nice codex exec resume -m "$model" -c "model_reasoning_effort=\\"$effort\\"" \\\n'
       '    --skip-git-repo-check --json "$session_id"')
mutant("scripts/controller/codex-impl.sh", "codex-autoresume-child-close.sh",
       '  if (cd -- "$worktree" && controller_close_ownership_fds && \\\n'
       '    nice codex exec resume -m "$model" -c "model_reasoning_effort=\\"$effort\\"" \\\n'
       '    --skip-git-repo-check --json "$thread_id"',
       '  if (cd -- "$worktree" && \\\n'
       '    nice codex exec resume -m "$model" -c "model_reasoning_effort=\\"$effort\\"" \\\n'
       '    --skip-git-repo-check --json "$thread_id"')
mutant("scripts/controller/job-check.sh", "job-check-report-litter.sh",
       "stray=$(git -C \"$worktree\" status --short --ignored 2>/dev/null | grep -E '^\\?\\? (reports|scripts/.*/reports)/' | head -2 || true)",
       "stray='' ")
mutant("scripts/controller/job-check.sh", "job-check-merge-trailer.sh",
       'commits=$(git -C "$worktree" rev-list "$base..HEAD" 2>/dev/null)',
       'commits=$(git -C "$worktree" rev-list --no-merges "$base..HEAD" 2>/dev/null)')
mutant("scripts/controller/job-check.sh", "job-check-any-claude-trailer.sh",
       'Claude [A-Za-z0-9. ]+', 'Claude NEVER')
mutant("scripts/controller/job-check.sh", "job-check-deepseek-pinned-routes.sh",
       '  deepseek|deepseek-volker|deepseek-github2|deepseek-pi|deepseek-pi-volker|deepseek-pi-github2|opencode)\n    trailer_mode=single',
       '  deepseek|deepseek-pi|opencode)\n    trailer_mode=single')
account_helper_mutant(
    "codex-deepseek-catalog-safety",
    'not isinstance(model, dict) or model.get("tool_mode") is not None',
    'False',
)
mutant("scripts/controller/codex-impl.sh", "codex-pinned-route-fallback.sh",
       'start "$od_account" "$catalog" >/dev/null || exit $?',
       'start auto "$volker_catalog" "$github2_catalog" >/dev/null || exit $?')
mutant("scripts/controller/codex-impl.sh", "codex-manual-resume-account.sh",
       'od_start_kind=resume', 'od_start_kind=auto-resume')
mutant("scripts/controller/codex-impl.sh", "codex-bookkeeping-exit-overwrite.sh",
       '    bookkeeping_rc=$?', '    bookkeeping_rc=$?\n    rc=$bookkeeping_rc')
mutant("scripts/controller/codex-impl.sh", "codex-route-sidecar-unpinned.sh",
       'route_record=deepseek-$od_account', 'route_record=deepseek')
mutant("scripts/controller/codex-impl.sh", "codex-unknown-route-fallback.sh",
       '*) echo "unknown CODEX_ROUTE=$route" >&2; exit 2 ;;',
       '*) route=luna; route_home=${CODEX_HOME_LUNA:-"$HOME/.codex"}; default_model=gpt-6-luna; default_effort=xhigh; auto_default=0 ;;')
mutant("scripts/controller/opencode-impl.sh", "opencode-default-provider.sh",
       'provider=${OPENCODE_PROVIDER:-opendesign_github2}', 'provider=${OPENCODE_PROVIDER:-opendesign}')
mutant("scripts/controller/README.md", "README-home-override-route.md",
       '`CODEX_HOME_OVERRIDE` is honoured only on the luna route.',
       '`CODEX_HOME_OVERRIDE` is honoured on every route.')
mutant("scripts/controller/codex-impl.sh", "codex-autoresume-bound.sh",
       'while (( auto_count < auto_limit )) && reasoning_end',
       'while (( auto_count <= auto_limit )) && reasoning_end')
mutant("scripts/controller/codex-impl.sh", "codex-autoresume-limit-validation.sh",
       'raise SystemExit(0 if value <= 2147483647 else 1)', 'raise SystemExit(0)')
mutant("scripts/controller/codex-impl.sh", "codex-stale-run-markers.sh",
       '  if (( ${#existing[@]} > 0 )); then\n    if [[ ${CONTROLLER_RUN_FORCE:-0} != 1 ]]; then',
       '  if (( ${#existing[@]} > 0 )); then\n    if false; then')
mutant("scripts/controller/codex-impl.sh", "codex-stale-run-resume-mode.sh",
       'if [[ $mode == new ]]; then\n  existing=()',
       'if true; then\n  existing=()')
mutant("scripts/controller/job-check.sh", "job-check-any-sol-trailer.sh",
       '|gpt-6-sol|', '|')
mutant("scripts/controller/job-check.sh", "job-check-sol-route-trailer.sh",
       '  sol)\n    trailer_mode=single', '  sol)\n    trailer_mode=none')
mutant("scripts/controller/job-check.sh", "job-check-any-multi-trailer.sh",
       'if (( count == 0 )); then', 'if (( count != 1 )); then')
mutant("scripts/controller/job-check.sh", "job-check-usage-limit-message-flags.sh",
       'if not usage_limited:', 'if True:')
mutant("scripts/controller/job-check.sh", "job-check-reattach-evidence.sh",
       'if reattach_ok:', 'if False:')
mutant("scripts/controller/lib.sh", "usage-retry-time-only-lib.sh",
       'r"[A-Za-z]{3,9}\\s+\\d{1,2}(?:st|nd|rd|th)?,\\s+\\d{4}\\s+\\d{1,2}:\\d{2}(?:\\s*[AP]M)?"',
       'r"NEVER_MATCH_A_DATED_RETRY"')
mutant("scripts/controller/lib.sh", "usage-retry-insufficient-balance.sh",
       'if current.get("code") == "insufficient_balance":', 'if False:')
mutant("scripts/controller/lib.sh", "controller-run-dir-override.sh",
       '[[ -z $requested_runs_dir ]] || CONTROLLER_RUNS_DIR=$requested_runs_dir',
       ': # ignore the explicit environment override')
mutant("scripts/controller/resume-usage-limited.sh", "resume-usage-detection.sh",
       'if ! reset_time=$(usage_retry_text "$run_file" "$runs_dir/$run.err"); then continue; fi',
       'reset_time=unknown')
mutant("scripts/controller/resume-usage-limited.sh", "resume-future-retry.sh",
       "    if (( reset_epoch > now_epoch )); then", "    if false; then")
mutant("scripts/controller/resume-usage-limited.sh", "resume-skip-list.sh",
       'if is_skipped "$run"; then', 'if false; then')
mutant("scripts/controller/resume-usage-limited.sh", "resume-route-sidecar.sh",
       'if [[ ! -f $runs_dir/$run.route ]]; then', 'if false; then')
mutant("scripts/controller/resume-usage-limited.sh", "resume-route-value.sh",
       'case $route in luna|deepseek|deepseek-volker|deepseek-github2) ;; *) echo "SKIP $run (invalid route sidecar)"; continue ;; esac',
       'case $route in *) ;; esac')
mutant("scripts/controller/resume-usage-limited.sh", "resume-max-age-validation.sh",
       "if [[ ! $max_age =~ ^[0-9]+$ ]] || (( max_age <= 0 )); then",
       'if false; then')
mutant("scripts/controller/resume-usage-limited.sh", "resume-excluded-prefixes.sh",
       'if prefix=$(excluded_prefix "$run"); then', 'if false; then')
mutant("scripts/controller/resume-usage-limited.sh", "resume-worktree-map.sh",
       'if ! load_worktree_map "$worktree_map"; then', 'if false; then')
mutant("scripts/controller/resume-usage-limited.sh", "resume-live-wrapper.sh",
       'resumed_wrapper_is_alive "$resumed_name"\n  process_rc=$?', 'resumed_wrapper_is_alive "$resumed_name"\n  process_rc=1')
mutant("scripts/controller/resume-usage-limited.sh", "resume-claim-lock.sh",
       'flock -n "$lock_fd"\n  lock_rc=$?', 'true\n  lock_rc=0')
mutant("scripts/controller/start-after.sh", "start-after-time-guard.sh",
       '  [[ $now < $target ]] || break', '  break')
mutant("scripts/controller/start-after.sh", "start-after-target-validation.sh",
       '[[ $target =~ ^([01][0-9]|2[0-3])[0-5][0-9]$ ]]',
       '[[ $target =~ ^[0-9]{4}$ ]]')
mutant("scripts/controller/gate-run.sh", "gate-run-distinct-locks.sh",
       '[[ $CONTROLLER_GATE_LOCK != "$CONTROLLER_GATE2_LOCK" ]] ||', 'true ||')
mutant("scripts/controller/gate-run.sh", "gate-run-absolute-locks.sh",
       '[[ -n $lock && $lock == /* ]] || {', '[[ -n $lock ]] || {')
mutant("scripts/controller/gate-run.sh", "gate-run-poll-second-slot.sh",
       '  poll_lock 8 "$CONTROLLER_GATE2_LOCK" gate2.lock "$@"',
       '  : # deliberately skip the second slot')
mutant("scripts/controller/gate-run.sh", "gate-run-refusal-example.sh",
       'gate-run: CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK must both be configured as absolute paths; copy both names from controller.local.env.example and export them before running',
       'gate-run: configure the gate lock paths before running')
mutant("scripts/controller/build-readback.py", "build-readback-budget-minimum.py",
       'if args.budget_chars < 512:', 'if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-since-marker.py",
       '    if marker:\n        command += ["--since", marker]',
       '    if False:\n        command += ["--since", marker]')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-number-next.py",
       '    return highest + 1', '    return highest')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-subagent-own.py",
       '    if agent_id:\n        own = Path(path).parent',
       '    if False:\n        own = Path(path).parent')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-failure-record.py",
       '    log(log_path, {"ev": "readback", **result, "rc": rc})\n    write_state(',
       '    log(log_path, {"ev": "readback", **result, "rc": rc})\n    return result\n    write_state(')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-pressure-rearm.py",
       '    if step < recorded:\n        # The context shrank',
       '    if False:\n        # The context shrank')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-hard-timeout.py",
       'HARD_TIMEOUT = 20.0', 'HARD_TIMEOUT = 600.0')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-kind-wording.py",
       '    controller = kind != "spawned"', '    controller = False')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-pattern-leak.py",
       'shown += ["--redact-pattern", "<REGEX>"]', 'shown += ["--redact-pattern", pattern]')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-spawned-goal.py",
       '        or kind == "spawned"\n', '        or False\n')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-subagent-skip.py",
       '        if hook.get("agent_id") and not explicit:', '        if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-crash-log.py",
       'log(ACTIVE_LOG, {"ev": "hook-error"', 'log(None, {"ev": "hook-error"')
mutant("scripts/controller/release-from-wt.sh", "release-psi-validation.sh",
       "[[ $psi_limit =~ ^[0-9]+([.][0-9]+)?$ && $poll_seconds =~ ^[0-9]+$ ]] || {\n  echo 'RELEASE_PSI_LIMIT must be numeric and RELEASE_PSI_POLL_SECONDS must be integer seconds' >&2; exit 2;\n}",
       ': # removed PSI input validation')
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-pause-alias.sh",
       'controller_host_lanes "$host" >/dev/null || exit 2; touch "$BASE/host-sat-queue.disabled.$host"',
       ': "$(controller_host_lanes "$host")" || exit 2; touch "$BASE/host-sat-queue.disabled.$host"', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-resume-alias.sh",
       'controller_host_lanes "$host" >/dev/null || exit 2; rm -f -- "$BASE/host-sat-queue.disabled.$host"',
       ': "$(controller_host_lanes "$host")" || exit 2; rm -f -- "$BASE/host-sat-queue.disabled.$host"', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-status-results.sh",
       'if [[ -f $RESULTS ]]; then', 'if true; then', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-run-identity.sh",
       "  controller_host_api_check \"$HOST\" >/dev/null 2>&1 || {\n    log 'host API identity check failed; not launching'; LAST_VERDICT=SKIP; LAST_RESULT=SKIP; return;\n  }",
       '  : # removed provider identity check', runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-numeric-probe.sh",
       "  local number\n  for number in \"$nproc\" \"$memavail\" \"$swapused\" \"$oomkill\" \"$rootfree\"; do\n    [[ $number =~ ^[0-9]+$ ]] || { echo 'probe contains non-numeric data'; return 1; }\n  done\n  for number in \"$load1\" \"$memsome\" \"$iofull\"; do\n    [[ $number =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'probe contains non-numeric data'; return 1; }\n  done\n  (( nproc > 0 )) || { echo 'probe reports zero processors'; return 1; }\n",
       '', runner)
mutant("scripts/controller/ctl-status.sh", "ctl-status-fresh-results.sh",
       'if [[ -f $CONTROLLER_HOME/logs/host-sat-results.tsv ]]; then', 'if true; then')
mutant("scripts/controller/ctl-status.sh", "ctl-status-pi-route.sh",
       'route=$(cat "$CONTROLLER_HOME/runs/$name.route" 2>/dev/null || echo unknown)', 'route=unknown')
mutant("scripts/controller/ctl-status.sh", "ctl-status-pi-stall.sh",
       '(( now - mtime > 900 ))', '(( now - mtime > 2147483647 ))')
mutant("scripts/controller/job-check.sh", "job-check-pi-route.sh",
       'deepseek|deepseek-volker|deepseek-github2|deepseek-pi|deepseek-pi-volker|deepseek-pi-github2|opencode)',
       'deepseek|deepseek-volker|deepseek-github2|opencode)')
mutant("scripts/controller/job-check.sh", "job-check-pi-message-end.sh",
       'elif event_type == "message_end":', 'elif event_type == "pi_message_end":')
mutant("scripts/controller/job-check.sh", "job-check-pi-402-error-code.sh",
       'pi_usage_limited = pi_402_error(message.get("errorMessage"))', 'pi_usage_limited = False')
# A context-full run is a real end state with its own remedy, and a final
# message that is a tool call with no text ended mid-work: neither may read as
# an ordinary finished/unfinished job.
mutant("scripts/controller/job-check.sh", "job-check-context-full-class.sh",
       '    if pi_detected and pi_stop_reason == "length":', '    if False:')
mutant("scripts/controller/job-check.sh", "job-check-tool-call-no-text-class.sh",
       '    elif pi_detected and pi_stop_reason == "stop" and pi_has_tool_call and not text:',
       '    elif False:')
mutant("scripts/controller/job-check.sh", "job-check-tool-call-part-detection.sh",
       '                isinstance(part, dict) and part.get("type") == "toolCall" for part in content',
       '                isinstance(part, dict) and part.get("type") == "tool_call" for part in content')
mutant("scripts/controller/lib.sh", "usage-retry-pi-error-message.sh",
       'gateway_text_reset(event) or coding_plan_reset(event) or pi_error_reset(event)',
       'gateway_text_reset(event) or coding_plan_reset(event)')
mutant("scripts/controller/pi-impl.sh", "pi-prompt-absolute.sh",
       '[[ $2 == /* ]] || { echo \'prompt path must be absolute\' >&2; exit 2; }',
       ': # removed absolute prompt guard')
mutant("scripts/controller/pi-impl.sh", "pi-worktree-exists.sh",
       'worktree=$(realpath -e -- "$1") || usage', 'worktree=$1')
mutant("scripts/controller/pi-impl.sh", "pi-session-id-guard.sh",
       '[[ -n $session_id && $session_id =~ ^[A-Za-z0-9._-]+$ && $session_id != *..* ]] || usage',
       '[[ -n $session_id ]] || usage')
mutant("scripts/controller/pi-impl.sh", "pi-mode-guard.sh",
       'elif [[ $mode != new || $# -gt 4 ]]; then', 'elif [[ $mode != new ]]; then')
mutant("scripts/controller/pi-impl.sh", "pi-run-name-guard.sh",
       '[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || {',
       '[[ -n $name ]] || {')
mutant("scripts/controller/pi-impl.sh", "pi-run-collision-guard.sh",
       '    if [[ ${CONTROLLER_RUN_FORCE:-0} != 1 ]]; then',
       '    if false; then')
# CTL-14: the run-name ownership guard. Three ways it can be lost: no claim at
# all (two wrappers interleave), a claim taken but never released (a killed
# wrapper strands the name), and a claim that can be bypassed by force (a live
# owner's records get overwritten under it).
mutant("scripts/controller/pi-impl.sh", "pi-run-name-claim.sh",
       'controller_run_claim "$name" || exit 2', ': # name ownership not claimed')
# The lock lives in lib.sh, and pi-impl.sh sources lib.sh from its own
# directory, so a lib mutant judged against test-pi-impl.sh needs its own wrapper
# copy beside the mutated library (otherwise the wrapper would load the real
# lib.sh and the mutation would never be exercised).
#
# child-close: the lock is held by the wrapper's open file description, which a
# child that forked from it inherits; the agent and its sidecar must close their
# copy, so a killed wrapper's name is free even while the agent it started still
# runs.
# live-owner-bypass: an exclusive lock that is not obtained reads as obtained,
# so a second invocation interleaves with a live owner.
# retire-deletes: a forced reuse deletes the previous owner's records instead of
# archiving them.
def pi_wrapper_with_lib_mutant(name: str, before: str, after: str) -> Path:
    variant = controller / name
    variant.mkdir(parents=True, exist_ok=True)
    for sibling in ("pi-impl.sh", "continue-empty-completion.md", "opendesign-accounts.py", "pi-live.py"):
        shutil.copy2(repo / "scripts/controller" / sibling, variant / sibling)
    mutant("scripts/controller/lib.sh", "lib.sh", before, after, variant)
    return variant / "pi-impl.sh"


pi_wrapper_with_lib_mutant(
    "pi-run-name-child-close",
    '  eval "exec $fd>&-" 2>/dev/null || :\n  return 0\n}\n\ncontroller_run_owner_pid',
    '  :\n  return 0\n}\n\ncontroller_run_owner_pid',
)
pi_wrapper_with_lib_mutant(
    "pi-run-name-live-owner-bypass",
    '  flock -n "$CONTROLLER_RUN_LOCK_FD"\n  rc=$?',
    '  flock -n "$CONTROLLER_RUN_LOCK_FD" || true\n  rc=0',
)
# The owner record is written after the caller's own preflight, so a REFUSED
# invocation cannot overwrite the identity of the owner it refused on.
mutant("scripts/controller/pi-impl.sh", "pi-run-name-refusal-overwrites.sh",
       'controller_run_claim "$name" || exit 2',
       'controller_run_claim "$name" || exit 2\ncontroller_run_record_owner')
# CTL-23: the owner record belongs to a launch that is REAL. One mutant per
# refusal the record must stay behind, so each clause is judged by its own case.
mutant("scripts/controller/pi-impl.sh", "pi-record-owner-before-accounts.sh",
       'if [[ -n $od_start_kind ]]; then\n  case $od_start_kind in',
       'controller_run_record_owner\nif [[ -n $od_start_kind ]]; then\n  case $od_start_kind in')
mutant("scripts/controller/pi-impl.sh", "pi-record-owner-before-credentials.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then',
       'controller_run_record_owner\nif [[ ! -r $HOME/.env.ai ]]; then')
mutant("scripts/controller/pi-impl.sh", "pi-done-removed-before-credentials.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then',
       'if [[ $mode == resume ]]; then rm -f -- "$out/$name.done"; fi\nif [[ ! -r $HOME/.env.ai ]]; then')
mutant("scripts/controller/pi-impl.sh", "pi-route-written-before-credentials.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then',
       'printf \'%s\\n\' "$route_record" > "$out/$name.route"\nif [[ ! -r $HOME/.env.ai ]]; then')
# The block a real launch DOES run, flipped: an accepted RESUME must remove the
# previous marker and record its thread, and every accepted launch records its owner.
mutant("scripts/controller/pi-impl.sh", "pi-resume-block-inverted.sh",
       'if [[ $mode == resume ]]; then\n  rm -f -- "$out/$name.done"',
       'if [[ $mode != resume ]]; then\n  rm -f -- "$out/$name.done"')
mutant("scripts/controller/pi-impl.sh", "pi-record-owner-new-only.sh",
       'controller_run_record_owner\nprintf \'%s\\n\' "$worktree" > "$out/$name.wt"',
       '[[ $mode == new ]] && controller_run_record_owner\nprintf \'%s\\n\' "$worktree" > "$out/$name.wt"')
# A forced launch retires the previous records only once it is real: restoring
# the early retirement moves them aside on a refusal that follows.
mutant("scripts/controller/pi-impl.sh", "pi-force-retire-early.sh",
       'controller_run_claim "$name" || exit 2\nretire_existing=0',
       'controller_run_claim "$name" || exit 2\n'
       '[[ $mode != new || ${CONTROLLER_RUN_FORCE:-0} != 1 ]] || controller_run_retire_records "$name" >/dev/null\n'
       'retire_existing=0')
mutant("scripts/controller/pi-impl.sh", "pi-force-retire-before-credentials.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then',
       'if [[ $retire_existing == 1 ]]; then controller_run_retire_records "$name" >/dev/null; fi\n'
       'if [[ ! -r $HOME/.env.ai ]]; then')
mutant("scripts/controller/codex-impl.sh", "codex-force-retire-early.sh",
       'controller_run_claim "$name" || exit 2\nretire_existing=0',
       'controller_run_claim "$name" || exit 2\n'
       '[[ $mode != new || ${CONTROLLER_RUN_FORCE:-0} != 1 ]] || controller_run_retire_records "$name" >/dev/null\n'
       'retire_existing=0')
mutant("scripts/controller/codex-impl.sh", "codex-record-owner-early.sh",
       'controller_run_claim "$name" || exit 2\nretire_existing=0',
       'controller_run_claim "$name" || exit 2\ncontroller_run_record_owner\nretire_existing=0')
mutant("scripts/controller/codex-impl.sh", "codex-record-owner-before-count.sh",
       'auto_limit=${CODEX_AUTO_CONTINUE:-$auto_default}\n',
       'controller_run_record_owner\nauto_limit=${CODEX_AUTO_CONTINUE:-$auto_default}\n')
mutant("scripts/controller/codex-impl.sh", "codex-record-owner-before-accounts.sh",
       'if [[ -n $od_start_kind ]]; then\n  volker_home=',
       'controller_run_record_owner\nif [[ -n $od_start_kind ]]; then\n  volker_home=')
# CTL-23: a claimed name that never started is its own state, told from a
# preflighting wrapper by the kernel lock and from a started run by its stream.
mutant("scripts/controller/job-check.sh", "job-check-not-started-off.sh",
       'if [[ $lock_state == free ]]; then',
       'if [[ $lock_state == never ]]; then')
mutant("scripts/controller/job-check.sh", "job-check-not-started-held.sh",
       'if flock -n "$probe_fd"; then lock_state=free; else lock_state=held; fi',
       'if flock -n "$probe_fd"; then lock_state=free; else lock_state=free; fi')
# A held lock with a dead previous owner's record is a resume still preflighting.
mutant("scripts/controller/job-check.sh", "job-check-died-held-lock.sh",
       'if [[ -n $dead_reason && $lock_state == held ]]; then',
       'if [[ -n $dead_reason && $lock_state == never ]]; then')
mutant("scripts/controller/job-check.sh", "job-check-died-no-probe.sh",
       'if [[ -n $dead_reason ]]; then probe_run_lock "$owner_record"; fi',
       ': # the lock of a dead owner is not probed')
mutant("scripts/controller/job-check.sh", "job-check-not-started-stream.sh",
       '[[ -e $owner_record && ! -s $owner_record && ! -e $run_file ]]',
       '[[ -e $owner_record && ! -s $owner_record ]]')
mutant("scripts/controller/job-check.sh", "job-check-not-started-content.sh",
       '[[ -e $owner_record && ! -s $owner_record && ! -e $run_file ]]',
       '[[ -e $owner_record && ! -e $run_file ]]')
pi_wrapper_with_lib_mutant(
    "pi-run-name-retire-deletes",
    '    mv -- "$runs/$name.$extension" "$archive/" || return 2',
    '    rm -f -- "$runs/$name.$extension" || return 2',
)
mutant("scripts/controller/pi-impl.sh", "pi-prompt-preserve-newlines.sh",
       'IFS= read -r -d \'\' prompt_text < "$prompt" || :',
       'prompt_text=$(<"$prompt")')
mutant("scripts/controller/pi-impl.sh", "pi-unknown-route-guard.sh",
       '  *) echo "unknown PI_ROUTE=$route" >&2; exit 2 ;;',
       '  *) route=deepseek-pi; od_start_kind=auto ;;')
mutant("scripts/controller/pi-impl.sh", "pi-route-sidecar-pinned.sh",
       'volker|github2) route_record=deepseek-pi-$od_account ;;',
       'volker|github2) route_record=deepseek-pi ;;')
mutant("scripts/controller/pi-impl.sh", "pi-resume-recorded-account.sh",
       '        od_start_kind=resume', '        od_start_kind=auto-resume')
mutant("scripts/controller/pi-impl.sh", "pi-provider-account.sh",
       '    provider=opendesign_volker', '    provider=opendesign_github2')
mutant("scripts/controller/pi-impl.sh", "pi-source-auth-env.sh",
       '. "$HOME/.env.ai" >/dev/null 2>&1', 'true')
mutant("scripts/controller/pi-impl.sh", "pi-auth-file-guard.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then', 'if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-auth-source-guard.sh",
       'if ! . "$HOME/.env.ai" >/dev/null 2>&1; then', 'if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-volker-key-guard.sh",
       '    [[ -v OPENDESIGN_API_KEY_VOLKER ]] && (( ${#OPENDESIGN_API_KEY_VOLKER} > 0 )) || {\n      echo "OPENDESIGN_API_KEY_VOLKER is not set by \\$HOME/.env.ai" >&2\n      exit 2\n    }',
       ': # removed selected Volker key guard')
mutant("scripts/controller/pi-impl.sh", "pi-volker-key-xtrace.sh",
       '[[ -v OPENDESIGN_API_KEY_VOLKER ]] && (( ${#OPENDESIGN_API_KEY_VOLKER} > 0 )) || {',
       '[[ -n ${OPENDESIGN_API_KEY_VOLKER:-} ]] || {')
mutant("scripts/controller/pi-impl.sh", "pi-github2-key-xtrace.sh",
       '[[ -v OPENDESIGN_API_KEY_GITHUB2 ]] && (( ${#OPENDESIGN_API_KEY_GITHUB2} > 0 )) || {',
       '[[ -n ${OPENDESIGN_API_KEY_GITHUB2:-} ]] || {')
mutant("scripts/controller/pi-impl.sh", "pi-github2-key-guard.sh",
       '    [[ -v OPENDESIGN_API_KEY_GITHUB2 ]] && (( ${#OPENDESIGN_API_KEY_GITHUB2} > 0 )) || {\n      echo "OPENDESIGN_API_KEY_GITHUB2 is not set by \\$HOME/.env.ai" >&2\n      exit 2\n    }',
       ': # removed selected github2 key guard')
mutant("scripts/controller/pi-impl.sh", "pi-session-sidecar.sh",
       '  printf \'%s\\n\' "$thread_id" > "$out/$name.thread"',
       '  : # omit the pi session sidecar')
mutant("scripts/controller/pi-impl.sh", "pi-stdin-closed.sh",
       '    < /dev/null > "$stream" 2> "$out/$name.err")',
       '    > "$stream" 2> "$out/$name.err")')
mutant("scripts/controller/pi-impl.sh", "pi-402-outcome.sh",
       'if [[ $stop_reason == error && $usage_limited == 1 ]]; then',
       'if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-error-outcome.sh",
       'elif [[ $stop_reason == error ]]; then\n    echo ERROR',
       'elif false; then\n    echo ERROR')
mutant("scripts/controller/pi-impl.sh", "pi-nonzero-exit-outcome.sh",
       'elif [[ $rc != 0 ]]; then\n    echo ERROR',
       'elif false; then\n    echo ERROR')
# A settled stopReason that is not `stop` must not read as a success.
mutant("scripts/controller/pi-impl.sh", "pi-success-stop-only.sh",
       'elif [[ $stop_reason != stop ]]; then\n    echo INCOMPLETE',
       'elif false; then\n    echo INCOMPLETE')
# A full context window is its own class, decided BEFORE the generic
# not-a-stop branch: dropping the branch folds it back into INCOMPLETE, and
# moving it after the generic branch makes it unreachable.
mutant("scripts/controller/pi-impl.sh", "pi-context-full-missing.sh",
       'elif [[ $stop_reason == length ]]; then', 'elif false; then')
mutant("scripts/controller/pi-impl.sh", "pi-context-full-order.sh",
       '  elif [[ $stop_reason == length ]]; then\n'
       '    # The context window filled: a real end state of a long run, with a DIFFERENT\n'
       '    # remedy from an unattributable stream. Decided before the generic\n'
       '    # not-a-stop branch, and never continued.\n'
       '    echo CONTEXT-FULL\n', '')
# The retry loop must ignore every class but EMPTY-COMPLETION.
mutant("scripts/controller/pi-impl.sh", "pi-context-full-continued.sh",
       "while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do",
       "while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION || $outcome == CONTEXT-FULL ]]; do")
# A final message with a tool call and no text is not a success.
mutant("scripts/controller/pi-impl.sh", "pi-tool-call-no-text-success.sh",
       'elif [[ $has_text != 1 && $has_tool_call == 1 ]]; then', 'elif false; then')
mutant("scripts/controller/pi-impl.sh", "pi-account-block-run.sh",
       'block-run "$od_account"', 'record-thread "$thread_id"')
mutant("scripts/controller/pi-impl.sh", "pi-key-selected-export.sh",
       '    export OPENDESIGN_API_KEY_GITHUB2', '    : # selected key not exported')
mutant("scripts/controller/pi-impl.sh", "pi-key-volker-export.sh",
       '    export OPENDESIGN_API_KEY_VOLKER', '    : # selected key not exported')
mutant("scripts/controller/pi-impl.sh", "pi-key-unselected-unset.sh",
       '    unset OPENDESIGN_API_KEY_VOLKER', '    : # unselected key not removed')
# pi-overwatch: the wrapper's outcome classification, the bounded
# continuation, the liveness sidecar and the read-only liveness report. Each
# mutation turns a state the code must distinguish into its nearest neighbour.

# Classification: an empty final message must not fall through to SUCCESS.
mutant("scripts/controller/pi-impl.sh", "pi-empty-completion-class.sh",
       'elif [[ $has_text != 1 ]]; then',
       'elif false; then')
# A thinking-only message is the same class as an empty one: the check must
# look for text, not for content at all.
mutant("scripts/controller/pi-impl.sh", "pi-thinking-only-class.sh",
       '    "1" if last_text.strip() else "0", "1" if has_tool_call else "0",',
       '    "1" if last_text else "0", "1" if has_tool_call else "0",')
# Uncommitted work must not read as SUCCESS.
mutant("scripts/controller/pi-impl.sh", "pi-uncommitted-class.sh",
       'elif [[ -n $worktree_state ]]; then',
       'elif false; then')
# An unreadable worktree status must fail closed, not read as a clean tree.
mutant("scripts/controller/pi-impl.sh", "pi-status-fail-closed.sh",
       'elif ! worktree_state=$(git -C "$worktree" --no-optional-locks status --porcelain 2>/dev/null); then',
       'elif worktree_state=$(git -C "$worktree" --no-optional-locks status --porcelain 2>/dev/null) && false; then')
# A continuation must write into the run's own stream while it runs.
mutant("scripts/controller/pi-impl.sh", "pi-continue-live-stream.sh",
       'if invoke_pi "$continue_prompt" "$out/$name.jsonl" 0; then',
       'if invoke_pi "$continue_prompt" "$out/.$name.continue.tmp" 0; then')
# A named report that is missing must not read as SUCCESS.
mutant("scripts/controller/pi-impl.sh", "pi-no-report-class.sh",
       'elif [[ -n $report_path && ! -f $report_path ]]; then',
       'elif false; then')
# An unset report path skips the check; it must not be treated as a report that
# exists (the reverse mistake: refusing every run without a report).
mutant("scripts/controller/pi-impl.sh", "pi-report-unset-skips.sh",
       'if [[ -n $report_path ]]; then\n  [[ $report_path == /* ]]',
       'if [[ -z $report_path ]]; then\n  [[ $report_path == /* ]]')
# A worktree that cannot answer git is refused; it must never be read as clean.
mutant("scripts/controller/pi-impl.sh", "pi-worktree-git-fact.sh",
       'if ! git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1; then',
       'if false; then')
# The continuation bound: one extra attempt survives a two-attempt bound.
mutant("scripts/controller/pi-impl.sh", "pi-continue-bound.sh",
       'while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do',
       'while (( attempt <= retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do')
# The continuation is class-specific: no other outcome may be continued.
mutant("scripts/controller/pi-impl.sh", "pi-continue-class.sh",
       'while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do',
       'while (( attempt < retry_limit )) && [[ $outcome != SUCCESS ]]; do')
# An unparsable bound must be refused, not used.
mutant("scripts/controller/pi-impl.sh", "pi-continue-bound-validation.sh",
       "[[ $retry_limit =~ ^[0-9]+$ ]] || {",
       "true || {")
# The canonical continuation prompt is one file the wrapper must read.
mutant("scripts/controller/pi-impl.sh", "pi-continue-prompt-file.sh",
       'if [[ ! -r $continue_prompt_file ]]; then',
       'if false; then')
# Exhaustion is a distinct word, not the silent intermediate one.
mutant("scripts/controller/pi-impl.sh", "pi-continue-exhausted.sh",
       'if (( attempt > 0 )) && [[ $outcome == EMPTY-COMPLETION ]]; then',
       'if false; then')
# The liveness sidecar must be replaced by rename, never rewritten in place.
mutant("scripts/controller/pi-live.py", "pi-live-atomic.py",
       '        os.replace(temporary, target)',
       '        with target.open("wb") as sink, open(temporary, "rb") as source:\n'
       '            sink.write(source.read())\n'
       '        os.unlink(temporary)')
# A tool call that never ends must still be visible in the snapshot.
mutant("scripts/controller/pi-live.py", "pi-live-tool-in-flight.py",
       '        elif event_type == "tool_execution_start":',
       '        elif event_type == "tool_execution_started":')
# A value that cannot be read stays null rather than being invented.
mutant("scripts/controller/pi-live.py", "pi-live-missing-fact.py",
       '        "uncommitted_files": None if porcelain is None else len(',
       '        "uncommitted_files": 0 if porcelain is None else len(')
# Usage reported before a message completes is the only live token figure.
mutant("scripts/controller/pi-live.py", "pi-live-usage-trend.py",
       '        elif event_type == "message_update":',
       '        elif event_type == "message_update_disabled":')
# CTL-15: a 0 usage report is a report, never a measurement. Publishing it as the
# context size is the defect (a 200k run reads as an empty context).
mutant("scripts/controller/pi-live.py", "pi-live-tokens-zero-as-measurement.py",
       '        if total > 0:', '        if True:')
# CTL-18: progress evidence is stream growth OR kernel counter growth. Without
# the counters a long tool that keeps working but prints nothing reads as hung.
mutant("scripts/controller/pi-live.py", "pi-live-progress-stream-only.py",
       '            if previous is not None and survivor_delta(previous, sample):',
       '            if False:')
# CTL-18 (blocker 1): the summed set is the in-flight TOOL's subtree. The agent
# (a direct child of the wrapper) and the sidecar grow on their own every tick,
# so counting them makes a blocked tool read as working forever.
mutant("scripts/controller/pi-live.py", "pi-live-progress-counts-agent.py",
       '        found, cut = descendants(root, children, limit - len(pids))',
       '        found, cut = descendants(root, children, limit - len(pids))\n        pids.append(root)')
mutant("scripts/controller/pi-live.py", "pi-live-progress-counts-sidecar.py",
       '    pids: list[int] = []\n    truncated = False',
       '    pids: list[int] = [self_pid] if self_pid in table else []\n    truncated = False')
# Any difference is not progress: a decrease and a changed process set are
# baselines. Deciding on "it differs" reintroduces exactly the wrong direction.
mutant("scripts/controller/pi-live.py", "pi-live-progress-any-change.py",
       '        if new_cpu > old_cpu or new_io > old_io:',
       '        if new_cpu != old_cpu or new_io != old_io:')
# CTL-20: an epoch the sidecar could not re-read is NOT a measurement. Lifting
# an epoch older than the in-flight tool up to the tool's own start publishes the
# tool's AGE as its progress age, and HUNG-TOOL is then decided on wall-clock
# alone - the CTL-18 defect in its third form (the fixture's stream grew BEFORE
# the tool started, and the tool's subtree could not be located).
mutant("scripts/controller/pi-live.py", "pi-live-progress-lift-unmeasured.py",
       '    if (\n'
       '        tool_started is not None\n'
       '        and progress_epoch is not None\n'
       '        and progress_epoch < tool_started\n'
       '    ):\n'
       '        progress_epoch = None',
       '    if tool_started is not None and progress_epoch is not None:\n'
       '        if progress_epoch < tool_started:\n'
       '            progress_epoch = tool_started')
# An unreadable /proc must not license the stronger word either: clamping a null
# progress fact up to the tool's start is the same wall-clock rule where nothing
# could be read at all.
mutant("scripts/controller/pi-live.py", "pi-live-progress-null-clamped.py",
       '    if (\n'
       '        tool_started is not None\n'
       '        and progress_epoch is not None\n'
       '        and progress_epoch < tool_started\n'
       '    ):\n'
       '        progress_epoch = None',
       '    if tool_started is not None:\n'
       '        if progress_epoch is None or progress_epoch < tool_started:\n'
       '            progress_epoch = tool_started')
# The MEASURED flag is the reader's licence: a tick that could not read the
# tool's subtree carries the previous epoch forward, so it must say so or the
# carried leftover is read as a measurement.
mutant("scripts/controller/pi-live.py", "pi-live-progress-measured-kept.py",
       '            self.reason = "unread"\n            self.measured = False',
       '            self.reason = "unread"\n            self.measured = self.measured')
# A survivor's growth is progress; the SUM over a changed set is not.
mutant("scripts/controller/pi-live.py", "pi-live-progress-sum-delta.py",
       '    for member in previous.identity & sample.identity:',
       '    for member in sample.identity:')
# A set change must not reset the quiet clock either: resetting the baseline on
# every tick keeps a silently churning tool at LONG-TOOL forever.
mutant("scripts/controller/pi-live.py", "pi-live-progress-churn-resets-baseline.py",
       '            if self.baseline is None:\n'
       '                self.baseline = now',
       '            self.baseline = now')
# A recycled pid number is a different process, so its counters say nothing
# about the one measured before.
mutant("scripts/controller/pi-live.py", "pi-live-progress-pid-recycle.py",
       '        members[(pid, table.get(pid, (0, None))[1])] = (cpu, io)',
       '        members[(pid, None)] = (cpu, io)')
# A tool call's own announcement is a boundary, not evidence.
mutant("scripts/controller/pi-live.py", "pi-live-progress-tool-start.py",
       '        if tool_started:\n            # A tool call',
       '        if False:\n            # A tool call')
# The node cap must be stated, not silent.
mutant("scripts/controller/pi-live.py", "pi-live-progress-silent-cap.py",
       '        truncated = truncated or cut',
       '        truncated = False')
# A tool's own partial output is progress evidence.
mutant("scripts/controller/pi-live.py", "pi-live-tool-output-progress.py",
       '        elif event_type == "tool_execution_update":',
       '        elif event_type == "tool_execution_update_disabled":')
# The wrapper and this sidecar are never steering targets.
mutant("scripts/controller/pi-live.py", "pi-live-steering-includes-wrapper.py",
       '    steering_pids = [] if located is None else located[0]',
       '    steering_pids = [pid for pid in process_table() if pid > 0]')
# A quiet run must read as STALLED at the configured threshold.
mutant("scripts/controller/job-status.sh", "job-status-stall-threshold.sh",
       '    elif since_event is not None and since_event > stall_limit:',
       '    elif since_event is not None and since_event > stall_limit * 10:')
# A long tool call must read as HUNG-TOOL, and before STALLED.
mutant("scripts/controller/job-status.sh", "job-status-hung-tool.sh",
       '    elif tool_seconds is not None and tool_seconds > hung_limit:',
       '    elif False:')
# CTL-18: HUNG-TOOL is age AND absence of progress evidence. Deciding it on the
# tool age alone is exactly the defect (a heavy-but-working tool reads as hung).
mutant("scripts/controller/job-status.sh", "job-status-hung-without-progress.sh",
       '        if progress_age is not None and progress_age > hung_limit:',
       '        if True:')
# An unknown progress fact must not license the stronger word either.
mutant("scripts/controller/job-status.sh", "job-status-progress-unknown-as-hung.sh",
       '        if progress_age is not None and progress_age > hung_limit:',
       '        if progress_age is None or progress_age > hung_limit:')
# CTL-20: the reader must take the producer's MEASURED flag, not the epoch it
# carried: an unmeasured leftover printed as a progress age licenses HUNG-TOOL
# on the tool's age alone.
mutant("scripts/controller/job-status.sh", "job-status-progress-unmeasured-as-measured.sh",
       '    progress_unknown = progress_flag is not True',
       '    progress_unknown = False')
# Absence of the flag is not a measurement: a payload that predates it must not
# be read as if it had certified the epoch.
mutant("scripts/controller/job-status.sh", "job-status-progress-absent-flag-as-measured.sh",
       '    progress_unknown = progress_flag is not True',
       '    progress_unknown = progress_flag is False')
# The row must name the tool's command and the exact child pid to steer.
mutant("scripts/controller/job-status.sh", "job-status-hung-tool-command.sh",
       '        tool_text += f" cmd={tool_command[:80]}"',
       '        tool_text += ""')
mutant("scripts/controller/job-status.sh", "job-status-steer-target.sh",
       '        tool_text += f" steer={steer}"',
       '        tool_text += ""')
# CTL-16: before the run's own first commit the age belongs to the base commit,
# so it must not be printed as this run's.
mutant("scripts/controller/job-status.sh", "job-status-since-commit-unqualified.sh",
       '    elif ahead == 0:\n        commit_text = "no-commits-yet"',
       '    elif ahead == 0:\n        commit_text = f"{show(age(payload.get(\'last_commit_epoch\')))}s"')
# CTL-15: the last zero usage report must never stand in for the context size.
mutant("scripts/controller/job-status.sh", "job-status-tokens-last-report.sh",
       "        f\"tokens-last-report={show(payload.get('latest_total_tokens_last_report'))} \"",
       "        f\"tokens-last-report={show(payload.get('latest_total_tokens'))} \"")
# A dead wrapper with no .done must not read as ACTIVE.
mutant("scripts/controller/job-status.sh", "job-status-wrapper-dead.sh",
       '    wrapper_dead = verdict == "dead"',
       '    wrapper_dead = False')
# A finished run must be read from its marker, never from a live snapshot.
mutant("scripts/controller/job-status.sh", "job-status-finished.sh",
       '    if (runs_dir / f"{name}.done").is_file():',
       '    if False:')
# A run without a snapshot must say so instead of reading as healthy.
mutant("scripts/controller/job-status.sh", "job-status-no-live.sh",
       '    if not live_path.is_file():',
       '    if False:')
# The documented defaults (600 s quiet, 1800 s in one tool call) must hold when
# no environment override is given.
mutant("scripts/controller/job-status.sh", "job-status-default-stall.sh",
       'stall_seconds=${JOB_STATUS_STALL_SECONDS:-600}',
       'stall_seconds=${JOB_STATUS_STALL_SECONDS:-6000}')
mutant("scripts/controller/job-status.sh", "job-status-default-hung.sh",
       'hung_tool_seconds=${JOB_STATUS_HUNG_TOOL_SECONDS:-1800}',
       'hung_tool_seconds=${JOB_STATUS_HUNG_TOOL_SECONDS:-18000}')
# Liveness is the OWNER RECORD's verdict, not `kill -0` on the payload's pid:
# a recycled pid answers "alive" for a stranger.
mutant("scripts/controller/job-status.sh", "job-status-owner-record-ignored.sh",
       '    verdict, detail = owner_verdict(owner, wrapper_pid)', 
       '    verdict, detail = ("alive", None) if pid_alive(wrapper_pid) else ("dead", "gone")')
# A dead wrapper with a LIVE agent is its own state and must be named.
mutant("scripts/controller/job-status.sh", "job-status-orphan-agent-unnamed.sh",
       '        pid_text += " wrapper-dead-agent-alive"',
       '        pid_text += ""')
mutant("scripts/controller/job-status.sh", "job-status-orphan-agent-scan.sh",
       '        ancestors = agent_processes(worktree_of(name))',
       '        ancestors = []')
# The stale dead sidecar must not be the newest event: the stream's mtime is.
mutant("scripts/controller/job-status.sh", "job-status-dead-sidecar-event.sh",
       '            since_event = stream_age',
       '            since_event = since_event')
mutant("scripts/controller/job-check.sh", "job-check-thread-recovery.sh",
       '      thread_id=$(recover_thread_from_stream "$CONTROLLER_RUNS_DIR/$run.jsonl") || thread_id=\'\'',
       '      thread_id=\'\'')

# The states list and the steering action are user-facing facts, so a document
# that lost them must fail its own content check.
mutant("scripts/controller/README.md", "README-long-tool-state.md",
       '**`LONG-TOOL` and `HUNG-TOOL` are different words on purpose**',
       '**One age threshold covers every slow tool call**')
mutant("scripts/controller/README.md", "README-steering-action.md",
       'prints as `steer=` — never the wrapper',
       'prints as `steer=` — and the wrapper once it also looks idle')
mutant("scripts/controller/README.md", "README-died-state.md",
       '`DIED (wrapper pid <pid> gone, no .done)` as a red flag',
       '`RUNNING (no .done)` as a red flag')
mutant("scripts/controller/README.md", "README-pi-route-table.md",
       '| `deepseek-pi` | `pi-impl.sh` | Favored Open Design route; alternates available accounts. |',
       '| `deepseek-pi` | `pi-impl.sh` | Route. |')
mutant("scripts/controller/docs/DESIGN-GUIDE.md", "DESIGN-long-tool-rationale.md",
       '**Why `LONG-TOOL` exists as its own word.**',
       '**Why one word covers every slow tool call.**')
mutant("scripts/controller/docs/DESIGN-GUIDE.md", "DESIGN-pi-402-classification.md",
       'Pi 1.5.0 can report a provider 402 in the final assistant `message_end` event',
       'Pi 1.5.0 reports provider errors outside the final assistant `message_end` event')
mutant("scripts/controller/docs/CONSUMERS.md", "CONSUMER-steering-target.md",
       'Kill `8451`, not `8123`.',
       'Kill the wrapper `8123` to stop the tool.')
mutant("scripts/controller/docs/CONSUMERS.md", "CONSUMER-codex-marker-shape.md",
       '`EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k>`',
       '`EXIT=<n> AUTORESUME=<k>`')
mutant("scripts/controller/docs/CONSUMERS.md", "CONSUMER-pi-launch-route.md",
       '`deepseek-pi` is the favored Pi route',
       '`deepseek-pi` is unavailable in the Pi route selector.')
account_helper_mutant(
    'od-pi-402-reset-detection',
    'return coding_plan_reset(error)',
    'return False, None',
)
account_helper_mutant(
    'od-pi-session-event-id',
    'if isinstance(event, dict) and event.get("type") == "session":',
    'if False:',
)
account_helper_mutant(
    'od-pi-route-thread-map',
    '"deepseek-pi-volker": "volker",',
    '"deepseek-pi-volker": "github2",',
)
account_helper_mutant(
    'od-pi-blocked-account-selection',
    'return [account for account in ACCOUNTS if state[account]["blocked_until_epoch"] <= now]',
    'return list(ACCOUNTS)',
)
account_helper_mutant(
    'od-pi-resume-blocked-fallback',
    '    return other',
    '    return account',
)
account_helper_mutant(
    'od-pi-resume-both-blocked-exit',
    '        raise SystemExit(3)\n    return other',
    '        return account\n    return other',
)
account_helper_mutant(
    'od-pi-unknown-reset-default',
    'observed_until = time.time() + block_seconds',
    'observed_until = time.time()',
)

account_helper_mutant(
    "od-blocked-account-selection",
    'return [account for account in ACCOUNTS if state[account]["blocked_until_epoch"] <= now]',
    'return list(ACCOUNTS)',
)
account_helper_mutant(
    "od-prefer-expiring-window",
    'if prefer_expiring == "1":',
    'if False:',
)
account_helper_mutant(
    "od-preference-value-guard",
    'if prefer_expiring not in ("0", "1"):',
    'if False:',
)
account_helper_mutant(
    "od-insufficient-balance-detection",
    'if current.get("code") == "insufficient_balance":',
    'if False:',
)
account_helper_mutant(
    "od-unknown-reset-default-block",
    'if reset is None:\n            block_seconds = unknown_block_seconds()',
    'if False:\n            block_seconds = unknown_block_seconds()',
)
account_helper_mutant(
    "od-unknown-reset-knob",
    'raw = os.environ.get(\n        "CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS", str(DEFAULT_UNKNOWN_BLOCK_SECONDS)\n    )',
    'raw = str(DEFAULT_UNKNOWN_BLOCK_SECONDS)',
)
account_helper_mutant(
    "od-unknown-block-invalid-value-fallback",
    'seconds = DEFAULT_UNKNOWN_BLOCK_SECONDS',
    'seconds = 0',
)
account_helper_mutant(
    "od-preserve-later-block",
    '        previous_until = state[account]["blocked_until_epoch"]\n        blocked_until = max(previous_until, observed_until)',
    '        previous_until = state[account]["blocked_until_epoch"]\n        blocked_until = observed_until',
)
account_helper_mutant(
    "od-manual-resume-blocked-fallback",
    '    if state[account]["blocked_until_epoch"] <= now:\n        return account',
    '    if True:\n        return account',
)
account_helper_mutant(
    "od-catalog-guard-before-start-record",
    '            ensure_catalog_safe(catalogs[selected])\n            record_start(state, selected)\n            if thread_id:\n                state["threads"][thread_id] = selected',
    '            record_start(state, selected)\n            if thread_id:\n                state["threads"][thread_id] = selected',
)
account_helper_mutant(
    "od-resume-blocked-fallback",
    '    return other',
    '    return account',
)
account_helper_mutant(
    "od-reject-unknown-state-fields",
    'if not isinstance(entry, dict) or set(entry) - FIELDS:',
    'if not isinstance(entry, dict):',
)
account_helper_mutant(
    "od-numeric-state-fields",
    'if isinstance(value, bool) or not isinstance(value, (int, float)):',
    'if False:',
)
account_helper_mutant(
    "od-both-blocked-exit-code",
    '    if not candidates:\n        earliest = min(state[account]["blocked_until_epoch"] for account in ACCOUNTS)\n        print(\n            "both Open Design accounts are blocked; earliest reset is " + format_epoch(earliest),\n            file=sys.stderr,\n        )\n        raise SystemExit(3)',
    '    if not candidates:\n        earliest = min(state[account]["blocked_until_epoch"] for account in ACCOUNTS)\n        print(\n            "both Open Design accounts are blocked; earliest reset is " + format_epoch(earliest),\n            file=sys.stderr,\n        )\n        raise SystemExit(2)',
)
account_helper_mutant(
    "od-state-path-absolute-guard",
    'if not path.is_absolute():',
    'if False:',
)
account_helper_mutant(
    "od-gateway-text-detection",
    'match = GATEWAY_402.match(message)',
    'match = None',
)
account_helper_mutant(
    "od-gateway-text-anchor",
    'GATEWAY_402_PREFIX.match(message) is None',
    'GATEWAY_402.search(message) is None',
)
account_helper_mutant(
    "od-gateway-text-event-type",
    'if not isinstance(event, dict) or event.get("type") not in ("error", "turn.failed"):',
    'if not isinstance(event, dict):',
)
mutant("scripts/controller/lib.sh", "usage-retry-gateway-text.sh",
       'match = gateway_402.match(message) if isinstance(message, str) else None',
       'match = None')
mutant("scripts/controller/lib.sh", "usage-retry-gateway-anchor.sh",
       'match = gateway_402.match(message) if isinstance(message, str) else None',
       'match = gateway_402.search(message) if isinstance(message, str) else None')
mutant("scripts/controller/lib.sh", "usage-retry-gateway-event-type.sh",
       'if not isinstance(event, dict) or event.get("type") not in ("error", "turn.failed"):',
       'if not isinstance(event, dict):')
account_helper_mutant(
    "od-exclusive-state-flock",
    'fcntl.flock(stream.fileno(), fcntl.LOCK_EX)',
    'fcntl.flock(stream.fileno(), fcntl.LOCK_SH)',
)

queue_source = repo / "scripts/netcup/runner/host-sat-queue.sh"


def queue_with_lib_variant(name: str, before: str, after: str) -> Path:
    variant = root / f"mutants-{name}"
    controller_dir = variant / "scripts/controller"
    runner_dir = variant / "scripts/netcup/runner"
    controller_dir.mkdir(parents=True)
    runner_dir.mkdir(parents=True)
    queue = runner_dir / "host-sat-queue.sh"
    shutil.copy2(queue_source, queue)
    mutant("scripts/controller/lib.sh", "lib.sh", before, after, controller_dir)
    return queue


queue_with_lib_variant(
    "host-alias",
    'if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):',
    'if not isinstance(name, str):',
)
queue_with_lib_variant(
    "queue-lane-shape",
    'isinstance(x, str) and x.count("|") == 1 and "\\n" not in x and "\\r" not in x\n        and all(part.strip() for part in x.split("|", 1))',
    'isinstance(x, str) and "|" in x',
)
mutant("scripts/controller/gate-run.sh", "gate-run-profile-default.sh",
       'export RUN_GATE_PROFILE=${RUN_GATE_PROFILE:-off}', 'export RUN_GATE_PROFILE=${RUN_GATE_PROFILE:-on}')
mutant("scripts/controller/gate-run.sh", "gate-run-profile-override.sh",
       'export RUN_GATE_PROFILE=${RUN_GATE_PROFILE:-off}', 'export RUN_GATE_PROFILE=off')
mutant("scripts/controller/remote-gate.sh", "remote-gate-identity.sh",
       'api_check >/dev/null 2>&1 || die "provider identity', 'true || die "provider identity')
mutant("scripts/controller/remote-gate.sh", "remote-gate-allow-list.sh",
       '  *" $host "*) ;;', '  *) ;;')
mutant("scripts/controller/remote-gate.sh", "remote-gate-project-dir-guard.sh",
       '[[ $pdir != /* && $pdir != *..* ]] ||', 'true ||')
mutant("scripts/controller/remote-gate.sh", "remote-gate-slot-roundup.sh",
       'need=$(( (weight + unit - 1) / unit ));', 'need=$(( weight / unit ));')
mutant("scripts/controller/remote-gate.sh", "remote-gate-weight-min.sh",
       '    w = min(cands)', '    w = max(cands)')
mutant("scripts/controller/remote-gate.sh", "remote-gate-oom-delta.sh",
       '(( oomd > 0 ))', '(( oomd > 5 ))')
mutant("scripts/controller/remote-gate.sh", "remote-gate-commit-binding.sh",
       'if [[ ${checked:-0} -ge 1 ]]; then', 'if false; then')
mutant("scripts/controller/remote-gate.sh", "remote-gate-slot-descriptors.sh",
       '( close_slots; cd "$wt" &&', '( cd "$wt" &&')
mutant("scripts/controller/remote-gate.sh", "remote-gate-base-forward.sh",
       '[[ -z $base ]] || run_args+=(--base "$base")', ': # base dropped')
mutant("scripts/controller/remote-gate.sh", "remote-gate-api-override.sh",
       'if [[ -n ${CONTROLLER_REMOTE_API_CHECK:-} ]]; then', 'if false; then')
mutant("scripts/controller/remote-gate.sh", "remote-gate-runner-ref.sh",
       '    die "cannot read scripts/netcup/runner/remote-gate.py from $runner_ref" 3', '    : # unreadable runner ignored')
mutant("scripts/controller/remote-gate.sh", "remote-gate-base-passthrough.sh",
       '    --base|--base=*)', '    --base-never|--base-never=*)')
mutant("scripts/controller/remote-gate.sh", "remote-gate-base-twice.sh",
       "      [[ -z $base ]] || die '--base given twice (option and pass-through)'", '      : # a second base is accepted')
for _name, _before, _after in (
    ("old-env-silent", '[[ -z ${!old_name:-} ]] || echo', 'true || echo'),
    ("old-env-one-dropped", " REMOTE_GATE_DRYRUN:CONTROLLER_REMOTE_DRYRUN \\\n", " \\\n"),
    ("weights-unreadable-silent", "except FileNotFoundError:\n    pass", "except OSError:\n    pass"),
    ("weights-missing-warns", "except FileNotFoundError:\n    pass", "except KeyError:\n    pass"),
    ("repo-name-default", '"${CONTROLLER_REMOTE_REPO_NAME:-vbpub}"', '"${CONTROLLER_REMOTE_REPO_NAME:-}"'),
    ("snapshot-fixed-name", 'rg_tmp=$(mktemp "$rg.XXXXXX") || die "cannot create a temporary runner snapshot beside $rg" 3', 'rg_tmp=$rg.new'),
    ("snapshot-leftover", '    rm -f -- "$rg_tmp"\n    die "cannot read scripts', '    die "cannot read scripts'),
):
    mutant("scripts/controller/remote-gate.sh", f"remote-gate-{_name}.sh", _before, _after)
# chain-run.sh: the generic chain wrapper that replaces the per-package chain-*.sh files.
chain_gate_run = 'gate_run=${CONTROLLER_GATE_RUN_SCRIPT:-$SCRIPT_DIR/gate-run.sh}'
mutant("scripts/controller/chain-run.sh", "chain-run-log-name.sh",
       'log=$CONTROLLER_LOGS_DIR/$prefix-$lane.log', 'log=$CONTROLLER_LOGS_DIR/$lane.log')
mutant("scripts/controller/chain-run.sh", "chain-run-remote-log-name.sh",
       'log=$CONTROLLER_LOGS_DIR/$prefix-remote-$lane.log', 'log=$CONTROLLER_LOGS_DIR/$prefix-$lane.log')
mutant("scripts/controller/chain-run.sh", "chain-run-marker-text.sh",
       'write_marker CHAIN-DONE', 'write_marker DONE')
mutant("scripts/controller/chain-run.sh", "chain-run-stale-marker.sh",
       'rm -f -- "$marker"', ': # stale marker kept')
mutant("scripts/controller/chain-run.sh", "chain-run-head-pin.sh",
       '[[ -n $want && $want == "$head" ]] ||', 'true ||')
mutant("scripts/controller/chain-run.sh", "chain-run-dirty-tree.sh",
       "[[ -z $dirty ]] ||", "true ||")
mutant("scripts/controller/chain-run.sh", "chain-run-stop-on-fail.sh",
       'if (( lane_rc != 0 && stop_on_fail )); then', 'if false; then')
mutant("scripts/controller/chain-run.sh", "chain-run-stop-without-flag.sh",
       'if (( lane_rc != 0 && stop_on_fail )); then', 'if (( lane_rc != 0 )); then')
mutant("scripts/controller/chain-run.sh", "chain-run-remote-base.sh",
       '[[ -z $base ]] || cmd+=(--base "$base")', ': # base dropped')
mutant("scripts/controller/chain-run.sh", "chain-run-remote-commit.sh",
       '"$subdir" "$lane" "$head")', '"$subdir" "$lane" HEAD)')
mutant("scripts/controller/chain-run.sh", "chain-run-remote-head-lookup.sh",
       'if [[ -n $head_pin ]] || (( want_remote )); then', 'if [[ -n $head_pin ]]; then')
mutant("scripts/controller/chain-run.sh", "chain-run-remote-host-ambiguity.sh",
       '(( ${#allowed[@]} == 1 )) ||', 'true ||')
mutant("scripts/controller/chain-run.sh", "chain-run-launcher-check.sh",
       '(( ! want_local )) || [[ -x $gate_run ]] || refuse', 'true || refuse')
mutant("scripts/controller/chain-run.sh", "chain-run-subdir-guard.sh",
       '[[ $subdir != /* && $subdir != *..* ]] ||', 'true ||')
mutant("scripts/controller/chain-run.sh", "chain-run-lane-name-guard.sh",
       '[[ $spec =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || refuse', 'true || refuse')
mutant("scripts/controller/chain-run.sh", "chain-run-lane-cwd.sh",
       '(cd -- "$worktree/$subdir" && "$gate_run" "$lane")', '(cd -- "$worktree" && "$gate_run" "$lane")')
mutant("scripts/controller/chain-run.sh", "chain-run-default-launcher.sh",
       chain_gate_run, 'gate_run=${CONTROLLER_GATE_RUN_SCRIPT:-$SCRIPT_DIR/gate-run-missing.sh}')
mutant("scripts/controller/chain-run.sh", "chain-run-export-image.sh",
       chain_gate_run, 'export RUN_GATE_TESTER_UNIFIED_IMAGE=pinned-tag\n' + chain_gate_run)
mutant("scripts/controller/chain-run.sh", "chain-run-export-socket.sh",
       chain_gate_run, 'export CGPROFILE_CONTROL_SOCKET=/run/profiler.sock\n' + chain_gate_run)
mutant("scripts/controller/chain-run.sh", "chain-run-profile-default.sh",
       chain_gate_run, 'export RUN_GATE_PROFILE=off\n' + chain_gate_run)


# ctl-doctor.sh: one hand mutant per check; the lib.sh read-only/configured-path mutants ship beside their own doctor copy.
def doctor_with_lib_mutant(name: str, before: str, after: str) -> Path:
    variant = controller / name
    variant.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "scripts/controller/ctl-doctor.sh", variant / "ctl-doctor.sh")
    mutant("scripts/controller/lib.sh", "lib.sh", before, after, variant)
    return variant / "ctl-doctor.sh"


for _name, _before, _after in (
    ("tool-flock-severity", "tool_check flock FAIL util-linux", "tool_check flock WARN util-linux"),
    ("tool-docker-severity", "tool_check docker WARN 'a docker CLI'", "tool_check docker FAIL 'a docker CLI'"),
    ("home-missing", "if [[ ! -d $home_in ]]; then", "if false; then"),
    ("env-missing", "if [[ ! -f $env_file ]]; then", "if false; then"),
    ("env-syntax", 'if ! bash -n "$env_file" 2>/dev/null; then', "if false; then"),
    ("env-stray", "if [[ -n $stray ]]; then", "if false; then"),
    ("hosts-missing", "if [[ ! -f $file ]]; then", "if false; then"),
    ("hosts-ssh-shape", "if not strings(row.get(key)):", "if False:"),
    ("hosts-guard", 'if not isinstance(row.get("guard"), str) or not row["guard"].strip():', "if False:"),
    ("hosts-lanes-shape", 'or not all(isinstance(x, str) and x.count("|") == 1 and', "or not all(isinstance(x, str) and"),
    ("hosts-lanes-warn", "if lanes is None:", "if False:"),
    ("hosts-api-exec", "if not full or not os.path.isfile(full) or not os.access(full, os.X_OK):", "if False:"),
    ("hosts-key-missing", 'if not os.path.isfile(full):\n                out("FAIL", f"{where}:key"', 'if False:\n                out("FAIL", f"{where}:key"'),
    ("hosts-key-mode", "elif os.stat(full).st_mode & 0o077:", "elif False:"),
    ("hosts-pin", 'if pin != "/dev/null" and not os.path.isfile(full):', "if False:"),
    ("hosts-strict", "if not strict:", "if False:"),
    ("hosts-nopin", "if not pins:", "if False:"),
    ("hosts-leak", '"ssh does not set StrictHostKeyChecking=yes"', 'f"ssh does not set StrictHostKeyChecking=yes {argv}"'),
    ("allow-unknown", "if name not in hosts:", "if False:"),
    ("allow-unset", "if not names:", "if False:"),
    ("remote-file-split", 'if [[ $remote_file == "$hosts_file" ]]; then', "if true; then"),
    ("image-absent", "elif [[ $inspect_err == *'No such image'* ]]; then", "elif false; then"),
    ("image-exact-name", """docker image inspect --format '{{.Id}}' "$image" 2>&1""", """docker image inspect --format '{{.Id}}' "${image%%:*}" 2>&1"""),
    ("image-unreachable", 'record WARN image "docker is unreachable', 'record FAIL image "docker is unreachable'),
    ("image-no-docker", "elif ! command -v docker >/dev/null 2>&1; then", "elif false; then"),
    ("image-toml", "if [[ -z $image ]]; then", "if false; then"),
    ("pinned-warn", "if [[ -n $pinned_files ]]; then", "if false; then"),
    ("pinned-logs", "--exclude-dir=logs ", ""),
    ("pinned-scan", '[[ ${#scan_dirs[@]} -eq 0 ]] || scan_roots+=("${scan_dirs[@]}")', ":"),
    ("pinned-home", 'scan_roots=("$CONTROLLER_HOME")', "scan_roots=()"),
    ("profile-default", "${RUN_GATE_PROFILE:-off} (gate-run.sh", "${RUN_GATE_PROFILE:-on} (gate-run.sh"),
    ("dir-missing", "if [[ ! -d $2 ]]; then", "if false; then"),
    ("dir-writable", "elif [[ ! -w $2 ]]; then", "elif false; then"),
    ("symlink-detect", "[[ -L $p ]] && return 0", "false && return 0"),
    ("residency-ignore", 'git -C "$top" check-ignore -q -- "$real" 2>/dev/null && ignored=1', "ignored=1"),
    ("residency-outside", "if [[ -z $top ]]; then", "if true; then"),
    ("residency-runs", 'if [[ $CONTROLLER_RUNS_DIR != "$CONTROLLER_HOME"/* ]]; then', "if false; then"),
    ("json", "if (( json )); then", "if false; then"),
    ("exit-code", "(( fails == 0 )) || exit 1", ":"),
    ("usage-exit", """unknown argument '$1'" >&2; echo 'usage: ctl-doctor.sh [--json] [--scan <dir>]...' >&2; exit 2 ;;""",
     """unknown argument '$1'" >&2; echo 'usage: ctl-doctor.sh [--json] [--scan <dir>]...' >&2; exit 0 ;;"""),
):
    mutant("scripts/controller/ctl-doctor.sh", f"ctl-doctor-{_name}.sh", _before, _after)
doctor_with_lib_mutant("doctor-lib-readonly-mkdir",
                       '  [[ -n ${CONTROLLER_INIT_READONLY:-} ]] || mkdir -p -- "$CONTROLLER_LOGS_DIR"',
                       '  mkdir -p -- "$CONTROLLER_LOGS_DIR"')
doctor_with_lib_mutant("doctor-lib-readonly-resolve",
                       "if [[ -n ${CONTROLLER_INIT_READONLY:-} && ! -d $target ]]; then return 0; fi",
                       "if false; then return 0; fi")
doctor_with_lib_mutant("doctor-lib-configured-home",
                       "  CONTROLLER_HOME_CONFIGURED=$CONTROLLER_HOME\n  controller_init_resolve CONTROLLER_HOME\n",
                       "  controller_init_resolve CONTROLLER_HOME\n  CONTROLLER_HOME_CONFIGURED=$CONTROLLER_HOME\n")
doctor_with_lib_mutant("doctor-lib-configured-runs",
                       "  CONTROLLER_RUNS_DIR_CONFIGURED=$CONTROLLER_RUNS_DIR\n",
                       "  CONTROLLER_RUNS_DIR_CONFIGURED=\n")

# ctl-state-backup.sh: one hand mutant per guard and per selection rule.
for _name, _before, _after in (
    ("cap", "(( size > cap && ! include_large ))", "(( size > cap && include_large ))"),
    ("jsonl", " ! -name '*.jsonl'", ""),
    ("git-dir", " ! -path '*/.git/*'", ""),
    ("work-tree", 'if git -C "$anchor" rev-parse --is-inside-work-tree', "if false && git -C \"$anchor\" rev-parse --is-inside-work-tree"),
    ("unset", "[[ -n $backup_dir ]] || die", "true || die"),
    ("relative", "[[ $backup_dir == /* ]] || die", "true || die"),
    ("list-only", "if (( list_only )); then", "if false; then"),
    ("mode", 'chmod 600 -- "$tmp"', 'chmod 644 -- "$tmp"'),
    ("follow-links", "tar -${mode}f", "tar -${mode}hf"),
    ("sidecar", "printf '%s  %s\\n' \"$sum\"", "printf '%s  %s\\n' \"0$sum\""),
    ("verify-compare", '[[ $(cut -d\' \' -f1 "$verify.sha256") == "$actual" ]] || die', "true || die"),
    ("runs-state", " -o -name '*.state.md'", ""),
    ("remote-summaries", "-o -name meta.json \\)", "-o -name '*' \\)"),
    ("empty", "(( ${#files[@]} > 0 )) || die", ": || die"),
    ("overwrite", "while [[ -e $final ]]; do", "while false; do"),
    ("link-targets", '"flags=rh;s,^\\./,,;s,^,$label/,"', '"s,^\\./,,;s,^,$label/,"'),
    ("hardlink-targets", '"flags=rh;s,^\\./,,;s,^,$label/,"', '"flags=r;s,^\\./,,;s,^,$label/,"'),
):
    mutant("scripts/controller/ctl-state-backup.sh", f"ctl-state-backup-{_name}.sh", _before, _after)
