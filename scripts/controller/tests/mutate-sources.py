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
# The doctor runs ctl-hygiene.sh from its own directory: a doctor mutant must keep that default path working.
shutil.copy2(repo / "scripts/controller/ctl-hygiene.sh", controller / "ctl-hygiene.sh")
shutil.copy2(repo / "scripts/controller/opendesign-accounts.py", controller / "opendesign-accounts.py")
shutil.copy2(repo / "scripts/controller/pi-live.py", controller / "pi-live.py")
shutil.copy2(repo / "scripts/controller/job-status.sh", controller / "job-status.sh")

# A mutant wrapper must be judged on the change it carries, not on a missing
# sibling file: the continuation prompt and the run-mode rule files are read from
# SCRIPT_DIR, and review mode reads the one read-only argv definition.
for _sibling in (
    "continue-empty-completion.md",
    "implementer-prompt-rules.md",
    "review-prompt-rules.md",
    "pi-review-argv",
):
    shutil.copy2(repo / "scripts/controller" / _sibling, controller / _sibling)


def account_helper_mutant(name: str, before: str, after: str) -> Path:
    source = repo / "scripts/controller/opendesign-accounts.py"
    original = source.read_text(encoding="utf-8")
    count = original.count(before)
    if count != 1:
        raise SystemExit(f"{source}: expected one mutation target, found {count}: {before!r}")
    variant = controller / name
    variant.mkdir(parents=True)
    for filename in ("lib.sh", "codex-impl.sh", "pi-impl.sh", "resume-usage-limited.sh", "finisher-generic.md",
                     "continue-empty-completion.md", "implementer-prompt-rules.md"):
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

def pi_review_mutant(name: str, before: str, after: str) -> Path:
    """A pi-review.sh mutant keeps its siblings: the launcher runs
    "$SCRIPT_DIR/pi-impl.sh", so a copy without them would fail for every check
    and be "killed" by a missing file instead of by the mutation."""
    source = repo / "scripts/controller/pi-review.sh"
    original = source.read_text(encoding="utf-8")
    if original.count(before) != 1:
        raise SystemExit(f"{source}: expected one mutation target, found {original.count(before)}: {before!r}")
    variant = controller / name
    variant.mkdir(parents=True)
    for filename in ("lib.sh", "pi-impl.sh", "pi-live.py", "opendesign-accounts.py",
                     "continue-empty-completion.md", "review-prompt-rules.md",
                     "implementer-prompt-rules.md", "pi-review-argv"):
        shutil.copy2(repo / "scripts/controller" / filename, variant / filename)
    # pi-impl.sh refuses to launch without its signal-guard shims beside it.
    shutil.copytree(repo / "scripts/controller/pi-shims", variant / "pi-shims")
    target = variant / "pi-review.sh"
    target.write_text(original.replace(before, after, 1), encoding="utf-8")
    target.chmod(0o755)
    return target


pi_review_mutant("pi-review-account-inverted",
       "    volker) review_account=github2 ;;\n    github2) review_account=volker ;;",
       "    volker) review_account=volker ;;\n    github2) review_account=github2 ;;")
pi_review_mutant("pi-review-head-only",
       '  [[ $TREE_SYMHEAD == "$before_symhead" ]] || changed="${changed:+$changed, }head-branch"',
       '  :')
pi_review_mutant("pi-review-index-only",
       '  [[ $TREE_INDEX == "$before_index" ]] || changed="${changed:+$changed, }index-flags"',
       '  :')
pi_review_mutant("pi-review-exclude-only",
       '  [[ $TREE_EXCLUDE == "$before_exclude" ]] || changed="${changed:+$changed, }exclude"',
       '  :')
pi_review_mutant("pi-review-untracked-content-only",
       '  [[ $TREE_UNTRACKED == "$before_untracked" ]] || changed="${changed:+$changed, }untracked-content"',
       '  :')
pi_review_mutant("pi-review-diff-only",
       '  [[ $TREE_DIFF == "$before_diff" ]] || changed="${changed:+$changed, }diff"',
       '  :')
# One definition of the review's read-only set, in its own file: the mutant
# widens it, and a review launched through pi-impl.sh's review mode must then
# carry a shell -- so the check that asserts the tool list kills it.
def pi_review_argv_mutant(name: str, before: str, after: str) -> Path:
    variant = controller / name
    variant.mkdir(parents=True)
    for filename in ("lib.sh", "pi-impl.sh", "pi-live.py", "opendesign-accounts.py",
                     "continue-empty-completion.md", "pi-review.sh",
                     "review-prompt-rules.md", "implementer-prompt-rules.md"):
        shutil.copy2(repo / "scripts/controller" / filename, variant / filename)
    shutil.copytree(repo / "scripts/controller/pi-shims", variant / "pi-shims")
    original = (repo / "scripts/controller/pi-review-argv").read_text(encoding="utf-8")
    if original.count(before) != 1:
        raise SystemExit(f"pi-review-argv: expected one mutation target, found {original.count(before)}: {before!r}")
    (variant / "pi-review-argv").write_text(original.replace(before, after, 1), encoding="utf-8")
    return variant / "pi-review.sh"


pi_review_argv_mutant("pi-review-tools-wide",
                     "read,grep,find,ls",
                     "read,grep,find,ls,bash")
pi_review_mutant("pi-review-status-no-uall",
       '  status=$(git -C "$wt" --no-optional-locks status --porcelain=v1 -uall 2>/dev/null) || return 1',
       '  status=$(git -C "$wt" --no-optional-locks status --porcelain=v1 2>/dev/null) || return 1')
pi_review_mutant("pi-review-budget-off",
       '  if (( now_epoch - started_epoch >= review_max_seconds )); then',
       '  if (( 0 )); then')
pi_review_mutant("pi-review-budget-bytes-off",
       '      if [[ $stream_bytes =~ ^[0-9]+$ ]] && (( stream_bytes >= review_max_bytes )); then',
       '      if (( 0 )); then')
pi_review_mutant("pi-review-stale-report-overwrite",
       'if report_path_taken; then\n  echo "the review report already exists',
       'if false; then\n  echo "the review report already exists')
