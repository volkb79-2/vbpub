#!/usr/bin/env python3
"""run-gate — the per-project gate entrypoint (one parser, argv for everyone).

Owns ALL gate invocation mechanics that used to live scattered in consumer
config strings: container image + mounts, cgroup slice placement, artifact-pin
verification, clean-tree refusal, detached run form, exit-status passthrough.
Lane declarations live in run-gate.toml next to this script (per project);
shared environment facts may be declared once in an enclosing repo-root
run-gate.toml (nearest ancestor wins; project tables shadow central by name).
Judgment policy is NOT here: assay lanes reference assay.toml by name.

See run-gate-project/README.md (design authority) and CONSUMERS.md (adoption).
"""
# stdlib only — this launcher must run on a fresh clone with zero installs.
__revision__ = 3

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

PROG = "run-gate"
CONFIG_NAME = "run-gate.toml"
SCHEMA_VERSION = 1
CGROUP_ENV_VAR = "CGROUP_PARENT_DEV_BACKGROUND"
HOST_ENV = "host"
EXTRA_MOUNT_ENV_VAR = "RUN_GATE_EXTRA_MOUNTS"


class GateError(Exception):
    """One-line, user-facing failure. Never a traceback for config/env errors."""


def fail(msg: str) -> None:
    raise GateError(msg)


# ---------------------------------------------------------------------------
# config loading + validation (loud, names key + file, no silent defaults)
# ---------------------------------------------------------------------------

def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        fail(f"{CONFIG_NAME} not found at {path} — run-gate resolves it next to "
             f"the invoked script (symlink or copy); create it there")
    except tomllib.TOMLDecodeError as exc:
        fail(f"{path}: invalid TOML: {exc}")


def _check_keys(table: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        fail(f"{where}: unknown key(s) {', '.join(unknown)} "
             f"(allowed: {', '.join(sorted(allowed))})")


def _validate_environment(name: str, table: dict, where: str) -> None:
    if name == HOST_ENV:
        fail(f"{where}: '{HOST_ENV}' is a built-in environment and cannot be redefined")
    _check_keys(table, {"image", "cgroup_slice", "mode", "container_name",
                        "forward_env"},
                f"{where} [environments.{name}]")
    image = table.get("image")
    if not isinstance(image, str) or not image.strip():
        fail(f"{where} [environments.{name}]: 'image' must be a non-empty string")
    slice_ = table.get("cgroup_slice")
    if slice_ is not None and (not isinstance(slice_, str) or not slice_.strip()):
        fail(f"{where} [environments.{name}]: 'cgroup_slice' must be a non-empty string")

    mode = table.get("mode", "ephemeral")
    if mode not in ("ephemeral", "exec"):
        fail(f"{where} [environments.{name}]: 'mode' must be \"ephemeral\" or "
             f"\"exec\" (got {mode!r})")
    container_name = table.get("container_name")
    if container_name is not None and (not isinstance(container_name, str)
                                       or not container_name.strip()):
        fail(f"{where} [environments.{name}]: 'container_name' must be a non-empty string")
    forward_env = table.get("forward_env", [])
    if not isinstance(forward_env, list) or any(
            isinstance(item, bool) or not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)
            for item in forward_env):
        fail(f"{where} [environments.{name}]: 'forward_env' must be a list of "
             f"environment-variable names")
    if len(set(forward_env)) != len(forward_env):
        fail(f"{where} [environments.{name}]: 'forward_env' contains duplicates")


