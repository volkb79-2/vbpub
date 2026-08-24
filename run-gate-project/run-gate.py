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
__revision__ = 21  # rev 20: RG-13 adoption hygiene — worked run-gate×assay example, gitignore obligation, estate README retro ×9, root discovery line, budget↔timeout pairing sweep (R-32; docs/test-only, no behavior change); rev 19: RG-14 wheel as second artifact — pyproject derives version from __revision__, `run-gate` console script, byte-identical module discipline (R-31); rev 18: RG-9 doctor preflight verb — docker/slices/mountinfo/git/images in one command (R-30); rev 17: RG-20 resource-aware admission — slice-RAM budget from cgroupfs + shared-infra locks, lane `resources` key (R-29); rev 16: RG-8 --dry-run plan rehearsal on all three runners (R-28); rev 15: RG-2 validate-pointers verb + estate linkage certification (R-27); rev 14: RG-10 declared artifacts + unconditional evidence-path disclosure in all three runners (R-08/R-18); rev 13: RG-12 evidence preservation + stderr tail (R-26); rev 12: RG-1 override guard (R-25); rev 11: RG-17/19 required_env preflight + forwarding log + --check-env (R-24); rev 10 RG-6; rev 9 RG-5 (R-02); rev 8 RG-3 (R-23); rev 7 RG-16 (R-22); rev 6 RG-4; rev 5 RG-11; rev 4 RG-15

import argparse
import fcntl
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
MOUNT_ALIAS_ENV_VAR = "RUN_GATE_MOUNT_ALIAS"
EVIDENCE_DIR_ENV_VAR = "RUN_GATE_EVIDENCE_DIR"
EVIDENCE_DIR_DEFAULT = "/tmp/run-gate"
EVIDENCE_TAIL_LINES = 10
CGROUPFS_ROOT_ENV_VAR = "RUN_GATE_CGROUPFS_ROOT"  # tests / hidden cgroup mounts
SHARED_LOCK_DIR = "/tmp"  # RG-20 instance/service-scoped gate serialization


class GateError(Exception):
    """One-line, user-facing failure. Never a traceback for config/env errors.

    Reserved exit codes (RG-11, SPEC R-04): 2 = configuration or refusal
    (bad/unknown anything, dirty tree, preflight refusals); 3 = execution-
    infrastructure failure (docker/git/mountinfo could not do their job).
    Scripts consume the distinction; messages stay the human channel."""
    exit_code = 2


class GateInfraError(GateError):
    """Execution-infrastructure failure: the environment could not do its
    job (docker absent/failing, git failing, physical path underivable) —
    distinct from "your configuration says no" so CI can tell them apart."""
    exit_code = 3


def fail(msg: str) -> None:
    """Configuration error / policy refusal (exit 2)."""
    raise GateError(msg)


def fail_infra(msg: str) -> None:
    """Execution-infrastructure failure (exit 3)."""
    raise GateInfraError(msg)


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
         "clean_tree", "budget", "memory", "description", "required_env",
         "artifacts", "resources"},
        f"{where} [lanes.{name}]",
    )
    kind = table.get("kind")
    if kind not in ("command", "assay"):
        fail(f"{where} [lanes.{name}]: 'kind' must be \"command\" or \"assay\" (got {kind!r})")
    if not isinstance(table.get("environment"), str) or not table["environment"].strip():
        fail(f"{where} [lanes.{name}]: 'environment' must be a non-empty string")
    if "description" in table and (not isinstance(table["description"], str)
                                   or not table["description"].strip()):
        fail(f"{where} [lanes.{name}]: 'description' must be a non-empty string "
             f"(shown by --help; keep it one line)")
    if "required_env" in table:
        req = table["required_env"]
        if not isinstance(req, list) or \
                not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
                        for v in req):
            fail(f"{where} [lanes.{name}]: 'required_env' must be a list of "
                 f"valid environment-variable names")
        if len(set(req)) != len(req):
            fail(f"{where} [lanes.{name}]: 'required_env' entries must be unique")
    if "artifacts" in table:
        arts = table["artifacts"]
        if not isinstance(arts, list) or not arts \
                or not all(isinstance(v, str) and v.strip() for v in arts):
            fail(f"{where} [lanes.{name}]: 'artifacts' must be a non-empty "
                 f"list of non-empty relative paths (printed on lane exit)")
    if "budget" in table:
        _validate_budget(table["budget"], f"{where} [lanes.{name}]")
    if "memory" in table:
        _validate_memory(table["memory"], f"{where} [lanes.{name}]")
    if "resources" in table:
        res = table["resources"]
        if not isinstance(res, dict):
            fail(f"{where} [lanes.{name}]: 'resources' must be a table")
        _check_keys(res, {"memory", "memory_swap", "cpu_weight", "io_weight",
                          "shared"},
                    f"{where} [lanes.{name}.resources]")
        if "memory" in res and table.get("memory"):
            fail(f"{where} [lanes.{name}]: declare RAM once — top-level 'memory' "
                 f"and 'resources.memory' are the same knob; use 'resources.memory'")
        for key in ("memory", "memory_swap"):
            if key in res and (not isinstance(res[key], str)
                               or not re.fullmatch(r"\d+[bkmg]?", res[key])):
                fail(f"{where} [lanes.{name}.resources]: '{key}' must be a size "
                     f"like '512m' or '4g'")
        for key in ("cpu_weight", "io_weight"):
            if key in res and (not isinstance(res[key], int)
                               or isinstance(res[key], bool)
                               or not 1 <= res[key] <= 10000):
                fail(f"{where} [lanes.{name}.resources]: '{key}' must be an "
                     f"integer 1..10000 (cgroup v2 weight scale)")
        shared = res.get("shared")
        if shared is not None:
            if not isinstance(shared, list) \
                    or not all(isinstance(v, str)
                               and re.fullmatch(r"[A-Za-z0-9_.-]+", v) for v in shared):
                fail(f"{where} [lanes.{name}.resources]: 'shared' must be a list "
                     f"of service names ([A-Za-z0-9_.-]+)")
            if len(set(shared)) != len(shared):
                fail(f"{where} [lanes.{name}.resources]: 'shared' names must "
                     f"be unique")
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
            if "version" in pin and (not isinstance(pin["version"], str)
                                     or not pin["version"].strip()):
                fail(f"{where} [lanes.{name}].pins.{pin_name}: 'version' must be a "
                     f"non-empty string; declaring it asserts the lane's "
                     f"assay_command supports '--version' (verified in-lane)")
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
    for name, table in envs.items():
        _validate_environment(name, table, where)
    for name, table in lanes.items():
        _validate_lane(name, table, where)
    return cfg


def load_config(project_dir: Path) -> tuple[dict, Path, dict, Path | None]:
    """Load the project config + the nearest ancestor (central) config.

    Central configs may define shared environments AND shared lanes
    (RG-16); every declared lane is schema-validated wherever it lives."""
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