# M1' (r2): the two post-launch write branches must record REVIEW-NO-REPORT in
# the marker before exiting 2, or their `EXIT=0 OUTCOME=SUCCESS` line 1 certifies
# a review whose report was never saved. The mutant drops the `cp` failure
# branch body, so a failed write falls through to exit 0: the test's rc-2 and
# marker assertions both catch it. The sibling branch (the pre-write re-check)
# needs no mutant of its own -- its test asserts rc 2 AND that the racing
# writer's file is untouched, which the same mutation would break -- so this
# suite pays one extra full run for the pair, not two.
pi_review_mutant("pi-review-report-write-failure-silent",
       'if ! cp -- "$CONTROLLER_RUNS_DIR/$name.last.txt" "$review_path"; then',
       'if false; then')
# M1: the report is persisted only for a settled-success class. The mutant keeps
# the old rule (any text in last.txt) and must be killed by the CONTEXT-FULL
# fixture, which carries partial text and a zero pi exit.
pi_review_mutant("pi-review-report-on-any-text",
       'if (( launch_rc == 0 )) && [[ $run_outcome == SUCCESS || $run_outcome == UNCOMMITTED ]] &&',
       'if (( launch_rc == 0 )) && [[ 1 == 1 ]] &&')
# M2: the earlier review's before-state must survive a retried run name. The
# mutant drops the refusal, so the material is re-rendered (and pi-impl then
# refuses), which the byte-identical assertion catches.
pi_review_mutant("pi-review-material-overwrite",
       'if [[ -e $REVIEW_MATERIAL_DIR/before-state.txt ]]; then',
       'if false; then')
# M3: the process group must be VERIFIED before it is signalled. The mutant
# signals the group unconditionally, which the pgid shim makes an ESRCH: the log
# then claims a group stop it never made, and the pid-only message is gone.
pi_review_mutant("pi-review-group-unverified",
       'if [[ $launch_pgid =~ ^[0-9]+$ ]] && (( launch_pgid == launch_pid )); then',
       'if true; then')
# NOTE: the "a launch not confirmed stopped must not be waited on" branch has no
# mutant. Nothing in this suite can construct a child that survives TERM+KILL
# (a KILL to the wrapper's own child always works), so a mutant of it would
# survive on behaviour the suite cannot observe -- a false green, which is worse
# than an uncertified safety valve. The branch is stated in the script header
# and the README instead.
pi_review_mutant("pi-review-foreign-marker",
       'if ! marker_is_ours && [[ $marker_before != absent ]]; then',
       'if false; then')
pi_review_mutant("pi-review-realpath-fallback",
       'review_path=$(realpath -m -- "$report_path") || {',
       'review_path=$(realpath -m -- "$report_path") || review_path=$report_path; false || {')

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
       '  rm -f -- "$out/$name.done" "$out/$name.last.txt"\n  codex_launched=1',
       '  codex_launched=1')
mutant("scripts/controller/codex-impl.sh", "codex-resume-stale-last-text.sh",
       'rm -f -- "$out/$name.done" "$out/$name.last.txt"',
       'rm -f -- "$out/$name.done"')
mutant("scripts/controller/codex-impl.sh", "codex-finish-stale-last-text.sh",
       '  if (( codex_launched == 1 )); then\n'
       '    codex_extract_last_text "$out/$name.jsonl" "$out/$name.last.txt"\n'
       '    if [[ ! -e $out/$name.done ]]; then',
       '  if (( codex_launched == 1 )); then\n'
       '    if [[ ! -e $out/$name.done ]]; then')
mutant("scripts/controller/codex-impl.sh", "codex-term-trap.sh",
       "trap 'exit 143' TERM", ': # TERM does not run codex_finish')
mutant("scripts/controller/codex-impl.sh", "codex-effort-vocabulary.sh",
       'case $effort in\n  minimal|low|medium|high|xhigh|max) ;;\n  *)\n    echo \'CODEX_EFFORT must be one of minimal, low, medium, high, xhigh, max\' >&2\n    exit 2\n    ;;\nesac',
       ': # CODEX_EFFORT vocabulary is not validated')
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
    shutil.copy2(repo / "scripts/controller/implementer-prompt-rules.md", variant / "implementer-prompt-rules.md")
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
mutant("scripts/controller/codex-impl.sh", "codex-pinned-route-effort.sh",
       '  deepseek-volker|deepseek-github2)\n'
       '    od_account=${route#deepseek-}\n'
       '    route_record=$route\n'
       '    od_start_kind=pinned\n'
       '    default_model=deepseek-v4.1-flash-eco\n'
       '    default_effort=max',
       '  deepseek-volker|deepseek-github2)\n'
       '    od_account=${route#deepseek-}\n'
       '    route_record=$route\n'
       '    od_start_kind=pinned\n'
       '    default_model=deepseek-v4.1-flash-eco\n'
       '    default_effort=high')
mutant("scripts/controller/opencode-impl.sh", "opencode-default-provider.sh",
       'provider=${OPENCODE_PROVIDER:-opendesign_github2}', 'provider=${OPENCODE_PROVIDER:-opendesign}')
mutant("scripts/controller/README.md", "README-home-override-route.md",
       '`CODEX_HOME_OVERRIDE` wins on Luna and an explicit `CODEX_ROUTE=sol`',
       '`CODEX_HOME_OVERRIDE` is honoured on every route.')
mutant("scripts/controller/rules.md", "RULES-stale-last-text.md",
       'Every Codex exec path derives `.last.txt` from that invocation\'s stream on\n   normal and signal exits; verify the result from git before publication.',
       'A\n   resumed Codex run\'s `.last.txt` may be stale: verify its result from git.')
mutant("scripts/controller/codex-impl.sh", "codex-autoresume-bound.sh",
       'while (( auto_count < auto_limit )) && reasoning_end',
       'while (( auto_count <= auto_limit )) && reasoning_end')
mutant("scripts/controller/codex-impl.sh", "codex-autoresume-attempt-stream.sh",
       '  # The whole invocation stream is the source of truth. A failed or empty later\n'
       '  # attempt must not erase a completed agent message that this invocation\'s\n'
       '  # primary child (or an earlier continuation) already produced.\n'
       '  codex_extract_last_text "$out/$name.jsonl" "$out/$name.last.txt"',
       '  codex_extract_last_text "$attempt_stream" "$out/$name.last.txt"')
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
       'case $route in sol|luna|deepseek|deepseek-volker|deepseek-github2) ;; *) echo "SKIP $run (invalid route sidecar)"; continue ;; esac',
       'case $route in *) ;; esac')
