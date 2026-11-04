#!/usr/bin/env python3
"""Run a registered Run-Gate lane on a dedicated test host instead of the shared controller host.

usage:
  remote-gate run <host|auto> <worktree> <lane> [--project-dir DIR] [--base REF] [--name N] [--detach]
              [--wait-psi SECONDS] [--out-dir DIR] [-- extra run-gate args...]
  remote-gate status|logs|collect|cancel <id> [--out-dir DIR]

The host table (ssh argv, provider identity check, guard, cockpit name) lives in a gitignored JSON file
(`--hosts-file`, $REMOTE_GATE_HOSTS or ~/.config/remote-gate/hosts.json); see remote-gate-hosts.example.json.
All process execution goes through the `Runner` object so tests inject a fake one (stdlib only).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

ALLOWED_HOSTS = ("r1002", "v1001")  # hard allowlist: no other host is ever contacted
PSI_LIMIT = 25.0
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
DEFAULT_OUT = "/home/vscode/vbpub-release-program-20261027/remote-gates"
DEFAULT_HOSTS = os.path.expanduser("~/.config/remote-gate/hosts.json")
VERDICT_RE = re.compile(r"lane '?([A-Za-z0-9_.:-]+)'? verdict ([A-Z_]+)")


class GateError(Exception):
    """A refusal or failure the caller should read; carries an exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Result:
    rc: int
    out: str = ""
    err: str = ""