def merge_lanes(project_lanes: dict, central: dict, project_dir: Path,
                project_path: Path, central_path: Path | None) -> dict:
    """Effective lane set: central [lanes.*] inherited, project entries
    shadow BY NAME (whole lane — no field merging, RG-16).

    Per-consumer existence check: a central lane's pin sidecars must exist
    relative to THIS consuming project — a shared gate referencing artifacts
    the project does not vendor refuses at load, naming both files. Free-form
    argv strings are deliberately NOT stat'd: they are shell text, not
    declared paths, and pretending otherwise would certify nothing.
    """
    if not central.get("lanes"):
        return project_lanes
    merged = dict(central["lanes"])
    merged.update(project_lanes)
    for name, lane in central["lanes"].items():
        if name in project_lanes:
            continue  # shadowed wholesale by the project's own definition
        for pin_name, pin in lane.get("pins", {}).items():
            sidecar = project_dir / pin["sha256"]
            if not sidecar.is_file():
                fail(f"central lane '[lanes.{name}]' ({central_path}): pin "
                     f"'{pin_name}' sidecar {pin['sha256']} does not exist in this "
                     f"project ({project_dir}) — vendor it or shadow the lane in "
                     f"{project_path}")
    return merged


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
            fail_infra(f"cannot read /proc/self/mountinfo to derive the physical host path "
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
        fail_infra(f"could not derive a physical host path for {path} from /proc/self/mountinfo "
                   f"— is the repo bind-mounted into this container?")
    return Path(best_root + str(path)[len(best_mp):])


def git_out(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() \
            else f"exit {proc.returncode}"
        fail_infra(f"git {' '.join(args)} failed in {cwd}: {detail}")
    return proc.stdout.strip()


def resolve_repo_and_worktree(project_dir: Path, worktree_override: str | None
                              ) -> tuple[Path, Path, Path]:
    """repo = the checkout owning the shared .git (worktrees live under it);
    judged worktree = the toplevel containing the project, unless overridden;
    also returns the invocation toplevel (the base the project dir is
    relocated from when an override selects a different tree).

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
    return repo, worktree, toplevel


def effective_project_dir(project_dir: Path, toplevel: Path,
                          worktree: Path) -> Path:
    """RG-15: the project's position INSIDE the judged tree.

    All user-declared execution paths — the assay cd target, pin verification,
    verdict/artifact locations, host-lane cwd — resolve against the SELECTED
    worktree, never the invocation checkout: <worktree>/<project-relative-
    to-toplevel>. With no --worktree override this is exactly project_dir.
    Refuses when the project sits outside its own toplevel: nothing then
    defines its position inside the override tree, and guessing would run the
    lane against an unrelated directory. Existence is deliberately NOT
    pre-checked here — the override tree may live in another mount namespace,
    and a local stat would ask the wrong kernel; the inner `cd` fails loudly
    where the right view exists.
    """
    try:
        rel = project_dir.relative_to(toplevel)
    except ValueError:
        try:  # symlinked layouts: compare through realpath, keep caller's prefix
            rel = project_dir.resolve().relative_to(toplevel.resolve())
        except ValueError:
            fail(f"project dir {project_dir} is outside its git toplevel "
                 f"{toplevel} — cannot relocate it into the judged worktree "
                 f"{worktree}")
    return worktree / rel


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


# ---------------------------------------------------------------------------
# RG-20 — resource-aware admission: RAM is the real contention hazard
# ---------------------------------------------------------------------------

_SIZE_MULT = {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def parse_size_bytes(value: str) -> int:
    """'4g' -> bytes. Callers validate the \\d+[bkmg]? shape first."""
    m = re.fullmatch(r"(\d+)([bkmg]?)", value)
    return int(m.group(1)) * _SIZE_MULT[m.group(2)]


def _fmt_mb(n: int) -> str:
    return f"{n / 1024 ** 2:.0f}MB"


def slice_cgroupfs_dir(slice_name: str) -> Path:
    """systemd slice nesting under the cgroupfs root: dev-background.slice
    lives at dev.slice/dev-background.slice (dashes are hierarchy)."""
    stem = slice_name[:-len(".slice")] if slice_name.endswith(".slice") \
        else slice_name
    parts = stem.split("-")
    if len(parts) == 1:
        return Path(f"{stem}.slice")
    return Path("/".join(p + ".slice" for p in parts[:-1])) / slice_name


def check_slice_memory_admission(lane: dict, lane_name: str,
                                 slice_name: str, slice_src: str) -> None:
    """RG-20 admission, memory half: the lane's declared RAM must fit in the
    slice's REMAINING budget, read from cgroupfs kernel truth at admission
    time (memory.current + declared <= memory.max). This counts EVERYTHING
    already running in the slice — other gates, live services — not just
    gates this tool started, so no cross-process bookkeeping can drift.

    No derivable ceiling ('max', cgroupfs hidden/unreadable): say so loudly
    and admit on shared-infra rules only — a hard refuse here would make the
    gate unusable in every namespace that cannot see the host cgroupfs."""
    res = lane.get("resources", {})
    declared = res.get("memory") or lane.get("memory")
    if not declared:
        print(f"run-gate: admission: lane {lane_name!r} declares no "
              f"resources.memory — not memory-accounted (shared-infra rules "
              f"still apply)", flush=True)
        return
    root = Path(os.environ.get(CGROUPFS_ROOT_ENV_VAR, "/sys/fs/cgroup"))
    sl_dir = root / slice_cgroupfs_dir(slice_name)

    def _read(name: str) -> str | None:
        try:
            return (sl_dir / name).read_text().strip()
        except OSError:
            return None

    max_raw = _read("memory.max")
    cur_raw = _read("memory.current")
    if max_raw is None or max_raw == "max":
        print(f"run-gate: admission WARNING: no derivable memory ceiling for "
              f"slice {slice_name} ({sl_dir}/memory.max "
              f"{'absent' if max_raw is None else '= max'}; export "
              f"${CGROUPFS_ROOT_ENV_VAR} if the host cgroupfs hides here) — "
              f"admission by shared-infra rules only", flush=True)
        return
    if cur_raw is None or not cur_raw.isdigit():
        print(f"run-gate: admission WARNING: slice {slice_name} current usage "
              f"unreadable ({sl_dir}/memory.current) — admission by shared-infra "
              f"rules only", flush=True)
        return
    cap = int(max_raw)
    current = int(cur_raw)
    need = parse_size_bytes(declared)
    if current + need > cap:
        fail(f"resource admission REFUSED for lane {lane_name!r}: slice "
             f"{slice_name} ({slice_src}) is using {_fmt_mb(current)} of its "
             f"{_fmt_mb(cap)} budget and this lane declares {declared} — "
             f"{_fmt_mb(current + need - cap)} over. Wait for a consumer to "
             f"finish, or lower 'resources.memory'")
    print(f"run-gate: admission OK: slice {slice_name} usage {_fmt_mb(current)} "
          f"+ {lane_name!r} {declared} <= budget {_fmt_mb(cap)}", flush=True)


def acquire_shared_locks(lane: dict, lane_name: str, dry_run: bool) -> list[int]:
    """RG-20 admission, shared-infra half: lanes declaring the same
    resources.shared service name serialize on a per-name flock
    (/tmp/run-gate-shared-<name>.lock), so two gates hitting one PG/Redis
    instance wait instead of corrupting each other — while fully isolated
    instances never meet here and run concurrently. Dry runs plan the wait
    but never block. Returns held fds; closing each releases its lock."""
    names = lane.get("resources", {}).get("shared") or []
    if dry_run:
        if names:
            print(f"run-gate: DRY RUN — shared-infra serialization planned "
                  f"for: {', '.join(sorted(names))}", flush=True)
        return []
    fds: list[int] = []
    for svc in names:
        path = Path(SHARED_LOCK_DIR) / f"run-gate-shared-{svc}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"run-gate: lane {lane_name!r}: waiting for shared infra "
                  f"'{svc}' — another gate holds {path}", flush=True)
            fcntl.flock(fd, fcntl.LOCK_EX)
        fds.append(fd)
    return fds


def check_clean_tree(worktree: Path) -> None:
    proc = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() \
            else proc.returncode
        fail_infra(f"git status failed in {worktree}: {detail}")
    entries = [l for l in proc.stdout.splitlines() if l.strip()]
    if entries:
        fail(f"refusing to judge a dirty tree: {worktree} has {len(entries)} uncommitted "
             f"change(s) (first: {entries[0]!r}) — commit or pass --allow-dirty")


# ---------------------------------------------------------------------------
# command assembly + run
# ---------------------------------------------------------------------------

def substitute_worktree(argv: list[str], worktree: Path) -> list[str]:
    return [a.replace("{worktree}", str(worktree)) for a in argv]


def redact_forwarded_values(argv: list[str], keys: list[str]) -> list[str]:
    """RG-19 companion to log_forwarded_env: the printed docker argv shows
    mechanics (R-05) but must NOT echo forwarded credential VALUES — mask
    every `-e KEY=...` payload for allowlisted keys; names stay visible."""
    prefixes = tuple(f"{k}=" for k in keys)
    out: list[str] = []
    expect_value = False
    for tok in argv:
        if expect_value:
            if tok.startswith(prefixes):
                out.append(tok.split("=", 1)[0] + "=<redacted>")
            else:
                out.append(tok)
            expect_value = False
        else:
            out.append(tok)
            expect_value = tok == "-e"
    return out


def print_lane_artifacts(lane: dict, lane_name: str, project_dir: Path,
                         worktree: Path) -> None:
    """R-18/RG-10: after EVERY run — any kind, any runner mode, success or
    failure — say where the evidence landed. Assay lanes always disclose the
    verdict convention; declared `artifacts` add to it. Paths resolve
    against the EFFECTIVE project dir (relocated into the judged tree,
    R-21); `{worktree}` tokens inside entries are substituted."""
    verdict_rel = None
    if lane["kind"] == "assay":
        verdict_rel = ".assay/verdict-" + lane["assay_lane"] + ".json"
        print(f"run-gate: verdict artifact: {project_dir / verdict_rel}",
              flush=True)
    for entry in lane.get("artifacts", []):
        substituted = substitute_worktree([entry], worktree)[0]
        target = Path(substituted)
        if not target.is_absolute():
            target = project_dir / target
        if verdict_rel is not None and substituted == verdict_rel:
            continue  # already disclosed above
        print(f"run-gate: artifact: {target}", flush=True)


# ---------------------------------------------------------------------------
# failing-container evidence preservation (RG-12)
# ---------------------------------------------------------------------------

def evidence_dir() -> Path:
    return Path(os.environ.get(EVIDENCE_DIR_ENV_VAR) or EVIDENCE_DIR_DEFAULT)


def save_container_logs(docker: str, name: str) -> Path | None:
    """RG-12: copy the container's full logs somewhere readable BEFORE the
    `rm -f` destroys them. Returns the written path, or None when capture
    fails (never raises — evidence is best-effort, the lane result stands)."""
    try:
        grabbed = subprocess.run([docker, "logs", name], capture_output=True,
                                 text=True)
        combined = grabbed.stdout + grabbed.stderr
        if grabbed.returncode != 0 and not combined.strip():
            return None
        target = evidence_dir() / f"{name}.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(combined)
        return target
    except OSError:
        return None


# ---------------------------------------------------------------------------
# required_env preflight + forwarding transparency (RG-17 / RG-19)
# ---------------------------------------------------------------------------

def preflight_required_env(lane: dict, lane_name: str) -> None:
    """RG-19: a lane that declares required_env refuses to start unless every
    named variable is PRESENT and NON-EMPTY in the invoking environment —
    credentials must be verified by the gate, not discovered by a fixture
    mid-run (or worse, by silently skipped assertions inside a green run)."""
    for key in lane.get("required_env", []):
        value = os.environ.get(key)
        if value is None or value == "":
            fail(f"lane '{lane_name}' requires ${key} but it is unset or empty "
                 f"— export it before invoking this gate (run-gate refuses to "
                 f"start a lane whose declared inputs are missing)")


def check_required_reaches_container(lane: dict, lane_name: str, env: dict,
                                     env_name: str, env_source: str) -> None:
    """RG-17: for container lanes, a required variable that is not on the
    environment's forward_env allowlist can NEVER reach the lane — refuse at
    load instead of failing (or hollow-skipping) inside the container."""
    forwarded = set(env.get("forward_env", []))
    missing = [k for k in lane.get("required_env", []) if k not in forwarded]
    if missing:
        fail(f"lane '{lane_name}' requires {', '.join(missing)} but they are "
             f"not on environment '{env_name}'s forward_env allowlist "
             f"({env_source}) — the container can never receive them; add "
             f"them to forward_env or drop the required_env entry")


def log_forwarded_env(env: dict, prefix: str) -> None:
    """RG-19: print WHICH forwarding keys were present at start — names
    only, never values — so omissions are visible in the run record."""
    present, absent = [], []
    for key in env.get("forward_env", []):
        (present if os.environ.get(key) else absent).append(key)
    parts = []
    if present:
        parts.append(f"forwarded: {', '.join(sorted(present))}")
    if absent:
        parts.append(f"declared but ABSENT: {', '.join(sorted(absent))}")
    print(f"run-gate: {prefix} env ({' ; '.join(parts) if parts else 'nothing declared'})",
          flush=True)


ENV_REF_RE = re.compile(
    r"(?:os\.environ\[|os\.environ\.get\(|\bgetenv\()"
    r"\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def cmd_check_env(lanes: dict, project_dir: Path, cfg: dict, central: dict,
                  cfg_path: Path, central_path: Path | None) -> int:
    """RG-17 drift sweep (ADVISORY): scan the project's Python sources for
    os.environ[...] / os.environ.get(...) / getenv(...) literals and flag
    names covered by neither forward_env nor required_env. Heuristic by
    nature (a .get with a default may be deliberately optional), so this
    WARNS; enforcement lives in required_env + the preflight."""
    covered = {CGROUP_ENV_VAR}
    for name, lane in lanes.items():
        covered.update(lane.get("required_env", []))
        try:
            env, _ = resolve_environment(lane, name, cfg, central,
                                         cfg_path, central_path)
        except GateError:
            continue  # config errors surface elsewhere, louder
        if env:
            covered.update(env.get("forward_env", []))
    findings = []
    for path in sorted(project_dir.rglob("*.py")):
        try:
            text = path.read_text()
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in ENV_REF_RE.finditer(line):
                var = match.group(1)
                if var not in covered:
                    form = "subscript" if "environ[" in line else "access"
                    findings.append((var, path.relative_to(project_dir),
                                     lineno, form))
    seen = set()
    for var, rel, lineno, form in findings:
        if (var, str(rel)) in seen:
            continue
        seen.add((var, str(rel)))
        print(f"run-gate: env-drift: ${var} referenced in {rel}:{lineno} "
              f"is neither forwarded nor declared required_env — add it to "
              f"the environment's forward_env or the lane's required_env",
              flush=True)
    print(f"run-gate: env-drift scan: {len(seen)} uncovered reference(s)"
          f"{' — ADVISORY ONLY, the run was not affected' if seen else ''}",
          flush=True)
    return 0


# ---------------------------------------------------------------------------
# Pointer↔lane linkage (RG-2): a consumer pointer is certified, not assumed
# ---------------------------------------------------------------------------

_CD_TARGET_RE = re.compile(r"\bcd\s+(\S+)")
_INVOCATION_RE = re.compile(r"run-gate\.py(?:\s+[^&;]*)?")


def _collect_pointers(node, where: str) -> list[tuple[str, str]]:
    """Every (location, text) pair in a parsed consumer document that invokes
    run-gate.py. An argv-style LIST whose first element IS run-gate.py is one
    pointer (joined) — list-form consumers otherwise split the command across
    elements, so no single string contains both the tool and the lane."""
    found: list[tuple[str, str]] = []

    def visit(node, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, f"{where}.{key}" if where else str(key))
        elif isinstance(node, list):
            if node and isinstance(node[0], str) \
                    and node[0].endswith("run-gate.py") \
                    and all(isinstance(v, str) for v in node):
                visit(" ".join(node), f"{where}[argv]")
            else:
                for i, v in enumerate(node):
                    visit(v, f"{where}[{i}]")
        elif isinstance(node, str) and "run-gate.py" in node:
            found.append((where, node))

    visit(node, where)
    return found


def _pointer_project_dir(text: str, file_path: Path, root: Path,
                         where: str) -> tuple[Path | None, list[str]]:
    """Resolve the project a pointer judges: its single `cd {worktree}/rel`
    target — or, for legacy list-form steps with no cd at all, the pointer
    file's own directory when that directory IS a project."""
    defects: list[str] = []
    targets: set[str] = set()
    noncanonical: set[str] = set()
    for m in _CD_TARGET_RE.finditer(text):
        raw = m.group(1).strip("'\"")
        if raw.startswith("{worktree}/"):
            targets.add(raw[len("{worktree}/"):])
        elif raw == "{worktree}":
            targets.add(".")
        else:
            noncanonical.add(raw)
    for raw in sorted(noncanonical):
        defects.append(
            f"{where}: cd target '{raw}' is not '{{worktree}}/<project-relative>' "
            f"— the daemon substitutes {{worktree}} textually; a cd bound to any "
            f"other cwd judges whatever tree it happens to run from")
    if len(targets) > 1:
        defects.append(f"{where}: pointer cds into {sorted(targets)} — exactly "
                       f"one project target is allowed")
        return None, defects
    if targets:
        proj = root / next(iter(targets))
        if not (proj / CONFIG_NAME).is_file():
            defects.append(f"{where}: cd target resolves to {proj}, which has "
                           f"no {CONFIG_NAME} — no lanes to certify against")
            return None, defects
        return proj, defects
    fallback = file_path.parent
    if (fallback / CONFIG_NAME).is_file():
        return fallback, defects
    defects.append(f"{where}: pointer declares no 'cd {{worktree}}/<project>' "
                   f"and {fallback} has no {CONFIG_NAME} — cannot resolve the "
                   f"judged project")
    return None, defects


def _pointer_defects(text: str, file_path: Path, root: Path, where: str,
                     lanes_cache: dict) -> tuple[list[str], int]:
    """Validate EVERY run-gate.py invocation inside one pointer string.
    Returns (defects, invocations_checked)."""
    defects: list[str] = []
    checked = 0
    uses_worktree = "{worktree}" in text
    proj, resolve_defects = _pointer_project_dir(text, file_path, root, where)
    defects += resolve_defects
    if proj is None:
        return defects, 0
    key = str(proj)
    if key not in lanes_cache:
        try:
            cfg, cfg_path, central, central_path = load_config(proj)
            lanes_cache[key] = merge_lanes(cfg.get("lanes", {}), central,
                                           proj, cfg_path, central_path)
        except GateError as exc:
            lanes_cache[key] = None
            defects.append(f"{where}: loading {proj / CONFIG_NAME}: {exc}")
            return defects, 0
    lanes = lanes_cache[key]
    if lanes is None:
        return defects, 0
    for m in _INVOCATION_RE.finditer(text):
        checked += 1
        tokens = m.group(0).split()
        positional: list[str] = []
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--worktree":
                i += 2  # flag plus its value
                continue
            if tok.startswith("-"):
                i += 1
                continue
            positional.append(tok)
            i += 1
        if uses_worktree and "--worktree" not in tokens:
            defects.append(
                f"{where}: pointer substitutes {{worktree}} but its run-gate "
                f"invocation drops '--worktree {{worktree}}' — sub-steps would "
                f"re-derive their own tree (the RG-1 silent false-PASS class)")
        if not positional:
            defects.append(f"{where}: run-gate invocation names no lane")
        elif len(positional) > 1:
            defects.append(f"{where}: unexpected trailing arguments "
                           f"{positional[1:]!r} — a pointer names exactly one lane")
        elif positional[0] not in lanes:
            defects.append(
                f"{where}: lane {positional[0]!r} is not declared in "
                f"{proj / CONFIG_NAME} — known lanes: "
                f"{', '.join(sorted(lanes)) or '(none)'}")
    return defects, checked


def cmd_doctor(lanes: dict, project_dir: Path, cfg: dict, central: dict,
               cfg_path: Path, central_path: Path | None) -> int:
    """RG-9: recompose the implemented preflights into one first-contact
    command. Pure recomposition — every check here already exists on the run
    path; doctor just runs them BEFORE a newcomer's first lane does."""
    results: list[tuple[str, str, str]] = []

    def record(status: str, topic: str, detail: str) -> None:
        results.append((status, topic, detail))
        print(f"run-gate: doctor: [{status}] {topic}: {detail}", flush=True)

    # 1. docker present
    docker = shutil.which("docker")
    if docker:
        record("OK", "docker", docker)
    else:
        record("FAIL", "docker", "not found on PATH — container/exec lanes need it")

    # 2. per-environment facts: resolution + slice LoadState
    env_cache: dict[str, tuple[dict, str]] = {}
    for name in sorted(lanes):
        try:
            env, env_source = resolve_environment(lanes[name], name, cfg,
                                                 central, cfg_path, central_path)
        except GateError as exc:
            record("FAIL", f"lane {name!r} environment", str(exc))
            continue
        env_name = lane_environment_name(lanes[name])
        if env_name == HOST_ENV or not env:
            env_cache.setdefault("<host>", (env, "built-in 'host'"))
            continue
        if env_name in env_cache:
            continue
        env_cache[env_name] = (env, env_source)
        try:
            slice_name, slice_src = resolve_slice(env, env_source)
            record("OK", f"slice for env {env_name}",
                   f"{slice_name} ({slice_src})")
            verify_slice_loaded(slice_name)  # no-op where systemd unreachable
            record("OK", f"slice LoadState {slice_name}",
                   "loaded (or systemd unreachable — skipped)")
        except GateError as exc:
            record("FAIL", f"slice for env {env_name}", str(exc))

    # 3. physical-path derivability + git health
    try:
        repo, worktree, _ = resolve_repo_and_worktree(project_dir, None)
        record("OK", "git", f"worktree {worktree}")
        try:
            phys = physical_path(repo)
            if phys != repo:
                record("OK", "mountinfo", f"namespace alias derivable: {phys}")
            else:
                record("WARN", "mountinfo",
                       "physical path equals namespace path (bare-host view) — "
                       f"container lanes need ${MOUNT_ALIAS_ENV_VAR}")
        except GateError as exc:
            record("FAIL", "mountinfo", str(exc))
    except GateError as exc:
        record("FAIL", "git", str(exc))
    except OSError as exc:
        # A preflight that tracebacks on a broken host defeats its purpose.
        record("FAIL", "git", f"git not runnable: {exc}")
    if os.access("/tmp", os.W_OK):
        record("OK", "git-config", "/tmp writable for GIT_CONFIG_GLOBAL "
                                   "(safe.directory isolation)")
    else:
        record("WARN", "git-config", "/tmp NOT writable — safe.directory "
                                     "isolation via GIT_CONFIG_GLOBAL will fail")

    # 4. referenced images exist locally (advisory — a missing image may pull)
    if docker:
        for env_name, (env, _src) in sorted(env_cache.items()):
            image = env.get("image")
            if not image:
                continue
            probe = subprocess.run([docker, "image", "inspect", image],
                                   capture_output=True, text=True)
            if probe.returncode == 0:
                record("OK", f"image {env_name}", f"{image} present locally")
            else:
                record("WARN", f"image {env_name}",
                       f"{image} not local — it must pull or exist before the "
                       f"lane runs")

    ok_n = sum(1 for s, *_ in results if s == "OK")
    warn_n = sum(1 for s, *_ in results if s == "WARN")
    fail_n = sum(1 for s, *_ in results if s == "FAIL")
    print(f"run-gate: doctor: {len(results)} check(s): {ok_n} OK, "
          f"{warn_n} warning(s), {fail_n} failure(s)", flush=True)
    return 2 if fail_n else 0


def cmd_validate_pointers(file_path: Path, root_override: str | None) -> int:
    """RG-2: certify every run-gate pointer in a consumer document (a trove
    nyxloom.toml [gates.*], cmru.toml steps, anything TOML) against the SSOT
    lane table it must name. Renaming a lane while pointers still use the old
    name goes RED HERE — at test time, never at daemon dispatch time."""
    if not file_path.is_file():
        fail(f"validate-pointers: no such file: {file_path}")
    doc = _read_toml(file_path)
    if root_override:
        root = Path(root_override)
        if not root.is_dir():
            fail(f"validate-pointers: --root {root_override} is not a directory")
    else:
        # {worktree} stands for the judged worktree root; for a committed
        # pointer that is this checkout's git toplevel.
        root = Path(git_out("rev-parse", "--show-toplevel",
                            cwd=file_path.parent).strip())
    pointers = _collect_pointers(doc, "")
    if not pointers:
        print(f"run-gate: validate-pointers: {file_path}: no run-gate pointers "
              f"(nothing to certify)")
        return 0
    cache: dict = {}
    defects: list[str] = []
    total = 0
    for where, text in pointers:
        d, n = _pointer_defects(text, file_path, root, where, cache)
        defects += d
        total += n
    if defects:
        for d in defects:
            print(f"run-gate: DEFECT {d}")
        print(f"run-gate: validate-pointers FAILED: {len(defects)} defect(s) "
              f"across {total} invocation(s) in {file_path}")
        return 2
    print(f"run-gate: validate-pointers OK: {total} invocation(s) in "
          f"{file_path} certified against their lanes")
    return 0


# RG-5: consumer pointers embed {worktree} into bash -c STRINGS unquoted
# (`cd {worktree}/proj && exec ./run-gate.py --worktree {worktree} <lane>`),
# so any path the tool substitutes must survive that embedding verbatim.
# Gate-safe charset: letters/digits/_ . / -, no leading '-' (flag look-alike);
# whitespace and shell metacharacters word-split or execute downstream, so a
# tree living at such a path is refused instead of half-working.
GATE_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./][A-Za-z0-9_./-]*$")


def check_worktree_charset(worktree: Path) -> None:
    text = str(worktree)
    if GATE_SAFE_PATH_RE.fullmatch(text):
        return
    bad = sorted({c for c in text if not re.fullmatch(r"[A-Za-z0-9_./-]", c)})
    fail(f"worktree path {text!r} is not gate-safe (offending character(s): "
         f"{' '.join(repr(c) for c in sorted(bad))}): consumer pointers embed "
         f"{{worktree}} into shell strings, so paths with whitespace or shell "
         f"metacharacters are refused — relocate or rename the tree")


def build_assay_inner(lane: dict, project_dir: Path) -> str:
    verdict = f".assay/verdict-{lane['assay_lane']}.json"
    parts = ["set -euo pipefail",
             "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig",
             shlex.join(["git", "config", "--global", "safe.directory", "*"]),
             f"cd {shlex.quote(str(project_dir))}"]
    for pin_name, pin in lane.get("pins", {}).items():
        sha = Path(pin["sha256"])
        # verify FROM the pin file's own directory (bare-filename resolution trap)
        parts.append(f"(cd {shlex.quote(str(Path(project_dir / sha.parent)))} && "
                     f"sha256sum -c {shlex.quote(sha.name)})")
        if pin.get("version"):
            # RG-4: a declared version is a CLAIM the artifact must satisfy,
            # checked in-lane right after byte verification — provenance, not
            # decoration. Declaring version asserts the command honors the
            # `--version` convention (documented in SPEC R-08/CONSUMERS).
            declared = shlex.quote(pin["version"])
            probe = shlex.join([*lane["assay_command"], "--version"])
            parts.append(
                f"{{ reported=$({probe}) || "
                f"{{ echo \"run-gate: pin '{pin_name}': version probe failed: {probe}\" "
                f">&2; exit 2; }}; "
                f"case \"$reported\" in *{declared}*) ;; *) "
                f"echo \"run-gate: pin '{pin_name}' version mismatch: declared "
                f"{declared}, artifact reports: $reported — fix pins.{pin_name}.version "
                f"or republish the artifact\" >&2; exit 2; ;; esac; }}")
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


def dual_mount_flags(repo: Path, phys: Path) -> list[str]:
    """RG-3: the repo is dual-mounted (physical AND namespace paths) so
    worktree gitfiles recorded under EITHER namespace resolve (AGENTS trap
    #2). Inside the devcontainer the second view comes from mountinfo; on a
    bare host there is no alias to derive and phys == repo — letting both -v
    flags collapse would be a silent single mount diverging from the
    documented recipe, so the alias must be declared explicitly instead.
    """
    if phys != repo:
        return ["-v", f"{phys}:{phys}", "-v", f"{phys}:{repo}"]
    raw = os.environ.get(MOUNT_ALIAS_ENV_VAR, "").strip()
    if not raw:
        fail(f"cannot dual-mount {repo}: the derived physical path EQUALS the "
             f"namespace path, so both -v flags would collapse into one silent "
             f"mount (container lanes assume the devcontainer namespace alias "
             f"and none is derivable outside a container). Declare it: export "
             f"{MOUNT_ALIAS_ENV_VAR}='{repo}=<namespace-path-git-was-recording>'")
    if raw.count("=") != 1:
        fail(f"invalid ${MOUNT_ALIAS_ENV_VAR} entry {raw!r}: expected "
             f"'host-path=namespace-path'")
    host, namespace = (part.strip() for part in raw.split("=", 1))
    if not host or not namespace:
        fail(f"invalid ${MOUNT_ALIAS_ENV_VAR} entry {raw!r}: empty path")
    if Path(host) != repo:
        fail(f"${MOUNT_ALIAS_ENV_VAR} declares host path {host!r} but this gate's "
             f"repo root is {repo} — the alias names THIS repo's namespace view")
    return ["-v", f"{phys}:{phys}", "-v", f"{phys}:{namespace}"]


def run_container_lane(lane: dict, lane_name: str, project_dir: Path, repo: Path,
                       worktree: Path, env: dict, env_source: str,
                       slice_name: str, slice_src: str,
                       dry_run: bool = False) -> int:
    # project_dir arrives already relocated into the judged worktree (RG-15):
    # pin verification, assay config, and artifacts all resolve there.
    docker = shutil.which("docker")
    if not docker:
        fail_infra("docker not found on PATH — container lanes need it")
    phys = physical_path(repo)
    mounts = dual_mount_flags(repo, phys)  # dual: worktree gitfiles (RG-3)
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
        if value:  # empty string counts as ABSENT — matches log_forwarded_env
            argv += ["-e", f"{key}={value}"]
    mem_cap = lane.get("resources", {}).get("memory") or lane.get("memory")
    if mem_cap:
        argv += ["--memory", mem_cap]
    if lane.get("resources", {}).get("memory_swap"):
        # cmru pattern (RG-20): tight RAM cap + ample swap absorbs bursts
        # without OOM-killing the lane mid-campaign.
        argv += ["--memory-swap", lane["resources"]["memory_swap"]]
    argv += [env["image"], "bash", "-c", inner]
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env {env_source} | "
          f"slice {slice_name} ({slice_src})", flush=True)
    log_forwarded_env(env, "ephemeral")  # names only, never values (RG-19)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    print(f"run-gate: docker argv: "
          f"{shlex.join(redact_forwarded_values(argv, env.get('forward_env', [])))}",
          flush=True)
    if dry_run:
        # RG-8: the plan above IS what the live run executes — same assembly
        # code path, only the `docker run` itself skipped.
        print("run-gate: DRY RUN — no container was started", flush=True)
        return 0
    started = subprocess.run(argv, capture_output=True, text=True)
    if started.returncode != 0:
        # RG-12: a failed `docker run` may still have created the container
        # (pull/entrypoint failures) — preserve its logs, then show a REAL
        # tail of stderr instead of only the last line.
        saved = save_container_logs(docker, name)
        lines = started.stderr.strip().splitlines() or ["(no stderr)"]
        tail = "\n".join(f"    {line}" for line in lines[-EVIDENCE_TAIL_LINES:])
        subprocess.run([docker, "rm", "-f", name], capture_output=True)
        where = f"\nrun-gate: partial container logs: {saved}" if saved else ""
        fail_infra(f"docker run failed (exit {started.returncode}); last "
                   f"stderr line(s):\n{tail}{where}")
    try:
        logs = subprocess.run([docker, "logs", "-f", name])
        waited = subprocess.run([docker, "wait", name], capture_output=True, text=True)
        out = waited.stdout.strip()
        code = int(out) if waited.returncode == 0 and re.fullmatch(r"-?\d+", out) else None
    finally:
        saved_log = save_container_logs(docker, name)
        subprocess.run([docker, "rm", "-f", name], capture_output=True)
    if logs.returncode != 0:
        print(f"run-gate: WARNING: docker logs exit {logs.returncode}", file=sys.stderr)
    if code is None:
        fail_infra("could not read the container's exit status (docker wait failed) — "
                   "refusing to guess")
    if code != 0:
        where = (f"; full container logs preserved at {saved_log}"
                 if saved_log else
                 "; container logs could NOT be captured before removal")
        print(f"run-gate: lane {lane_name!r} failed with exit {code}{where}",
              flush=True)
    print_lane_artifacts(lane, lane_name, project_dir, worktree)
    return code