mutant("scripts/controller/resume-usage-limited.sh", "resume-sol-route.sh",
       'case $route in sol|luna|deepseek|deepseek-volker|deepseek-github2) ;; *) echo "SKIP $run (invalid route sidecar)"; continue ;; esac',
       'case $route in luna|deepseek|deepseek-volker|deepseek-github2) ;; *) echo "SKIP $run (invalid route sidecar)"; continue ;; esac')
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
       '[[ ${slot_lock[slot]} != "${slot_lock[other]}" ]] || {', 'true || {')
mutant("scripts/controller/gate-run.sh", "gate-run-absolute-locks.sh",
       '[[ -n $lock && $lock == /* ]] || {', '[[ -n $lock ]] || {')
mutant("scripts/controller/gate-run.sh", "gate-run-poll-second-slot.sh",
       '    poll_lock "$slot" "$@"\n    rc=$?',
       '    if [[ $slot == 2 ]]; then continue; fi\n    poll_lock "$slot" "$@"\n    rc=$?')
mutant("scripts/controller/gate-run.sh", "gate-run-refusal-example.sh",
       'gate-run: CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK must both be configured as absolute paths; copy both names from controller.local.env.example and export them before running',
       'gate-run: configure the gate lock paths before running')
for _name, _before, _after in (
    ("slots-range", '[[ $slots =~ ^[12345678]$ ]] || {', '[[ $slots =~ ^[0-9]+$ ]] || {'),
    ("slots-default", 'slots=${CONTROLLER_GATE_SLOTS:-2}', 'slots=${CONTROLLER_GATE_SLOTS:-3}'),
    ("one-slot-needs-gate2", 'if (( slots == 1 )); then\n  required_locks', 'if false; then\n  required_locks'),
    ("one-slot-message", 'gate-run: CONTROLLER_GATE_LOCK must be configured as an absolute path (CONTROLLER_GATE_SLOTS=1 uses one slot)',
     'gate-run: CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK must be set (CONTROLLER_GATE_SLOTS=1 uses one slot)'),
    ("lock-dir-absolute", '(( slots < 3 )) || [[ -z ${CONTROLLER_GATE_LOCK_DIR:-} || $CONTROLLER_GATE_LOCK_DIR == /* ]] || {', 'true || {'),
    ("lock-dir-unused-below-three", '(( slots < 3 )) || [[ -z ${CONTROLLER_GATE_LOCK_DIR:-}', '(( slots < 1 )) || [[ -z ${CONTROLLER_GATE_LOCK_DIR:-}'),
    ("distinct-detail", '(( slots == 2 )) || echo "gate-run: slots $slot and $other both use ${slot_lock[slot]}" >&2', ':'),
    ("two-slot-distinct-oneline", '(( slots == 2 )) || echo "gate-run: slots', '(( slots == 9 )) || echo "gate-run: slots'),
    ("first-pass-last-slot", 'for (( slot = 1; slot <= slots; slot++ )); do\n  run_slot', 'for (( slot = 1; slot < slots; slot++ )); do\n  run_slot'),
    ("poll-last-slot", 'for (( slot = 1; slot <= slots; slot++ )); do\n    poll_lock', 'for (( slot = 1; slot < slots; slot++ )); do\n    poll_lock'),
    ("poll-open-last-slot", 'for (( slot = 1; slot <= slots; slot++ )); do\n  # The message names', 'for (( slot = 1; slot < slots; slot++ )); do\n  # The message names'),
    ("fd-shared", '[7]=14 [8]=15)', '[7]=14 [8]=14)'),
    ("fd-first-slots", 'slot_fd=([1]=9 [2]=8 ', 'slot_fd=([1]=9 [2]=9 '),
    ("slot-label", 'slot_label[slot]=gate$slot.lock', 'slot_label[slot]=gate2.lock'),
    ("lock-held-while-running", '    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  eval', '    eval "exec ${fd}>&-"\n    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  eval'),
    ("wait-two", '  2) wait_text="both slots busy,', '  2) wait_text="all 2 slots busy,'),
    ("wait-many", '  *) wait_text="all $slots slots busy,', '  *) wait_text="both slots busy,'),
    ("wait-one", '  1) wait_text="the only slot is busy,', '  1) wait_text="both slots busy,'),
    ("progress-many", "busy_text='are all busy'", "busy_text='are both busy'"),
    ("progress-two", "busy_text='are both busy'", "busy_text='are all busy'"),
    ("progress-one", "busy_text='is busy'", "busy_text='are busy'"),
    ("labels-join", 'if (( slot == slots )); then labels_text+=" and ${slot_label[slot]}"; else labels_text+=", ${slot_label[slot]}"; fi',
     'labels_text+=" and ${slot_label[slot]}"'),
    ("exit-code", '    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  [[ $rc == 75 ]]', '    nice -n 10 run-gate "$@"\n    return 0\n  else\n    rc=$?\n  fi\n  [[ $rc == 75 ]]'),
    # CTL-57: the memory-PSI admission after a slot is held
    ("psi-threshold", 'BEGIN { exit !(m + 0 >= 10) }', 'BEGIN { exit !(m + 0 > 10) }'),
    ("psi-window", 'sub("avg60=", "", $3); print $3', 'sub("avg10=", "", $2); print $2'),
    ("psi-unreadable-quiet", "    ''|*[!0-9.]*|.*|*.|*.*.*) return 2 ;;", "    ''|*[!0-9.]*|.*|*.|*.*.*) return 1 ;;"),
    ("psi-unreadable-waits", '(( state != 2 )) || memory_refuse_unreadable\n  (( state == 0 )) || return 0',
     '(( state != 2 )) || state=0\n  (( state == 0 )) || return 0'),
    ("psi-unreadable-in-wait", '    (( state != 2 )) || memory_refuse_unreadable\n    (( state == 0 )) || return 0',
     '    (( state != 2 )) || state=0\n    (( state == 0 )) || return 0'),
    ("psi-timeout-rc", '      exit 75\n    fi\n    if (( waited >= next_log ))', '      exit 1\n    fi\n    if (( waited >= next_log ))'),
    ("psi-bound", 'if (( waited >= psi_wait_limit )); then', 'if (( waited > psi_wait_limit )); then'),
    ("psi-log-rate", 'next_log=$((next_log + 60))', 'next_log=$((next_log + 10))'),
    ("psi-first-pass-skip", '    admit_memory\n    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  eval',
     '    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  eval'),
    ("psi-poll-skip", '    admit_memory\n    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  [[ $rc == 75 ]] && return 75\n  echo "cannot lock gate slot $label (flock exit $rc; $lock)"',
     '    nice -n 10 run-gate "$@"\n    return $?\n  else\n    rc=$?\n  fi\n  [[ $rc == 75 ]] && return 75\n  echo "cannot lock gate slot $label (flock exit $rc; $lock)"'),
):
    mutant("scripts/controller/gate-run.sh", f"gate-run-{_name}.sh", _before, _after)