def _validate_budget(value: object, where: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+[smh]", value):
        fail(f"{where}: 'budget' must look like '30s', '20m' or '2h' (got {value!r})")


def _validate_memory(value: object, where: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+[bkmg]?", value, re.IGNORECASE):
        fail(f"{where}: 'memory' must look like '536870912', '512m' or '4g' (got {value!r})")


def _validate_lane(name: str, table: dict, where: str) -> None:
    _check_keys(
        table,
        {"kind", "environment", "argv", "assay_lane", "assay_command", "pins",
         "clean_tree", "budget", "memory"},
        f"{where} [lanes.{name}]",
    )
    kind = table.get("kind")
    if kind not in ("command", "assay"):
        fail(f"{where} [lanes.{name}]: 'kind' must be \"command\" or \"assay\" (got {kind!r})")
    if not isinstance(table.get("environment"), str) or not table["environment"].strip():
        fail(f"{where} [lanes.{name}]: 'environment' must be a non-empty string")
    if "budget" in table:
        _validate_budget(table["budget"], f"{where} [lanes.{name}]")
    if "memory" in table:
        _validate_memory(table["memory"], f"{where} [lanes.{name}]")
    if kind == "command":
        argv = table.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            fail(f'{where} [lanes.{name}]: kind "command" requires a non-empty '
                 f"string list 'argv'")
    else:
        if not isinstance(table.get("assay_lane"), str) or not table["assay_lane"].strip():
            fail(f'{where} [lanes.{name}]: kind "assay" requires a non-empty '
                 f"string 'assay_lane' (the lane name in the project's assay.toml)")
        cmd = table.get("assay_command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            fail(f'{where} [lanes.{name}]: kind "assay" requires a non-empty string list '
                 f"'assay_command' (e.g. the pinned-pyz interpreter + script path); "
                 f"run-gate never invents an assay invocation")
        pins = table.get("pins", {})
        if not isinstance(pins, dict):
            fail(f"{where} [lanes.{name}]: 'pins' must be a table")
        for pin_name, pin in pins.items():
            if not isinstance(pin, dict) or not isinstance(pin.get("sha256"), str) \
                    or not pin["sha256"].strip():
                fail(f'{where} [lanes.{name}].pins.{pin_name}: requires a non-empty '
                     f"string 'sha256' (path to the .sha256 file, relative to the project)")
            if "version" in pin and not isinstance(pin["version"], str):
                fail(f"{where} [lanes.{name}].pins.{pin_name}: 'version' must be a string")
    if "clean_tree" in table and not isinstance(table["clean_tree"], bool):
        fail(f"{where} [lanes.{name}]: 'clean_tree' must be a boolean")


def _validate_config(cfg: dict, path: Path, *, central: bool) -> dict:
    where = str(path)
    _check_keys(cfg, {"schema_version", "environments", "lanes"}, where)
    if cfg.get("schema_version") != SCHEMA_VERSION:
        fail(f"{where}: 'schema_version' must be {SCHEMA_VERSION} (got "
             f"{cfg.get('schema_version')!r})")
    envs = cfg.get("environments", {})
    lanes = cfg.get("lanes", {})
    if not isinstance(envs, dict) or not isinstance(lanes, dict):
        fail(f"{where}: 'environments' and 'lanes' must be tables")
    if central and lanes:
        fail(f"{where}: a central (repo-root) config defines environment facts only — "
             f"move [lanes.*] into the project config ({sorted(lanes)})")
    for name, table in envs.items():
        _validate_environment(name, table, where)
    for name, table in lanes.items():
        _validate_lane(name, table, where)
    return cfg


def load_config(project_dir: Path) -> tuple[dict, Path, dict, Path | None]:
    """Load the project config + the nearest ancestor (central) config."""
    project_path = project_dir / CONFIG_NAME
    project = _validate_config(_read_toml(project_path), project_path, central=False)
    central_path: Path | None = None
    central: dict = {"environments": {}}
    for parent in project_dir.resolve().parents:  # Path.parents: nearest FIRST
        candidate = parent / CONFIG_NAME
        if candidate.is_file():
            central_path = candidate
            central = _validate_config(_read_toml(candidate), candidate, central=True)
            break
    return project, project_path, central, central_path


def resolve_environment(lane: dict, lane_name: str, project: dict, central: dict,
                        project_path: Path, central_path: Path | None
                        ) -> tuple[dict, str]:
    """Returns (env_table_or_empty_for_host, human source description)."""
    name = lane["environment"]
    if name == HOST_ENV:
        return {}, "built-in 'host'"
    if name in project.get("environments", {}):
        return dict(project["environments"][name]), f"[environments.{name}] in {project_path}"
    if central_path is not None and name in central.get("environments", {}):
        return dict(central["environments"][name]), \
            f"[environments.{name}] in central {central_path}"
    fail(f"[lanes.{lane_name}] in {project_path}: environment '{name}' is not defined "
         f"in {project_path} nor in a central repo-root {CONFIG_NAME}")


def lane_environment_name(lane: dict) -> str:
    return str(lane["environment"])


# ---------------------------------------------------------------------------
# environment-fact derivation (DERIVE / READ / FAIL — never invent)
# ---------------------------------------------------------------------------

_UNESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def _unescape_mountinfo(value: str) -> str:
    return re.sub(r"\\(040|011|012|134)",
                  lambda m: _UNESCAPES[m.group(1)], value)


def physical_path(path: Path, mountinfo_text: str | None = None,
                  container: bool | None = None) -> Path:
    """Map a namespace path to the host path Docker binds, via /proc/self/mountinfo.

    Outside a container the path is already physical. Inside one, the repo MUST
    appear as a bind mount whose mount point contains it; no entry -> hard error
    (a wrong guess would mount the wrong tree silently).
    """
    if container is None:
        container = Path("/.dockerenv").exists()
    if not container:
        return path
    if mountinfo_text is None:
        try:
            mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError as exc:
            fail(f"cannot read /proc/self/mountinfo to derive the physical host path "
                 f"of {path}: {exc}")
    best_mp, best_root = "", ""
    for line in mountinfo_text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        root, mountpoint = _unescape_mountinfo(fields[3]), _unescape_mountinfo(fields[4])
        if mountpoint == "/":
            continue  # the container's own root overlay maps nothing usefully
        if str(path) == mountpoint or str(path).startswith(mountpoint.rstrip("/") + "/"):
            if len(mountpoint) > len(best_mp):
                best_mp, best_root = mountpoint, root
    if not best_mp:
        fail(f"could not derive a physical host path for {path} from /proc/self/mountinfo "
             f"— is the repo bind-mounted into this container?")
    return Path(best_root + str(path)[len(best_mp):])


def git_out(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() \
            else f"exit {proc.returncode}"
        fail(f"git {' '.join(args)} failed in {cwd}: {detail}")
    return proc.stdout.strip()


def resolve_repo_and_worktree(project_dir: Path, worktree_override: str | None
                              ) -> tuple[Path, Path]:
    """repo = the checkout owning the shared .git (worktrees live under it);
    judged worktree = the toplevel containing the project, unless overridden.

    NOTE: `--git-common-dir` is relative to the INVOCATION CWD (here:
    project_dir), never to the toplevel — joining it onto the wrong base
    silently relocates the repo root."""
    toplevel = Path(git_out("rev-parse", "--show-toplevel", cwd=project_dir))
    common = git_out("rev-parse", "--git-common-dir", cwd=project_dir)
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (project_dir / common_path).resolve()
    if common_path == (toplevel / ".git").resolve():
        repo = toplevel                      # plain checkout
    else:
        repo = common_path.parent            # linked worktree: common-dir owner
    worktree = Path(worktree_override) if worktree_override else toplevel
    return repo, worktree


def resolve_slice(env: dict, env_source: str) -> tuple[str, str]:
    """Declared slice (explicit policy) > $CGROUP_PARENT_DEV_BACKGROUND. No fallbacks."""
    declared = env.get("cgroup_slice")
    if declared:
        return declared, f"declared {env_source}"
    ambient = os.environ.get(CGROUP_ENV_VAR)
    if not ambient:
        fail(f"no cgroup slice for the gate: set ${CGROUP_ENV_VAR} (ambient, from "
             f"devcontainer.json) or declare cgroup_slice on the lane's environment "
             f"{env_source}")
    return ambient, f"${CGROUP_ENV_VAR}"


def verify_slice_loaded(slice_name: str) -> None:
    """LoadState pre-check ONLY where systemd is reachable (containerized
    contexts ship a shim / no systemd — there the -e passthrough carries the
    slice and the suite's own governance tests verify placement)."""
    if not os.path.isdir("/run/systemd/system"):
        return
    proc = subprocess.run(["systemctl", "show", "--property=LoadState", "--value",
                           slice_name], capture_output=True, text=True)
    if proc.returncode != 0 or proc.stdout.strip() != "loaded":
        state = proc.stdout.strip() or f"systemctl exit {proc.returncode}"
        fail(f"gate slice {slice_name} is not LoadState=loaded (got: {state}) — "
             f"a typo'd slice name fails OPEN (systemd auto-creates an unlimited "
             f"transient slice)")


def check_clean_tree(worktree: Path) -> None:
    proc = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() \
            else proc.returncode
        fail(f"git status failed in {worktree}: {detail}")
    entries = [l for l in proc.stdout.splitlines() if l.strip()]
    if entries:
        fail(f"refusing to judge a dirty tree: {worktree} has {len(entries)} uncommitted "
             f"change(s) (first: {entries[0]!r}) — commit or pass --allow-dirty")


# ---------------------------------------------------------------------------
# command assembly + run
# ---------------------------------------------------------------------------

def substitute_worktree(argv: list[str], worktree: Path) -> list[str]:
    return [a.replace("{worktree}", str(worktree)) for a in argv]


def build_assay_inner(lane: dict, project_dir: Path) -> str:
    verdict = f".assay/verdict-{lane['assay_lane']}.json"
    parts = ["set -euo pipefail",
             "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig",
             shlex.join(["git", "config", "--global", "safe.directory", "*"]),
             f"cd {shlex.quote(str(project_dir))}"]
    for _pin_name, pin in lane.get("pins", {}).items():
        sha = Path(pin["sha256"])
        # verify FROM the pin file's own directory (bare-filename resolution trap)
        parts.append(f"(cd {shlex.quote(str(Path(project_dir / sha.parent)))} && "
                     f"sha256sum -c {shlex.quote(sha.name)})")
    parts.append("mkdir -p .assay")
    parts.append(shlex.join([*lane["assay_command"], "run", lane["assay_lane"],
                             "--file", "assay.toml", "--verdict-json", verdict]))
    return " && ".join(parts)


def build_command_inner(lane: dict, worktree: Path) -> str:
    return " && ".join(["set -euo pipefail",
                        "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig",
                        shlex.join(["git", "config", "--global",
                                    "safe.directory", "*"]),
                        shlex.join(substitute_worktree(lane["argv"], worktree))])


def run_container_lane(lane: dict, lane_name: str, project_dir: Path, repo: Path,
                       worktree: Path, env: dict, env_source: str) -> int:
    docker = shutil.which("docker")
    if not docker:
        fail("docker not found on PATH — container lanes need it")
    phys = physical_path(repo)
    mounts = ["-v", f"{phys}:{phys}", "-v", f"{phys}:{repo}"]  # dual: worktree gitfiles
    extra_mounts_raw = os.environ.get(EXTRA_MOUNT_ENV_VAR, "")
    if extra_mounts_raw:
        mount_specs = extra_mounts_raw.split(":")
        if "" in mount_specs:
            fail(f"invalid ${EXTRA_MOUNT_ENV_VAR}: empty element in {extra_mounts_raw!r}")
    else:
        mount_specs = []
    for mount_spec in mount_specs:
        if "=" not in mount_spec or mount_spec.count("=") != 1:
            fail(f"invalid ${EXTRA_MOUNT_ENV_VAR} entry {mount_spec!r}: expected 'host=container'")
        source, target = mount_spec.split("=", 1)
        if not source or not target:
            fail(f"invalid ${EXTRA_MOUNT_ENV_VAR} entry {mount_spec!r}: empty path")
        mounts += ["-v", f"{source}:{target}"]
    slice_name, slice_src = resolve_slice(env, env_source)
    verify_slice_loaded(slice_name)
    inner = build_assay_inner(lane, project_dir) if lane["kind"] == "assay" \
        else build_command_inner(lane, worktree)
    name = f"run-gate-{repo.name}-{lane_name}-{os.getpid()}-{int(time.time())}"
    argv = [docker, "run", "-d", "--name", name,
            "--cgroup-parent", slice_name,
            "-e", f"{CGROUP_ENV_VAR}={slice_name}",
            *mounts]
    for key in env.get("forward_env", []):
        value = os.environ.get(key)
        if value is not None:
            argv += ["-e", f"{key}={value}"]
    if lane.get("memory"):
        argv += ["--memory", lane["memory"]]
    argv += [env["image"], "bash", "-c", inner]
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env {env_source} | "
          f"slice {slice_name} ({slice_src})", flush=True)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    print(f"run-gate: docker argv: {shlex.join(argv)}", flush=True)
    started = subprocess.run(argv, capture_output=True, text=True)
    if started.returncode != 0:
        detail = started.stderr.strip().splitlines()[-1:] or ["(no stderr)"]
        subprocess.run([docker, "rm", "-f", name], capture_output=True)
        fail(f"docker run failed: {detail[0]}")
    try:
        logs = subprocess.run([docker, "logs", "-f", name])
        waited = subprocess.run([docker, "wait", name], capture_output=True, text=True)
        out = waited.stdout.strip()
        code = int(out) if waited.returncode == 0 and re.fullmatch(r"-?\d+", out) else None
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True)
    if logs.returncode != 0:
        print(f"run-gate: WARNING: docker logs exit {logs.returncode}", file=sys.stderr)
    if lane["kind"] == "assay":
        verdict_path = project_dir / f".assay/verdict-{lane['assay_lane']}.json"
        print(f"run-gate: verdict artifact: {verdict_path}", flush=True)
    if code is None:
        fail("could not read the container's exit status (docker wait failed) — "
             "refusing to guess")
    return code


def resolve_container_name(env_name: str, env: dict, repo: Path,
                           env_source: str) -> tuple[str, str]:
    """Resolve the persistent container name for an exec-mode environment.

    Priority: declared `container_name` > CIU convention `{prefix}-{env_name}`.
    The CIU prefix is read from the rendered ciu.global.toml [deploy] table
    (project_name + environment_tag), falling back to DOCKER_NETWORK_INTERNAL.
    No silent default — missing config is a hard error naming what to fix.
    """
    if env.get("container_name"):
        return env["container_name"], f"declared container_name ({env_source})"
    global_toml = repo / "ciu.global.toml"
    if not global_toml.is_file():
        fail(f"exec-mode environment '{env_name}' needs either a declared "
             f"container_name or a rendered {global_toml} with [deploy] "
             f"(run 'ciu render' first)")
    try:
        with open(global_toml, "rb") as fh:
            deploy = tomllib.load(fh).get("deploy", {})
    except tomllib.TOMLDecodeError as exc:
        fail(f"{global_toml}: invalid TOML: {exc}")
    project = deploy.get("project_name") or ""
    tag = deploy.get("environment_tag") or ""
    if project and tag:
        return f"{project}-{tag}-{env_name}", \
            f"ciu.global.toml deploy.project_name+environment_tag ({global_toml})"
    network = deploy.get("network_name") or ""
    if network and network.endswith("-network"):
        prefix = network[:-len("-network")]
        return f"{prefix}-{env_name}", \
            f"ciu.global.toml deploy.network_name stripped of '-network' ({global_toml})"
    fail(f"cannot derive container name from {global_toml}: need "
         f"[deploy] project_name+environment_tag OR network_name ending '-network'; "
         f"or declare container_name on the environment")


def run_exec_lane(lane: dict, lane_name: str, project_dir: Path, repo: Path,
                  worktree: Path, env: dict, env_source: str, env_name: str) -> int:
    """Exec into a PERSISTENT runner (started externally by CIU).

    Fail-fast: refuses to start the runner itself — that is CIU's job.
    This keeps run-gate as pure gate orchestration without duplicating
    lifecycle management that belongs to the deployment authority.
    """
    docker = shutil.which("docker")
    if not docker:
        fail("docker not found on PATH — exec-mode lanes need it")
    name, name_src = resolve_container_name(env_name, env, repo, env_source)
    running = subprocess.run([docker, "ps", "--format", "{{.Names}}"],
                             capture_output=True, text=True)
    if running.returncode != 0:
        detail = running.stderr.strip().splitlines()[-1:] or [f"exit {running.returncode}"]
        fail(f"docker ps failed for exec-mode preflight: {detail[0]}")
    names = set(running.stdout.strip().splitlines())
    if name not in names:
        fail(f"persistent runner '{name}' ({name_src}) is not running — "
             f"start it via 'ciu up --dir tools/test-runner' or the project's "
             f"runner lifecycle command before invoking this lane; run-gate "
             f"refuses to guess or auto-start deployment-managed containers")
    inner = build_assay_inner(lane, project_dir) if lane["kind"] == "assay" \
        else build_command_inner(lane, worktree)
    argv = [docker, "exec", "--workdir", str(repo)]
    # Infrastructure variables are implicit; project data inputs must be
    # declared on the environment so every consumer gets the same contract.
    for key in (CGROUP_ENV_VAR, *env.get("forward_env", [])):
        value = os.environ.get(key)
        if value:
            argv += ["-e", f"{key}={value}"]
    argv += [name, "bash", "-c", inner]
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env {env_source} | "
          f"container {name}", flush=True)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    code = subprocess.run(argv).returncode
    return code


