#!/usr/bin/env python3
"""Run a registered Run-Gate lane on a dedicated test host instead of the shared controller host.

usage:
  remote-gate run <host|auto> <worktree-of-any-git-repo> <lane> [--project-dir DIR] [--repo NAME] [--base REF] [--name N] [--detach]
              [--wait-psi SECONDS] [--mem-need-mb MIB] [--mem-floor-mb MIB] [--wait-mem SECONDS] [--out-dir DIR]
              [--collect-store REL]... [--seed-store LOCALDIR=REL]... [--store-max-mib MIB] [-- extra run-gate args...]
  remote-gate status|logs|collect|cancel <id> [--out-dir DIR]

The host table (ssh argv, provider identity check, guard, cockpit name) lives in a gitignored JSON file
(`--hosts-file`, $REMOTE_GATE_HOSTS or ~/.config/remote-gate/hosts.json); see remote-gate-hosts.example.json.
All process execution goes through the `Runner` object so tests inject a fake one (stdlib only).
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
import tomllib
from dataclasses import dataclass, field, replace
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
STORE_MAX_MIB = 512  # --store-max-mib default: uncompressed tar bytes of one store directory (collect refuses above it)
STORE_RC = 77  # `collect` exit code when the run itself ended 0 but its fetched store archive failed its checksum (the lane verdict is untouched)
SEED_RC = 78  # the job's exit code (verdict STORE_SEED_FAILED) when a --seed-store upload could not be installed: a transport error, never a lane verdict
STORE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._@+=,:-]+$")
STORE_LOG_RE = re.compile(r"^\S+ (STORE-(?:ABSENT|TOO-LARGE|COLLECT-FAILED|COLLECTED) .*)$", re.M)
PREREQ_RC = 76  # the job's exit code (and verdict PREREQ_MISSING) when a per-repo prerequisite is not met

# Built-in per-repo prerequisites. A repository not listed here (any other git repo) gets the empty profile: only the
# cockpit and its own run-gate are needed. A host's `repos` table (hosts file) overrides these key by key.
REPO_DEFAULTS = {
    "vbpub": {
        # the estate's lane configs are written against run-gate 25.x (the controller runs 25.1.x); r1002's default cockpit run-gate
        # is 24.3.0 (v1001: 25.0.0), so the host's gate-tools2502 venv (25.0.2) is taken when the default is below the floor
        "run_gate_min": "25.0.1",
        "run_gate_bins": [DEFAULT_RG_BIN, "/home/vscode/.venvs/gate-tools2502/bin/run-gate"],
    },
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
    """(cpu some avg10, memory some avg10, io full avg10). CPU PSI is a stall measure, not utilisation: it only ranks hosts."""
    r = remote(ctx, host, "{ awk '/^some/ {sub(\"avg10=\",\"\",$2); print $2}' /proc/pressure/cpu /proc/pressure/memory; "
                          "awk '/^full/ {sub(\"avg10=\",\"\",$2); print $2}' /proc/pressure/io; }", timeout=60)
    vals = r.out.split()
    if r.rc != 0 or len(vals) != 3:
        raise GateError(f"{host.name}: cannot read PSI over ssh (rc {r.rc}): {r.err.strip()[:200]}", 3)
    return float(vals[0]), float(vals[1]), float(vals[2])


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
        cpu, mem, io = psi_of(ctx, host)
        rows.append(f"{name} mem_some={mem} io_full={io} cpu_some={cpu}")
        # refusal signals are memory some / io full only (operator 2026-11-04b); CPU PSI just prefers the less busy host
        if max(mem, io) <= PSI_LIMIT and (best is None or cpu < best[0]):
            best = (cpu, host)
    if best is None:
        raise GateError(f"refusing: every test host is above {PSI_LIMIT:g} on memory PSI some avg10 or io PSI full avg10 ({'; '.join(rows)}); retry when quiet", 75)
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


MAIN_NAMES = ("main", "origin/main", "refs/heads/main", "refs/remotes/origin/main")  # names that mean origin/main on the host
FETCH_TIMEOUT = 60


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
    # `main` on the host means origin/main (never the controller's local branch, which can lag by dozens of commits): lanes that declare
    # `base = "main"` or `"origin/main"` resolve those names inside the throwaway checkout, which has no branches. The fetch comes first so
    # that `--base main` resolves to the same fetched commit. Explicit refspec (a single-branch clone's configured one would only move FETCH_HEAD),
    # no tags / auto-maintenance in the shared repo, no credential prompt, bounded time.
    fetched = ctx.runner.run(["env", "GIT_TERMINAL_PROMPT=0", "git", "-C", top_path, "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                              "fetch", "-q", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"], timeout=FETCH_TIMEOUT)
    main = git(ctx, top_path, "for-each-ref", "--format=%(objectname)", "refs/remotes/origin/main").out.strip()
    main = main if SHA_RE.match(main) else None
    if fetched.rc != 0:
        ctx.say(f"WARNING: `git fetch origin main` failed ({fetched.err.strip()[:120] or 'rc ' + str(fetched.rc)}); using the existing origin/main {main or '(none)'}")
    elif not main:
        ctx.say("WARNING: no origin/main after the fetch; lanes with base = main or origin/main will not resolve on the host")
    base_sha = None
    if base in MAIN_NAMES:  # the name means the fetched origin/main, never the stale local branch
        if not main:
            raise GateError(f"--base {base!r} means origin/main, which is not available (fetch failed and no origin/main exists)")
        base_sha = main
    elif base:
        r = git(ctx, top_path, "rev-parse", "--verify", f"{base}^{{commit}}")
        if r.rc != 0:
            raise GateError(f"--base {base!r} does not resolve")
        base_sha = r.out.strip()
    prefix = git(ctx, wt, "rev-parse", "--show-prefix").out.strip().rstrip("/")
    return {"top": top_path, "sha": sha, "base": base_sha, "prefix": prefix, "main": main}


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def lane_checks(ctx: Ctx, st: dict, proj: str, lane: str, base_given: bool, allow_exec: bool) -> None:
    """Refuse locally, before anything is shipped, what the committed lane declaration guarantees to fail on a host:
    a pinned comparison base with --base (assay: base_source 'declared' refuses --request-base) and an exec-mode environment
    without a container_name (run-gate then needs the worktree's own `ciu up` stack; a throwaway checkout has none).
    Reads the declaration from the worktree (clean, equal to HEAD); an absent or unparsable file means no opinion."""
    top = Path(st["top"])
    project = _toml(top / proj / "run-gate.toml")
    central = _toml(top / "run-gate.root.toml")
    # run-gate: `cfg.get("assay", central.get("assay", {}))` is the [assay] defaults table (environment, command, pins, import)
    assay_cfg = project.get("assay", central.get("assay", {}))
    assay_cfg = assay_cfg if isinstance(assay_cfg, dict) else {}
    declared = {**(central.get("lanes") or {}), **(project.get("lanes") or {})}
    imp = assay_cfg.get("import")
    imp = imp if isinstance(imp, dict) else {}
    imported = imp.get("lanes")

    def find(name: str) -> Optional[dict]:
        """The lane as run-gate resolves it: declared, else an `[assay.import]` lane (kind assay, assay_lane = its name, the import environment)."""
        if isinstance(declared.get(name), dict):
            return declared[name]
        if imp.get("environment") and (imported == "all" or (isinstance(imported, list) and name in imported)):
            return {"kind": "assay", "environment": imp["environment"], "assay_lane": name}
        return None

    decl = find(lane)
    if decl is None:
        if project or central:
            ctx.say(f"WARNING: lane {lane!r} is not declared in {proj}/run-gate.toml or run-gate.root.toml; run-gate will report an unknown lane")
        return
    if decl.get("kind") == "assay":
        alane = decl.get("assay_lane", lane)
        judge = ((_toml(top / proj / "assay.toml").get("lanes") or {}).get(alane) or {}).get("judge") or {}
        src = judge.get("base_source") or ("declared" if judge.get("base") else None)
        if base_given and src == "declared":
            raise GateError(
                f"--base refused: assay lane {alane!r} (run-gate lane {lane!r}) declares its comparison base "
                f"(judge.base = {judge.get('base', '?')!r}, base_source 'declared') and assay refuses --request-base; "
                "drop --base (or set judge.base_source = \"request\" in assay.toml)", 2)
        if src == "declared" and not base_given and judge.get("base"):  # an unresolvable declared base fails on the host after the upload
            ref = str(judge["base"])
            host_main = ref in MAIN_NAMES and "main" in st  # these names resolve to the shipped origin/main whatever local branches exist
            if (not st["main"] if host_main else git(ctx, st["top"], "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").rc != 0):
                raise GateError(
                    f"refused: assay lane {alane!r} (run-gate lane {lane!r}) declares judge.base = {ref!r}, which does not resolve in this "
                    "checkout" + (" (no origin/main after the fetch)" if host_main else "") + "; the host would fail it with GIT_FAILED. "
                    "Fix the ref, fetch it, or run `git fetch origin main`", 2)
    if allow_exec:
        return
    seen: set = set()

    def check_env(name: str, d: dict) -> None:
        if name in seen:
            return
        seen.add(name)
        if d.get("kind") == "sequence":
            for member in d.get("lanes") or []:
                m = find(member) if isinstance(member, str) else None
                if m is not None:
                    check_env(member, m)
            return
        # run-gate gives an assay lane without its own `environment` the [assay] default
        env_name = d.get("environment") or (assay_cfg.get("environment") if d.get("kind") == "assay" else None)
        env = ((project.get("environments") or {}).get(env_name) or (central.get("environments") or {}).get(env_name) or {}) if env_name else {}
        if env.get("mode") == "exec" and not env.get("container_name"):
            via = "" if name == lane else f" (member {name!r} of the sequence)"
            raise GateError(
                f"lane {lane!r}{via} runs in the exec-mode environment {env_name!r} without a declared container_name: run-gate needs the "
                "worktree's own started stack (`ciu up --dir <test-runner stack> --deploy`), which a throwaway host checkout never has "
                "and this helper does not start. Run it on the controller, use a container-mode variant of the lane, or pass "
                "--allow-exec-env to try anyway", 2)

    check_env(lane, decl)


TAG_GLOB = "refs/tags/*-v[0-9]*"  # release tags of every project (run-gate-v25.1.0, assay-v7.2.0, ...)
TAG_FORMAT = "%(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end) %(refname:short)"
TAG_LINE_RE = re.compile(r"^([0-9a-f]{40,64}) (\S+)$")


def release_tags(ctx: Ctx, st: dict) -> list:
    """(peeled commit, name) for every release tag of the local repository."""
    out = git(ctx, st["top"], "for-each-ref", f"--format={TAG_FORMAT}", TAG_GLOB).out
    return [m.groups() for m in (TAG_LINE_RE.match(x.strip()) for x in out.splitlines()) if m]


BASE_REF = "refs/remote-gate/base"  # temporary local ref (and bundle head) naming the --base commit


def build_bundle(ctx: Ctx, st: dict, have: list, dest: str) -> Optional[str]:
    """Thin bundle of HEAD plus all release tags against the closest commit the host already holds.

    Returns the seed checkout name ("" for none). No bundle file is written when the seed already holds
    HEAD and every tagged commit; the tags themselves always travel in the job script (st["tags"])."""
    tips = [st["sha"]] + ([st["base"]] if st.get("base") else []) + ([st["main"]] if st.get("main") else [])  # --base and main must be on the host too (job exit 72 otherwise)
    best = None
    for c in have:
        if git(ctx, st["top"], "cat-file", "-e", f"{c}^{{commit}}").rc != 0:
            continue
        n = git(ctx, st["top"], "rev-list", "--count", *tips, f"^{c}").out.strip()
        if n.isdigit() and (best is None or int(n) < best[0]):
            best = (int(n), c)
    tags = st.get("tags", [])
    if best:
        need = best[0]
        if need == 0 and tags:
            n = git(ctx, st["top"], "rev-list", "--count", *sorted({t[0] for t in tags}), f"^{best[1]}").out.strip()
            need = int(n) if n.isdigit() else 1
        if need == 0:
            return best[1]  # HEAD, the base and every tagged commit already reachable from an existing checkout: no bundle needed
    refs = ["HEAD"] + (["refs/remotes/origin/main"] if st.get("main") else []) + [f"refs/tags/{t[1]}" for t in tags]
    base_ref = ""
    if st.get("base"):
        # git bundle only carries named refs (a bare sha is silently dropped), so name the base for the duration of the build
        base_ref = BASE_REF
        r = git(ctx, st["top"], "update-ref", base_ref, st["base"])
        if r.rc != 0:
            raise GateError(f"cannot name the base commit for the bundle: {r.err.strip()[:300]}")
        refs.append(base_ref)
    try:
        r = git(ctx, st["top"], "bundle", "create", dest, *refs, *([f"^{best[1]}"] if best else []))
    finally:
        if base_ref:
            git(ctx, st["top"], "update-ref", "-d", base_ref)
    if r.rc != 0:
        raise GateError(f"git bundle failed: {r.err.strip()[:300]}")
    return best[1] if best else ""


STORE_FUNCS = r"""# --collect-store: packing of the run's own store directories, before the throwaway checkout is removed. $CKDIR, $D, $STORES,
# $STORE_MAX_BYTES and log() come from the job. Every outcome is one STORE-* log line; nothing here ever changes the lane's rc.
STORE_PATH_RE='^[A-Za-z0-9._@+=,:-]+(/[A-Za-z0-9._@+=,:-]+)*$'
STORE_WHY=
# store_rel_check <rel>: 0 when <rel> is a canonical relative path (no absolute, empty, '.', '..' or '.git' component) none of whose
# components below $CKDIR is a symlink; otherwise 1 with STORE_WHY = invalid-path | symlink.
store_rel_check() {
  local rel=$1 comp p=$CKDIR
  local -a parts
  STORE_WHY=
  if [[ ! $rel =~ $STORE_PATH_RE ]]; then STORE_WHY=invalid-path; return 1; fi
  IFS=/ read -r -a parts <<<"$rel"
  for comp in "${parts[@]}"; do
    if [[ $comp == . || $comp == .. || $comp == .git ]]; then STORE_WHY=invalid-path; return 1; fi
    p+=/$comp
    if [[ -L $p ]]; then STORE_WHY=symlink; return 1; fi
  done
}
# One archive for all stores: assay-store.tgz (members keep their <rel>/ prefix, so the archive says where each store lives) plus
# assay-store.tgz.sha256 (`sha256sum` format). A store that is absent, too large or refused is left out and reported on its own line.
collect_stores() {
  local rel bytes err
  local -a ok=()
  (( ${#STORES[@]} )) || return 0
  for rel in "${STORES[@]}"; do
    if ! store_rel_check "$rel"; then log "STORE-COLLECT-FAILED $rel $STORE_WHY"; continue; fi
    if [[ ! -e $CKDIR/$rel ]]; then log "STORE-ABSENT $rel"; continue; fi
    if [[ ! -d $CKDIR/$rel ]]; then log "STORE-COLLECT-FAILED $rel not-a-directory"; continue; fi
    # the tree was written by code under test: anything but regular files and directories (a symlink, hardlink, fifo, device) could
    # make the consumer's `tar -xzf` write outside its target, so such a store is refused whole
    if [[ -n $(find "$CKDIR/$rel" ! -type f ! -type d -print -quit 2>/dev/null) ]]; then log "STORE-COLLECT-FAILED $rel special-file"; continue; fi
    if ! bytes=$(tar -cf - -C "$CKDIR" -- "$rel" 2>"$D/assay-store.err" | wc -c); then
      err=$(head -c 200 "$D/assay-store.err" 2>/dev/null | tr '\n' ' ')
      log "STORE-COLLECT-FAILED $rel unreadable ${err:-no-detail}"; continue
    fi
    if (( bytes > STORE_MAX_BYTES )); then log "STORE-TOO-LARGE $rel $bytes"; continue; fi
    ok+=("$rel")
  done
  rm -f -- "$D/assay-store.err"
  (( ${#ok[@]} )) || return 0
  rm -f -- "$D/assay-store.tgz" "$D/assay-store.tgz.sha256" "$D/assay-store.tgz.part"
  if tar -czf "$D/assay-store.tgz.part" -C "$CKDIR" -- "${ok[@]}" 2>"$D/assay-store.err" \
     && mv -f -- "$D/assay-store.tgz.part" "$D/assay-store.tgz" \
     && ( cd "$D" && sha256sum assay-store.tgz > assay-store.tgz.sha256 ); then
    log "STORE-COLLECTED ${ok[*]}"
  else
    err=$(head -c 200 "$D/assay-store.err" 2>/dev/null | tr '\n' ' ')
    for rel in "${ok[@]}"; do log "STORE-COLLECT-FAILED $rel pack-failed ${err:-no-detail}"; done
    rm -f -- "$D/assay-store.tgz" "$D/assay-store.tgz.sha256" "$D/assay-store.tgz.part"
  fi
  rm -f -- "$D/assay-store.err"
  return 0
}
# --seed-store: archive n (assay-seed-<n>.tgz + .sha256, uploaded by the controller) goes to SEEDS[n]. Every refusal is
# STORE-SEED-FAILED <rel> <reason> and exit 78 (a transport error; the lane never starts and no lane verdict exists).
seed_fail() { log "STORE-SEED-FAILED $1 $2"; rc=78; exit 78; }
seed_stores() {
  local i=0 rel f bytes want have lst
  (( ${#SEEDS[@]} )) || return 0
  for rel in "${SEEDS[@]}"; do
    f=$D/assay-seed-$i.tgz
    store_rel_check "$rel" || seed_fail "$rel" "$STORE_WHY"
    [[ -s $f && -s $f.sha256 ]] || seed_fail "$rel" missing-archive
    want=$(awk '{print $1; exit}' "$f.sha256" 2>/dev/null); have=$(sha256sum -- "$f" 2>/dev/null | awk '{print $1}')
    [[ -n $want && $want == "$have" ]] || seed_fail "$rel" checksum
    if ! bytes=$(gzip -dc -- "$f" 2>/dev/null | wc -c); then seed_fail "$rel" unreadable-archive; fi
    (( bytes <= STORE_MAX_BYTES )) || seed_fail "$rel" "too-large $bytes"
    # only regular files and directories, every member inside <rel>/. The WHOLE listing is read (a reader that stops at its first
    # match dies of SIGPIPE on a long listing and, under pipefail, the check would fail open).
    lst=$(tar -tvzf "$f" 2>/dev/null) || seed_fail "$rel" unreadable-archive
    if printf '%s\n' "$lst" | awk 'substr($0, 1, 1) !~ /[-d]/ {b = 1} END {exit !b}'; then seed_fail "$rel" special-member; fi
    tar -tzf "$f" 2>/dev/null | awk -v r="$rel" '$0 !~ /(^|\/)\.\.(\/|$)/ && ($0 == r || index($0, r "/") == 1) {next} {bad=1} END {exit bad}' || seed_fail "$rel" member-outside-path
    if [[ -n $(git -C "$CKDIR" ls-files -- "$rel" 2>/dev/null) ]]; then seed_fail "$rel" tracked-files; fi
    rm -rf -- "${CKDIR:?}/$rel"
    mkdir -p -- "$(dirname -- "$CKDIR/$rel")" || seed_fail "$rel" mkdir-failed
    tar -xzf "$f" -C "$CKDIR" --no-same-owner 2>/dev/null || seed_fail "$rel" unpack-failed
    rm -f -- "$f" "$f.sha256"
    i=$((i + 1))
  done
  log "STORE-SEEDED ${SEEDS[*]}"
}"""



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
MAIN_SHA=@@MAIN@@
SEED=@@SEED@@
PSI_LIMIT=@@PSI_LIMIT@@
PSI_WAIT=@@PSI_WAIT@@
MEM_NEED_MB=@@MEM_NEED_MB@@
MEM_FLOOR_MB=@@MEM_FLOOR_MB@@
MEM_WAIT=@@MEM_WAIT@@
MEM_SETTLE=@@MEM_SETTLE@@
# The memory facts are read from the kernel's own /proc/meminfo; mem_admit_ok takes a path argument so a test can hand it
# a fixture, and there is deliberately no environment override on the production path (an env seam here would let a caller
# displace the fact the check exists to read).
PIDF=/tmp/rg-@@NAME@@.pid
EXTRA=(@@EXTRA@@)
ENVX=(@@ENVX@@)
RG_MIN=@@RG_MIN@@
RG_BINS=(@@RG_BINS@@)
REQ_IMAGES=(@@REQ_IMAGES@@)
UID_IMAGE=@@UID_IMAGE@@
UID_NUM=@@UID_NUM@@
ADMISSION_CHECK=@@ADMISSION_CHECK@@
STORES=(@@STORES@@)
SEEDS=(@@SEEDS@@)
STORE_MAX_BYTES=@@STORE_MAX_BYTES@@
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
    done < <({ docker ps -a --filter label=run-gate.lane --format '{{.Names}}' 2>/dev/null; docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^run-gate-' || true; } | sort -u)
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
@@STORE_FUNCS@@
check_prereqs || prereq_fail
{ hostname -f; echo "repo $REPO"; echo "run-gate $RG_VER ($RG_BIN)"
  docker image inspect "$IMAGE" --format 'image {{.Id}} created {{.Created}}' 2>&1; } | sed 's/^/header: /'

waited=0
while psi_high; do
  if (( waited >= PSI_WAIT )); then log "PSI above $PSI_LIMIT (memory some / io full) after ${waited}s: refusing to start"; rc=75; exit 75; fi
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
  # the --base commit (not an ancestor of HEAD when main moved on) travels as its own bundle head
  if [[ -n $BASE_SHA ]] && git ls-remote "$D/in.bundle" refs/remote-gate/base | grep -q .; then
    git -C "$CKDIR" fetch -q "$D/in.bundle" refs/remote-gate/base || log "base fetch from the bundle failed"
  fi
  # the controller's origin/main (lanes declaring `base = "main"` or "origin/main"); its commits ride the bundle as the head refs/remotes/origin/main
  if [[ -n $MAIN_SHA ]] && git ls-remote "$D/in.bundle" refs/remotes/origin/main | grep -q .; then
    git -C "$CKDIR" fetch -q "$D/in.bundle" refs/remotes/origin/main || log "main fetch from the bundle failed"
  fi
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
# origin/main and a local `main` branch, both at the controller's fetched origin/main (the checkout is detached, so main is not checked out):
# `base = "main"` and `base = "origin/main"` resolve to it (`--base main` was already mapped to that commit by the helper)
if [[ -n $MAIN_SHA ]] && git -C "$CKDIR" cat-file -e "$MAIN_SHA^{commit}" 2>/dev/null; then
  git -C "$CKDIR" update-ref refs/remotes/origin/main "$MAIN_SHA" && log "origin/main -> $MAIN_SHA"
  git -C "$CKDIR" update-ref refs/heads/main "$MAIN_SHA" && log "local main -> $MAIN_SHA"
else
  [[ -z $MAIN_SHA ]] || log "origin/main $MAIN_SHA is missing on the host; lanes declaring base = main or origin/main will not resolve it"
  # a seed-cloned checkout carries the seed's own (stale) main and origin/main: with nothing to set, none must survive
  git -C "$CKDIR" update-ref -d refs/remotes/origin/main 2>/dev/null || true
  git -C "$CKDIR" update-ref -d refs/heads/main 2>/dev/null || true
fi

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

# --seed-store: unpack the uploaded store directories into this run's own checkout, after it is prepared and before the lane
seed_stores

# memory admission: one host-wide ticket (fd 9, a fixed path under ~/rg so every caller of this host shares the same inode)
# so two lanes never read the same MemAvailable; the ticket is held until the new lane's memory shows up in MemAvailable
# (MEM_SETTLE seconds after the lane started). A single lane on an idle host always passes: this is not a static budget.
# Only a caller that DECLARES the lane's expected peak is admitted this way. With MEM_NEED_MB=0 there is no peak to compare
# against, so the pre-2026-11-16 behaviour is kept unchanged: no ticket, no floor, no wait, the lane starts immediately.
# (A floor with no peak would make every lane wait for memory no lane asked for, and a ticket nobody needed.)
if (( MEM_NEED_MB > 0 )); then
if ! { exec 9>>"$HOME/rg/.mem-admit.lock"; }; then
  log "cannot open the host-wide memory admission ticket ($HOME/rg/.mem-admit.lock): refusing to start"
  rc=75; exit 75
fi
flock -w "$MEM_WAIT" 9 || { log "memory admission ticket not obtained within ${MEM_WAIT}s: refusing to start"; rc=75; exit 75; }
waited=0
until mem_admit_ok; do
  admitted=$?
  if (( admitted == 2 )); then log "memory admission: ${MEM_LAST}: refusing to start"; rc=75; exit 75; fi
  if (( waited >= MEM_WAIT )); then log "memory admission: ${MEM_LAST}: refusing to start after ${waited}s"; rc=75; exit 75; fi
  (( waited % 60 == 0 && waited > 0 )) && log "memory admission: waiting (${MEM_LAST})"
  sleep 10; waited=$((waited + 10))
done
log "memory admission ok (waited ${waited}s): ${MEM_LAST}"
fi
args=("$LANE" "${EXTRA[@]}")
[[ -n $BASE_SHA ]] && args+=(--base "$BASE_SHA")
log "run-gate ${args[*]} (cwd $CKIN/$PROJ, run-gate $RG_VER)"
LANE_STARTED=1
docker exec -u vscode -w "$CKIN/$PROJ" -e RUN_GATE_TESTER_UNIFIED_IMAGE="$IMAGE" -e RUN_GATE_ALLOW_MAIN_CHECKOUT=1 -e ASSAY_GATE_HOST_REPO_ROOT="$HOME/checkouts/$REPO" -e RUN_GATE_PROFILE=off "${ENVX[@]}" \
  "$COCKPIT" sh -c 'echo $$ > "$1"; b=$2; shift 2; d=${b%/*}; PATH=${d:-/}:$PATH; exec nice -n 19 "$b" "$@"' sh "$PIDF" "$RG_BIN" "${args[@]}" > "$D/lane.log" 2>&1 9>&- &
lanepid=$!
if (( MEM_NEED_MB > 0 )); then
  ( sleep "$MEM_SETTLE" ) &   # inherits fd 9: keeps the admission ticket until the lane's memory is visible
  exec 9>&-
fi
wait "$lanepid"
rc=$?
while kill -0 "$lanepid" 2>/dev/null; do wait "$lanepid"; rc=$?; done
log "lane exited rc=$rc"
detail=$(grep -o 'log /tmp/run-gate/lanes/[^ ;]*\.log' "$D/lane.log" | tail -1 | cut -d' ' -f2 || true)
if [[ -n $detail ]]; then docker exec -u vscode "$COCKPIT" cat -- "$detail" > "$D/lane-detail.log" 2>/dev/null || true; fi

( cd "$CKDIR/$PROJ" 2>/dev/null && find . \( -path './.assay/*' -o -path './.run-gate/*' \) -type f \
    \( -name 'verdict*.json' -o -name 'progress*.jsonl' -o -name 'coverage*.json' -o -name '*.log' -o -name '*.xml' \) -size -50M -print0 \
  | tar --null -T - -czf "$D/artifacts.tgz" ) 2>/dev/null || true
# store collection (--collect-store): HERE, while the throwaway checkout still exists (finish() removes it on exit). It never changes
# the lane's rc: every outcome is one STORE-* log line and, when packed, assay-store.tgz + assay-store.tgz.sha256 in $D.
collect_stores
exit "$rc"
"""


PREREQ_FUNCS = r"""PREREQ_MSGS=()
SEEN=()
RG_BIN=
RG_VER=
COCKPIT_UP=0
# stop signals: memory some avg10 and io full avg10 only; CPU PSI is a stall measure, not utilisation (operator 2026-11-04b)
psi_high() { awk -v lim="$PSI_LIMIT" 'FNR==1 {n++} (n==1 && /^some/) || (n==2 && /^full/) {sub("avg10=","",$2); if ($2+0 > lim) f=1} END {exit !f}' /proc/pressure/memory /proc/pressure/io; }
# Live memory admission (overcommit and paging are by design, so there is no static ceiling): a lane may start when the host's
# MemAvailable minus the lane's expected peak still leaves the control-plane floor. The expected peak is capped so that one lane
# bigger than RAM can always run ALONE (MemTotal - 2 x floor); it then pages by design instead of waiting forever.
# Three outcomes, never two: 0 admitted, 1 wait (memory present, not enough), 2 unreadable (a fact that exists and could not be
# read is NOT "there is plenty", so the caller refuses instead of admitting). $1 is the meminfo to read: the production call
# passes nothing and reads the kernel's own /proc/meminfo; only a test passes a fixture.
MEM_LAST='MemAvailable unreadable'
mem_admit_ok() {
  local total avail need meminfo_file=${1:-/proc/meminfo}
  total=$(awk '/^MemTotal:/{print int($2/1024); exit}' "$meminfo_file" 2>/dev/null) || total=''
  avail=$(awk '/^MemAvailable:/{print int($2/1024); exit}' "$meminfo_file" 2>/dev/null) || avail=''
  if [[ ! $total =~ ^[1-9][0-9]*$ || ! $avail =~ ^[0-9]+$ ]]; then
    MEM_LAST="cannot read MemTotal/MemAvailable from $meminfo_file (MemTotal '${total:-unreadable}', MemAvailable '${avail:-unreadable}')"
    return 2
  fi
  need=$MEM_NEED_MB; (( need > total - 2 * MEM_FLOOR_MB )) && need=$(( total - 2 * MEM_FLOOR_MB ))
  (( need < 0 )) && need=0
  MEM_LAST="MemAvailable ${avail} MiB, lane peak ${need} MiB (declared ${MEM_NEED_MB}), floor ${MEM_FLOOR_MB} MiB, MemTotal ${total} MiB"
  (( avail - need >= MEM_FLOOR_MB ))
}
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
  if docker exec -u vscode "$COCKPIT" true >/dev/null 2>&1; then
    COCKPIT_UP=1
  else
    PREREQ_MSGS+=("cockpit container $COCKPIT is not running")
  fi
  for i in "${REQ_IMAGES[@]}"; do
    docker image inspect "$i" >/dev/null 2>&1 || PREREQ_MSGS+=("docker image $i is not present on this host")
  done
  # A down cockpit cannot answer `--version`; do not turn that transport failure into a fabricated run-gate verdict.
  if (( COCKPIT_UP )); then
    for b in "${RG_BINS[@]}"; do
      v=$(ver_of "$b")
      [[ -n $v ]] || { SEEN+=("$b=unusable"); continue; }
      if [[ -z $RG_MIN ]] || ver_ge "$v" "$RG_MIN"; then RG_BIN=$b; RG_VER=$v; break; fi
      SEEN+=("$b=$v")
    done
    [[ -n $RG_BIN ]] || PREREQ_MSGS+=("no run-gate >= ${RG_MIN:-any} in the cockpit (tried: ${SEEN[*]:-none})")
  fi
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
               repo: str = "vbpub", prof: Optional[RepoProfile] = None, mem_need_mb: int = 0, mem_floor_mb: int = 1024,
               mem_wait: int = 900, mem_settle: int = 20, collect_stores: Sequence[str] = (), store_max_mib: int = STORE_MAX_MIB, seed_stores: Sequence[str] = ()) -> str:
    prof = prof or host.profile(repo)
    sub = {"NAME": name, "SHORT": st["sha"][:12], "PROJ": shlex.quote(proj), "LANE": shlex.quote(lane), "REPO": shlex.quote(repo),
           "SHA": st["sha"], "BASE": st["base"] or "''", "MAIN": st.get("main") or "''", "SEED": shlex.quote(seed), "PSI_LIMIT": f"{PSI_LIMIT:g}",
           "PSI_WAIT": str(int(psi_wait)), "MEM_NEED_MB": str(int(mem_need_mb)), "MEM_FLOOR_MB": str(int(mem_floor_mb)),
           "MEM_WAIT": str(int(mem_wait)), "MEM_SETTLE": str(int(mem_settle)), "TAGS": "\n".join(f"{c} {n}" for c, n in st.get("tags", [])),
           "EXTRA": " ".join(shlex.quote(a) for a in extra), "STORES": " ".join(shlex.quote(x) for x in collect_stores), "SEEDS": " ".join(shlex.quote(x) for x in seed_stores),
           "STORE_MAX_BYTES": str(int(store_max_mib) * 1024 * 1024), "STORE_FUNCS": STORE_FUNCS, **profile_subs(host, prof)}
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
        unmet = missing or ["no answer from the host check"]
        hint = ("\na newer run-gate: scripts/netcup/runner/setup-cockpit-run-gate.sh <version> <sha256> (own venv; the default cockpit run-gate is left alone)"
                if unmet[0].startswith("no run-gate") else "")
        # host-sat-queue.sh records tail -1 of this error; the final line must name the causal/first prerequisite.
        raise GateError(f"{host.name} does not meet the prerequisites of repo {repo}:\n  - " + "\n  - ".join(unmet)
                        + "\nbring-up steps: scripts/netcup/CONSUMERS.md, 'Remote gate' -> 'Per-repo prerequisites'"
                        + hint + f"\nPREREQ {unmet[0]}", 4)
    return ok[0][1]


def default_name(ctx: Ctx, lane: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9]', '', lane)[:20]}-{ctx.now().strftime('%y%m%d%H%M%S')}"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normalise_store_rel(raw: str, top: str, flag: str = "--collect-store") -> str:
    """A store path relative to the run's own host checkout, normalised ('a//b/./c/' -> 'a/b/c'). Absolute paths, '..', an empty
    result and any symlinked component (checked against the local worktree, which the host checkout is a clone of; the host job checks
    again on its own tree) are refused with exit 2 before anything is sent."""
    if not raw or raw.startswith("/"):
        raise GateError(f"{flag} {raw!r} must be a path relative to the repository root, not absolute")
    parts = [x for x in raw.split("/") if x not in ("", ".")]
    if not parts or ".." in parts:
        raise GateError(f"{flag} {raw!r} must name a directory inside the repository checkout (no '..', not the root itself)")
    if ".git" in parts:
        raise GateError(f"{flag} {raw!r}: a '.git' path component is refused (it would replace or read the checkout's repository)")
    bad = [x for x in parts if not STORE_COMPONENT_RE.match(x)]
    if bad:
        raise GateError(f"{flag} {raw!r}: component {bad[0]!r} has characters outside A-Za-z0-9._@+=,:-")
    p = Path(top)
    for comp in parts:
        p = p / comp
        if p.is_symlink():
            raise GateError(f"{flag} {raw!r}: component {comp!r} is a symlink")
    return "/".join(parts)


class _ByteCount:
    """A write-only sink that only counts: the exact uncompressed tar bytes of a seed directory, without a temporary file."""

    def __init__(self) -> None:
        self.n = 0

    def write(self, b: bytes) -> int:
        self.n += len(b)
        return len(b)


def _seed_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    if not (ti.isreg() or ti.isdir()):
        raise GateError(f"--seed-store: {ti.name!r} is not a regular file or directory (symlinks, hardlinks and devices are never uploaded)")
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def write_seed_archive(local: str, rel: str, sink) -> None:
    """The directory `local` as a tar stream whose members are named `<rel>/...` (regular files and directories only)."""
    with tarfile.open(fileobj=sink, mode="w|", format=tarfile.GNU_FORMAT) as tf:
        tf.add(local, arcname=rel, filter=_seed_filter)


def parse_seed(raw: str, top: str, max_mib: int) -> tuple:
    """`<local dir>=<rel>` -> (absolute local dir, normalised rel); exit 2 for a missing or non-directory local side, a bad rel, a
    symlink or special file inside the directory, or more uncompressed tar bytes than --store-max-mib (the host checks again)."""
    if raw.count("=") != 1:
        raise GateError(f"--seed-store {raw!r} must be <local dir>=<path relative to the repository root> (exactly one '=')")
    local, rel = raw.split("=")
    if not local or not os.path.isdir(local):
        raise GateError(f"--seed-store {raw!r}: the local side {local!r} is not an existing directory")
    rel = normalise_store_rel(rel, top, "--seed-store")
    sink = _ByteCount()
    write_seed_archive(local, rel, sink)
    if sink.n > max_mib * 1024 * 1024:
        raise GateError(f"--seed-store {raw!r}: {sink.n} uncompressed tar bytes exceed --store-max-mib {max_mib}")
    return os.path.abspath(local), rel


def upload_seeds(ctx: Ctx, host: Host, run_dir: Path, rgd: str, seeds: Sequence[tuple]) -> None:
    """Archive n = assay-seed-<n>.tgz + .sha256 into the run's host directory over the bundle's transport, checksum-verified."""
    for n, (local, rel) in enumerate(seeds):
        name = f"assay-seed-{n}.tgz"
        tgz = run_dir / name
        with open(tgz, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            write_seed_archive(local, rel, gz)
        want = sha256_file(str(tgz))
        sumf = run_dir / (name + ".sha256")
        sumf.write_text(f"{want}  {name}\n")
        for f in (tgz, sumf):
            r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/{f.name}", stdin_path=str(f), timeout=1800)
            if r.rc != 0:
                raise GateError(f"{host.name}: seed upload of {f.name} failed: {r.err.strip()[:200]}", 3)
        got = remote(ctx, host, f"sha256sum {shlex.quote(rgd)}/{name}").out.split()[:1]
        if got != [want]:
            raise GateError(f"{host.name}: seed checksum mismatch after upload of {name}", 3)
        tgz.unlink()
        sumf.unlink()


def check_mem_args(need_mb: int, floor_mb: int, wait_s: int) -> None:
    """Refuse a nonsensical admission argument by name instead of letting arithmetic turn it into something else: a negative
    expected peak would admit anything, and a zero floor would let a lane consume the whole control plane's memory."""
    if not 0 <= need_mb < 10 ** 7:
        raise GateError(f"--mem-need-mb must be a non-negative number of MiB below 10^7, got {need_mb}", 2)
    if not 1 <= floor_mb < 10 ** 7:
        raise GateError(f"--mem-floor-mb must be a positive number of MiB below 10^7, got {floor_mb}", 2)
    if not 0 <= wait_s < 10 ** 6:
        raise GateError(f"--wait-mem must be a non-negative number of seconds below 10^6, got {wait_s}", 2)


def cmd_run(ctx: Ctx, a: argparse.Namespace) -> int:
    lane = a.lane
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", lane):
        raise GateError(f"invalid lane name {lane!r}")
    check_mem_args(a.mem_need_mb, a.mem_floor_mb, a.wait_mem)
    name = a.name or default_name(ctx, lane)
    if not NAME_RE.match(name):
        raise GateError(f"invalid --name {name!r}")
    if not 1 <= a.store_max_mib < 10 ** 7:
        raise GateError(f"--store-max-mib must be a positive number of MiB below 10^7, got {a.store_max_mib}")
    st = local_state(ctx, a.worktree, a.base)
    stores = list(dict.fromkeys(normalise_store_rel(x, st["top"]) for x in a.collect_store))  # refused here, before a host is contacted
    seeds = [parse_seed(x, st["top"], a.store_max_mib) for x in a.seed_store]
    if len({r for _, r in seeds}) != len(seeds):
        raise GateError("--seed-store names the same target path twice")
    proj = a.project_dir if a.project_dir is not None else (st["prefix"] or ".")
    if proj.startswith("/") or ".." in Path(proj).parts:
        raise GateError("--project-dir must be relative to the worktree root and stay inside it")
    repo = repo_name(ctx, st["top"], a.repo)
    lane_checks(ctx, st, proj, lane, bool(a.base), a.allow_exec_env)  # before a host is contacted or anything shipped
    host = choose_host(ctx, a.host)
    prof = host.profile(repo)
    if a.min_run_gate:
        if not VERSION_RE.match(a.min_run_gate):
            raise GateError(f"--min-run-gate {a.min_run_gate!r} is not a dotted version")
        prof = replace(prof, run_gate_min=a.min_run_gate)
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
    upload_seeds(ctx, host, run_dir, rgd, seeds)
    job = render_job(host, name, st, lane, proj, seed or "", a.wait_psi, a.extra, repo, prof,
                     a.mem_need_mb, a.mem_floor_mb, a.wait_mem, collect_stores=stores, store_max_mib=a.store_max_mib,
                     seed_stores=[r for _, r in seeds])
    job_path = run_dir / "job.sh"
    job_path.write_text(job)
    r = remote(ctx, host, f"cat > {shlex.quote(rgd)}/job.sh", stdin_path=str(job_path))
    if r.rc != 0:
        raise GateError(f"{host.name}: job upload failed: {r.err.strip()[:200]}", 3)
    meta = {"name": name, "host": host.name, "repo": repo, "run_gate": rg_bin, "lane": lane, "project_dir": proj, "sha": st["sha"], "base": st["base"],
            "seed_checkout": seed, "bundle": need_bundle, "started_utc": ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "remote_dir": rgd, "extra": list(a.extra), "collect_store": stores, "store_max_mib": a.store_max_mib, "seed_store": [r for _, r in seeds]}
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


REASON_RE = re.compile(r"verdict [A-Z_]+;(?: exit_code \d+;)? reason ([^;\n]+)")
# A refusal line, not a substring anywhere in the log: every memory-admission refusal ends in "refusing to start", while the
# success line ("memory admission ok (waited Ns): ...") does not — so a lane that was admitted and then failed for some other
# reason is never labelled a memory refusal.
JOB_REASON_RE = re.compile(r"^\S+ (PREREQ MISSING: .*|STORE-SEED-FAILED .*|PSI above .*|memory admission.*refusing to start.*|terminated by cancel|base \S+ missing on the host)$", re.M)
# A memory-admission refusal shares exit 75 with the PSI refusal (both mean "the host said not yet, nothing ran"), so the
# verdict is named from the job's own log rather than folded into PSI_REFUSED: reporting a memory refusal as a PSI refusal
# would certify a cause that was never observed. It must be a refusal LINE (the `refusing to start` phrase, which the
# success line `memory admission ok (waited Ns): ...` does not contain): an admitted lane that later exited 75 is not one.
MEM_REFUSAL_RE = re.compile(r"memory admission.*refusing to start\b")


def memory_refused(job_log: str) -> bool:
    return any(MEM_REFUSAL_RE.search(line.strip()) for line in job_log.splitlines())


# known failure causes of a host run -> what to do about them (matched against the lane's own reason/diagnostics)
REASON_HINTS = (
    ("exec-mode environment", "the lane needs the worktree's own started stack, which remote-gate does not start; run it on the controller"),
    ("does not delegate its comparison base", "the lane declares its base: rerun without --base"),
    ("not inside the declared", "the lane needs /workspaces/<repo> to be a bind mount of its own; the host cockpit mounts all checkouts as /workspaces (backlog NC-14)"),
)


def reason_of(lane_log: str, job_log: str, verdict: str, rc: int) -> str:
    """Why a run did not pass, for the final line: the verdict line's `reason`, the lane script's own last diagnostic line (output not
    prefixed `run-gate:`), else the job log's refusal line. Empty for PASS/FAIL, which carry no reason."""
    if verdict in ("PASS", "FAIL", "CANCELLED") or rc == 0:
        return ""
    parts = []
    m = REASON_RE.findall(lane_log)
    if m:
        parts.append(m[-1].strip())
    diag = [x.strip() for x in lane_log.splitlines() if x.strip() and not x.startswith("run-gate:") and not x.startswith("+ ")]
    if diag and (not parts or diag[-1] not in parts[0]):
        parts.append(diag[-1])
    if not parts:
        j = JOB_REASON_RE.findall(job_log)
        parts.append(j[-1] if j else f"no lane output (job exit {rc}); see job.log")
    text = " | ".join(parts)
    hint = next((h for needle, h in REASON_HINTS if needle in text), "")
    text = " ".join(text.split())
    if len(text) > 400:
        text = text[:397] + "..."
    return text + (f" [hint: {hint}]" if hint else "")


def fetch_store(ctx: Ctx, host: Host, meta: dict, run_dir: Path) -> str:
    """Fetch the run's assay-store.tgz + assay-store.tgz.sha256 (only for a run started with --collect-store) into the run dir and
    verify the checksum locally. Returns "" (fetched and verified, or the job packed nothing: its STORE-* line says why) or a one-line
    error. A fetched archive that fails the checksum is renamed assay-store.tgz.rejected, never left under its usable name."""
    if not meta.get("collect_store"):
        return ""
    rgd = meta["remote_dir"]
    for stale in ("assay-store.tgz", "assay-store.tgz.sha256", "assay-store.tgz.rejected"):
        (run_dir / stale).unlink(missing_ok=True)
    r = remote(ctx, host, f"cat {shlex.quote(rgd)}/assay-store.tgz.sha256 2>/dev/null; true")
    if r.rc != 0:
        return f"STORE-FETCH-FAILED cannot read the store checksum over ssh (rc {r.rc}): {(r.err or r.out).strip()[:200]}"
    text = r.out.strip()
    if not text:
        return ""
    m = re.fullmatch(r"([0-9a-f]{64}) [ *]assay-store\.tgz", text)
    if not m:
        return f"STORE-FETCH-FAILED unreadable checksum file: {text[:120]!r}"
    tgz = run_dir / "assay-store.tgz"
    got = ctx.runner.run_to_file(host.ssh + [guarded(host, f"cat {shlex.quote(rgd)}/assay-store.tgz")], str(tgz))
    if got.rc != 0:
        tgz.unlink(missing_ok=True)
        return f"STORE-FETCH-FAILED cannot fetch assay-store.tgz (rc {got.rc}): {got.err.strip()[:200]}"
    have = sha256_file(str(tgz))
    if have != m.group(1):
        tgz.rename(run_dir / "assay-store.tgz.rejected")
        return f"STORE-CHECKSUM-MISMATCH assay-store.tgz: host {m.group(1)}, fetched {have}; kept as assay-store.tgz.rejected"
    (run_dir / "assay-store.tgz.sha256").write_text(text + "\n")
    return ""


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
    store_err = fetch_store(ctx, host, meta, run_dir)  # a store problem is reported and returned as STORE_RC; it never edits the lane verdict
    lane_log = (run_dir / "lane.log").read_text(errors="replace") if (run_dir / "lane.log").exists() else ""
    verdict = verdict_of(lane_log, meta["lane"])
    job_log = (run_dir / "job.log").read_text(errors="replace") if (run_dir / "job.log").exists() else ""
    if rc == 75 and verdict == "UNKNOWN":
        verdict = "MEM_REFUSED" if memory_refused(job_log) else "PSI_REFUSED"
    if rc == SEED_RC and verdict == "UNKNOWN":
        verdict = "STORE_SEED_FAILED"
    if rc == PREREQ_RC and verdict == "UNKNOWN":
        verdict = "PREREQ_MISSING"
    if rc == 143:
        verdict = "CANCELLED"
    reason = reason_of(lane_log, job_log, verdict, rc)
    result = {"lane": meta["lane"], "verdict": verdict, "exit_code": rc}
    if reason:
        result["reason"] = reason
    (run_dir / "result.json").write_text(json.dumps(result) + "\n")
    ctx.say(f"collected into {run_dir} (job.log, lane.log, artifacts/)")
    for line in STORE_LOG_RE.findall(job_log):
        ctx.say(line)
    if store_err:
        ctx.say(store_err)
    ctx.say(f"lane {meta['lane']} verdict {verdict}; exit_code {rc}" + (f"; reason {reason}" if reason else ""))
    return rc if rc != 0 or not store_err else STORE_RC


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
    r.add_argument("--min-run-gate", default=None, help="require at least this run-gate version in the host cockpit (overrides the repo profile's floor)")
    r.add_argument("--allow-exec-env", action="store_true", help="do not refuse a lane in an exec-mode environment without container_name")
    r.add_argument("--name", default=None)
    r.add_argument("--detach", action="store_true", help="start and return; use status/logs/collect later")
    r.add_argument("--wait-psi", type=int, default=900, help="seconds the host job may wait for memory PSI some / io PSI full <= 25 before refusing (default 900)")
    r.add_argument("--mem-need-mb", type=int, default=0, help="the lane's expected peak memory in MiB (the controller's weight); with a peak above 0 the host job starts the lane only when MemAvailable minus this still leaves --mem-floor-mb, and a host-wide memory ticket is taken. The default 0 declares no peak: no ticket, no floor, no wait (the lane starts immediately)")
    r.add_argument("--mem-floor-mb", type=int, default=1024, help="MiB of MemAvailable that must remain for the control plane after the lane's expected peak (default 1024; a lane larger than RAM runs alone and pages)")
    r.add_argument("--wait-mem", type=int, default=900, help="seconds the host job may wait for memory admission before refusing with exit 75 (default 900)")
    r.add_argument("--collect-store", action="append", default=[], metavar="REL",
                   help="repeatable: pack this directory of the run's OWN host checkout (path relative to the repository root, e.g. scripts/netcup/.run-gate/assay-shards) into assay-store.tgz + assay-store.tgz.sha256 before the checkout is removed; `collect` fetches and verifies them into the run dir. Absent -> STORE-ABSENT, above --store-max-mib -> STORE-TOO-LARGE, failure -> STORE-COLLECT-FAILED; none changes the lane verdict")
    r.add_argument("--seed-store", action="append", default=[], metavar="LOCALDIR=REL",
                   help="repeatable: upload this local directory (regular files and directories only; tar + sha256) and unpack it at REL of the run's own host checkout after the checkout is prepared and before the lane starts, replacing what is there (never tracked files). Any failure ends the run as exit 78 (STORE_SEED_FAILED), never a lane FAIL; the bound is --store-max-mib")
    r.add_argument("--store-max-mib", type=int, default=STORE_MAX_MIB, help=f"bound on the uncompressed tar bytes of each --collect-store directory (default {STORE_MAX_MIB})")
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