def gate_with_lib_mutant(name: str, before: str, after: str) -> Path:
    variant = controller / name
    variant.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "scripts/controller/gate-run.sh", variant / "gate-run.sh")
    mutant("scripts/controller/lib.sh", "lib.sh", before, after, variant)
    return variant / "gate-run.sh"


gate_with_lib_mutant("gate-lib-derive-default-dir",
                     '[[ -n $dir ]] || dir=$(dirname -- "${CONTROLLER_GATE_LOCK:-}")', 'dir=$(dirname -- "${CONTROLLER_GATE_LOCK:-}")')
gate_with_lib_mutant("gate-lib-derive-dir-slash",
                     "printf '%s/gate%s.lock\\n' \"${dir%/}\" \"$number\"", "printf '%s/gate%s.lock\\n' \"${dir}\" \"$number\"")
gate_with_lib_mutant("gate-lib-derive-name",
                     "printf '%s/gate%s.lock\\n' \"${dir%/}\" \"$number\"", "printf '%s/gate-%s.lock\\n' \"${dir%/}\" \"$number\"")
gate_with_lib_mutant("gate-lib-derive-range",
                     "[[ $number =~ ^[3-8]$ ]] || return 2", "[[ $number =~ ^[2-8]$ ]] || return 2")
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
       '        if (hook.get("agent_id") or role_env == "agent") and not explicit:', '        if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-role-env-skip.py",
       '(hook.get("agent_id") or role_env == "agent") and not explicit:', 'hook.get("agent_id") and not explicit:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-sid-state.py",
       'return state / "hooks" / f"readback-state-{session}.json"', 'return state / "hooks" / "readback-state-x.json"')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-pressure-dir.py",
       'else state / "hooks" / f"{prefix}-{safe}.json"', 'else state / f"{prefix}-{safe}.json"')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-force-at.py", 'FORCE_AT = 300_000', 'FORCE_AT = 350_000')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-force-grace.py", 'if used <= grace:', 'if used < grace:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-force-below.py",
       '    if fill < force:\n        try:', '    if fill <= force:\n        try:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-force-agent.py",
       '        if session_kind(args, hook) == "spawned":\n            return 0\n        reason', '        reason')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-allow-chain.py", '    if CHAIN_RE.search(text):', '    if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-allow-root.py",
       'root in target.parents', 'root.parent in target.parents')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-prune-keep.py",
       'for _number, entry in found[:-KEEP_READBACKS] if KEEP_READBACKS > 0 else found:', 'for _number, entry in found:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-prune-age.py",
       'if entry.stat().st_mtime < cutoff:', 'if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-first-action.py",
       '        out.append(action[:600])', '        pass')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-legacy-state.py",
       'return stored or read_state(state / f"readback-state-{session}.json")', 'return stored')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-state-fallback.py",
       'return own if own.is_dir() else None', 'return own')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-crash-log.py",
       'log(ACTIVE_LOG, {"ev": "hook-error"', 'log(None, {"ev": "hook-error"')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-subagent-path.py",
       '/ "subagents" / (', '/ "subagent" / (')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-stale-check.py",
       'if age and age[1] >= STALE_MIN:', 'if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-stale-age.py",
       'if age and age[1] >= STALE_MIN:', 'if age:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-urge-wording.py",
       '        urgent = fill >= urge', '        urgent = False')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-handover-read.py",
       '        if tool == "Read":\n            # Read is read-only',
       '        if False:\n            # Read is read-only')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-allow-skill.py",
       '        return data.get("skill") == HANDOVER_SKILL', '        return False')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-allow-scratch.py",
       '        scratch = target_of(hook.get("scratchpad_dir"))',
       '        scratch = None')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-amp-chain.py",
       '    if "&" in strip_safe_amps(text):', '    if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-script-path.py",
       '    if os.path.realpath(os.path.join(base, words[index])) not in allowed_scripts:',
       '    if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-compact-boundary.py",
       '            if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":\n                return None, None',
       '            if False:\n                return None, None')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-synthetic-fill.py",
       '            if fill <= 0 or (isinstance(model, str) and model.startswith("<synthetic")):',
       '            if False:')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-force-drop.py",
       '        drop_force_files(state, safe_id(hook.get("session_id")))\n        # Every source',
       '        pass\n        # Every source')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-precompact-force-drop.py",
       '        # The grace counter belongs to the fill band that is ending: drop it now, so a\n        # stale count is gone even if the SessionStart hook is skipped or arrives late\n        # (review M2).\n        drop_force_files(state, safe_id(hook.get("session_id")))',
       '        # (review M2)\n        pass')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-grace-left.py",
       '        None if fill < limits[2] else max(0, limits[3] - force_count(state, session, who)),',
       '        None,')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-limits-order.py",
       '    urge = max(inform, positive("CTX_URGE_AT", URGE_AT))',
       '    urge = positive("CTX_URGE_AT", URGE_AT)')
mutant("scripts/controller/resume-read.sh", "resume-read-no-budget.sh",
       'TOTAL=${RESUME_READ_TOTAL:-24000}', 'TOTAL=0')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-agent-soft.py",
       '        if fill < soft:\n            return marker', '        if True:\n            return marker')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-role-env.py",
       'role = (os.environ.get("CTX_HOOK_ROLE") or "").strip().lower()', 'role = ""')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-read-order-newest.py",
       'block = text[heads[-1].start():] if heads else ""', 'block = text[heads[0].start():] if heads else ""')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-merges-window.py",
       'f"--since={minutes + 1} minutes ago"', 'f"--since={minutes + 10000} minutes ago"')