def run_host_lane(lane: dict, lane_name: str, project_dir: Path, worktree: Path) -> int:
    argv = substitute_worktree(lane["argv"], worktree)
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env built-in 'host'",
          flush=True)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    return subprocess.run(argv, cwd=str(project_dir)).returncode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_list(cfg: dict) -> int:
    for name, lane in sorted(cfg.get("lanes", {}).items()):
        print(f"{name}\t{lane['kind']}\t{lane['environment']}")
    return 0


def usage(project_cfg: dict) -> str:
    lines = [
        f"{PROG} rev {__revision__} — the per-project gate entrypoint",
        "",
        "usage: run-gate.py <lane> [--worktree PATH] [--allow-dirty]",
        "       run-gate.py --list",
        "",
        "lanes (run-gate.toml):",
    ]
    table = sorted(project_cfg.get("lanes", {}).items())
    if not table:
        lines.append("  (none defined)")
    for name, lane in table:
        lines.append(f"  {name:<24} kind={lane['kind']:<8} environment={lane['environment']}")
    lines += [
        "",
        "Lane declarations: run-gate.toml next to this script; shared environment",
        "facts may live in an enclosing repo-root run-gate.toml. Judgment policy",
        "belongs to assay (assay.toml), never here. See run-gate-project/README.md.",
    ]
    return "\n".join(lines)


