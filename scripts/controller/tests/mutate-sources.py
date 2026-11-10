#!/usr/bin/env python3
"""Create one-line hand mutations in a disposable test tree."""
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