mutant("scripts/controller/hooks/ctx-hooks.py", "ctx-hooks-state-walkup.py",
       'if (parent / ".controller-state").is_dir():', 'if False:')
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
       '  [[ $1 =~ ^[A-Za-z0-9._-]+$ && $1 != *..* && $1 != . && $1 != *.jsonl ]]',
       '  return 0')
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
mutant("scripts/controller/pi-impl.sh", "pi-default-thinking.sh",
       'thinking=${PI_THINKING:-max}', 'thinking=${PI_THINKING:-high}')
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
    for sibling in ("pi-impl.sh", "continue-empty-completion.md", "opendesign-accounts.py", "pi-live.py",
                    "implementer-prompt-rules.md", "review-prompt-rules.md", "pi-review-argv"):
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
# Keep the same owner-ordering defect under a distinct mutant name for the
# accepted-record oracle; test-mutations.sh gives each named mutant one loop
# entry even when two focused checks exercise the same weak source edge.
mutant("scripts/controller/pi-impl.sh", "pi-run-name-refusal-overwrites-records.sh",
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
       'recheck_claimed_resume_mode || exit 2\nretire_existing=0',
       'recheck_claimed_resume_mode || exit 2\n'
       '[[ $mode != new || ${CONTROLLER_RUN_FORCE:-0} != 1 ]] || controller_run_retire_records "$name" >/dev/null\n'
       'retire_existing=0')
mutant("scripts/controller/pi-impl.sh", "pi-force-retire-before-credentials.sh",
       'if [[ ! -r $HOME/.env.ai ]]; then',
       'if [[ $retire_existing == 1 ]]; then controller_run_retire_records "$name" >/dev/null; fi\n'
       'if [[ ! -r $HOME/.env.ai ]]; then')
# P-plat-26c: the run mode (review/implementer), its sticky record and the rule
# paragraph. Each mutant turns one refusal or one record into its nearest
# neighbour; the mode checks in test-pi-impl.sh are the only ones that can see
# them, so each is judged by the focused name that covers the guard it weakened.
mutant("scripts/controller/pi-impl.sh", "pi-mode-value-guard.sh",
       "      echo \"PI_RUN_MODE must be 'implementer' or 'review' (got '$requested_mode')\" >&2\n      exit 2\n",
       "      : # unknown mode accepted\n")
mutant("scripts/controller/pi-impl.sh", "pi-mode-jsonl-session-id-allowed.sh",
       " && $1 != *.jsonl ]]", " ]]")
mutant("scripts/controller/pi-impl.sh", "pi-mode-unknown-session-allowed.sh",
       "(( session_id_found == 1 )) || {", "true || {")
mutant("scripts/controller/pi-impl.sh", "pi-mode-stream-session-mismatch-allowed.sh",
       'if [[ $mode == resume && -n $stream_session && $stream_session != "$session_id" ]]; then',
       "if false; then")
mutant("scripts/controller/pi-impl.sh", "pi-mode-claimed-name-reread-dropped.sh",
       'recheck_claimed_resume_mode || exit 2', ': # claim-time mode is not re-read')
mutant("scripts/controller/pi-impl.sh", "pi-mode-argv-append-dropped.sh",
       '  base_pi_args+=("${review_argv[@]}")',
       '  : # review argv left out of the launch')
mutant("scripts/controller/pi-impl.sh", "pi-mode-argv-missing-guard.sh",
       '  if [[ ! -r $review_argv_file ]]; then\n'
       '    echo "the review argv file is missing or unreadable: $review_argv_file" >&2\n'
       "    echo '  the read-only tool set is defined once there; a review cannot launch without it' >&2\n"
       '    exit 2\n'
       '  fi\n'
       '  review_argv=()\n'
       '  mapfile -t review_argv < "$review_argv_file" || exit 2',
       '  if [[ -r $review_argv_file ]]; then\n'
       '    review_argv=()\n'
       '    mapfile -t review_argv < "$review_argv_file" || exit 2\n'
       '  else\n'
       "    review_argv=(--tools 'read,grep,find,ls' --exclude-tools 'mcp__*' --no-extensions --no-mcp)\n"
       '  fi')
mutant("scripts/controller/pi-impl.sh", "pi-mode-argv-empty-guard.sh",
       '  (( ${#review_argv[@]} > 0 )) || { echo "the review argv file is empty: $review_argv_file" >&2; exit 2; }',
       '  : # empty review argv accepted')
mutant("scripts/controller/pi-impl.sh", "pi-mode-record-not-written.sh",
       'printf \'%s\\n\' "$run_mode" > "$out/$name.mode"',
       ': # mode record not written')
mutant("scripts/controller/pi-impl.sh", "pi-mode-record-written-before-refusal.sh",
       'controller_run_claim "$name" || exit 2',
       'controller_run_claim "$name" || exit 2\n'
       'printf \'%s\\n\' "$run_mode" > "$out/$name.mode"')
# A resume of a review must refuse a CALLER that asks for implementer mode, and
# must otherwise take the record: dropping the refusal widens a review, dropping
# the record reading loses the stickiness.
mutant("scripts/controller/pi-impl.sh", "pi-mode-sticky-refusal-dropped.sh",
       '      if [[ $record_mode == review && $request_origin == caller && $requested_mode == implementer ]]; then',
       '      if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-record-ignored.sh",
       '      run_mode=$record_mode\n      mode_source=record',
       '      run_mode=$requested_mode\n      mode_source=record')
mutant("scripts/controller/pi-impl.sh", "pi-mode-narrow-dropped.sh",
       '      if [[ $record_mode == implementer && $request_origin == caller && $requested_mode == review ]]; then',
       '      if false; then')
# A record that says neither word is not a fact to guess from.
mutant("scripts/controller/pi-impl.sh", "pi-mode-invalid-record-guessed.sh",
       'mode_record_state=invalid; fi',
       'mode_record_state=absent; fi')
# Review EVIDENCE beside a name with no mode record: the session is known to be
# a review, so the missing record must not license the implementer tool set.
mutant("scripts/controller/pi-impl.sh", "pi-mode-evidence-dropped.sh",
       '    review_material_present=1',
       '    review_material_present=0')
