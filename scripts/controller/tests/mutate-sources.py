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
mutant("scripts/controller/job-check.sh", "job-check-mid-wait.sh",
       'r"(still running|waiting for|in progress)"', 'r"(never matches)"')
mutant("scripts/controller/job-check.sh", "job-check-empty-final.sh",
       'if not text:', 'if False:')
mutant("scripts/controller/job-check.sh", "job-check-tool-call-case.sh",
       "text[-600:], re.M | re.I", "text[-600:], re.M")
mutant("scripts/controller/job-check.sh", "job-check-report-path.sh",
       'if [[ $report != /* ]]; then', 'if [[ $report == /* ]]; then')
mutant("scripts/controller/hx.sh", "hx-identity.sh",
       'if ! controller_host_api_check "$host" >/dev/null; then', 'if false; then')
mutant("scripts/controller/release-from-wt.sh", "release-archive.sh",
       'if [[ $archive_mode == auto && $branch == rel-* ]] || [[ $archive_mode == on ]]; then',
       'if [[ $archive_mode == off ]]; then')
mutant("scripts/controller/release-from-wt.sh", "release-clean.sh",
       'if [[ -n $(git -C "$worktree" status --porcelain 2>/dev/null) ]]; then', 'if false; then')
mutant("scripts/controller/push-image.sh", "push-image-status.sh",
       'for rc in "${statuses[@]}"; do (( rc == 0 )) || exit 1; done', ': # deliberately ignore pipeline exit statuses')

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
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-start-config.sh",
       'host_text=$(queue_hosts) || exit 2', "host_text=$(queue_hosts) || host_text=''", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-stop-config.sh",
       'host_text=$(controller_host_list) || exit 2', "host_text=$(controller_host_list) || host_text=''", runner)
mutant("scripts/netcup/runner/host-sat-queue.sh", "queue-controller-lib.sh",
       'if [[ -f $REPO_ROOT/scripts/controller/lib.sh ]]; then', 'if [[ -f $SCRIPT_DIR/lib.sh ]]; then', runner)

mutant("scripts/controller/job-check.sh", "job-check-run-traversal.sh",
       '[[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || { echo "invalid run name" >&2; exit 2; }',
       ': # removed run-name traversal guard')
mutant("scripts/controller/job-check.sh", "job-check-missing-stream.sh",
       'if [[ ! -f $run_file ]]; then echo "RUN FILE MISSING: $run_file"; red=1',
       'if [[ ! -f $run_file ]]; then echo "RUNNING (no .done)"; exit 0')
mutant("scripts/controller/job-check.sh", "job-check-report-litter.sh",
       "stray=$(git -C \"$worktree\" status --short --ignored 2>/dev/null | grep -E '^\\?\\? (reports|scripts/.*/reports)/' | head -2 || true)",
       "stray='' ")
mutant("scripts/controller/job-check.sh", "job-check-merge-trailer.sh",
       'commits=$(git -C "$worktree" rev-list "$base..HEAD" 2>/dev/null)',
       'commits=$(git -C "$worktree" rev-list --no-merges "$base..HEAD" 2>/dev/null)')
mutant("scripts/controller/job-check.sh", "job-check-any-claude-trailer.sh",
       'Claude [A-Za-z0-9. ]+', 'Claude NEVER')
mutant("scripts/controller/codex-impl.sh", "codex-deepseek-catalog.sh",
       'sys.exit(1 if any(m.get("tool_mode") is not None for m in data.get("models", [])) else 0)',
       'sys.exit(0)')
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