class Runner:
    """The only place that executes processes (ssh, git, identity checks)."""

    def run(self, argv: Sequence[str], stdin_path: Optional[str] = None, timeout: Optional[float] = 300) -> Result:
        stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
        try:
            p = subprocess.run(list(argv), stdin=stdin, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return Result(124, "", "timeout")
        finally:
            if stdin_path:
                stdin.close()
        return Result(p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace"))

    def run_to_file(self, argv: Sequence[str], dest: str, timeout: Optional[float] = 600) -> Result:
        with open(dest, "wb") as fh:
            try:
                p = subprocess.run(list(argv), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.PIPE, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                return Result(124, "", "timeout")
        return Result(p.returncode, "", p.stderr.decode(errors="replace"))


@dataclass
class Host:
    name: str
    ssh: list
    api_check: list
    guard: str
    runner_home: str = "/home/runner"
    cockpit: str = "mdt-cockpit-runner"
    image: str = "tester-unified:local"


@dataclass
class Ctx:
    runner: Runner
    hosts: dict
    out_dir: Path
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    say: Callable[[str], None] = lambda s: print(s, flush=True)
    verified: set = field(default_factory=set)


def load_hosts(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    hosts = {}
    for name, row in data["hosts"].items():
        if name not in ALLOWED_HOSTS:
            raise GateError(f"hosts file names {name!r}; only {', '.join(ALLOWED_HOSTS)} are allowed")
        hosts[name] = Host(name=name, ssh=row["ssh"], api_check=row["api_check"], guard=row["guard"],
                           runner_home=row.get("runner_home", "/home/runner"), cockpit=row.get("cockpit", "mdt-cockpit-runner"),
                           image=row.get("image", "tester-unified:local"))
    return hosts


def get_host(ctx: Ctx, name: str) -> Host:
    if name not in ALLOWED_HOSTS or name not in ctx.hosts:
        raise GateError(f"unknown host {name!r}; use one of {', '.join(sorted(ctx.hosts))} or auto")
    return ctx.hosts[name]


def identity(ctx: Ctx, host: Host) -> None:
    """Provider-side API identity check (once per invocation); every remote command also repeats the on-host guard."""
    if host.name in ctx.verified:
        return
    r = ctx.runner.run(host.api_check, timeout=120)
    if r.rc != 0:
        raise GateError(f"{host.name}: Netcup identity check failed (rc {r.rc}): {(r.err or r.out).strip()[:300]}", 3)
    ctx.verified.add(host.name)


def remote(ctx: Ctx, host: Host, script: str, stdin_path: Optional[str] = None, timeout: float = 300) -> Result:
    return ctx.runner.run(host.ssh + [f"{host.guard}; {script}"], stdin_path=stdin_path, timeout=timeout)


def psi_of(ctx: Ctx, host: Host) -> tuple:
    r = remote(ctx, host, "awk '/^some/ {sub(\"avg10=\",\"\",$2); print $2}' /proc/pressure/cpu /proc/pressure/memory", timeout=60)
    vals = r.out.split()
    if r.rc != 0 or len(vals) != 2:
        raise GateError(f"{host.name}: cannot read PSI over ssh (rc {r.rc}): {r.err.strip()[:200]}", 3)
    return float(vals[0]), float(vals[1])


def choose_host(ctx: Ctx, wanted: str) -> Host:
    if wanted != "auto":
        host = get_host(ctx, wanted)
        identity(ctx, host)
        return host
    best, rows = None, []
    for name in ALLOWED_HOSTS:
        if name not in ctx.hosts:
            continue
        host = ctx.hosts[name]
        identity(ctx, host)
        cpu, mem = psi_of(ctx, host)
        rows.append(f"{name} cpu={cpu} mem={mem}")
        if max(cpu, mem) <= PSI_LIMIT and (best is None or max(cpu, mem) < best[0]):
            best = (max(cpu, mem), host)
    if best is None:
        raise GateError(f"refusing: every test host is above PSI {PSI_LIMIT:g} some avg10 ({'; '.join(rows)}); retry when quiet", 75)
    ctx.say(f"host auto -> {best[1].name} ({'; '.join(rows)})")
    return best[1]


def git(ctx: Ctx, wt: str, *args: str) -> Result:
    return ctx.runner.run(["git", "-C", wt, *args], timeout=120)


def local_state(ctx: Ctx, wt: str, base: Optional[str]) -> dict:
    top = git(ctx, wt, "rev-parse", "--show-toplevel")
    if top.rc != 0:
        raise GateError(f"{wt} is not a git worktree")
    top_path = top.out.strip()
    sha = git(ctx, top_path, "rev-parse", "HEAD").out.strip()
    if not SHA_RE.match(sha):
        raise GateError("cannot resolve HEAD")
    dirty = git(ctx, top_path, "status", "--porcelain", "--untracked-files=no").out.strip()
    if dirty:
        raise GateError("worktree has uncommitted tracked changes; commit them first (`git commit --only -- <paths>`):\n" + dirty[:500])
    base_sha = None
    if base:
        r = git(ctx, top_path, "rev-parse", "--verify", f"{base}^{{commit}}")
        if r.rc != 0:
            raise GateError(f"--base {base!r} does not resolve")
        base_sha = r.out.strip()
    prefix = git(ctx, wt, "rev-parse", "--show-prefix").out.strip().rstrip("/")
    return {"top": top_path, "sha": sha, "base": base_sha, "prefix": prefix}


def build_bundle(ctx: Ctx, st: dict, have: list, dest: str) -> Optional[str]:
    """Thin bundle of HEAD against the closest commit the host already holds; None when the host has HEAD's history."""
    best = None
    for c in have:
        if git(ctx, st["top"], "cat-file", "-e", f"{c}^{{commit}}").rc != 0:
            continue
        n = git(ctx, st["top"], "rev-list", "--count", st["sha"], f"^{c}").out.strip()
        if n.isdigit() and (best is None or int(n) < best[0]):
            best = (int(n), c)
    if best and best[0] == 0:
        return best[1]  # HEAD already reachable from an existing checkout: no bundle needed
    args = ["bundle", "create", dest, "HEAD"] + ([f"^{best[1]}"] if best else [])
    r = git(ctx, st["top"], *args)
    if r.rc != 0:
        raise GateError(f"git bundle failed: {r.err.strip()[:300]}")
    return best[1] if best else ""


JOB_TEMPLATE = r"""#!/usr/bin/env bash
# remote-gate job for @@NAME@@: generated by remote-gate.py, runs on the test host as the runner user.
set -Euo pipefail
umask 022
D=$HOME/rg/@@NAME@@
CK=rg-@@NAME@@-@@SHORT@@
CKDIR=$HOME/checkouts/$CK
PROJ=@@PROJ@@
LANE=@@LANE@@
COCKPIT=@@COCKPIT@@
IMAGE=@@IMAGE@@
SHA=@@SHA@@
BASE_SHA=@@BASE@@
SEED=@@SEED@@
PSI_LIMIT=@@PSI_LIMIT@@
PSI_WAIT=@@PSI_WAIT@@
PIDF=/tmp/rg-@@NAME@@.pid
EXTRA=(@@EXTRA@@)
rc=70
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
printf '%s\n' "$$" > "$D/pid"
psi_high() { awk -v lim="$PSI_LIMIT" '/^some/ {sub("avg10=","",$2); if ($2+0 > lim) f=1} END {exit !f}' /proc/pressure/cpu /proc/pressure/memory; }
# shellcheck disable=SC2329  # invoked from the TERM trap
cancel_lane() {
  docker exec -u vscode "$COCKPIT" sh -c 'test -s "$1" && kill -TERM "$(cat "$1")"' sh "$PIDF" 2>/dev/null || true
}
# shellcheck disable=SC2329  # invoked from the EXIT trap
finish() {
  local names n
  log "cleanup: lane containers of this run, then the throwaway checkout"
  names=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^run-gate-' | grep -F -- "${SHA:0:12}" | grep -F -- "$LANE" || true)
  for n in $names; do docker rm -f "$n" >/dev/null 2>&1 && log "removed container $n"; done
  docker exec -u vscode "$COCKPIT" rm -f "$PIDF" 2>/dev/null || true
  if [[ -d $CKDIR && $CK == rg-* && $CKDIR == "$HOME"/checkouts/rg-* ]]; then
    docker exec -u vscode "$COCKPIT" rm -rf -- "/workspaces/$CK" 2>/dev/null || rm -rf -- "$CKDIR"
    rm -rf -- "$CKDIR"
    log "removed checkout $CKDIR"
  fi
  rm -f -- "$D/in.bundle"
  printf '%s\n' "$rc" > "$D/rc"
  log "done rc=$rc"
}
trap finish EXIT
trap 'log "terminated by cancel"; cancel_lane; rc=143; exit 143' TERM INT

{ hostname -f; docker exec -u vscode "$COCKPIT" /home/vscode/.local/bin/run-gate --version 2>&1 | head -1
  docker image inspect "$IMAGE" --format 'image {{.Id}} created {{.Created}}' 2>&1; } | sed 's/^/header: /'

waited=0
while psi_high; do
  if (( waited >= PSI_WAIT )); then log "PSI above $PSI_LIMIT after ${waited}s: refusing to start"; rc=75; exit 75; fi
  sleep 30; waited=$((waited + 30))
done
log "PSI ok (waited ${waited}s)"

if [[ -n $SEED ]]; then
  git clone -q --no-checkout "$HOME/checkouts/$SEED" "$CKDIR"
else
  git -c init.defaultBranch=main init -q "$CKDIR"
fi
[[ -f $D/in.bundle ]] && git -C "$CKDIR" fetch -q "$D/in.bundle" HEAD
git -C "$CKDIR" checkout -q --detach "$SHA" || { rc=71; exit 71; }
[[ $(git -C "$CKDIR" rev-parse HEAD) == "$SHA" ]] || { rc=71; exit 71; }
if [[ -n $BASE_SHA ]]; then git -C "$CKDIR" cat-file -e "$BASE_SHA^{commit}" || { log "base $BASE_SHA missing on the host"; rc=72; exit 72; }; fi
log "checkout $CKDIR at $SHA"

args=("$LANE" "${EXTRA[@]}")
[[ -n $BASE_SHA ]] && args+=(--base "$BASE_SHA")
log "run-gate ${args[*]} (cwd /workspaces/$CK/$PROJ)"
docker exec -u vscode -w "/workspaces/$CK/$PROJ" -e RUN_GATE_TESTER_UNIFIED_IMAGE="$IMAGE" -e RUN_GATE_ALLOW_MAIN_CHECKOUT=1 -e RUN_GATE_PROFILE=off \
  "$COCKPIT" sh -c 'echo $$ > "$1"; shift; exec nice -n 19 /home/vscode/.local/bin/run-gate "$@"' sh "$PIDF" "${args[@]}" > "$D/lane.log" 2>&1 &
lanepid=$!
wait "$lanepid"
rc=$?
while kill -0 "$lanepid" 2>/dev/null; do wait "$lanepid"; rc=$?; done
log "lane exited rc=$rc"
detail=$(grep -o 'log /tmp/run-gate/lanes/[^ ;]*\.log' "$D/lane.log" | tail -1 | cut -d' ' -f2 || true)
if [[ -n $detail ]]; then docker exec -u vscode "$COCKPIT" cat -- "$detail" > "$D/lane-detail.log" 2>/dev/null || true; fi

( cd "$CKDIR/$PROJ" 2>/dev/null && find . \( -path './.assay/*' -o -path './.run-gate/*' \) -type f \
    \( -name 'verdict*.json' -o -name 'progress*.jsonl' -o -name 'coverage*.json' -o -name '*.log' -o -name '*.xml' \) -size -50M -print0 \
  | tar --null -T - -czf "$D/artifacts.tgz" ) 2>/dev/null || true
exit "$rc"
"""


def render_job(host: Host, name: str, st: dict, lane: str, proj: str, seed: str, psi_wait: int, extra: Sequence[str]) -> str:
    sub = {"NAME": name, "SHORT": st["sha"][:12], "PROJ": shlex.quote(proj), "LANE": shlex.quote(lane),
           "COCKPIT": shlex.quote(host.cockpit), "IMAGE": shlex.quote(host.image), "SHA": st["sha"],
           "BASE": st["base"] or "''", "SEED": shlex.quote(seed), "PSI_LIMIT": f"{PSI_LIMIT:g}",
           "PSI_WAIT": str(int(psi_wait)), "EXTRA": " ".join(shlex.quote(a) for a in extra)}
    text = JOB_TEMPLATE
    for k, v in sub.items():
        text = text.replace(f"@@{k}@@", v)
    return text


def default_name(ctx: Ctx, lane: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9]', '', lane)[:20]}-{ctx.now().strftime('%y%m%d%H%M%S')}"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_run(ctx: Ctx, a: argparse.Namespace) -> int:
    lane = a.lane
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", lane):
        raise GateError(f"invalid lane name {lane!r}")
    name = a.name or default_name(ctx, lane)
    if not NAME_RE.match(name):
        raise GateError(f"invalid --name {name!r}")
    st = local_state(ctx, a.worktree, a.base)
    proj = a.project_dir if a.project_dir is not None else (st["prefix"] or ".")
    if proj.startswith("/") or ".." in Path(proj).parts:
        raise GateError("--project-dir must be relative to the worktree root and stay inside it")
    host = choose_host(ctx, a.host)
    run_dir = ctx.out_dir / name
    if run_dir.exists():
        raise GateError(f"{run_dir} already exists; pick another --name")
    run_dir.mkdir(parents=True)
    home, rgd = host.runner_home, f"{host.runner_home}/rg/{name}"
    # the identity check ran in choose_host; every remote command below repeats the on-host guard
    r = remote(ctx, host, f"mkdir -p -m 0700 {shlex.quote(rgd)} && ls {shlex.quote(home)}/checkouts")
    if r.rc != 0:
        raise GateError(f"{host.name}: cannot prepare {rgd}: {r.err.strip()[:200]}", 3)
    if remote(ctx, host, f"test -e {shlex.quote(rgd)}/job.sh").rc == 0:
        raise GateError(f"{host.name}: run {name} already exists on the host", 3)
    have = [x for x in r.out.split() if SHA_RE.match(x)]
    bundle = str(run_dir / "in.bundle")
    seed = build_bundle(ctx, st, have, bundle)
    need_bundle = os.path.exists(bundle)
    if need_bundle:
        r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/in.bundle", stdin_path=bundle, timeout=1800)
        if r.rc != 0:
            raise GateError(f"{host.name}: bundle upload failed: {r.err.strip()[:200]}", 3)
        want = sha256_file(bundle)
        got = remote(ctx, host, f"sha256sum {shlex.quote(rgd)}/in.bundle").out.split()[:1]
        if got != [want]:
            raise GateError(f"{host.name}: bundle checksum mismatch after upload", 3)
    job = render_job(host, name, st, lane, proj, seed or "", a.wait_psi, a.extra)
    job_path = run_dir / "job.sh"
    job_path.write_text(job)
    r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/job.sh", stdin_path=str(job_path))
    if r.rc != 0:
        raise GateError(f"{host.name}: job upload failed: {r.err.strip()[:200]}", 3)
    meta = {"name": name, "host": host.name, "lane": lane, "project_dir": proj, "sha": st["sha"], "base": st["base"],
            "seed_checkout": seed, "bundle": need_bundle, "started_utc": ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "remote_dir": rgd, "extra": list(a.extra)}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    if need_bundle:
        os.unlink(bundle)
    r = remote(ctx, host, f"{home}/bin/run-job rg-{name} -- bash {shlex.quote(rgd)}/job.sh")
    if r.rc != 0:
        raise GateError(f"{host.name}: could not start the job: {(r.err or r.out).strip()[:200]}", 3)
    ctx.say(f"started: id={name} host={host.name} sha={st['sha'][:12]} lane={lane} dir={run_dir}")
    ctx.say(f"follow with: remote-gate status {name} | logs {name} | collect {name} | cancel {name}")
    if a.detach:
        return 0
    return follow(ctx, host, meta, run_dir, a.poll)


def read_exit(ctx: Ctx, host: Host, name: str) -> Optional[int]:
    r = remote(ctx, host, f"cat {shlex.quote(host.runner_home)}/jobs/rg-{name}/EXIT 2>/dev/null; true")
    t = r.out.strip()
    return int(t) if t.lstrip("-").isdigit() else None


def tail_log(ctx: Ctx, host: Host, meta: dict, offset: int, which: str = "job.log") -> tuple:
    rgd = meta["remote_dir"]
    path = f"{host.runner_home}/jobs/rg-{meta['name']}/job.log" if which == "job.log" else f"{rgd}/{which}"
    r = remote(ctx, host, f"tail -c +{offset + 1} {shlex.quote(path)} 2>/dev/null; true")
    return r.out, offset + len(r.out.encode())


def follow(ctx: Ctx, host: Host, meta: dict, run_dir: Path, poll: float) -> int:
    off = 0
    while True:
        done = read_exit(ctx, host, meta["name"])
        chunk, off = tail_log(ctx, host, meta, off)
        if chunk:
            ctx.say(chunk.rstrip("\n"))
        if done is not None:
            break
        ctx.sleep(poll)
    return collect(ctx, host, meta, run_dir)


def verdict_of(text: str, lane: str) -> str:
    found = VERDICT_RE.findall(text)
    return found[-1][1] if found else "UNKNOWN"


def collect(ctx: Ctx, host: Host, meta: dict, run_dir: Path) -> int:
    identity(ctx, host)
    rgd, name = meta["remote_dir"], meta["name"]
    rc_txt = remote(ctx, host, f"cat {shlex.quote(rgd)}/rc 2>/dev/null; true").out.strip()
    exit_marker = read_exit(ctx, host, name)
    if exit_marker is None:
        raise GateError(f"run {name} is still running (no completion marker yet)", 75)
    rc = int(rc_txt) if rc_txt.lstrip("-").isdigit() else exit_marker
    for fname in ("job.log", "lane.log"):
        src = f"{host.runner_home}/jobs/rg-{name}/job.log" if fname == "job.log" else f"{rgd}/lane.log"
        ctx.runner.run_to_file(host.ssh + [f"{host.guard}; cat {shlex.quote(src)} 2>/dev/null; true"], str(run_dir / fname))
    ctx.runner.run_to_file(host.ssh + [f"{host.guard}; cat {shlex.quote(rgd)}/lane-detail.log 2>/dev/null; true"], str(run_dir / "lane-detail.log"))
    tgz = run_dir / "artifacts.tgz"
    ctx.runner.run_to_file(host.ssh + [f"{host.guard}; cat {shlex.quote(rgd)}/artifacts.tgz 2>/dev/null; true"], str(tgz))
    if tgz.exists() and tgz.stat().st_size > 0:
        art = run_dir / "artifacts"
        art.mkdir(exist_ok=True)
        ctx.runner.run(["tar", "-xzf", str(tgz), "-C", str(art), "--no-same-owner"], timeout=300)
    lane_log = (run_dir / "lane.log").read_text(errors="replace") if (run_dir / "lane.log").exists() else ""
    verdict = verdict_of(lane_log, meta["lane"])
    if rc == 75 and verdict == "UNKNOWN":
        verdict = "PSI_REFUSED"
    if rc == 143:
        verdict = "CANCELLED"
    (run_dir / "result.json").write_text(json.dumps({"lane": meta["lane"], "verdict": verdict, "exit_code": rc}) + "\n")
    ctx.say(f"collected into {run_dir} (job.log, lane.log, artifacts/)")
    ctx.say(f"lane {meta['lane']} verdict {verdict}; exit_code {rc}")
    return rc


def load_meta(ctx: Ctx, rid: str) -> tuple:
    if not NAME_RE.match(rid):
        raise GateError(f"invalid id {rid!r}")
    run_dir = ctx.out_dir / rid
    try:
        meta = json.loads((run_dir / "meta.json").read_text())
    except FileNotFoundError:
        raise GateError(f"no such run {rid!r} under {ctx.out_dir}") from None
    return get_host(ctx, meta["host"]), meta, run_dir


def cmd_status(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, _ = load_meta(ctx, a.id)
    identity(ctx, host)
    done = read_exit(ctx, host, meta["name"])
    state = "running" if done is None else f"finished exit={done}"
    ctx.say(f"{meta['name']} host={host.name} lane={meta['lane']} sha={meta['sha'][:12]}: {state}")
    tail = remote(ctx, host, f"tail -n 4 {shlex.quote(host.runner_home)}/jobs/rg-{meta['name']}/job.log 2>/dev/null; true").out
    ctx.say(tail.rstrip("\n"))
    return 0


def cmd_logs(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, _ = load_meta(ctx, a.id)
    identity(ctx, host)
    which = "lane.log" if a.lane_log else "job.log"
    off = 0
    while True:
        done = read_exit(ctx, host, meta["name"])
        chunk, off = tail_log(ctx, host, meta, off, which)
        if chunk:
            ctx.say(chunk.rstrip("\n"))
        if not a.follow or done is not None:
            return 0
        ctx.sleep(a.poll)


def cmd_collect(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, run_dir = load_meta(ctx, a.id)
    return collect(ctx, host, meta, run_dir)


def cmd_cancel(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, _ = load_meta(ctx, a.id)
    identity(ctx, host)
    rgd = shlex.quote(meta["remote_dir"])
    r = remote(ctx, host, f"test -s {rgd}/pid && kill -TERM \"$(cat {rgd}/pid)\"")
    ctx.say(f"cancel {meta['name']}: {'SIGTERM sent to the job (it stops the lane, removes its containers and checkout)' if r.rc == 0 else 'job not running'}")
    return 0 if r.rc == 0 else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="remote-gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hosts-file", default=os.environ.get("REMOTE_GATE_HOSTS", DEFAULT_HOSTS))
    p.add_argument("--out-dir", default=os.environ.get("REMOTE_GATE_OUT", DEFAULT_OUT))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a lane on a test host")
    r.add_argument("host", choices=[*ALLOWED_HOSTS, "auto"])
    r.add_argument("worktree")
    r.add_argument("lane")
    r.add_argument("--project-dir", default=None, help="project dir relative to the worktree root (default: the worktree path's own subdirectory)")
    r.add_argument("--base", default=None, help="comparison base ref passed to run-gate --base (resolved locally to a sha)")
    r.add_argument("--name", default=None)
    r.add_argument("--detach", action="store_true", help="start and return; use status/logs/collect later")
    r.add_argument("--wait-psi", type=int, default=900, help="seconds the host job may wait for PSI <= 25 before refusing (default 900)")
    r.add_argument("--poll", type=float, default=20)
    r.add_argument("extra", nargs="*", help="after `--`: extra run-gate arguments")
    for n in ("status", "logs", "collect", "cancel"):
        s = sub.add_parser(n)
        s.add_argument("id")
        if n == "logs":
            s.add_argument("-f", "--follow", action="store_true")
            s.add_argument("--lane-log", action="store_true", help="show lane.log instead of the job log")
            s.add_argument("--poll", type=float, default=10)
    return p


def main(argv: Optional[Sequence[str]] = None, ctx: Optional[Ctx] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    a = parser().parse_args(args)
    try:
        if ctx is None:
            ctx = Ctx(runner=Runner(), hosts=load_hosts(a.hosts_file), out_dir=Path(a.out_dir))
        return {"run": cmd_run, "status": cmd_status, "logs": cmd_logs, "collect": cmd_collect, "cancel": cmd_cancel}[a.cmd](ctx, a)
    except GateError as e:
        print(f"remote-gate: {e}", file=sys.stderr)
        return e.code
    except FileNotFoundError as e:
        print(f"remote-gate: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