mutant("scripts/controller/pi-impl.sh", "pi-mode-thread-evidence-dropped.sh",
       '    if (( thread_has_review_material == 1 )); then',
       '      if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-stream-review-ignored.sh",
       '    if read_mode_record_if_present "$stream_mode_path"; then\n'
       '      if [[ $mode_record_value == review ]]; then',
       '    if read_mode_record_if_present "$stream_mode_path"; then\n'
       '      if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-review-material-unreadable-allowed.sh",
       '  if [[ -L $directory || ( -e $directory && ( ! -d $directory || ! -r $directory || ! -x $directory ) ) ]]; then',
       '  if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-retired-session-not-preserved.sh",
       '  if ! persist_retired_run_session_mode "$name"; then',
       '  if false; then')
# A session sticks across caller-selected run names. These mutants each remove
# one source or consequence of that rule; their focused arms also verify that an
# implementer session is not broadened into a review by an unrelated match.
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-thread-ignored.sh",
       '      if [[ $mode_record_value == review ]]; then\n'
       '        session_review_found=1\n'
       '        session_review_source=thread-record',
       '      if false; then\n'
       '        session_review_found=1\n'
       '        session_review_source=thread-record')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-record-ignored.sh",
       '  if [[ $session_mode_state == valid && $session_mode_record_value == review ]]; then',
       '  if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-thread-invalid-record-guessed.sh",
       '    else\n'
       '      record_rc=$?\n'
       '      (( record_rc == 1 )) || exit 2\n'
       '    fi\n'
       '    if (( thread_has_review_material == 1 )); then',
       '    else\n'
       '      record_rc=$?\n'
       '      : # invalid matching-name mode record ignored\n'
       '    fi\n'
       '    if (( thread_has_review_material == 1 )); then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-write-dropped.sh",
       '  if ! printf \'%s\\n\' "$write_mode" > "$path"; then',
       '  if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-monotonic-dropped.sh",
       '  if [[ $stored_mode == review || $requested_mode == review ]]; then write_mode=review; fi',
       '  if [[ $requested_mode == review ]]; then write_mode=review; fi')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-implementer-as-review.sh",
       '  if [[ $session_mode_state == valid && $session_mode_record_value == review ]]; then',
       '  if [[ $session_mode_state == valid ]]; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-implementer-refusal-dropped.sh",
       '    if [[ $request_origin == caller && $requested_mode == implementer ]]; then\n'
       '      if [[ $session_review_source == review-material ]]; then',
       '    if false; then\n'
       '      if [[ $session_review_source == review-material ]]; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-session-invalid-record-guessed.sh",
       '  [[ $session_mode_state != invalid ]] || {',
       '  true || {')
# In review mode the seam adds nothing at all -- the general refusal is the one
# that holds for a spelling this wrapper never classified.
mutant("scripts/controller/pi-impl.sh", "pi-mode-extra-args-allowed.sh",
       'if [[ $run_mode == review ]] && (( ${#extra_pi_args[@]} > 0 )); then',
       'if false; then')
mutant("scripts/controller/pi-impl.sh", "pi-mode-extra-args-list-dropped.sh",
       '    if [[ $run_mode == review ]]; then\n      # Review mode owns what the reviewer may DO',
       '    if false; then\n      # Review mode owns what the reviewer may DO')
# The rule paragraph is part of the contract: a launch that cannot read it is
# refused, and the paragraph is what pi actually receives.
mutant("scripts/controller/pi-impl.sh", "pi-mode-rules-file-refusal-dropped.sh",
       'if [[ ! -r $rules_file ]]; then\n'
       '  echo "pi $run_mode prompt rule file is missing or unreadable: $rules_file" >&2\n'
       "  echo '  every launch appends its fixed rule paragraph; a prompt without it is not the contract this wrapper promises' >&2\n"
       '  exit 2\n'
       'fi',
       'if false; then\n'
       '  echo "pi $run_mode prompt rule file is missing or unreadable: $rules_file" >&2\n'
       "  echo '  every launch appends its fixed rule paragraph; a prompt without it is not the contract this wrapper promises' >&2\n"
       '  exit 2\n'
       'fi')
mutant("scripts/controller/pi-impl.sh", "pi-mode-prompt-dropped.sh",
       "printf -v prompt_text '%s\\n%s' \"$prompt_text\" \"$rules_text\"",
       ': # the rule paragraph is not appended')
# A host's controller.local.env may default a launch, never flip a caller's mode.
mutant("scripts/controller/pi-impl.sh", "pi-mode-local-env-flip.sh",
       'if (( caller_pi_run_mode_set == 1 )); then PI_RUN_MODE=$caller_pi_run_mode; fi',
       ': # the local environment file decides the mode')
# P-plat-26d: every implementer shell has the fixed pi extension, a successful
# preflight before the name is claimed, and no caller extension-control seam.
mutant("scripts/controller/pi-impl.sh", "pi-pidns-args-dropped.sh",
       '  base_pi_args+=(--no-extensions -e "$pidns_extension")',
       '  : # the implementer shell extension is omitted')
mutant("scripts/controller/pi-impl.sh", "pi-pidns-project-extensions-enabled.sh",
       '  base_pi_args+=(--no-extensions -e "$pidns_extension")',
       '  base_pi_args+=(-e "$pidns_extension")')
mutant("scripts/controller/pi-impl.sh", "pi-pidns-preflight-dropped.sh",
       '  if pidns_probe_output=$(/usr/bin/env -u SHELLOPTS -u BASHOPTS "$pidns_wrapper" -c true 2>&1); then',
       '  if true; then')
mutant("scripts/controller/pi-impl.sh", "pi-pidns-extension-check-dropped.sh",
       '  if [[ ! -r $pidns_extension ]]; then\n'
       '    echo "pi PID namespace extension is missing or unreadable: $pidns_extension" >&2\n'
       '    exit 2\n'
       '  fi\n',
       '  : # the extension file is assumed present\n')
mutant("scripts/controller/pi-impl.sh", "pi-pidns-extension-args-allowed.sh",
       '    --extension | --extension=* | -e | --no-extensions | -ne) return 0 ;;',
       '    --extension | --extension=* | -e | --no-extensions | -ne) return 1 ;;')