def find_project_dir() -> Path | None:
    # directory of the INVOKED script path — absolute() deliberately does NOT
    # resolve symlinks: a project-root symlink's PARENT is the project (the
    # target's parent is run-gate-project itself, which has no lanes). CWD
    # is the fallback for pipes/odd invocations.
    invoked = Path(sys.argv[0]).absolute()
    for candidate in (invoked.parent, Path.cwd()):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False, prog=PROG)
    parser.add_argument("lane", nargs="?")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--worktree")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args(argv)

    try:
        project_dir = find_project_dir()
        if args.help or (args.lane is None and not args.list):
            if project_dir is None:
                fail(f"no {CONFIG_NAME} found next to the invoked script or CWD "
                     f"(run-gate rev {__revision__})")
            cfg, _, _, _ = load_config(project_dir)
            print(usage(cfg))
            return 0
        if project_dir is None:
            fail(f"no {CONFIG_NAME} found next to the invoked script or "
                 f"{Path.cwd()} — run-gate resolves its config beside the invoked "
                 f"(sym)link/copy")
        cfg, cfg_path, central, central_path = load_config(project_dir)
        if args.list:
            return cmd_list(cfg)
        lanes = dict(cfg.get("lanes", {}))
        if args.lane not in lanes:
            fail(f"unknown lane {args.lane!r} — known lanes: "
                 f"{', '.join(sorted(lanes)) or '(none)'} (config: {cfg_path})")
        lane = lanes[args.lane]
        env, env_source = resolve_environment(lane, args.lane, cfg, central,
                                              cfg_path, central_path)
        repo, worktree = resolve_repo_and_worktree(project_dir, args.worktree)
        if lane.get("clean_tree", True) and not args.allow_dirty:
            check_clean_tree(worktree)
        if not env:  # built-in 'host'
            code = run_host_lane(lane, args.lane, project_dir, worktree)
        elif env.get("mode") == "exec":
            code = run_exec_lane(lane, args.lane, project_dir, repo, worktree,
                                 env, env_source, lane_environment_name(lane))
        else:
            code = run_container_lane(lane, args.lane, project_dir, repo, worktree,
                                      env, env_source)
        print(f"run-gate: lane {args.lane!r} exit {code}", flush=True)
        return code
    except GateError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