def resolve_container_name(env_name: str, env: dict, repo: Path,
                           env_source: str) -> tuple[str, str, str]:
    """Resolve the persistent container name for an exec-mode environment.

    Returns (name, human-readable source, START REMEDY). The remedy names the
    authority that actually owns the resolved name — a declared container_name
    points at the project's own deployment authority, a ciu-derived name at
    the ciu lifecycle (RG-6: a dstdns-shaped project must never be told to
    run a vbpub-specific ciu directory).
    """
    if env.get("container_name"):
        return env["container_name"], f"declared container_name ({env_source})", \
            "start it via YOUR project's deployment authority (whoever owns " \
            "this container); run-gate refuses to guess or auto-start " \
            "deployment-managed containers"
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
    ciu_remedy = (f"start it via this project's ciu lifecycle ('ciu render' "
                  f"if stale, then 'ciu up'; config: {global_toml}); run-gate "
                  f"refuses to guess or auto-start deployment-managed containers")
    project = deploy.get("project_name") or ""
    tag = deploy.get("environment_tag") or ""
    if project and tag:
        return f"{project}-{tag}-{env_name}", \
            f"ciu.global.toml deploy.project_name+environment_tag ({global_toml})", \
            ciu_remedy
    network = deploy.get("network_name") or ""
    if network and network.endswith("-network"):
        prefix = network[:-len("-network")]
        return f"{prefix}-{env_name}", \
            f"ciu.global.toml deploy.network_name stripped of '-network' ({global_toml})", \
            ciu_remedy
    fail(f"cannot derive container name from {global_toml}: need "
         f"[deploy] project_name+environment_tag OR network_name ending '-network'; "
         f"or declare container_name on the environment")