mutant("scripts/controller/pi-impl.sh", "pi-session-scan-python-not-isolated.sh",
       '  python3 -I - "$1" <<\'PY\'',
       '  python3 - "$1" <<\'PY\'')
mutant("scripts/controller/pi-impl.sh", "pi-run-inspection-python-not-isolated.sh",
       '  python3 -I - "$out/$name.jsonl" "$out/$name.last.txt" <<\'PY\'',
       '  python3 - "$out/$name.jsonl" "$out/$name.last.txt" <<\'PY\'')

mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-user-namespace",
       '  --unshare-user\n  --unshare-pid',
       '  --unshare-pid')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-pid-namespace",
       '  --unshare-pid\n  --die-with-parent',
       '  --die-with-parent')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-die-with-parent",
       '  --die-with-parent\n  --dev-bind / /',
       '  --dev-bind / /')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-root-bind",
       '  --dev-bind / /\n  --perms 0700',
       '  --perms 0700')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-tmux-perms-not-private",
       '  --perms 0700 --tmpfs "$tmux_socket_dir"',
       '  --tmpfs "$tmux_socket_dir"')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-tmux-tmpfs",
       '  --perms 0700 --tmpfs "$tmux_socket_dir"\n',
       '')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-no-docker-api-tmpfs",
       '  --perms 0700 --tmpfs /run/docker-api\n',
       '')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-keeps-docker-host",
       'unset TMUX TMUX_PANE DOCKER_HOST',
       'unset TMUX TMUX_PANE')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-keeps-tmux",
       'unset TMUX TMUX_PANE DOCKER_HOST',
       'unset TMUX_PANE DOCKER_HOST')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-keeps-tmux-pane",
       'unset TMUX TMUX_PANE DOCKER_HOST',
       'unset TMUX DOCKER_HOST')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-docker-socket-uncovered",
       '  bwrap_args+=(--bind /dev/null "$docker_socket")',
       '  : # Docker socket remains visible')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-fail-open",
       'if [[ $(<"$status_file") != *\'"child-pid"\'* ]]; then',
       'if false; then')
mutant("scripts/controller/pi-pidns-bash-worker.sh", "pi-pidns-command-status-zero",
       'exit "$command_rc"',
       'exit 0')
mutant("scripts/controller/pi-pidns-bash", "pi-pidns-python-startup-unisolated",
       '#!/usr/bin/python3 -IS',
       '#!/usr/bin/python3')
mutant("scripts/controller/pi-pidns-bash", "pi-pidns-signal-dispositions-dropped",
       'signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n'
       'signal.signal(signal.SIGXFSZ, signal.SIG_DFL)',
       '')
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
# L3' (r2): the three .done readers share ONE rule -- the verdict is any line
# of the marker that CONTAINS it. job-status used to scan lines 2+ only, so a
# marker whose line 1 carries the verdict (the launcher's no-marker fallback,
# `EXIT=5 OUTCOME=REVIEW-BUDGET`) was named only because line 1 was printed
# verbatim; the class word was never put in front of it the way ctl-status does.
mutant("scripts/controller/job-status.sh", "job-status-verdict-scan-line1.sh",
       '        if any(verdict in line for line in marker):',
       '        if any(verdict in line for line in marker[1:]):')
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
       '`EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k> EFFORT=<value>`',
       '`EXIT=<n> AUTORESUME=<k>`')
mutant("scripts/controller/docs/CONSUMERS.md", "CONSUMER-pi-thinking-not-recorded.md",
       'Pi records no thinking level in its `.done` marker.',
       'Pi records its thinking level in its `.done` marker.')
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
mutant("scripts/controller/remote-gate.sh", "remote-gate-budget-verdict.sh",
       '[[ $rg_verdict != BUDGET_EXCEEDED ]]', 'true')
mutant("scripts/controller/remote-gate.sh", "remote-gate-verdict-anchor.sh",
       "index($0, p) == 1 { l = $0 }", "index($0, p) >= 1 { l = substr($0, index($0, p)) }")
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
    ("old-env-py-name", "REMOTE_GATE_PY:CONTROLLER_REMOTE_PYTHON ", "REMOTE_GATE_PY:CONTROLLER_REMOTE_GATE_PY "),
    ("old-env-dry-hold", " REMOTE_GATE_DRY_HOLD:CONTROLLER_REMOTE_DRY_HOLD; do", "; do"),
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
mutant("scripts/controller/chain-run.sh", "chain-run-unset-env.sh",
       'for name in ${unset_names[@]+"${unset_names[@]}"}; do unset "$name"; done', ':')
mutant("scripts/controller/chain-run.sh", "chain-run-unset-env-name.sh",
       '--unset-env) [[ $# -ge 2 && $2 =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || usage;', '--unset-env) [[ $# -ge 2 ]] || usage;')


