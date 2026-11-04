#!/usr/bin/env python3
"""Run a registered Run-Gate lane on a dedicated test host instead of the shared controller host.

usage:
  remote-gate run <host|auto> <worktree-of-any-git-repo> <lane> [--project-dir DIR] [--repo NAME] [--base REF] [--name N] [--detach]
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


DEFAULT_RG_BIN = "/home/vscode/.local/bin/run-gate"  # the cockpit's own run-gate (inside the cockpit container)
REPO_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,40}$")
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
BIN_RE = re.compile(r"^/[A-Za-z0-9._/+-]+$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# set by the job itself; a profile `env` may not override them (they carry the safety posture of the lane)
RESERVED_ENV = {"RUN_GATE_ALLOW_MAIN_CHECKOUT", "ASSAY_GATE_HOST_REPO_ROOT", "ASSAY_GATE_HOSTS_REPO_ROOT", "RUN_GATE_PROFILE",
                "RUN_GATE_TESTER_UNIFIED_IMAGE", "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "LD_PRELOAD", "LD_LIBRARY_PATH"}
REPO_KEYS = {"run_gate_min", "run_gate_bins", "required_images", "uid_check", "admission_check", "tester_image", "env"}
PREREQ_RC = 76  # the job's exit code (and verdict PREREQ_MISSING) when a per-repo prerequisite is not met

# Built-in per-repo prerequisites. A repository not listed here (any other git repo) gets the empty profile: only the
# cockpit and its own run-gate are needed. A host's `repos` table (hosts file) overrides these key by key.
REPO_DEFAULTS = {
    "dstdns": {
        "run_gate_min": "25.0.1",  # `--jobs` on a pyz-pinned consumer (RG-154); the cockpit's own run-gate may be older
        "run_gate_bins": [DEFAULT_RG_BIN, "/home/vscode/.venvs/gate-tools2502/bin/run-gate"],
        "required_images": ["dstdns/test-runner:local"],
        "uid_check": {"image": "dstdns/test-runner:local", "uid": 1003},  # run-gate >= 25.0 refuses `user = "invoker"` without a passwd entry
        "admission_check": True,  # dstdns run-gate.toml [admission] enabled: a published gates cap must exist on the host
    },
}


@dataclass
class RepoProfile:
    """What a repository needs on a host besides the cockpit (all of it checked before the lane starts)."""
    name: str
    run_gate_min: str = ""
    run_gate_bins: list = field(default_factory=lambda: [DEFAULT_RG_BIN])
    required_images: list = field(default_factory=list)
    uid_check: Optional[dict] = None
    admission_check: bool = False
    tester_image: str = ""
    env: dict = field(default_factory=dict)


def build_profile(name: str, *layers: dict) -> RepoProfile:
    """Validate and merge profile layers (later layers win key by key); every value ends up in a shell script, so it is strict."""
    row: dict = {}
    for layer in layers:
        unknown = set(layer) - REPO_KEYS
        if unknown:
            raise GateError(f"repo profile {name!r}: unknown keys {sorted(unknown)}; allowed: {sorted(REPO_KEYS)}")
        row.update(layer)
    prof = RepoProfile(name=name)
    if row.get("run_gate_min"):
        if not VERSION_RE.match(str(row["run_gate_min"])):
            raise GateError(f"repo profile {name!r}: run_gate_min {row['run_gate_min']!r} is not a dotted version")
        prof.run_gate_min = str(row["run_gate_min"])
    if "run_gate_bins" in row:
        bins = row["run_gate_bins"]
        if not isinstance(bins, list) or not bins or not all(isinstance(b, str) and BIN_RE.match(b) for b in bins):
            raise GateError(f"repo profile {name!r}: run_gate_bins must be a non-empty list of absolute in-cockpit paths")
        prof.run_gate_bins = list(bins)
    images = row.get("required_images", [])
    if not isinstance(images, list) or not all(isinstance(i, str) and IMAGE_RE.match(i) for i in images):
        raise GateError(f"repo profile {name!r}: required_images must be a list of image references")
    prof.required_images = list(images)
    uc = row.get("uid_check")
    if uc:
        if not (isinstance(uc, dict) and set(uc) == {"image", "uid"} and isinstance(uc["image"], str) and IMAGE_RE.match(uc["image"])
                and isinstance(uc["uid"], int) and not isinstance(uc["uid"], bool) and uc["uid"] >= 0):
            raise GateError(f"repo profile {name!r}: uid_check must be {{\"image\": <ref>, \"uid\": <int>}}")
        prof.uid_check = {"image": uc["image"], "uid": uc["uid"]}
    prof.admission_check = bool(row.get("admission_check", False))
    timg = row.get("tester_image", "")
    if timg and not (isinstance(timg, str) and IMAGE_RE.match(timg)):
        raise GateError(f"repo profile {name!r}: tester_image {timg!r} is not an image reference")
    prof.tester_image = timg
    env = row.get("env", {})
    if not isinstance(env, dict) or not all(ENV_KEY_RE.match(str(k)) and isinstance(v, str) for k, v in env.items()):
        raise GateError(f"repo profile {name!r}: env must map UPPER_CASE names to strings")
    reserved = sorted(set(env) & RESERVED_ENV)
    if reserved or any(str(k).startswith("RUN_GATE_ALLOW_") for k in env):
        raise GateError(f"repo profile {name!r}: env may not set reserved names {reserved or sorted(k for k in env if k.startswith('RUN_GATE_ALLOW_'))}")
    prof.env = dict(env)
    return prof


@dataclass
class Host:
    name: str
    ssh: list
    api_check: list
    guard: str
    runner_home: str = "/home/runner"
    cockpit: str = "mdt-cockpit-runner"
    image: str = "tester-unified:local"
    repos: dict = field(default_factory=dict)  # per-repo profile overrides for this host (see REPO_DEFAULTS)

    def profile(self, repo: str) -> RepoProfile:
        return build_profile(repo, REPO_DEFAULTS.get(repo, {}), self.repos.get(repo, {}))


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
        if not str(row.get("guard", "")).strip() or not row.get("api_check"):
            raise GateError(f"hosts file: {name} needs a non-empty guard and api_check")
        hosts[name] = Host(name=name, ssh=row["ssh"], api_check=row["api_check"], guard=row["guard"],
                           runner_home=row.get("runner_home", "/home/runner"), cockpit=row.get("cockpit", "mdt-cockpit-runner"),
                           image=row.get("image", "tester-unified:local"), repos=row.get("repos", {}))
        for repo in hosts[name].repos:
            check_repo_name(repo)
            hosts[name].profile(repo)  # validate at load time, not in the middle of a run
    return hosts


def check_repo_name(name: str) -> str:
    if not REPO_NAME_RE.match(name or "") or SHA_RE.match(name) or name.endswith(".git"):
        raise GateError(f"invalid repository name {name!r} (letters, digits, . _ -; it becomes ~/checkouts/<repo>)")
    return name


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


def guarded(host: Host, script: str) -> str:
    """Remote command text that is fail-closed: the guard runs under `set -e` (so `a; b` guards stop at the first failing
    test) and the script runs only if the whole guard succeeded."""
    # a separate `bash -c` (not a subshell): `set -e` is ignored inside a compound command whose status is tested by `&&`
    return f"bash -c {shlex.quote('set -e' + chr(10) + host.guard)} && {{ {script}\n}}"


def remote(ctx: Ctx, host: Host, script: str, stdin_path: Optional[str] = None, timeout: float = 300) -> Result:
    return ctx.runner.run(host.ssh + [guarded(host, script)], stdin_path=stdin_path, timeout=timeout)


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


def repo_name(ctx: Ctx, top: str, override: Optional[str]) -> str:
    """The repository's name: --repo, else the directory of its main checkout (a linked worktree's common git dir is
    `<main checkout>/.git`). Anything else is refused (never guessed from a worktree directory name): the name selects the
    per-repo prerequisite profile, and a wrong guess would silently run with the empty profile (no floor, image or admission checks)."""
    if override:
        return check_repo_name(override)
    common = git(ctx, top, "rev-parse", "--path-format=absolute", "--git-common-dir").out.strip()
    if not common.endswith("/.git"):
        raise GateError(f"cannot derive the repository name: the git common dir {common or '(unknown)'!r} is not <main checkout>/.git; pass --repo NAME", 2)
    path = Path(common).parent
    try:
        return check_repo_name(path.name)
    except GateError:
        raise GateError(f"cannot derive a repository name from the main checkout directory {path.name!r}; pass --repo NAME", 2) from None


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


TAG_GLOB = "refs/tags/*-v[0-9]*"  # release tags of every project (run-gate-v25.1.0, assay-v7.2.0, ...)
TAG_FORMAT = "%(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end) %(refname:short)"
TAG_LINE_RE = re.compile(r"^([0-9a-f]{40,64}) (\S+)$")


def release_tags(ctx: Ctx, st: dict) -> list:
    """(peeled commit, name) for every release tag of the local repository."""
    out = git(ctx, st["top"], "for-each-ref", f"--format={TAG_FORMAT}", TAG_GLOB).out
    return [m.groups() for m in (TAG_LINE_RE.match(x.strip()) for x in out.splitlines()) if m]


def build_bundle(ctx: Ctx, st: dict, have: list, dest: str) -> Optional[str]:
    """Thin bundle of HEAD plus all release tags against the closest commit the host already holds.

    Returns the seed checkout name ("" for none). No bundle file is written when the seed already holds
    HEAD and every tagged commit; the tags themselves always travel in the job script (st["tags"])."""
    best = None
    for c in have:
        if git(ctx, st["top"], "cat-file", "-e", f"{c}^{{commit}}").rc != 0:
            continue
        n = git(ctx, st["top"], "rev-list", "--count", st["sha"], f"^{c}").out.strip()
        if n.isdigit() and (best is None or int(n) < best[0]):
            best = (int(n), c)
    tags = st.get("tags", [])
    if best:
        need = best[0]
        if need == 0 and tags:
            n = git(ctx, st["top"], "rev-list", "--count", *sorted({t[0] for t in tags}), f"^{best[1]}").out.strip()
            need = int(n) if n.isdigit() else 1
        if need == 0:
            return best[1]  # HEAD and every tagged commit already reachable from an existing checkout: no bundle needed
    refs = ["HEAD"] + [f"refs/tags/{t[1]}" for t in tags]
    r = git(ctx, st["top"], "bundle", "create", dest, *refs, *([f"^{best[1]}"] if best else []))
    if r.rc != 0:
        raise GateError(f"git bundle failed: {r.err.strip()[:300]}")
    return best[1] if best else ""


JOB_TEMPLATE = r"""#!/usr/bin/env bash
# remote-gate job for @@NAME@@: generated by remote-gate.py, runs on the test host as the runner user.
set -Euo pipefail
umask 022
D=$HOME/rg/@@NAME@@
CK=rg-@@NAME@@-@@SHORT@@
REPO=@@REPO@@
CKROOT=$HOME/checkouts/$REPO/.worktrees
CKDIR=$CKROOT/$CK
CKIN=/workspaces/$REPO/.worktrees/$CK
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
ENVX=(@@ENVX@@)
RG_MIN=@@RG_MIN@@
RG_BINS=(@@RG_BINS@@)
REQ_IMAGES=(@@REQ_IMAGES@@)
UID_IMAGE=@@UID_IMAGE@@
UID_NUM=@@UID_NUM@@
ADMISSION_CHECK=@@ADMISSION_CHECK@@
LANE_STARTED=0
rc=70
@@PREREQ_FUNCS@@
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
printf '%s\n' "$$" > "$D/pid"
# shellcheck disable=SC2329  # invoked from the TERM trap
cancel_lane() {
  docker exec -u vscode "$COCKPIT" sh -c 'test -s "$1" && kill -TERM "$(cat "$1")"' sh "$PIDF" 2>/dev/null || true
}
# shellcheck disable=SC2329  # invoked from the EXIT trap
finish() {
  local n
  rm -f -- "$D/pid"
  log "cleanup: lane containers of this run, then the throwaway checkout"
  # Only after the lane started, and only containers whose inspect data references THIS run's unique checkout
  # directory name (rg-<name>-<sha12>) as a whole path component; removal is by exact name.
  if (( LANE_STARTED )); then
    while IFS= read -r n; do
      [[ -n $n ]] || continue
      if docker inspect "$n" 2>/dev/null | grep -oE '/rg-[A-Za-z0-9._-]+' | grep -qxF -- "/$CK"; then
        docker rm -f -- "$n" >/dev/null 2>&1 && log "removed container $n"
      fi
    done < <(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^run-gate-' || true)
  fi
  docker exec -u vscode "$COCKPIT" rm -f "$PIDF" 2>/dev/null || true
  if [[ -d $CKDIR && $CK == rg-* && $CKDIR == "$HOME/checkouts/$REPO/.worktrees"/rg-* ]]; then
    docker exec -u vscode "$COCKPIT" rm -rf -- "$CKIN" 2>/dev/null || rm -rf -- "$CKDIR"
    rm -rf -- "$CKDIR"
    log "removed checkout $CKDIR"
  fi
  rm -f -- "$D/in.bundle"
  printf '%s\n' "$rc" > "$D/rc"
  log "done rc=$rc"
}
trap finish EXIT
trap 'log "terminated by cancel"; cancel_lane; rc=143; exit 143' TERM INT

prereq_fail() {
  local m
  for m in "${PREREQ_MSGS[@]}"; do log "PREREQ MISSING: $m"; done
  log "bring-up steps: scripts/netcup/CONSUMERS.md, 'Remote gate' -> 'Per-repo prerequisites'"
  rc=76; exit 76
}
check_prereqs || prereq_fail
{ hostname -f; echo "repo $REPO"; echo "run-gate $RG_VER ($RG_BIN)"
  docker image inspect "$IMAGE" --format 'image {{.Id}} created {{.Created}}' 2>&1; } | sed 's/^/header: /'

waited=0
while psi_high; do
  if (( waited >= PSI_WAIT )); then log "PSI above $PSI_LIMIT after ${waited}s: refusing to start"; rc=75; exit 75; fi
  sleep 30; waited=$((waited + 30))
done
log "PSI ok (waited ${waited}s)"
check_uid "rg-@@NAME@@-uidchk" || prereq_fail   # also checked before the upload when the host was quiet enough; this is the authoritative one

mkdir -p -- "$CKROOT"
if [[ -n $SEED ]]; then
  git -c init.defaultBranch=main clone -q --no-checkout "$HOME/checkouts/$SEED" "$CKDIR"
else
  git -c init.defaultBranch=main init -q "$CKDIR"
fi
if [[ -f $D/in.bundle ]]; then
  git -C "$CKDIR" fetch -q "$D/in.bundle" HEAD
  # release tags from the bundle (annotated ones intact); tags the host already has are left alone
  git -C "$CKDIR" fetch -q "$D/in.bundle" 'refs/tags/*:refs/tags/*' || log "tag fetch from the bundle failed; falling back to the tag list"
fi
# every release tag of the source repository, so tag-dependent tests (run-gate RG-159) see the full set
while read -r tsha tname; do
  [[ -n $tname ]] || continue
  git -C "$CKDIR" show-ref --verify -q "refs/tags/$tname" && continue
  git -C "$CKDIR" cat-file -e "$tsha^{commit}" 2>/dev/null && git -C "$CKDIR" tag -- "$tname" "$tsha"
done <<'TAGS_EOF'
@@TAGS@@
TAGS_EOF
git -C "$CKDIR" checkout -q --detach "$SHA" || { rc=71; exit 71; }
[[ $(git -C "$CKDIR" rev-parse HEAD) == "$SHA" ]] || { rc=71; exit 71; }
if [[ -n $BASE_SHA ]]; then git -C "$CKDIR" cat-file -e "$BASE_SHA^{commit}" || { log "base $BASE_SHA missing on the host"; rc=72; exit 72; }; fi
log "checkout $CKDIR at $SHA"

if (( ADMISSION_CHECK )); then
  # a repo whose run-gate.toml enables admission needs a published gates cap on this host, or every lane ends NOT_RUN no-headroom
  # fail-closed: an erroring `admission show` or an output without an `enabled=true|false` statement is a failed check, not a pass
  adm=$(docker exec -u vscode -w "$CKIN/$PROJ" "$COCKPIT" "$RG_BIN" admission show 2>&1); arc=$?
  if (( arc != 0 )); then
    PREREQ_MSGS=("run-gate admission show failed (rc $arc): $(head -c 200 <<<"$adm" | tr '\n' ' ')")
    prereq_fail
  fi
  if ! grep -qE 'admission: enabled=(true|false)' <<<"$adm"; then
    PREREQ_MSGS=("run-gate admission show printed no 'enabled=true|false' statement (output format changed?): $(head -c 200 <<<"$adm" | tr '\n' ' ')")
    prereq_fail
  fi
  if grep -q 'admission: enabled=true' <<<"$adm" && ! grep -qE 'max_concurrent=[0-9]+' <<<"$adm"; then
    PREREQ_MSGS=("run-gate admission is enabled by the repo but this host publishes no gates cap (run-gate admission set --max-concurrent N --unreadable-policy refuse)")
    prereq_fail
  fi
  log "admission: $(grep -oE 'max_concurrent=[0-9]+' <<<"$adm" | head -n1 || true)"
fi

args=("$LANE" "${EXTRA[@]}")
[[ -n $BASE_SHA ]] && args+=(--base "$BASE_SHA")
log "run-gate ${args[*]} (cwd $CKIN/$PROJ, run-gate $RG_VER)"
LANE_STARTED=1
docker exec -u vscode -w "$CKIN/$PROJ" -e RUN_GATE_TESTER_UNIFIED_IMAGE="$IMAGE" -e RUN_GATE_ALLOW_MAIN_CHECKOUT=1 -e ASSAY_GATE_HOST_REPO_ROOT="$HOME/checkouts/$REPO" -e RUN_GATE_PROFILE=off "${ENVX[@]}" \
  "$COCKPIT" sh -c 'echo $$ > "$1"; b=$2; shift 2; d=${b%/*}; PATH=${d:-/}:$PATH; exec nice -n 19 "$b" "$@"' sh "$PIDF" "$RG_BIN" "${args[@]}" > "$D/lane.log" 2>&1 &
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


PREREQ_FUNCS = r"""PREREQ_MSGS=()
SEEN=()
RG_BIN=
RG_VER=
psi_high() { awk -v lim="$PSI_LIMIT" '/^some/ {sub("avg10=","",$2); if ($2+0 > lim) f=1} END {exit !f}' /proc/pressure/cpu /proc/pressure/memory; }
# PEP 440 ordering: 25.0.1.dev1 < 25.0.1a1 < 25.0.1b1 < 25.0.1rc1 < 25.0.1 < 25.0.1.post1; an unparsable version is never >= anything.
VERCMP='import re, sys
def key(v):
    m = re.fullmatch(r"(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?(?:\+[A-Za-z0-9.]+)?", v)
    if not m:
        sys.exit(2)
    rel = [int(x) for x in m.group(1).split(".")]
    rel += [0] * (8 - len(rel))
    pre = {"a": 1, "b": 2, "rc": 3}[m.group(2)] if m.group(2) else (0 if m.group(5) and not m.group(4) else 4)
    return (rel, pre, int(m.group(3) or 0), int(m.group(4)) if m.group(4) else -1, int(m.group(5)) if m.group(5) else 10**9)
sys.exit(0 if key(sys.argv[1]) >= key(sys.argv[2]) else 1)'
ver_ge() { python3 -c "$VERCMP" "$1" "$2" 2>/dev/null; }  # $1 >= $2
# the version of a run-gate: the last version-looking word of the first `--version` line (so "run-gate (python 3.12) 25.0.2" reads 25.0.2)
ver_of() {
  docker exec -u vscode "$COCKPIT" "$1" --version 2>/dev/null | head -n1 | tr ' ' '\n' \
    | grep -E '^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?(\+[A-Za-z0-9.]+)?$' | tail -n1 || true
}
# the repo's lane user must exist in its image (run-gate >= 25 refuses `user = "invoker"` without a passwd entry); $1 = a unique container name
check_uid() {
  [[ -n $UID_IMAGE ]] || return 0
  if ! docker run --rm --pull=never --network none --name "$1" --entrypoint getent "$UID_IMAGE" passwd "$UID_NUM" >/dev/null 2>&1; then
    PREREQ_MSGS+=("image $UID_IMAGE has no passwd entry for uid $UID_NUM (run-gate >= 25 refuses the lane user; rebuild the image with the uid entry)")
    return 1
  fi
}
# Fills PREREQ_MSGS with every unmet prerequisite and sets RG_BIN/RG_VER to the first cockpit run-gate that satisfies RG_MIN.
check_prereqs() {
  local i b v
  docker exec -u vscode "$COCKPIT" true >/dev/null 2>&1 || PREREQ_MSGS+=("cockpit container $COCKPIT is not running")
  for i in "${REQ_IMAGES[@]}"; do
    docker image inspect "$i" >/dev/null 2>&1 || PREREQ_MSGS+=("docker image $i is not present on this host")
  done
  for b in "${RG_BINS[@]}"; do
    v=$(ver_of "$b")
    [[ -n $v ]] || { SEEN+=("$b=unusable"); continue; }
    if [[ -z $RG_MIN ]] || ver_ge "$v" "$RG_MIN"; then RG_BIN=$b; RG_VER=$v; break; fi
    SEEN+=("$b=$v")
  done
  [[ -n $RG_BIN ]] || PREREQ_MSGS+=("no run-gate >= ${RG_MIN:-any} in the cockpit (tried: ${SEEN[*]:-none})")
  (( ${#PREREQ_MSGS[@]} == 0 ))
}"""

PREFLIGHT = r"""COCKPIT=@@COCKPIT@@
RG_MIN=@@RG_MIN@@
RG_BINS=(@@RG_BINS@@)
REQ_IMAGES=(@@REQ_IMAGES@@)
UID_IMAGE=@@UID_IMAGE@@
UID_NUM=@@UID_NUM@@
PSI_LIMIT=@@PSI_LIMIT@@
@@PREREQ_FUNCS@@
check_prereqs || true
# the lane-user check starts a throwaway container: only on a quiet host (otherwise the job repeats it after its own PSI wait)
if [[ -n $UID_IMAGE ]] && docker image inspect "$UID_IMAGE" >/dev/null 2>&1; then
  if psi_high; then echo "NOTE uid check deferred to the job (PSI above $PSI_LIMIT)"; else check_uid "rg-preflight-uidchk-$$" || true; fi
fi
if (( ${#PREREQ_MSGS[@]} )); then printf 'PREREQ %s\n' "${PREREQ_MSGS[@]}"; else echo "OK $RG_BIN $RG_VER"; fi
"""


def profile_subs(host: Host, prof: RepoProfile) -> dict:
    uc = prof.uid_check or {}
    return {"COCKPIT": shlex.quote(host.cockpit), "PREREQ_FUNCS": PREREQ_FUNCS, "RG_MIN": shlex.quote(prof.run_gate_min),
            "RG_BINS": " ".join(shlex.quote(b) for b in prof.run_gate_bins),
            "REQ_IMAGES": " ".join(shlex.quote(i) for i in prof.required_images),
            "UID_IMAGE": shlex.quote(uc.get("image", "")), "UID_NUM": str(uc.get("uid", "")), "PSI_LIMIT": f"{PSI_LIMIT:g}",
            "ADMISSION_CHECK": "1" if prof.admission_check else "0",
            "ENVX": " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in sorted(prof.env.items())),
            "IMAGE": shlex.quote(prof.tester_image or host.image)}


def substitute(template: str, sub: dict) -> str:
    """One pass, so a value that itself contains @@KEY@@ (a run-gate argument, say) is never expanded again."""
    return re.sub(r"@@([A-Z_]+)@@", lambda m: sub.get(m.group(1), m.group(0)), template)


def render_job(host: Host, name: str, st: dict, lane: str, proj: str, seed: str, psi_wait: int, extra: Sequence[str],
               repo: str = "vbpub", prof: Optional[RepoProfile] = None) -> str:
    prof = prof or host.profile(repo)
    sub = {"NAME": name, "SHORT": st["sha"][:12], "PROJ": shlex.quote(proj), "LANE": shlex.quote(lane), "REPO": shlex.quote(repo),
           "SHA": st["sha"], "BASE": st["base"] or "''", "SEED": shlex.quote(seed), "PSI_LIMIT": f"{PSI_LIMIT:g}",
           "PSI_WAIT": str(int(psi_wait)), "TAGS": "\n".join(f"{c} {n}" for c, n in st.get("tags", [])),
           "EXTRA": " ".join(shlex.quote(a) for a in extra), **profile_subs(host, prof)}
    return substitute(JOB_TEMPLATE, sub)


def preflight(ctx: Ctx, host: Host, repo: str, prof: RepoProfile) -> str:
    """Read-only check over ssh, before anything is uploaded: cockpit up, required images present, a run-gate that meets the floor.
    Returns the selected in-cockpit run-gate path."""
    script = substitute(PREFLIGHT, profile_subs(host, prof))
    r = remote(ctx, host, f"bash -c {shlex.quote(script)}", timeout=120)
    if r.rc != 0:
        raise GateError(f"{host.name}: prerequisite check for repo {repo} could not run (rc {r.rc}): {(r.err or r.out).strip()[:200]}", 3)
    lines = [x.strip() for x in r.out.splitlines() if x.strip()]
    missing = [x[len("PREREQ "):] for x in lines if x.startswith("PREREQ ")]
    ok = [x.split() for x in lines if x.startswith("OK ")]
    if missing or not ok:
        raise GateError(f"{host.name} does not meet the prerequisites of repo {repo}:\n  - " + "\n  - ".join(missing or ["no answer from the host check"])
                        + "\nbring-up steps: scripts/netcup/CONSUMERS.md, 'Remote gate' -> 'Per-repo prerequisites'", 4)
    return ok[0][1]


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
    repo = repo_name(ctx, st["top"], a.repo)
    host = choose_host(ctx, a.host)
    prof = host.profile(repo)
    rg_bin = preflight(ctx, host, repo, prof)  # refuses (exit 4) before anything is uploaded or created on the host
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
    st["tags"] = release_tags(ctx, st)
    seed =build_bundle(ctx, st, have, bundle)
    need_bundle = os.path.exists(bundle)
    if need_bundle:
        r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/in.bundle", stdin_path=bundle, timeout=1800)
        if r.rc != 0:
            raise GateError(f"{host.name}: bundle upload failed: {r.err.strip()[:200]}", 3)
        want = sha256_file(bundle)
        got = remote(ctx, host, f"sha256sum {shlex.quote(rgd)}/in.bundle").out.split()[:1]
        if got != [want]:
            raise GateError(f"{host.name}: bundle checksum mismatch after upload", 3)
    job = render_job(host, name, st, lane, proj, seed or "", a.wait_psi, a.extra, repo, prof)
    job_path = run_dir / "job.sh"
    job_path.write_text(job)
    r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/job.sh", stdin_path=str(job_path))
    if r.rc != 0:
        raise GateError(f"{host.name}: job upload failed: {r.err.strip()[:200]}", 3)
    meta = {"name": name, "host": host.name, "repo": repo, "run_gate": rg_bin, "lane": lane, "project_dir": proj, "sha": st["sha"], "base": st["base"],
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
    if r.rc != 0:  # ssh down or the on-host guard failed: not "still running"
        raise GateError(f"{host.name}: remote check failed (rc {r.rc}): {(r.err or r.out).strip()[:200]}", 3)
    t = r.out.strip()
    return int(t) if t.lstrip("-").isdigit() else None


def tail_log(ctx: Ctx, host: Host, meta: dict, offset: int, which: str = "job.log") -> tuple:
    rgd = meta["remote_dir"]
    path = f"{host.runner_home}/jobs/rg-{meta['name']}/job.log" if which == "job.log" else f"{rgd}/{which}"
    r = remote(ctx, host, f"tail -c +{offset + 1} {shlex.quote(path)} 2>/dev/null; true")
    if r.rc != 0:
        raise GateError(f"{host.name}: remote log read failed (rc {r.rc}): {(r.err or r.out).strip()[:200]}", 3)
    return r.out, offset + len(r.out.encode(errors="replace"))


MAX_POLL_FAILURES = 5


def poll_once(ctx: Ctx, host: Host, meta: dict, off: int, fails: int, which: str = "job.log") -> tuple:
    """One bounded-retry poll step -> (done, new_offset, consecutive_failures); raises after MAX_POLL_FAILURES in a row."""
    try:
        done = read_exit(ctx, host, meta["name"])
        chunk, off = tail_log(ctx, host, meta, off, which)
    except GateError as e:
        fails += 1
        if fails >= MAX_POLL_FAILURES:
            raise GateError(f"giving up after {fails} consecutive failed polls of {meta['name']} on {host.name}: {e}", 3) from None
        return None, off, fails
    if chunk:
        ctx.say(chunk.rstrip("\n"))
    return (done if done is not None else None), off, 0


def follow(ctx: Ctx, host: Host, meta: dict, run_dir: Path, poll: float) -> int:
    off, fails, done = 0, 0, None
    while True:
        done, off, fails = poll_once(ctx, host, meta, off, fails)
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
        ctx.runner.run_to_file(host.ssh + [guarded(host, f"cat {shlex.quote(src)} 2>/dev/null; true")], str(run_dir / fname))
    ctx.runner.run_to_file(host.ssh + [guarded(host, f"cat {shlex.quote(rgd)}/lane-detail.log 2>/dev/null; true")], str(run_dir / "lane-detail.log"))
    tgz = run_dir / "artifacts.tgz"
    ctx.runner.run_to_file(host.ssh + [guarded(host, f"cat {shlex.quote(rgd)}/artifacts.tgz 2>/dev/null; true")], str(tgz))
    if tgz.exists() and tgz.stat().st_size > 0:
        art = run_dir / "artifacts"
        art.mkdir(exist_ok=True)
        ctx.runner.run(["tar", "-xzf", str(tgz), "-C", str(art), "--no-same-owner"], timeout=300)
    lane_log = (run_dir / "lane.log").read_text(errors="replace") if (run_dir / "lane.log").exists() else ""
    verdict = verdict_of(lane_log, meta["lane"])
    if rc == 75 and verdict == "UNKNOWN":
        verdict = "PSI_REFUSED"
    if rc == PREREQ_RC and verdict == "UNKNOWN":
        verdict = "PREREQ_MISSING"
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
    off, fails = 0, 0
    while True:
        done, off, fails = poll_once(ctx, host, meta, off, fails, which)
        if not a.follow or done is not None:
            if not a.follow and fails:
                raise GateError(f"{host.name}: could not read the log", 3)
            return 0
        ctx.sleep(a.poll)


def cmd_collect(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, run_dir = load_meta(ctx, a.id)
    return collect(ctx, host, meta, run_dir)


def cmd_cancel(ctx: Ctx, a: argparse.Namespace) -> int:
    host, meta, _ = load_meta(ctx, a.id)
    identity(ctx, host)
    rgd = shlex.quote(meta["remote_dir"])
    # finish() removes the pid file; as a second line of defence the pid is signalled only while its cmdline is this run's job.sh
    script = (f"test -s {rgd}/pid && p=$(cat {rgd}/pid) && case \"$p\" in ''|*[!0-9]*) false;; esac && "
              f"tr '\\0' ' ' < /proc/$p/cmdline | grep -qF -- {shlex.quote(meta['remote_dir'] + '/job.sh')} && kill -TERM \"$p\"")
    r = remote(ctx, host, script)
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
    r.add_argument("--repo", default=None, help="repository name = ~/checkouts/<repo> on the host (default: the directory of the worktree's main checkout)")
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