def run_exec_lane(lane: dict, lane_name: str, project_dir: Path, repo: Path,
                  worktree: Path, env: dict, env_source: str, env_name: str,
                  dry_run: bool = False) -> int:
    """Exec into a PERSISTENT runner (started externally by CIU).

    project_dir arrives already relocated into the judged worktree (RG-15).
    Fail-fast: refuses to start the runner itself — that is CIU's job.
    This keeps run-gate as pure gate orchestration without duplicating
    lifecycle management that belongs to the deployment authority.
    """
    docker = shutil.which("docker")
    if not docker:
        fail_infra("docker not found on PATH — exec-mode lanes need it")
    name, name_src, start_remedy = resolve_container_name(
        env_name, env, repo, env_source)
    running = subprocess.run([docker, "ps", "--format", "{{.Names}}"],
                             capture_output=True, text=True)
    if running.returncode != 0:
        detail = running.stderr.strip().splitlines()[-1:] or [f"exit {running.returncode}"]
        fail_infra(f"docker ps failed for exec-mode preflight: {detail[0]}")
    names = set(running.stdout.strip().splitlines())
    if name not in names:
        fail(f"persistent runner '{name}' ({name_src}) is not running — "
             f"{start_remedy}")
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
    log_forwarded_env(env, "exec")  # names only, never values (RG-19)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    if dry_run:
        # RG-8: name resolution + running-check above are rehearsed too —
        # a dry-run against a stopped runner reports the real refusal.
        print(f"run-gate: DRY RUN — would exec {name} ({name_src}); "
              f"no command was run", flush=True)
        return 0
    code = subprocess.run(argv).returncode
    print_lane_artifacts(lane, lane_name, project_dir, worktree)
    return code