# ctl-doctor.sh: one hand mutant per check; the lib.sh read-only/configured-path mutants ship beside their own doctor copy.
def doctor_with_lib_mutant(name: str, before: str, after: str) -> Path:
    variant = controller / name
    variant.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "scripts/controller/ctl-doctor.sh", variant / "ctl-doctor.sh")
    shutil.copy2(repo / "scripts/controller/ctl-hygiene.sh", variant / "ctl-hygiene.sh")
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
    ("hosts-pin", "elif not os.path.isfile(full):\n                out(\"FAIL\", f\"{where}:pin\"", "elif False:\n                out(\"FAIL\", f\"{where}:pin\""),
    ("hosts-pin-devnull", "if os.path.normpath(full) == os.devnull:", "if False:"),
    ("hosts-pin-devnull-ok", "                bad = True   # no OK line: the pin is not in place\n", ""),
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
    ("gate-lock-unset", "another directory'; fi\n  if [[ -z $path ]]; then", "another directory'; fi\n  if false; then"),
    ("gate-lock-absolute", "elif [[ $path != /* ]]; then", "elif false; then"),
    ("gate-lock-parent", "if [[ -d $parent && -w $parent ]]; then record OK", "if true; then record OK"),
    ("gate-lock-distinct", '[[ -n ${slot_paths[slot]:-} && ${slot_paths[slot]:-} == "${slot_paths[other]:-}" ]] || continue', "continue"),
    ("gate-slots-invalid", 'if [[ $gate_slots =~ ^[1-8]$ ]]; then', "if true; then"),
    ("gate-slots-print", 'record INFO gate-slots "effective local gate slots: $gate_slots (CONTROLLER_GATE_SLOTS)"',
     'record INFO gate-slots "effective local gate slots: 2 (CONTROLLER_GATE_SLOTS)"'),
    ("gate-slots-default-print", "if [[ -z ${CONTROLLER_GATE_SLOTS:-} ]]; then record INFO gate-slots", "if false; then record INFO gate-slots"),
    ("gate-slots-leak", "'CONTROLLER_GATE_SLOTS is not an integer from 1 to 8: gate-run.sh exits 2 for every local lane'",
     '"CONTROLLER_GATE_SLOTS=${CONTROLLER_GATE_SLOTS} is not an integer from 1 to 8: gate-run.sh exits 2 for every local lane"'),
    ("gate-slots-fallback", "gate_slots=2\n  record FAIL gate-slots", "gate_slots=1\n  record FAIL gate-slots"),
    ("gate-slot2-one-slot", "(( gate_slots < 2 )) || gate_lock_check CONTROLLER_GATE2_LOCK", "gate_lock_check CONTROLLER_GATE2_LOCK"),
    ("gate-derived-check", 'gate_lock_check "gate${slot}.lock" "${slot_paths[slot]}"', ":"),
    ("gate-derived-range", "for (( slot = 3; slot <= gate_slots; slot++ )); do", "for (( slot = 3; slot < gate_slots; slot++ )); do"),
    ("gate-lock-dir-absolute", 'if [[ -n ${CONTROLLER_GATE_LOCK_DIR:-} && $CONTROLLER_GATE_LOCK_DIR != /* ]]; then', "if false; then"),
    ("gate-lock-dir-unused", "if (( gate_slots > 2 )); then", "if true; then"),
    ("gate-collide-pair-text", "if (( slot == 1 && other == 2 )); then", "if false; then"),
    ("gate-derived-fix", "else move='set CONTROLLER_GATE_LOCK_DIR to another directory'; fi", 'else move="point $1 at another path"; fi'),
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
# The doctor's default path to ctl-hygiene.sh: a broken default silently drops every hygiene line from production doctor runs.
mutant("scripts/controller/ctl-doctor.sh", "ctl-doctor-hygiene-default.sh",
       '"$BASH" "${CTL_HYGIENE_SCRIPT:-$SCRIPT_DIR/ctl-hygiene.sh}" > "$hygiene"',
       '"$BASH" "${CTL_HYGIENE_SCRIPT:-/dev/null}" > "$hygiene"')
# ctl-hygiene.sh: the state directory default (main checkout from a linked worktree) and the heading-parenthesis scoping.
mutant("scripts/controller/ctl-hygiene.sh", "ctl-hygiene-state-default.sh",
       'if [[ $common == */.git ]]; then state_dir=${common%/.git}/.controller-state; else state_dir=$repo/.controller-state; fi',
       'state_dir=$repo/.controller-state')
mutant("scripts/controller/ctl-hygiene.sh", "ctl-hygiene-heading-paren.sh",
       r'in_heading = {w for inner in re.findall(r"\(([^)]*)\)", line) for w in re.findall(r"[A-Z-]+", inner)}',
       'in_heading = set(re.findall(r"[A-Z-]+", line))')
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
    ("chmod-existing", "(( ! created_dir )) || chmod 700", "(( created_dir )) || chmod 700"),
    ("chmod-never", "(( ! created_dir )) || chmod 700", ": || chmod 700"),
    ("verify-empty", "(( count > 0 )) || die", ": || die"),
    ("verify-no-sidecar", '[[ -f $verify.sha256 ]] || die', ': || die'),
    ("linked-detect", 'if [[ -n $git_dir && $git_dir != "$common_dir" ]]; then', 'if [[ -n $git_dir && $git_dir == "$common_dir" ]]; then'),
    ("linked-members", '[[ -e $repo_root/$member ]] || absent+=" $member"', 'absent+=" $member"'),
    ("hardlink-targets",'"flags=rh;s,^\\./,,;s,^,$label/,"', '"flags=r;s,^\\./,,;s,^,$label/,"'),
):
    mutant("scripts/controller/ctl-state-backup.sh", f"ctl-state-backup-{_name}.sh", _before, _after)

# the generic remote dev tool (tests/test_dev.py reads DEV_SCRIPT): one hand mutant per guard
for name, before, after in (
    ("ticket-hold", "( exec 9>&-; sleep 20 ) >/dev/null 2>&1 </dev/null &", ":"),
    ("psi-threshold", "exit !(m + 0 >= 3)", "exit !(m + 0 > 3)"),
    ("psi-file", '"$PSI_DIR/memory" 2>/dev/null', '"$PSI_DIR/io" 2>/dev/null'),
    ("psi-full-line", "/^full/ { sub", "/^some/ { sub"),
    ("image-id", 'elif [[ $lid != "$hid" ]]; then', "elif false; then"),
    ("quoting", "printf '%q' \"$a\"; done; }", "printf '%s' \"$a\"; done; }"),
    # not a TERM-trap mutant: bash runs the EXIT trap on an untrapped SIGTERM too (that mutant survived suite gate 2: equivalent)
    ("cleanup-by-cid", 'docker rm -f "$(cat "$D/cid")" >/dev/null 2>&1 7>&- 8>&- 9>&-', 'docker rm -f "$NAME" >/dev/null 2>&1 7>&- 8>&- 9>&-'),
    ("banner-dirty", "mark=''; if (( dirty )); then mark=+dirty; fi", "mark=+dirty"),
    ("tty-env", "printf 'export D W\\nSHELL=/bin/bash script", "printf 'SHELL=/bin/bash script"),
    ("git-ro", """mnt+=' -v "$W/.git:/work/.git:ro"'""", """mnt+=' -v "$W/.git:/work/.git"'"""),
):
    mutant("scripts/controller/dev", f"dev-{name}", before, after)
# the profile resolver is the host-injection gate (tests/test_dev.py reads DEV_PROFILE_SCRIPT)
mutant("scripts/controller/dev_profile.py", "dev-profile-image-regex", 'IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*$")', 'IMAGE = re.compile(r"(?s).*")')
mutant("scripts/controller/dev_profile.py", "dev-profile-work-mount", 'if mount.startswith("{work}:") and dest != "/work":', 'if False:')