def run_host_lane(lane: dict, lane_name: str, project_dir: Path, worktree: Path,
                  dry_run: bool = False) -> int:
    # cwd is the project dir RELOCATED into the judged worktree (RG-15) — a
    # host lane must not quietly operate on the invocation checkout either.
    argv = substitute_worktree(lane["argv"], worktree)
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env built-in 'host'",
          flush=True)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    if dry_run:
        print(f"run-gate: DRY RUN — would run in {project_dir}: "
              f"{shlex.join(argv)}", flush=True)
        return 0
    code = subprocess.run(argv, cwd=str(project_dir)).returncode
    print_lane_artifacts(lane, lane_name, project_dir, worktree)
    return code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_list(lanes: dict) -> int:
    for name, lane in sorted(lanes.items()):
        print(f"{name}\t{lane['kind']}\t{lane['environment']}")
    return 0


def usage(lanes: dict, inherited: set[str] | None = None) -> str:
    inherited = inherited or set()
    table = sorted(lanes.items())
    lines = [
        f"{PROG} rev {__revision__} — the per-project gate entrypoint",
        "",
        "usage: run-gate.py <lane> [--worktree PATH] [--allow-dirty]",
        "       run-gate.py --list   (machine-readable: name<TAB>kind<TAB>environment)",
        "       run-gate.py validate-pointers CONSUMER.toml [--root DIR]",
        "         (RG-2: certify every run-gate pointer in a consumer document —",
        "          trove gates, release steps — against the SSOT lanes they name)",
        "       run-gate.py doctor   (RG-9 preflight: docker, slices, mountinfo, git, images)",
        "",
        "lanes (run-gate.toml; * = inherited from the repo-root config):",
    ]
    if not table:
        lines.append("  (none defined)")
    for name, lane in table:
        marker = "*" if name in inherited else ""
        bits = [f"kind={lane['kind']}",
                f"environment={lane['environment']}",
                "clean_tree=true" if lane.get("clean_tree", True)
                else "clean_tree=FALSE"]
        if lane.get("budget"):
            bits.append(f"budget={lane['budget']} (advisory)")
        mem = lane.get("resources", {}).get("memory") or lane.get("memory")
        if mem:
            bits.append(f"memory={mem}")
        res = lane.get("resources", {})
        res_bits = []
        if res.get("memory_swap"):
            res_bits.append(f"swap={res['memory_swap']}")
        for key in ("cpu_weight", "io_weight"):
            if res.get(key):
                res_bits.append(f"{key}={res[key]} (advisory)")
        if res.get("shared"):
            res_bits.append(f"shared=[{','.join(res['shared'])}]")
        lines.append(f"  {name:<24}{marker} " + "  ".join(bits))
        if res_bits:
            lines.append(f"  {'':<24}  resources: " + "  ".join(res_bits))
        if lane.get("description"):
            lines.append(f"  {'':<24}  {lane['description']}")
    lines += [
        "",
        "flags:",
        "  --worktree PATH   judge — and execute lanes IN — a different tree; the",
        "                    invoking checkout is never judged by side effect",
        "  --allow-dirty     bypass THIS tool's clean-tree refusal; assay lanes",
        "                    still enforce assay's own clean-tree rule afterwards",
        "                    (two independent layers — the flag lifts only this one)",
        "  --dry-run         print the full execution plan (docker argv, mounts,",
        "                    slice, inner command) and exit 0 — every preflight",
        "                    is rehearsed, nothing runs",
        "  --check-env       advisory drift sweep: env references in the project's",
        "                    Python sources covered by neither forward_env nor a",
        "                    lane's required_env (heuristic — warns, never refuses)",
        "",
        "environment contract (DERIVE / READ / FAIL — no silent defaults):",
        "  CGROUP_PARENT_DEV_BACKGROUND  container lanes take their cgroup slice",
        "                                from the environment's declared cgroup_slice,",
        "                                else THIS variable; absent = hard error",
        "  RUN_GATE_EXTRA_MOUNTS         colon-separated host=container pairs appended",
        "                                to EPHEMERAL container lanes (e.g. docker.sock)",
        "  RUN_GATE_MOUNT_ALIAS          'host=namespace' declaring the repo's second",
        "                                mount view when none is derivable (bare host)",
        "  RUN_GATE_EVIDENCE_DIR         where preserved container logs are written",
        f"                                on failure (default {EVIDENCE_DIR_DEFAULT})",
        f"  {CGROUPFS_ROOT_ENV_VAR}      cgroupfs root for slice-memory admission",
        "                                (default /sys/fs/cgroup; override in namespaces",
        "                                that hide the host cgroup or in tests)",
        "",
        "Lane declarations: run-gate.toml next to this script; shared environment",
        "facts may live in an enclosing repo-root run-gate.toml. Judgment policy",
        "belongs to assay (assay.toml), never here. See run-gate-project/README.md.",
        "",
        "exit codes: the lane's own status passes through unchanged; refusals and",
        "            failures reserve 2 = configuration/refusal (bad key, unknown",
        "            lane, dirty tree, preflight) and 3 = execution infrastructure",
        "            (docker/git/mountinfo could not do their job).",
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
    parser.add_argument("target", nargs="?")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check-env", action="store_true",
                        help="advisory drift sweep: env references not covered "
                             "by forward_env/required_env")
    parser.add_argument("--root", help="validate-pointers only: the worktree "
                        "root {worktree} stands for (default: git toplevel of "
                        "the pointer file)")
    parser.add_argument("--worktree")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the full execution plan and exit 0 — "
                             "every preflight is rehearsed, nothing runs")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args(argv)

    try:
        project_dir = find_project_dir()
        if args.help or (args.lane is None and not args.list
                         and not args.check_env):
            if project_dir is None:
                fail(f"no {CONFIG_NAME} found next to the invoked script or CWD "
                     f"(run-gate rev {__revision__})")
            cfg, _, central, _ = load_config(project_dir)
            print(usage(cfg.get("lanes", {}), set(central.get("lanes", {}))
                        - set(cfg.get("lanes", {}))))
            return 0
        if args.lane == "validate-pointers":
            # RG-2 linkage verb — certifies CONSUMER documents; needs no
            # project config of its own.
            if not args.target:
                fail("validate-pointers requires the consumer file to certify "
                     "(e.g. <proj>/nyxloom-trove/nyxloom.toml)")
            return cmd_validate_pointers(Path(args.target), args.root)
        if project_dir is None:
            fail(f"no {CONFIG_NAME} found next to the invoked script or "
                 f"{Path.cwd()} — run-gate resolves its config beside the invoked "
                 f"(sym)link/copy")
        cfg, cfg_path, central, central_path = load_config(project_dir)
        # RG-16: effective lane set = project lanes shadowing shared central
        # lanes by name; per-consumer pin existence checked inside.
        lanes = merge_lanes(cfg.get("lanes", {}), central, project_dir,
                            cfg_path, central_path)
        if args.lane == "doctor":
            # RG-9 preflight — reads the world, runs nothing.
            return cmd_doctor(lanes, project_dir, cfg, central,
                              cfg_path, central_path)
        if args.list:
            return cmd_list(lanes)
        if args.check_env:
            return cmd_check_env(lanes, project_dir, cfg, central,
                                 cfg_path, central_path)
        if args.lane not in lanes:
            fail(f"unknown lane {args.lane!r} — known lanes: "
                 f"{', '.join(sorted(lanes)) or '(none)'} (config: {cfg_path}"
                 f"{f'; shared: {central_path}' if central_path else ''})")
        lane = lanes[args.lane]
        env, env_source = resolve_environment(lane, args.lane, cfg, central,
                                              cfg_path, central_path)
        # RG-17/19: declared inputs verified BEFORE anything runs — presence
        # in the invoking environment for every kind; for container lanes
        # also that the names are on the forward_env allowlist at all.
        preflight_required_env(lane, args.lane)
        if env:
            check_required_reaches_container(lane, args.lane, env,
                                             lane_environment_name(lane),
                                             env_source)
        repo, worktree, toplevel = resolve_repo_and_worktree(
            project_dir, args.worktree)
        # RG-15: runners receive the project RELOCATED into the judged tree —
        # their `project_dir` parameter is the effective one, never the
        # invocation checkout when --worktree selects a different tree.
        eff_proj = effective_project_dir(project_dir, toplevel, worktree)
        # RG-5: every lane kind refuses a metachar worktree path — the daemon
        # pointer recipe embeds {worktree} into bash strings regardless of
        # what this particular lane does with it.
        check_worktree_charset(worktree)
        # RG-1: an override the lane cannot possibly honor is a silent
        # false-PASS machine — a container command lane whose argv carries no
        # {worktree} token would let sub-steps re-derive their own tree and
        # judge something else. Assay lanes relocate automatically (R-21) and
        # host lanes relocate via cwd, so both are exempt.
        if args.worktree and lane["kind"] == "command" and env \
                and not any("{worktree}" in element for element in lane["argv"]):
            fail(f"--worktree '{args.worktree}' would be SILENTLY IGNORED by "
                 f"container lane '{args.lane}': its argv contains no "
                 f"{{worktree}} token, so sub-steps re-derive their own tree — "
                 f"declare '--worktree {{worktree}}' inside the lane argv "
                 f"(CONSUMERS 'Gate-conjunction lanes') or drop the flag")
        if lane.get("clean_tree", True) and not args.allow_dirty:
            check_clean_tree(worktree)
        # RG-20 resource-aware admission: shared-infra serialization for every
        # kind (acquired last — a blocking wait must never precede fast-fail
        # refusals; released in finally), and slice-memory accounting for
        # ephemeral container lanes whose slice is the accounting domain.
        locks = acquire_shared_locks(lane, args.lane, args.dry_run)
        try:
            slice_name = slice_src = None
            if env and env.get("mode") != "exec":
                slice_name, slice_src = resolve_slice(env, env_source)
                check_slice_memory_admission(lane, args.lane, slice_name,
                                             slice_src)
            if not env:  # built-in 'host'
                code = run_host_lane(lane, args.lane, eff_proj, worktree,
                                     dry_run=args.dry_run)
            elif env.get("mode") == "exec":
                code = run_exec_lane(lane, args.lane, eff_proj, repo, worktree,
                                     env, env_source, lane_environment_name(lane),
                                     dry_run=args.dry_run)
            else:
                code = run_container_lane(lane, args.lane, eff_proj, repo,
                                          worktree, env, env_source,
                                          slice_name, slice_src,
                                          dry_run=args.dry_run)
            print(f"run-gate: lane {args.lane!r} exit {code}", flush=True)
            return code
        finally:
            for fd in locks:
                os.close(fd)  # releases the flock
    except GateError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
