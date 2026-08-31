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
__revision__ = 30  # rev 30 (round-2 review fixes folded in: `history` honors --worktree on the READ side and refuses an override that names no git work tree, so a query can never answer with the invoking checkout's data under another tree's name; flushing a record is at-most-once, so a Ctrl-C inside the telemetry write surfaces as the KeyboardInterrupt instead of a second-flush traceback; `--json` is refused by name outside `history` instead of accepted and ignored; `history` as a lane name is a flagged LOAD-TIME breaking change): RG-27 lane invocation history — a per-(judged worktree × project) `.run-gate/history.json` store holding, per lane, a `latest` slot (ANY outcome, dirty/aborted/mid-rebase included) and a bounded per-commit trend series ([history] keep, default 10); completed fails join history WITH their outcome and the stats are split passes/completed, aborted+dirty+mid-rebase runs never do; new `history [LANE] [--json]` query verb; concurrency answered by SCOPE first (two worktrees address two files) then a sibling-lockfile + atomic-rename write; the store must be git-ignored or the write is refused with the remedy rather than dirtying the tree (R-36); rev 29: P02 review round — the RG-25 `command -v` fitness probe is BATCHED per environment over the union of every lane's tools (was one container per lane, which made R-30's own cost claim quantitatively false), and the three places still claiming `--dry-run`/`doctor` start nothing now say what they actually start; rev 28: RG-26 `--base REF` reaches a delegating assay lane as `--request-base` (assay B019 usable from the gate at last) — delegation DERIVED from `assay lanes --json`, no new run-gate.toml key; conjunction lanes propagate it through a `{base}` token; a non-delegating lane refuses it by name (R-35). Also RG-28: an assay lane on the built-in host environment no longer raises KeyError('argv') (R-19); rev 27: RG-25 doctor/--check-env ask the JUDGE (`assay lanes --json`, B044) what each assay lane needs and check the environment for it, through ONE in-environment probe builder shared with the pin probe; FAIL only for facts the inventory established, SKIP for every "could not determine" so an older judge never turns a healthy project red (R-34); rev 26: RG-21 doctor names the linked-worktree host-lane git view before a downstream host-path-mounting harness fails mid-run (R-30a; warning only — run-gate is not the defect, the harness's single mount is); rev 25: RG-23 exec-mode env forwarding is DECLARED, never implicit — the dropped MOCK_MODE/RUN_LIVE_TESTS allowlist is documented as a breaking change with its migration (R-24a), and --check-env's drift sweep is AST-based so it sees helper-wrapped reads, the shape that hid the false-green flag (R-24b); rev 24: RG-24 exec-mode container names resolve from the JUDGED WORKTREE's ciu.global.toml first (repo-relative is the fallback, not the authority — a Mode-B worktree no longer execs into the main landscape's runner); rev 23: RG-22 safe.directory global-config write is now idempotent under pre-existing entries (--replace-all, R-19a); rev 21-22: adversarial-review hardening — size grammar unified (_SIZE_RE), shared-infra locks sorted-order+O_NOFOLLOW+0600 with admission-before-wait, pointer collector recognizes console-script form + prose/discovery exemptions, exec-lane slice/argv disclosure (naming-only), central-lanes docs truth, evidence only-on-failure at 0600, doctor survives broken hosts, verdict dedup normalized, pin-version whole-token match, reserved lane names + symmetric sidecar checks; rev 20: RG-13 adoption hygiene — worked run-gate×assay example, gitignore obligation, estate README retro ×9, root discovery line, budget↔timeout pairing sweep (R-32; docs/test-only, no behavior change); rev 19: RG-14 wheel as second artifact — pyproject derives version from __revision__, `run-gate` console script, byte-identical module discipline (R-31); rev 18: RG-9 doctor preflight verb — docker/slices/mountinfo/git/images in one command (R-30); rev 17: RG-20 resource-aware admission — slice-RAM budget from cgroupfs + shared-infra locks, lane `resources` key (R-29); rev 16: RG-8 --dry-run plan rehearsal on all three runners (R-28); rev 15: RG-2 validate-pointers verb + estate linkage certification (R-27); rev 14: RG-10 declared artifacts + unconditional evidence-path disclosure in all three runners (R-08/R-18); rev 13: RG-12 evidence preservation + stderr tail (R-26); rev 12: RG-1 override guard (R-25); rev 11: RG-17/19 required_env preflight + forwarding log + --check-env (R-24); rev 10 RG-6; rev 9 RG-5 (R-02); rev 8 RG-3 (R-23); rev 7 RG-16 (R-22); rev 6 RG-4; rev 5 RG-11; rev 4 RG-15

import argparse
import ast
import fcntl
import json
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

# RG-27 lane invocation history. The store is PER (judged worktree × project):
# it lives under the EFFECTIVE project dir, which R-21 already relocates into
# the judged tree — so two worktrees' gates address two different files by
# construction and never contend. The remaining contention is real but small
# (two lanes of ONE project, in ONE tree, in parallel), and it is arbitrated by
# a sibling lock file + atomic rename, never by a lock on the store itself
# (the rename changes the inode; a lock held on the old one guards nothing).
HISTORY_DIR_NAME = ".run-gate"
HISTORY_FILE_NAME = "history.json"
HISTORY_LOCK_NAME = "history.lock"
HISTORY_SCHEMA = 1
HISTORY_KEEP_DEFAULT = 10
HISTORY_LOCK_TIMEOUT = 5.0   # seconds — telemetry NEVER blocks a gate
HISTORY_OUTCOMES = ("pass", "fail", "error", "aborted")


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
    if not isinstance(value, str) or not _SIZE_RE.fullmatch(value):
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
    if name in _RESERVED_POINTER_VERBS:
        # Review fix: a lane named like a CLI verb can never be invoked (the
        # verb wins) and validate-pointers deliberately exempts the verbs —
        # refuse the shadowing name at load instead.
        fail(f"{where} [lanes.{name}]: lane name {name!r} is reserved — it is "
             f"a run-gate CLI verb; rename the lane")
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
                 f"list of non-empty path strings (relative to the effective "
                 f"project dir or absolute; printed on lane exit)")
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
                               or not _SIZE_RE.fullmatch(res[key])):
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


def _validate_history_policy(table: object, where: str) -> None:
    """RG-27 `[history]`: the retention BOUND is declared policy, not ambient
    state. It belongs in the config (auditable, reviewable, shadowable by the
    R-09 rule) even though the data it bounds is per-instance — how much trend
    a project keeps is a decision, not a fact about this machine."""
    if not isinstance(table, dict):
        fail(f"{where}: 'history' must be a table")
    _check_keys(table, {"keep"}, f"{where} [history]")
    if "keep" in table:
        keep = table["keep"]
        # bool is an int subclass — `keep = true` is a config mistake, not 1.
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
            fail(f"{where} [history]: 'keep' must be an integer >= 1 "
                 f"(got {keep!r})")


def resolve_history_keep(cfg: dict, cfg_path: Path, central: dict,
                         central_path: Path | None) -> tuple[int, str]:
    """Project `[history]` shadows the central one entirely (R-09's rule,
    applied to the same-shaped question), then the documented default."""
    if "keep" in cfg.get("history", {}):
        return cfg["history"]["keep"], f"[history] in {cfg_path}"
    if central_path is not None and "keep" in central.get("history", {}):
        return central["history"]["keep"], f"[history] in central {central_path}"
    return HISTORY_KEEP_DEFAULT, f"default ({HISTORY_KEEP_DEFAULT})"


def _validate_config(cfg: dict, path: Path, *, central: bool) -> dict:
    where = str(path)
    _check_keys(cfg, {"schema_version", "environments", "lanes", "history"},
                where)
    if "history" in cfg:
        _validate_history_policy(cfg["history"], where)
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

    Per-consumer existence check: a pin sidecar must exist relative to THIS
    consuming project — for INHERITED lanes (a shared gate referencing
    artifacts the project does not vendor) and, symmetrically (review fix),
    for the project's OWN lane pins. Both refuse at load naming lane,
    sidecar, and project dir. Free-form argv strings are deliberately NOT
    stat'd: they are shell text, not declared paths, and pretending
    otherwise would certify nothing.
    """
    merged = dict(central.get("lanes", {}))
    merged.update(project_lanes)
    for name, lane in merged.items():
        inherited = name in central.get("lanes", {}) \
            and name not in project_lanes
        for pin_name, pin in lane.get("pins", {}).items():
            sidecar = project_dir / pin["sha256"]
            if sidecar.is_file():
                continue
            origin = (f"central lane '[lanes.{name}]' ({central_path})"
                      if inherited else f"lane '[lanes.{name}]'")
            fail(f"{origin}: pin '{pin_name}' sidecar {pin['sha256']} does not "
                 f"exist in this project ({project_dir}) — vendor it or shadow "
                 f"the lane")
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
    # Review fix: name the file ACTUALLY searched, never a generic claim —
    # when no central config exists at all, saying "nor in a repo-root
    # run-gate.toml" sends the reader hunting for a file that isn't there.
    if central_path is not None:
        fail(f"[lanes.{lane_name}] in {project_path}: environment '{name}' is "
             f"not defined in {project_path} nor in central config {central_path}")
    fail(f"[lanes.{lane_name}] in {project_path}: environment '{name}' is not "
         f"defined in {project_path} (no central {CONFIG_NAME} exists)")


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
    slice and the suite's own governance tests verify placement). A host
    that has the run-dir but no runnable systemctl counts as unreachable
    too — review fix: loud skip, never a FileNotFoundError traceback
    (R-30: a preflight must survive the broken host it diagnoses)."""
    if not os.path.isdir("/run/systemd/system"):
        return
    try:
        proc = subprocess.run(["systemctl", "show", "--property=LoadState",
                               "--value", slice_name],
                              capture_output=True, text=True)
    except OSError as exc:
        print(f"run-gate: WARNING: cannot LoadState-check {slice_name}: {exc} "
              f"— pre-check unreachable here; the -e passthrough carries the "
              f"slice either way", file=sys.stderr, flush=True)
        return
    if proc.returncode != 0 or proc.stdout.strip() != "loaded":
        state = proc.stdout.strip() or f"systemctl exit {proc.returncode}"
        fail(f"gate slice {slice_name} is not LoadState=loaded (got: {state}) — "
             f"a typo'd slice name fails OPEN (systemd auto-creates an unlimited "
             f"transient slice)")


# ---------------------------------------------------------------------------
# RG-20 — resource-aware admission: RAM is the real contention hazard
# ---------------------------------------------------------------------------

_SIZE_MULT = {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
# ONE size grammar for every declaration site and the parser (review fix:
# _validate_memory accepted '512M' case-insensitively while this parser was
# case-sensitive — a grammar-valid config used to traceback at admission).
_SIZE_RE = re.compile(r"(\d+)([bkmg]?)", re.IGNORECASE)


def parse_size_bytes(value: str) -> int:
    """'4g' -> bytes. Callers validate the \\d+[bkmg]? shape first."""
    m = _SIZE_RE.fullmatch(value)
    return int(m.group(1)) * _SIZE_MULT[m.group(2).lower()]


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
    # Sorted order is the deadlock fix (review MAJOR): declared-order
    # acquisition let gate A hold 'pg' while waiting on 'redis' and gate B
    # hold 'redis' while waiting on 'pg' — a classic ABBA hang with no
    # diagnostic. A canonical GLOBAL order makes hold-and-wait cycles
    # impossible regardless of how each project lists its services.
    for svc in sorted(names):
        path = Path(SHARED_LOCK_DIR) / f"run-gate-shared-{svc}.lock"
        try:
            # 0600 + O_NOFOLLOW (review MINOR): the file is content-free
            # coordination state with a predictable name; don't follow a
            # planted symlink and don't share it across accounts.
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"run-gate: lane {lane_name!r}: waiting for shared "
                      f"infra '{svc}' — another gate holds {path}", flush=True)
                fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            # Release everything this call already holds, then refuse as
            # infrastructure failure (exit 3 one-liner, never a traceback).
            for held in fds:
                os.close(held)
            fail_infra(f"lane {lane_name!r}: shared-infra lock {path} "
                       f"unusable: {exc}")
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
# lane invocation history (RG-27) — measure and persist; decide NOTHING
# ---------------------------------------------------------------------------
#
# run-gate is the layer that actually starts each lane, so it is the only one
# that sees start/stop and exit status first-hand. It records them and stops
# there: no rigor/defer POLICY lives here (RG-27 scope), only the series a
# controller needs to have such a policy at all.
#
# Two slots per lane, with DIFFERENT contracts, and the difference is the
# whole design:
#
#   latest  — the most recent invocation, WHATEVER happened to it (pass, fail,
#             tool error, Ctrl-C, dirty tree, mid-rebase). Diagnostics.
#   history — a curated trend series keyed by (lane, commit), bounded to the
#             last `keep` commits. ONLY runs whose (commit, duration) pairing
#             is actually meaningful get in.
#
# Letting the second inherit the first's permissiveness is the named trap: a
# dirty-tree run's duration attributed to a commit that never ran it, silently
# overwriting the real measurement.

def history_dir(project_dir: Path) -> Path:
    return project_dir / HISTORY_DIR_NAME


def history_store_path(project_dir: Path) -> Path:
    return history_dir(project_dir) / HISTORY_FILE_NAME


def history_written_paths(project_dir: Path) -> list[Path]:
    """EVERY path the recorder can leave in the tree — the store, the lock,
    and the temp file a crash between write and rename would strand. The
    ignore question has to be asked about all three, not just the one we
    think of first."""
    hdir = history_dir(project_dir)
    return [hdir / HISTORY_FILE_NAME, hdir / HISTORY_LOCK_NAME,
            hdir / f"{HISTORY_FILE_NAME}.tmp.{os.getpid()}"]


def paths_are_git_ignored(worktree: Path, targets: list[Path]) -> bool | None:
    """True only when EVERY target is ignored. True / False / None ("git
    could not tell us").

    Two things here are load-bearing and were both verified against real git
    rather than assumed:

    1. The paths asked about are the FILES, never the bare directory. `git
       check-ignore .run-gate` on a directory that does not exist yet answers
       *not ignored* even when `.gitignore` says `.run-gate/` — the
       trailing-slash pattern needs a directory to match, and the first run
       of a correctly-configured project has none. Asking about
       `.run-gate/history.json` answers correctly in every case.
    2. The verdict is read from the REPORTED PATHS, not the exit status.
       `git check-ignore a b` exits 0 when ANY argument matches — reading
       that as "both are ignored" is the false-certification shape AGENTS.md
       names: the message says "safe to write" while the comparison only
       established "at least one of these is safe".

    Run deliberately WITHOUT `--no-index`: the question is not "do the ignore
    rules match" but "would writing here dirty the tree", and a TRACKED path
    dirties it whatever .gitignore says — which is what the index-aware
    default reports."""
    wanted = [str(t) for t in targets]
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "check-ignore",
                               "--", *wanted], capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode not in (0, 1):
        return None  # 128 and friends: not an answer, do not pretend it is
    reported = {line.strip() for line in proc.stdout.splitlines()
                if line.strip()}
    return all(path in reported for path in wanted)


def git_operation_in_progress(worktree: Path) -> str | None:
    """Names an in-flight git operation (rebase/merge/cherry-pick/revert/
    bisect) whose HEAD is a transient the trend series must not be keyed to,
    else None. `None` is also returned when the git dir is unreadable — the
    caller pairs this with a separate 'could we even determine it' flag."""
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "rev-parse",
                               "--git-dir"], capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    gitdir = Path(proc.stdout.strip())
    if not gitdir.is_absolute():
        gitdir = worktree / gitdir
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD",
                   "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG"):
        if (gitdir / marker).exists():
            return marker
    return None


def head_commit(worktree: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "rev-parse",
                               "--verify", "HEAD"], capture_output=True,
                              text=True)
    except OSError:
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) \
        else None


def worktree_is_dirty(worktree: Path) -> bool | None:
    """None = could not determine. Sampled INDEPENDENTLY of clean_tree policy:
    the discriminator for the trend series is whether the tree WAS dirty, not
    whether dirt was permitted — a `clean_tree = false` lane run on a clean
    tree produces a perfectly good measurement and must not be excluded."""
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "status",
                               "--porcelain"], capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return any(line.strip() for line in proc.stdout.splitlines())


def start_run_record(lane_name: str, worktree: Path, repo: Path) -> dict:
    """Sample the state that is ABOUT to be judged, BEFORE the lane runs.

    Sampling afterwards would be a different tree: a lane may commit, stash,
    or leave artifacts behind, and a history entry keyed to a commit must
    describe the state that commit actually had when it was measured."""
    dirty = worktree_is_dirty(worktree)
    return {
        "lane": lane_name,
        "commit": head_commit(worktree),
        "outcome": None,
        "exit_code": None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": None,
        "worktree": str(worktree),
        "repo": str(repo),
        "dirty": dirty,
        "git_operation": git_operation_in_progress(worktree),
        "history_eligible": False,
        "excluded_reason": None,
        "revision": __revision__,
        "_started_monotonic": time.monotonic(),
    }


def finish_run_record(record: dict, *, exit_code: int | None = None,
                      error: BaseException | None = None) -> dict:
    """Close a record and decide — once, here — whether it may join history.

    Eligibility is a CONJUNCTION of four facts, each of which independently
    makes a (commit, duration) pair a lie:
      1. the lane completed and reported its own status (an abort measured a
         partial run; an infrastructure error measured no run at all);
      2. the tree was clean (a dirty tree is not the commit it claims to be);
      3. no git operation was in flight (a rebase's HEAD is a transient);
      4. HEAD resolved to a real commit sha (nothing to key on otherwise).
    "Could not determine" resolves toward EXCLUSION for 2-4: a possibly-wrong
    trend entry is worse than a missing one, because the missing one is
    visible in `count` and the wrong one is not visible at all."""
    # Tolerant of a missing start stamp rather than raising on it: this
    # function must not be the thing that turns a recording problem into a
    # traceback (R-36h). No stamp => no duration => not a measurement, which
    # the eligibility conjunction below then refuses on its own terms.
    started = record.pop("_started_monotonic", None)
    record["duration_seconds"] = None if started is None \
        else round(time.monotonic() - started, 3)
    if error is not None:
        record["outcome"] = "aborted" if not isinstance(error, Exception) \
            else "error"
        record["excluded_reason"] = (
            f"{record['outcome']}: {type(error).__name__} — the lane did not "
            f"report its own status, so its duration measures a partial run")
    else:
        record["exit_code"] = exit_code
        record["outcome"] = "pass" if exit_code == 0 else "fail"
    reasons = []
    if record["duration_seconds"] is None:
        reasons.append("no duration was measured — clause 1 of R-36b: an "
                       "entry without a duration is not a measurement")
    if record["outcome"] in ("aborted", "error"):
        pass  # already explained above; keep the specific message
    elif record["dirty"] is None:
        reasons.append("could not determine whether the tree was clean")
    elif record["dirty"]:
        reasons.append("the judged tree was dirty — the duration does not "
                       "belong to this commit")
    if record["git_operation"]:
        reasons.append(f"git operation in progress ({record['git_operation']})"
                       " — HEAD is a transient")
    if not record["commit"]:
        reasons.append("HEAD did not resolve to a commit")
    if reasons:
        record["excluded_reason"] = (record["excluded_reason"] or
                                     "; ".join(reasons))
    if record["excluded_reason"] is None:
        # RG-27 design call, recorded: a COMPLETED fail joins history. Its
        # duration is real measured cost of the same work — but it is stored
        # WITH its outcome, and the reported stats are split pass/completed,
        # because a failing lane can short-circuit (this project's own
        # `pytest && coverage_gate` never reaches the gate when pytest is
        # red), so averaging the two understates the lane's true cost in
        # exactly the direction that makes a "cheap, always run it" call
        # wrong. run-gate hands over both series; it does not pick one.
        record["history_eligible"] = True
    return record


def _empty_history_store() -> dict:
    return {"schema": HISTORY_SCHEMA, "lanes": {}}


def load_history_store(path: Path) -> dict:
    """Never raises: a missing, unreadable, or corrupt store yields an empty
    one. Telemetry that could take a gate down would be a net loss."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty_history_store()
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict):
        return _empty_history_store()
    data.setdefault("schema", HISTORY_SCHEMA)
    return data


def _apply_record(store: dict, record: dict, keep: int) -> dict:
    slot = store["lanes"].setdefault(record["lane"],
                                     {"latest": None, "history": []})
    entry = {k: v for k, v in record.items() if not k.startswith("_")}
    slot["latest"] = entry
    if not entry["history_eligible"]:
        return store
    hist = [e for e in slot.get("history", []) if isinstance(e, dict)]
    # Keyed by (lane, commit): a re-run of the same commit REPLACES its entry
    # rather than adding a second — otherwise ten re-runs of one commit fill
    # the whole window and "the last N commits" stops being true. It moves to
    # the tail, so eviction means "least recently measured".
    hist = [e for e in hist if e.get("commit") != entry["commit"]]
    hist.append(entry)
    slot["history"] = hist[-keep:]
    return store


def _write_history_store(store: dict, path: Path) -> None:
    """Atomic replace. This is what lets READERS take no lock at all: a
    reader either sees the whole old file or the whole new one, never a
    half-written middle."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _record_invocation(project_dir: Path, worktree: Path, record: dict,
                       keep: int) -> None:
    hdir = history_dir(project_dir)
    ignored = paths_are_git_ignored(worktree, history_written_paths(project_dir))
    if ignored is not True:
        why = ("is not fully git-ignored" if ignored is False
               else "could not be confirmed git-ignored")
        raise GateError(
            f"lane history not recorded: {hdir} {why}, and writing there "
            f"would leave the judged tree dirty for the NEXT lane's "
            f"clean-tree check — add '{HISTORY_DIR_NAME}/' to the .gitignore "
            f"covering {worktree}")
    hdir.mkdir(parents=True, exist_ok=True)
    lock_path = hdir / HISTORY_LOCK_NAME
    # 0600 + O_NOFOLLOW, matching acquire_shared_locks: content-free
    # coordination state at a predictable path.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + HISTORY_LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                # Bounded, unlike RG-20's shared-infra lock which blocks
                # forever ON PURPOSE. That one protects the CORRECTNESS of
                # the run; this one protects a measurement, and a gate that
                # hangs waiting to write telemetry has inverted the priority.
                if time.monotonic() >= deadline:
                    raise GateError(
                        f"lane history not recorded: {lock_path} held by "
                        f"another gate for >{HISTORY_LOCK_TIMEOUT:g}s")
                time.sleep(0.05)
        store_path = history_store_path(project_dir)
        _write_history_store(
            _apply_record(load_history_store(store_path), record, keep),
            store_path)
    finally:
        os.close(fd)  # releases the flock


def record_invocation(project_dir: Path, worktree: Path, record: dict,
                      keep: int) -> bool:
    """Best-effort by contract: the lane's verdict is the product, this is a
    note in the margin. Every failure degrades to ONE visible warning line —
    never a traceback (R-04), never a changed exit status."""
    try:
        _record_invocation(project_dir, worktree, record, keep)
        return True
    except Exception as exc:  # noqa: BLE001 — deliberate, see docstring
        detail = str(exc) if isinstance(exc, GateError) \
            else f"lane history not recorded: {type(exc).__name__}: {exc}"
        print(f"{PROG}: WARNING: {detail}", file=sys.stderr, flush=True)
        return False


def flush_run_record(record: dict | None, *, exit_code: int | None = None,
                     error: BaseException | None = None) -> None:
    """Close and persist in ONE step, so main()'s three exits — normal
    return, refusal, abort — cannot drift in how they record. `None` means
    there was nothing to record (a dry run, or a failure before the lane
    resolved); the caller does not branch on it.

    AT MOST ONCE per record, and the sentinel is set BEFORE the work. The
    normal-path flush in main() sits inside main()'s own try, and it is not
    instantaneous — it spawns `git check-ignore` and may wait up to
    HISTORY_LOCK_TIMEOUT on the lock. A Ctrl-C landing in that window is
    caught by main()'s BaseException handler, which flushes again; without
    the sentinel that second flush re-entered an already-consumed record and
    raised, replacing the real signal with a traceback — the exact opposite
    of what R-36h and R-04 promise. Claiming the record first means the
    second call is a clean no-op and the KeyboardInterrupt continues on its
    way, which is the whole point of catching it."""
    if record is None or record.get("_flushed"):
        return
    record["_flushed"] = True
    record_invocation(record["_project_dir"], Path(record["worktree"]),
                      finish_run_record(record, exit_code=exit_code,
                                        error=error),
                      record["_keep"])


def duration_stats(entries: list[dict]) -> dict:
    """min / median / max over a set of history entries.

    MEDIAN, not mean, and that is the point: the trap this whole entry exists
    to avoid is one slow outlier being read as the lane's permanent cost, and
    the mean is precisely the statistic that lets it. `max` is still reported
    — an outlier is information, it just is not the typical cost."""
    values = sorted(e["duration_seconds"] for e in entries
                    if isinstance(e.get("duration_seconds"), (int, float)))
    if not values:
        return {"count": 0, "min_seconds": None, "median_seconds": None,
                "max_seconds": None}
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else \
        round((values[mid - 1] + values[mid]) / 2, 3)
    return {"count": len(values), "min_seconds": values[0],
            "median_seconds": median, "max_seconds": values[-1]}


def lane_history_report(store: dict, lane_name: str) -> dict:
    slot = store.get("lanes", {}).get(lane_name) or {}
    hist = [e for e in slot.get("history", []) if isinstance(e, dict)]
    passes = [e for e in hist if e.get("outcome") == "pass"]
    return {
        "latest": slot.get("latest"),
        "history": hist,
        "stats": {"passes": duration_stats(passes),
                  "completed": duration_stats(hist)},
    }


def _fmt_seconds(value: object) -> str:
    return f"{value:.1f}s" if isinstance(value, (int, float)) else "-"


def _fmt_stats(label: str, stats: dict) -> str:
    if not stats["count"]:
        return f"{label}: none recorded"
    return (f"{label}: n={stats['count']} "
            f"median {_fmt_seconds(stats['median_seconds'])} "
            f"(min {_fmt_seconds(stats['min_seconds'])}, "
            f"max {_fmt_seconds(stats['max_seconds'])})")


def _print_lane_history(lane_name: str, report: dict, keep: int) -> None:
    latest = report["latest"]
    print(f"lane {lane_name}")
    if latest is None:
        print("  latest:  (no recorded invocation)")
    else:
        exit_bit = "" if latest.get("exit_code") is None \
            else f" exit {latest['exit_code']}"
        print(f"  latest:  {latest.get('outcome')}{exit_bit}  "
              f"{_fmt_seconds(latest.get('duration_seconds'))}  "
              f"{(latest.get('commit') or '(no commit)')[:12]}  "
              f"{latest.get('started_at')}")
        print(f"           worktree {latest.get('worktree')}")
        if not latest.get("history_eligible"):
            print(f"           NOT in history: {latest.get('excluded_reason')}")
    hist = report["history"]
    if not hist:
        print(f"  history: (empty; keep={keep})")
    else:
        print(f"  history: {len(hist)} of at most {keep} commit(s), "
              f"oldest first")
        print(f"    {'COMMIT':<14}{'OUTCOME':<9}{'DURATION':>9}  STARTED")
        for entry in hist:
            print(f"    {(entry.get('commit') or '')[:12]:<14}"
                  f"{str(entry.get('outcome')):<9}"
                  f"{_fmt_seconds(entry.get('duration_seconds')):>9}  "
                  f"{entry.get('started_at')}")
        print("    " + _fmt_stats("passes", report["stats"]["passes"]))
        print("    " + _fmt_stats("completed (passes + fails)",
                                  report["stats"]["completed"]))


def cmd_history(lanes: dict, project_dir: Path, cfg: dict, cfg_path: Path,
                central: dict, central_path: Path | None,
                lane_name: str | None, as_json: bool,
                worktree_scope: str | None = None) -> int:
    """RG-27 query verb. Reports; judges nothing, decides nothing, and — like
    `--list` — exits 0 whenever the QUERY succeeded, including when there is
    no data yet. An empty store is an answer, not a failure.

    `project_dir` is the EFFECTIVE project dir — already relocated into the
    tree the caller asked about — so the read scope always matches the write
    scope. `worktree_scope` is that tree's path when `--worktree` selected
    it, and it is DISCLOSED (R-05): the answer names the tree it describes,
    it is never left to be inferred."""
    if lane_name is not None and lane_name not in lanes:
        fail(f"unknown lane {lane_name!r} — known lanes: "
             f"{', '.join(sorted(lanes)) or '(none)'} (config: {cfg_path}"
             f"{f'; shared: {central_path}' if central_path else ''})")
    keep, keep_source = resolve_history_keep(cfg, cfg_path, central,
                                             central_path)
    store_path = history_store_path(project_dir)
    store = load_history_store(store_path)
    selected = [lane_name] if lane_name else sorted(lanes)
    if as_json:
        print(json.dumps({
            "schema": HISTORY_SCHEMA,
            "revision": __revision__,
            "store": str(store_path),
            "worktree_scope": worktree_scope,
            "keep": keep,
            "keep_source": keep_source,
            "lanes": {name: lane_history_report(store, name)
                      for name in selected},
        }, indent=2, sort_keys=True))
        return 0
    print(f"{PROG} rev {__revision__} — lane invocation history")
    if worktree_scope:
        print(f"tree:  {worktree_scope}  (--worktree; this answer describes "
              f"THAT tree, not the invoking checkout)")
    print(f"store: {store_path}"
          f"{'' if store_path.is_file() else '  (not written yet)'}")
    print(f"keep:  {keep}  ({keep_source})")
    if not selected:
        print("(no lanes defined)")
        return 0
    for name in selected:
        print("")
        _print_lane_history(name, lane_history_report(store, name), keep)
    return 0


# ---------------------------------------------------------------------------
# command assembly + run
# ---------------------------------------------------------------------------

def substitute_worktree(argv: list[str], worktree: Path,
                        base: str | None = None) -> list[str]:
    """`{worktree}` in every element (R-02), and — RG-26 — `{base}` when a
    comparison base was resolved. `{base}` is left untouched when there is
    none, which cannot happen on the run path: `plan_comparison_base()`
    refuses a `{base}`-carrying lane it could not resolve a base for."""
    out = [a.replace("{worktree}", str(worktree)) for a in argv]
    return [a.replace("{base}", base) for a in out] if base else out


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
    verdict_path: Path | None = None
    if lane["kind"] == "assay":
        verdict_path = Path(os.path.normpath(
            project_dir / (".assay/verdict-" + lane["assay_lane"] + ".json")))
        print(f"run-gate: verdict artifact: {verdict_path}", flush=True)
    for entry in lane.get("artifacts", []):
        substituted = substitute_worktree([entry], worktree)[0]
        target = Path(substituted)
        if not target.is_absolute():
            target = project_dir / target
        # Review fix: dedup compares NORMALIZED effective paths, not raw
        # strings — './.assay/verdict-x.json' or the absolute spelling of
        # the same file is the verdict convention too, disclosed once.
        target = Path(os.path.normpath(target))
        if target == verdict_path:
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
        # Review fix: container logs may echo credential material the suite
        # exercised — owner-only, never world-readable.
        target.write_text(combined)
        target.chmod(0o600)
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
    environment's forward_env allowlist can NEVER reach the lane — refuse
    before anything runs instead of failing (or hollow-skipping) inside the
    container. Run-path timing is deliberate: --list/doctor stay usable on
    an imperfect config."""
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

_ENV_READ_ATTRS = {"get", "setdefault", "pop"}


def _is_environ(node: ast.AST) -> bool:
    """`os.environ`, or a bare `environ` (`from os import environ`)."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ") \
        or (isinstance(node, ast.Name) and node.id == "environ")


def _env_name_expr(node: ast.AST) -> ast.AST | None:
    """The expression NAMING the variable this node reads from the
    environment, or None when the node is not an environment read."""
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        return node.slice
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _ENV_READ_ATTRS \
                and _is_environ(func.value) and node.args:
            return node.args[0]
        called = func.attr if isinstance(func, ast.Attribute) else \
            func.id if isinstance(func, ast.Name) else None
        if called == "getenv" and node.args:
            return node.args[0]
    return None


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def scan_env_references(text: str) -> list[tuple[str, int, str]]:
    """Every environment-variable NAME a Python source reads → (name, line,
    shape). Raises SyntaxError when the source does not parse.

    RG-23: the line regex this replaces could only see a name spelled as a
    literal INSIDE `getenv(...)`/`os.environ[...]`. dstdns reads its live-test
    flag through `_env_flag_enabled("RUN_LIVE_TESTS")`, whose body does
    `os.getenv(name, "")` — the literal and the read are in different
    functions, so the regex saw nothing and `--check-env` certified a clean
    sweep over the exact variable whose silent absence turned an all-skipped
    pytest run into a green live-test lane. A check whose comparison is
    narrower than its message issues a false certification (AGENTS "a check is
    only as strong as what it actually compares"), so the comparison is
    widened rather than the message weakened.

    Two passes over the AST:
      1. direct reads with a literal name — `os.environ["X"]`,
         `os.environ.get("X")/setdefault/pop`, `getenv("X")`, `"X" in
         os.environ` — plus, in the same walk, any function whose body reads
         the environment through one of its own PARAMETERS (an env-reader
         helper) and the position of that parameter;
      2. calls to those helpers, taking the literal at that position.

    Still a heuristic, and still ADVISORY: a name assembled at runtime
    (`os.getenv(prefix + suffix)`) is invisible to any static pass, which is
    why enforcement lives in `required_env` (R-24), not here.
    """
    tree = ast.parse(text)
    refs: list[tuple[str, int, str]] = []
    helpers: dict[str, int] = {}
    # A bound method's `self`/`cls` is not passed at the call site, so its
    # parameter positions are offset by one against the argv the caller
    # writes. Getting this wrong does not merely miss a read — it reports a
    # CONFIDENT name taken from the wrong position.
    methods = {id(fn) for cls in ast.walk(tree)
               if isinstance(cls, ast.ClassDef) for fn in cls.body
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        named = _env_name_expr(node)
        if isinstance(named, ast.Constant) and isinstance(named.value, str):
            refs.append((named.value, node.lineno,
                         "subscript" if isinstance(node, ast.Subscript)
                         else "access"))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], ast.In) \
                and _is_environ(node.comparators[0]) \
                and isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str):
            refs.append((node.left.value, node.lineno, "membership"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in (*node.args.posonlyargs, *node.args.args)]
            if id(node) in methods and params and params[0] in ("self", "cls"):
                params = params[1:]
            for inner in ast.walk(node):
                inner_named = _env_name_expr(inner)
                if isinstance(inner_named, ast.Name) and inner_named.id in params:
                    helpers[node.name] = params.index(inner_named.id)
                    break
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        pos = helpers.get(_called_name(node))
        if pos is None or pos >= len(node.args):
            continue
        arg = node.args[pos]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            refs.append((arg.value, node.lineno,
                         f"helper {_called_name(node)}()"))
    return refs


def cmd_check_env(lanes: dict, project_dir: Path, cfg: dict, central: dict,
                  cfg_path: Path, central_path: Path | None) -> int:
    """RG-17 drift sweep (ADVISORY): scan the project's Python sources for
    environment reads and flag names covered by neither forward_env nor
    required_env. Heuristic by nature (a .get with a default may be
    deliberately optional), so this WARNS; enforcement lives in required_env
    + the preflight.

    RG-23: the scan is AST-based (`scan_env_references`) so it also sees
    reads wrapped in a project's own helper — the shape that hid dstdns's
    `RUN_LIVE_TESTS` from the previous line regex."""
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
        rel = path.relative_to(project_dir)
        try:
            refs = scan_env_references(text)
        except SyntaxError as exc:
            # "Could not read it" must never look like "there is nothing
            # there" (AGENTS anti-pattern #2). Say so, and keep the old line
            # regex as the degraded-but-real fallback for this file.
            print(f"run-gate: env-drift: {rel} does not parse "
                  f"({exc.msg} line {exc.lineno}) — fell back to a line regex, "
                  f"which cannot see helper-wrapped reads", flush=True)
            refs = [(m.group(1), n, "subscript" if "environ[" in line else "access")
                    for n, line in enumerate(text.splitlines(), 1)
                    for m in ENV_REF_RE.finditer(line)]
        for var, lineno, form in refs:
            if var not in covered:
                findings.append((var, rel, lineno, form))
    seen = set()
    for var, rel, lineno, form in findings:
        if (var, str(rel)) in seen:
            continue
        seen.add((var, str(rel)))
        print(f"run-gate: env-drift: ${var} referenced in {rel}:{lineno} "
              f"({form}) is neither forwarded nor declared required_env — "
              f"add it to the environment's forward_env or the lane's "
              f"required_env", flush=True)
    print(f"run-gate: env-drift scan: {len(seen)} uncovered reference(s)"
          f"{' — ADVISORY ONLY, the run was not affected' if seen else ''}",
          flush=True)
    # RG-25: the env-drift half above stays ADVISORY (heuristic — a .get with
    # a default may be deliberately optional). The toolchain half is NOT a
    # heuristic: the judge itself said the lane needs the tool and the
    # environment does not have it, so a FAIL here exits 2, `--check-env`'s
    # existing severity for a broken contract.
    broken = 0
    for status, topic, detail in assay_toolchain_findings(
            lanes, project_dir, cfg, central, cfg_path, central_path):
        print(f"run-gate: check-env: [{status}] {topic}: {detail}", flush=True)
        broken += status == "FAIL"
    return 2 if broken else 0


# ---------------------------------------------------------------------------
# Pointer↔lane linkage (RG-2): a consumer pointer is certified, not assumed
# ---------------------------------------------------------------------------

_CD_TARGET_RE = re.compile(r"\bcd\s+(\S+)")
# One invocation shape, two tool names (review fix): the canonical script
# form `run-gate.py` and — since RG-14 made it real — the installed console
# script `run-gate`. The bare name must not match inside run-gate.toml,
# run-gate-project, or hyphenated prose; absolute-pathed console invocations
# are deliberately not recognized (fail closed: uncertified, not waved).
_INVOCATION_RE = re.compile(
    r"(?:run-gate\.py|(?<![\w./-])run-gate(?![\w.-]))(?:\s+[^&;]*)?")
_BARE_TOOL_RE = re.compile(r"(?<![\w./-])run-gate(?![\w.-])")
_RESERVED_POINTER_VERBS = {"doctor", "validate-pointers", "history"}
_DISCOVERY_FLAGS = {"--list", "--help", "--check-env"}
# Fields that are prose BY NAME: a label describes an invocation, it doesn't
# run one ("label = \"proj: run-gate gate conjunction\"" — found live in
# cmru/cmru.toml). Certifying prose would manufacture defects out of English;
# skipping it cannot hide a real pointer, which lives in a command-bearing
# field (argv/commands/steps), never in a label.
_PROSE_KEYS = {"label", "title", "description", "comment", "note", "notes",
               "summary", "help", "doc", "readme"}


def _collect_pointers(node, where: str) -> list[tuple[str, str]]:
    """Every (location, text) pair in a parsed consumer document that invokes
    run-gate (either tool name). An argv-style LIST whose first element IS a
    run-gate invocation is one pointer (joined) — list-form consumers
    otherwise split the command across elements, so no single string contains
    both the tool and the lane."""
    found: list[tuple[str, str]] = []

    def visit(node, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, f"{where}.{key}" if where else str(key))
        elif isinstance(node, list):
            if node and isinstance(node[0], str) \
                    and os.path.basename(node[0]) in {"run-gate.py",
                                                      "run-gate"} \
                    and all(isinstance(v, str) for v in node):
                visit(" ".join(node), f"{where}[argv]")
            else:
                for i, v in enumerate(node):
                    visit(v, f"{where}[{i}]")
        elif isinstance(node, str):
            leaf = re.sub(r"\[\d+\]$", "", where.rsplit(".", 1)[-1])
            if leaf not in _PROSE_KEYS and ("run-gate.py" in node
                                            or _BARE_TOOL_RE.search(node)):
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
        carries_worktree = False
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--worktree":
                carries_worktree = True
                i += 2  # flag plus its value
                continue
            if tok.startswith("--worktree="):
                carries_worktree = True  # equals-form counts (review fix)
                i += 1
                continue
            if tok.startswith("-"):
                i += 1
                continue
            positional.append(tok)
            i += 1
        # Discovery/verb invocations name no lane BY DESIGN (review fix): a
        # `--list` discovery snippet or a reserved verb is legitimate
        # consumer surface, not a missing lane — and needs no --worktree
        # either (--list reads config beside the script, never a tree).
        if len(positional) == 1 and positional[0] in _RESERVED_POINTER_VERBS:
            continue
        if not positional and any(tok in _DISCOVERY_FLAGS for tok in tokens):
            continue
        if uses_worktree and not carries_worktree:
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


# ---------------------------------------------------------------------------
# In-environment probes (RG-25/RG-26): ask the judge, never re-parse assay.toml
# ---------------------------------------------------------------------------

# The ONLY language→toolchain facts run-gate states, and it states them
# reluctantly. Everything else about a lane is READ from `assay lanes --json`.
# This table exists because assay's own docs/CONSUMERS.md says the fact lives
# in prose and NOT in the inventory: "In this release, external_tools is ()
# for every shipped adapter … a gate consumer should not build a
# MISSING_EXTERNAL_TOOL preflight around this field expecting it to name
# node/npm for a javascript lane — that check today has to come from
# `language` itself". A language absent from this table produces a CAVEAT on
# the report line, never silence: claiming a toolchain was verified when the
# fact is unknown is the false-certification class AGENTS forbids.
ASSAY_LANGUAGE_TOOLCHAIN = {
    "javascript": ("node", "npm"),
    "go": ("go",),
}


def build_env_probe_argv(docker: str, env: dict, env_name: str, repo: Path,
                         worktree: Path, env_source: str, slice_name: str,
                         script: str) -> list[str]:
    """The ONE way run-gate runs a short, synchronous, read-only command
    INSIDE a lane's environment (RG-25's `assay lanes --json` inventory and
    its `command -v` fitness checks; RG-26's base_source query).

    It reuses the SAME reach-an-environment machinery the lane runners use —
    `resolve_container_name()` for exec mode, `physical_path()` +
    `dual_mount_flags()` for ephemeral — so there is exactly one place in the
    tool that knows how to get inside an environment. It deliberately is NOT
    the lane RUN form: a probe is attached and captured, where a judged lane
    is detached (`-d` → `wait` → `logs`) precisely so its status cannot be
    forged over a lying transport. That difference is safe here and only
    here, because a probe's result is never a verdict — it becomes an
    `[OK]`/`[FAIL]`/`[SKIP]` line in a preflight report, never a lane's
    pass/fail. Ephemeral probes still pass `--cgroup-parent`: a container
    THIS tool starts is placed on the host, never left at Docker's unconfined
    default next to production work (AGENTS "Host cgroup placement").
    """
    if not env:
        # The built-in 'host' environment IS this machine: there is no
        # container to enter, and the same script answers the same question.
        return ["bash", "-c", script]
    if env.get("mode") == "exec":
        name, _src, _remedy = resolve_container_name(env_name, env, repo,
                                                     worktree, env_source)
        return [docker, "exec", "--workdir", str(repo), name, "bash", "-c", script]
    return [docker, "run", "--rm", "--cgroup-parent", slice_name,
            *dual_mount_flags(repo, physical_path(repo)),
            env["image"], "bash", "-c", script]


def _probe_slice(env: dict, env_source: str) -> str:
    """Host and exec probes need no slice (this machine's placement is not
    ours to set; docker exec can neither place nor cap). Ephemeral probes
    resolve it through the SAME policy as a lane (R-10)."""
    if not env or env.get("mode") == "exec":
        return ""
    return resolve_slice(env, env_source)[0]


def assay_inventory(docker: str, lane: dict, env: dict, env_name: str,
                    repo: Path, worktree: Path, env_source: str,
                    project_dir: Path) -> tuple[dict | None, str | None]:
    """(inventory document, None) or (None, why it could not be obtained).

    RG-25: run-gate never parses `assay.toml` — it ASKS the judge, the same
    way it already asks `--version`. "Could not ask" must never collapse into
    "nothing is declared" (AGENTS "absence for emptiness"), so every failure
    path returns a reason a report can print verbatim.
    """
    probe = shlex.join([*lane["assay_command"], "lanes", "--json",
                        "--file", "assay.toml"])
    argv = build_env_probe_argv(
        docker, env, env_name, repo, worktree, env_source,
        _probe_slice(env, env_source),
        f"cd {shlex.quote(str(project_dir))} && {probe}")
    out = subprocess.run(argv, capture_output=True, text=True)
    if out.returncode != 0:
        tail = (out.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        return None, (f"`assay lanes --json` did not run in environment "
                      f"{env_name!r} (exit {out.returncode}: {tail}) — an assay "
                      f"older than 3.2.0 has no inventory (B044). The pin "
                      f"declares the version this lane needs; run-gate does "
                      f"not impose a floor it never declared")
    try:
        doc = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        return None, (f"`assay lanes --json` produced no usable JSON in "
                      f"environment {env_name!r}: {exc}")
    schema = doc.get("inventory_schema")
    if schema != 1:
        return None, (f"assay inventory_schema is {schema!r}, not 1 — this "
                      f"run-gate reads schema 1 only; upgrade run-gate rather "
                      f"than guessing at a document it does not understand")
    return doc, None


def assay_lane_toolchain(entry: dict) -> tuple[list[str], str | None]:
    """(tools that must be on PATH, caveat or None) for one inventory entry.

    READ from the inventory: `external_tools` and `argv0`. MAPPED from
    `language` only through `ASSAY_LANGUAGE_TOOLCHAIN` (see its comment for
    why that table has to exist at all). An unmapped language yields a
    caveat, so the line never claims more than it checked.
    """
    language = entry.get("language") or ""
    argv0 = entry.get("argv0")
    tools = list(dict.fromkeys([
        *ASSAY_LANGUAGE_TOOLCHAIN.get(language, ()),
        *(entry.get("external_tools") or []),
        *([argv0] if argv0 else []),
    ]))
    caveat = None
    if language and language not in ASSAY_LANGUAGE_TOOLCHAIN:
        caveat = (f"language {language!r} has no toolchain fact run-gate "
                  f"knows — only argv0/external_tools were verified")
    return tools, caveat


def probe_missing_tools(docker: str, tools: list[str], env: dict,
                        env_name: str, repo: Path, worktree: Path,
                        env_source: str) -> tuple[list[str] | None, str | None]:
    """(tools NOT on PATH inside the environment, None) or (None, why the
    probe itself failed). The script always exits 0 and reports absence on
    stdout, so a non-zero status means the PROBE broke — never 'everything is
    present'."""
    script = "; ".join(
        f"command -v {shlex.quote(tool)} >/dev/null 2>&1 || echo {shlex.quote(tool)}"
        for tool in tools)
    argv = build_env_probe_argv(docker, env, env_name, repo, worktree,
                                env_source, _probe_slice(env, env_source),
                                script)
    out = subprocess.run(argv, capture_output=True, text=True)
    if out.returncode != 0:
        tail = (out.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        return None, (f"could not run `command -v` in environment "
                      f"{env_name!r} (exit {out.returncode}: {tail})")
    return out.stdout.split(), None


def assay_toolchain_findings(lanes: dict, project_dir: Path, cfg: dict,
                             central: dict, cfg_path: Path,
                             central_path: Path | None
                             ) -> list[tuple[str, str, str]]:
    """RG-25: one (status, topic, detail) per `kind = "assay"` lane.

    Statuses are doctor's own vocabulary. FAIL is reserved for a fact the
    inventory actually established — a named tool absent from the
    environment, or an `assay_lane` the judge does not declare. Everything
    that means "I could not determine this" is SKIP with the reason, which is
    also why an assay older than B044 can never turn a healthy project red.
    """
    plan: list[dict] = []
    assay_lanes = {n: l for n, l in sorted(lanes.items()) if l["kind"] == "assay"}
    if not assay_lanes:
        return []
    docker = shutil.which("docker")
    inventories: dict[tuple, tuple[dict | None, str | None]] = {}
    probe_ctx: dict[str, tuple] = {}
    # Pass 1 — ask the JUDGE. One inventory probe per (environment,
    # assay_command): two lanes sharing an environment AND a pinned judge ask
    # once, two lanes with different judges ask once each (they are different
    # judges, and caching across them would answer with the wrong one).
    for name, lane in assay_lanes.items():
        topic = f"lane {name!r} toolchain"
        env_name = lane_environment_name(lane)
        try:
            env, env_source = resolve_environment(lane, name, cfg, central,
                                                  cfg_path, central_path)
            if not env:
                plan.append({"finding": (
                    "SKIP", topic, "environment is the built-in 'host' — its "
                    "PATH is this machine's, and the lane's own run reports "
                    "what is missing")})
                continue
            if not docker:
                plan.append({"finding": (
                    "SKIP", topic,
                    "docker not on PATH — the environment cannot be probed")})
                continue
            repo, worktree, _ = resolve_repo_and_worktree(project_dir, None)
            key = (env_name, tuple(lane["assay_command"]))
            if key not in inventories:
                inventories[key] = assay_inventory(
                    docker, lane, env, env_name, repo, worktree, env_source,
                    project_dir)
            doc, why = inventories[key]
            if doc is None:
                plan.append({"finding": ("SKIP", topic, why)})
                continue
            entry = next((e for e in doc.get("lanes", [])
                          if e.get("name") == lane["assay_lane"]), None)
            if entry is None:
                declared = ", ".join(sorted(e.get("name", "?")
                                            for e in doc.get("lanes", []))) or "(none)"
                plan.append({"finding": (
                    "FAIL", topic,
                    f"assay lane {lane['assay_lane']!r} is not declared in "
                    f"assay.toml (declared: {declared}) — this lane can only "
                    f"ERROR at run time")})
                continue
            tools, caveat = assay_lane_toolchain(entry)
            if not tools:
                plan.append({"finding": (
                    "OK", topic,
                    "assay declares no toolchain requirement for this lane")})
                continue
            probe_ctx[env_name] = (docker, env, repo, worktree, env_source)
            plan.append({"topic": topic, "env": env_name, "tools": tools,
                         "caveat": caveat, "source": env_source})
        except GateError as exc:
            plan.append({"finding": ("SKIP", topic, str(exc))})
    # Pass 2 — ONE `command -v` probe per ENVIRONMENT, over the UNION of every
    # lane's tools. Probing per lane cost a container per lane on a shared
    # environment (measured: 4 containers for 3 lanes on one environment) and
    # made R-30's own cost claim false; the answer is a property of the
    # environment's PATH, not of the lane asking.
    absent: dict[str, tuple[set[str] | None, str | None]] = {}
    for env_name, (docker_bin, env, repo, worktree, env_source) in probe_ctx.items():
        union = sorted({tool for step in plan
                        if step.get("env") == env_name for tool in step["tools"]})
        # No GateError guard here on purpose: an environment only reaches
        # probe_ctx after pass 1 already built a probe argv for it through the
        # same `_probe_slice`/`dual_mount_flags` path with the same inputs, so
        # a refusal would have been caught (and reported) there. A defensive
        # except would be a branch no test could ever redden.
        missing, why = probe_missing_tools(docker_bin, union, env, env_name,
                                           repo, worktree, env_source)
        absent[env_name] = (None if missing is None else set(missing), why)
    findings: list[tuple[str, str, str]] = []
    for step in plan:
        if "finding" in step:
            findings.append(step["finding"])
            continue
        gone, why = absent[step["env"]]
        if gone is None:
            findings.append(("SKIP", step["topic"], why))
            continue
        missing = [tool for tool in step["tools"] if tool in gone]
        if missing:
            findings.append(("FAIL", step["topic"],
                             f"needs {', '.join(missing)} in environment "
                             f"{step['env']!r} ({step['source']}) — assay would "
                             f"reach MISSING_EXTERNAL_TOOL/NO_MEASUREMENT "
                             f"mid-run instead"))
        else:
            caveat = step["caveat"]
            findings.append(("OK", step["topic"],
                             f"{', '.join(step['tools'])}"
                             f"{f' ({caveat})' if caveat else ''}"))
    return findings


BASE_TOKEN = "{base}"
ASSAY_INVENTORY_FLOOR = "3.2.0"  # the assay that first ships `lanes --json` (B044)


def derive_upstream_base(worktree: Path) -> str | None:
    """`git merge-base HEAD @{upstream}` in the judged tree, or None when the
    tree has no upstream. Deliberately NOT `git_out` — a missing upstream is
    an ordinary state this function must report, not an infrastructure
    failure it should abort on."""
    proc = subprocess.run(["git", "merge-base", "HEAD", "@{upstream}"],
                          cwd=str(worktree), capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def resolve_comparison_base(lane_name: str, base_flag: str | None,
                            worktree: Path) -> tuple[str, str]:
    """(ref, where it came from) for a lane that NEEDS a base. `--base` wins;
    otherwise the judged tree's own merge-base with its upstream. No
    fallback to HEAD or to a default branch name: a changed-line judgment
    whose base was guessed is not a changed-line judgment (assay's own rule,
    B019/A-328), so absence refuses."""
    if base_flag:
        return base_flag, "--base"
    ref = derive_upstream_base(worktree)
    if ref is None:
        fail(f"lane {lane_name!r} delegates its comparison base; pass "
             f"--base REF (worktree has no upstream)")
    return ref, "merge-base HEAD @{upstream}"


def assay_inventory_entry(lane: dict, env: dict, env_name: str, repo: Path,
                          worktree: Path, env_source: str, project_dir: Path
                          ) -> tuple[dict | None, str | None]:
    """One lane's entry from `assay lanes --json`, or (None, why not)."""
    docker = shutil.which("docker")
    if env and not docker:
        return None, "docker is not on PATH, so the environment cannot be asked"
    doc, why = assay_inventory(docker, lane, env, env_name, repo, worktree,
                               env_source, project_dir)
    if doc is None:
        return None, why
    entry = next((e for e in doc.get("lanes", [])
                  if e.get("name") == lane["assay_lane"]), None)
    if entry is None:
        return None, (f"assay lane {lane['assay_lane']!r} is not declared in "
                      f"assay.toml")
    return entry, None


def plan_comparison_base(lane: dict, lane_name: str, base_flag: str | None,
                         env: dict, env_name: str, repo: Path, worktree: Path,
                         env_source: str, project_dir: Path
                         ) -> tuple[str | None, str]:
    """RG-26: (ref to hand this lane, where it came from) or (None, "").

    assay 3.0.0 shipped `judge.base_source = "request"` (B019): a
    changed-line lane that omits `judge.base` and takes the comparison base
    from the gate. Such a lane invoked WITHOUT `--request-base` refuses by
    design, so the feature was unusable from run-gate at all.

    The delegation fact is **DERIVED** from `assay lanes --json` (RG-25's
    shared probe), never restated as a `run-gate.toml` key: `assay.toml`
    already owns it, and a second spelling of an owned fact is the drift
    machine this project exists to avoid.

    A conjunction (command) lane declares propagation the way RG-1 made it
    declare `--worktree`: a `{base}` token in its argv, substituted into
    every sub-invocation. Every refusal below is exit 2 and names the lane.
    """
    if lane["kind"] == "command":
        if any(BASE_TOKEN in element for element in lane["argv"]):
            return resolve_comparison_base(lane_name, base_flag, worktree)
        if base_flag:
            fail(f"--base {base_flag!r} was given but lane {lane_name!r} does "
                 f"not delegate a comparison base: it is a command lane whose "
                 f"argv carries no {BASE_TOKEN} token, so the ref could only "
                 f"be silently dropped. Write '--base {BASE_TOKEN}' into a "
                 f"conjunction lane's sub-invocations, or drop the flag")
        return None, ""
    entry, why = assay_inventory_entry(lane, env, env_name, repo, worktree,
                                       env_source, project_dir)
    if entry is None:
        if base_flag:
            fail(f"--base {base_flag!r} was given but run-gate cannot tell "
                 f"whether lane {lane_name!r} delegates its comparison base: "
                 f"{why}. The lane inventory arrived in assay "
                 f"{ASSAY_INVENTORY_FLOOR} (B044) — upgrade the pinned judge, "
                 f"or drop --base")
        # Without --base nothing changes: an older judge keeps working exactly
        # as it did, and assay refuses at run time if the lane needed one.
        return None, ""
    if entry.get("base_source") != "request":
        if base_flag:
            fail(f"--base {base_flag!r} was given but assay lane "
                 f"{lane['assay_lane']!r} (run-gate lane {lane_name!r}) does "
                 f"not delegate its comparison base: it declares base_source "
                 f"{entry.get('base_source')!r}, so assay would refuse "
                 f"--request-base. Set judge.base_source = \"request\" in "
                 f"assay.toml, or drop --base")
        return None, ""
    return resolve_comparison_base(lane_name, base_flag, worktree)


def linked_worktree_gitdir(worktree: Path) -> Path | None:
    """RG-21: the absolute gitdir a LINKED worktree's `.git` FILE points at,
    when that gitdir lies OUTSIDE the worktree. None otherwise.

    None covers both benign shapes deliberately — a plain checkout (`.git` is
    a directory) and a gitfile whose target is inside the tree — because the
    condition this feeds is a warning about git plumbing failing inside a
    container that mounted only the judged tree, and neither of those two can
    produce it. Folding "unreadable gitfile" into None is the one lossy case:
    a `.git` we cannot read is a bigger problem that the git calls
    surrounding this will report first, loudly.
    """
    gitfile = worktree / ".git"
    if not gitfile.is_file():
        return None
    try:
        text = gitfile.read_text()
    except OSError:  # pragma: no cover - is_file() just succeeded
        return None
    for line in text.splitlines():
        if not line.startswith("gitdir:"):
            continue
        gitdir = Path(line.split(":", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = (worktree / gitdir).resolve()
        return None if gitdir.is_relative_to(worktree) else gitdir
    return None


def cmd_doctor(lanes: dict, project_dir: Path, cfg: dict, central: dict,
               cfg_path: Path, central_path: Path | None) -> int:
    """RG-9: recompose the implemented preflights into one first-contact
    command, run BEFORE a newcomer's first lane does.

    Mostly recomposition — checks 1-4 read the world and change nothing. The
    exception, and it is deliberate (RG-25, `R-30`/`R-34`): check 5 STARTS
    CONTAINERS. Toolchain fitness cannot be read, only observed, so doctor
    asks the judge for its lane inventory and runs `command -v` inside the
    lane's own environment — at most one inventory probe per (environment,
    assay_command) plus one batched tool probe per environment, all read-only
    and short-lived. Nothing is judged and nothing in the tree is written.
    Say so out loud rather than letting "doctor runs nothing" quietly become
    false."""
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
        if env.get("mode") == "exec":
            # Review fix (R-30): exec lanes need NO slice — docker exec can
            # neither place nor cap work. Disclose what governs the runner,
            # demand nothing; the old unconditional resolve_slice here made
            # doctor report a bogus [FAIL] for a healthy exec project.
            declared = env.get("cgroup_slice")
            ambient = os.environ.get(CGROUP_ENV_VAR)
            if declared or ambient:
                src = (f"declared {env_source}, naming-only" if declared
                       else f"${CGROUP_ENV_VAR}, naming-only")
                record("OK", f"slice for env {env_name} (exec)",
                       f"{declared or ambient} ({src})")
            else:
                record("WARN", f"slice for env {env_name} (exec)",
                       "none derivable — fine: docker exec cannot place or "
                       "cap work; the runner is governed by how it was started")
            continue
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
        # RG-21: a LINKED worktree's `.git` is a FILE naming an absolute
        # gitdir under the MAIN checkout. run-gate's own container lanes are
        # unaffected — R-23 dual-mounts the REPO root, so that gitdir is
        # inside the mount. The breakage is one layer down: a HOST lane whose
        # argv delegates to a harness that bind-mounts only its own
        # $repo_root (= the worktree) by host path, where every in-container
        # git plumbing call then dies with `not a git repository: <gitdir>`
        # (srdm's covergate, the evidence case). Only host lanes can reach
        # that harness, so the check is scoped to projects that declare one —
        # a warning that fires where it cannot bite gets switched off.
        if "<host>" in env_cache:
            gitdir = linked_worktree_gitdir(worktree)
            if gitdir is None:
                record("OK", "host-lane git view (RG-21)",
                       f"{worktree} resolves git in-tree — a harness that "
                       f"bind-mounts only the judged tree still sees a "
                       f"complete .git")
            else:
                record("WARN", "host-lane git view (RG-21)",
                       f"{worktree} is a LINKED worktree; its gitdir is "
                       f"{gitdir}, OUTSIDE the tree. run-gate's own container "
                       f"lanes are fine (they dual-mount the repo root), but a "
                       f"host lane delegating to a harness that bind-mounts "
                       f"only the judged tree by host path will fail with "
                       f"'not a git repository: {gitdir}'. Mount the common "
                       f"gitdir into that container too, or pass it as "
                       f"GIT_DIR, or run the lane from the main checkout")
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

    # 5. assay-lane toolchain fitness (RG-25) — ask the judge what each lane
    # needs from its environment, then check the environment for it. Runs at
    # most one short read-only probe per environment; nothing is judged.
    for status, topic, detail in assay_toolchain_findings(
            lanes, project_dir, cfg, central, cfg_path, central_path):
        record(status, topic, detail)

    ok_n = sum(1 for s, *_ in results if s == "OK")
    warn_n = sum(1 for s, *_ in results if s == "WARN")
    fail_n = sum(1 for s, *_ in results if s == "FAIL")
    skip_n = sum(1 for s, *_ in results if s == "SKIP")
    print(f"run-gate: doctor: {len(results)} check(s): {ok_n} OK, "
          f"{warn_n} warning(s), {fail_n} failure(s), {skip_n} skipped "
          f"(could not determine)", flush=True)
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
    if text.startswith("-"):
        # Review fix: '-' IS in the charset for later positions; listing it
        # as an "offending character" would misdescribe a POSITION problem —
        # a leading dash gets parsed as an option, not a path.
        fail(f"worktree path {text!r} is not gate-safe: it starts with '-', "
             f"which consumer shells would parse as an option prefix, not a "
             f"path — relocate or rename the tree")
    bad = sorted({c for c in text if not re.fullmatch(r"[A-Za-z0-9_./-]", c)})
    fail(f"worktree path {text!r} is not gate-safe (offending character(s): "
         f"{' '.join(repr(c) for c in bad)}): consumer pointers embed "
         f"{{worktree}} into shell strings, so paths with whitespace or shell "
         f"metacharacters are refused — relocate or rename the tree")


def build_assay_inner(lane: dict, project_dir: Path,
                      request_base: str | None = None) -> str:
    verdict = f".assay/verdict-{lane['assay_lane']}.json"
    parts = ["set -euo pipefail",
             "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig",
             shlex.join(["git", "config", "--global", "--replace-all",
                        "safe.directory", "*"]),
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
            # Review fix: WHOLE-TOKEN equality — edge punctuation stripped,
            # one decorative leading 'v' tolerated ('v2.1.0' == '2.1.0').
            # The old substring glob let declared '2.1' pass for reported
            # '2.11.0', a claim the artifact never made.
            declared = shlex.quote(pin["version"])
            probe = shlex.join([*lane["assay_command"], "--version"])
            parts.append(
                f"{{ reported=$({probe}) || "
                f"{{ echo \"run-gate: pin '{pin_name}': version probe failed: {probe}\" "
                f">&2; exit 2; }}; "
                f"hit=0; for tok in $reported; do "
                f"tok=${{tok#\"${{tok%%[![:punct:]]*}}\"}}; "
                f"tok=${{tok%\"${{tok##*[![:punct:]]}}\"}}; "
                f"case \"$tok\" in v[0-9]*) tok=${{tok#v}} ;; esac; "
                f"if [ \"$tok\" = {declared} ]; then hit=1; fi; done; "
                f"if [ \"$hit\" != 1 ]; then "
                f"echo \"run-gate: pin '{pin_name}' version mismatch: declared "
                f"{declared}, artifact reports: $reported — fix pins.{pin_name}.version "
                f"or republish the artifact\" >&2; exit 2; fi; }}")
    parts.append("mkdir -p .assay")
    run_argv = [*lane["assay_command"], "run", lane["assay_lane"],
                "--file", "assay.toml", "--verdict-json", verdict]
    if request_base:
        # RG-26: only ever appended for a lane the INVENTORY says delegates
        # its base (base_source == "request"); assay refuses it on any other.
        run_argv += ["--request-base", request_base]
    parts.append(shlex.join(run_argv))
    return " && ".join(parts)


def build_command_inner(lane: dict, worktree: Path,
                        base: str | None = None) -> str:
    return " && ".join(["set -euo pipefail",
                        "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig",
                        shlex.join(["git", "config", "--global", "--replace-all",
                                    "safe.directory", "*"]),
                        shlex.join(substitute_worktree(lane["argv"], worktree,
                                                       base))])


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
                       dry_run: bool = False,
                       request_base: str | None = None) -> int:
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
    inner = build_assay_inner(lane, project_dir, request_base) \
        if lane["kind"] == "assay" \
        else build_command_inner(lane, worktree, request_base)
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
    saved_log: Path | None = None
    try:
        logs = subprocess.run([docker, "logs", "-f", name])
        waited = subprocess.run([docker, "wait", name], capture_output=True, text=True)
        out = waited.stdout.strip()
        code = int(out) if waited.returncode == 0 and re.fullmatch(r"-?\d+", out) else None
        # Review fix (R-26): evidence is for FAILING containers — a green
        # lane leaves nothing in the evidence dir. Captured here, BEFORE the
        # finally removes the container (an unreadable exit status counts as
        # failing: infra diagnosis needs the logs too).
        if code is None or code != 0:
            saved_log = save_container_logs(docker, name)
    finally:
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
                           worktree: Path, env_source: str) -> tuple[str, str, str]:
    """Resolve the persistent container name for an exec-mode environment.

    Returns (name, human-readable source, START REMEDY). The remedy names the
    authority that actually owns the resolved name — a declared container_name
    points at the project's own deployment authority, a ciu-derived name at
    the ciu lifecycle (RG-6: a dstdns-shaped project must never be told to
    run a vbpub-specific ciu directory).

    RG-24 — WHICH `ciu.global.toml`: a live deployed container's name is a
    fact about the JUDGED TREE, not about the shared object store. `repo`
    (`resolve_repo_and_worktree`) is deliberately the checkout owning the
    shared `.git`, i.e. the MAIN checkout for any linked worktree — right for
    source-code/object-store questions, WRONG here: a multi-instance
    (dstdns "Mode-B") worktree gets its OWN rendered `ciu.global.toml` with
    its own `project_name`/`environment_tag` and its OWN deployed runner on
    its own network, and resolving from the main checkout silently execs the
    lane into the MAIN landscape's container (network attachment and baked
    env wrong; the inner `cd {worktree}` still finds the right FILES, which
    is why the failure is partial and believable). So: the judged worktree's
    own config WINS when it exists; a worktree that is not itself an adopted
    instance falls back to the repo-relative resolution unchanged (additive
    precedence, not a replacement). `repo`-relative resolution stays correct
    everywhere else it is used — those questions really are about the tree
    that owns the object store.
    """
    if env.get("container_name"):
        return env["container_name"], f"declared container_name ({env_source})", \
            "start it via YOUR project's deployment authority (whoever owns " \
            "this container); run-gate refuses to guess or auto-start " \
            "deployment-managed containers"
    worktree_toml = worktree / "ciu.global.toml"
    repo_toml = repo / "ciu.global.toml"
    if worktree_toml.is_file():
        global_toml = worktree_toml
    elif repo_toml.is_file():
        global_toml = repo_toml
    else:
        tried = (f"{worktree_toml}" if worktree_toml == repo_toml
                 else f"{worktree_toml} (judged worktree) nor {repo_toml} (repo)")
        fail(f"exec-mode environment '{env_name}' needs either a declared "
             f"container_name or a rendered {tried} with [deploy] "
             f"(run 'ciu render' first)")
    try:
        with open(global_toml, "rb") as fh:
            deploy = tomllib.load(fh).get("deploy", {})
    except tomllib.TOMLDecodeError as exc:
        fail(f"{global_toml}: invalid TOML: {exc}")
    ciu_remedy = (f"start it via this project's ciu lifecycle ('ciu render' "
                  f"if stale, then 'ciu up'; config: {global_toml}); run-gate "
                  f"refuses to guess or auto-start deployment-managed containers")
    # RG-24: name the SCOPE the config was read from, not only its path — the
    # whole defect was that "which ciu.global.toml" was invisible.
    scope = "judged worktree" if global_toml == worktree_toml else "repo"
    project = deploy.get("project_name") or ""
    tag = deploy.get("environment_tag") or ""
    if project and tag:
        return f"{project}-{tag}-{env_name}", \
            f"ciu.global.toml deploy.project_name+environment_tag " \
            f"({scope}: {global_toml})", \
            ciu_remedy
    network = deploy.get("network_name") or ""
    if network and network.endswith("-network"):
        prefix = network[:-len("-network")]
        return f"{prefix}-{env_name}", \
            f"ciu.global.toml deploy.network_name stripped of '-network' " \
            f"({scope}: {global_toml})", \
            ciu_remedy
    fail(f"cannot derive container name from {global_toml}: need "
         f"[deploy] project_name+environment_tag OR network_name ending '-network'; "
         f"or declare container_name on the environment")


def run_exec_lane(lane: dict, lane_name: str, project_dir: Path, repo: Path,
                  worktree: Path, env: dict, env_source: str, env_name: str,
                  slice_name: str | None, slice_src: str,
                  dry_run: bool = False,
                  request_base: str | None = None) -> int:
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
        env_name, env, repo, worktree, env_source)
    running = subprocess.run([docker, "ps", "--format", "{{.Names}}"],
                             capture_output=True, text=True)
    if running.returncode != 0:
        detail = running.stderr.strip().splitlines()[-1:] or [f"exit {running.returncode}"]
        fail_infra(f"docker ps failed for exec-mode preflight: {detail[0]}")
    names = set(running.stdout.strip().splitlines())
    if name not in names:
        fail(f"persistent runner '{name}' ({name_src}) is not running — "
             f"{start_remedy}")
    inner = build_assay_inner(lane, project_dir, request_base) \
        if lane["kind"] == "assay" \
        else build_command_inner(lane, worktree, request_base)
    argv = [docker, "exec", "--workdir", str(repo)]
    # Infrastructure variables are implicit; project data inputs must be
    # declared on the environment so every consumer gets the same contract.
    for key in (CGROUP_ENV_VAR, *env.get("forward_env", [])):
        value = os.environ.get(key)
        if value:
            argv += ["-e", f"{key}={value}"]
    argv += [name, "bash", "-c", inner]
    print(f"run-gate: rev {__revision__} | lane {lane_name} | env {env_source} | "
          f"container {name} ({name_src}) | "
          f"slice {slice_name or '(none)'} ({slice_src})",
          flush=True)
    log_forwarded_env(env, "exec")  # names only, never values (RG-19)
    if lane.get("budget"):
        print(f"run-gate: budget {lane['budget']} (advisory)", flush=True)
    # Review fix (R-05/R-28): exec lanes disclose the assembled plan exactly
    # like ephemeral lanes do — live AND dry, forwarded VALUES redacted
    # (RG-19). The slice name itself stays visible: it is mechanics, not a
    # credential.
    print(f"run-gate: docker argv: "
          f"{shlex.join(redact_forwarded_values(argv, env.get('forward_env', [])))}",
          flush=True)
    if dry_run:
        # RG-8: name resolution + running-check above are rehearsed too —
        # a dry-run against a stopped runner reports the real refusal.
        print(f"run-gate: DRY RUN — the argv above is what a live run would "
              f"exec into {name} ({name_src}); no command was run", flush=True)
        return 0
    code = subprocess.run(argv).returncode
    print_lane_artifacts(lane, lane_name, project_dir, worktree)
    return code


def run_host_lane(lane: dict, lane_name: str, project_dir: Path, worktree: Path,
                  dry_run: bool = False,
                  request_base: str | None = None) -> int:
    # cwd is the project dir RELOCATED into the judged worktree (RG-15) — a
    # host lane must not quietly operate on the invocation checkout either.
    # RG-28 (found while implementing RG-26): `kind = "assay"` with
    # `environment = "host"` is a config the validator ACCEPTS, and this
    # runner used to index lane["argv"] unconditionally — a KeyError
    # traceback for a legal config, which R-04 calls a defect. The assay
    # inner is built exactly as it is for the two container runners.
    argv = (["bash", "-c", build_assay_inner(lane, project_dir, request_base)]
            if lane["kind"] == "assay"
            else substitute_worktree(lane["argv"], worktree, request_base))
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
        "usage: run-gate.py <lane> [--worktree PATH] [--allow-dirty] [--base REF]",
        "       run-gate.py --list   (machine-readable: name<TAB>kind<TAB>environment)",
        "       run-gate.py validate-pointers CONSUMER.toml [--root DIR]",
        "         (RG-2: certify every run-gate pointer in a consumer document —",
        "          trove gates, release steps — against the SSOT lanes they name)",
        "       run-gate.py doctor   (RG-9 preflight: docker, slices, mountinfo, git,",
        "          images, the linked-worktree host-lane git view (RG-21), and each",
        "          assay lane's toolchain fitness asked of the judge itself (RG-25).",
        "          Judges nothing and writes nothing, but DOES start short read-only",
        "          probe containers for that last check — fitness can only be",
        "          observed, not read: one inventory probe per environment+judge,",
        "          plus one batched `command -v` probe per environment)",
        "       run-gate.py history [LANE] [--worktree PATH] [--json]",
        "         (RG-27: what each lane most recently did — ANY outcome, dirty or",
        "          aborted runs included — and its bounded per-commit duration",
        "          series. Reads the store, runs no lane, decides no policy.",
        "          --worktree redirects the READ exactly as it redirects a run:",
        "          the answer describes THAT tree\'s store, and says so)",
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
        "  --base REF        comparison base for a lane that DELEGATES it —",
        "                    an assay lane whose inventory reports base_source",
        "                    = \"request\" (appended as --request-base), or a",
        "                    conjunction lane carrying a {base} token. Omitted:",
        "                    the judged tree's merge-base with its upstream; no",
        "                    upstream refuses (a guessed base is not a base). A",
        "                    lane that does NOT delegate refuses --base by name",
        "  --dry-run         print the full execution plan (docker argv, mounts,",
        "                    slice, inner command) and exit 0 — every preflight",
        "                    is rehearsed and NO JUDGED lane is started. An",
        "                    assay lane's read-only `assay lanes --json`",
        "                    inventory probe IS a preflight, so it does run,",
        "                    in a short container of its own (RG-26)",
        "  --check-env       advisory drift sweep: env references in the project's",
        "                    Python sources covered by neither forward_env nor a",
        "                    lane's required_env (heuristic — warns, never refuses),",
        "                    PLUS the assay-lane toolchain fitness check, whose",
        "                    FAIL exits 2 (that half is the judge's own finding,",
        "                    not a heuristic)",
        "  --json            `history` ONLY: the same data as one JSON document",
        "                    (latest + bounded history + median/min/max, split",
        "                    passes vs all completed runs). Every other verb",
        "                    REFUSES it by name rather than silently printing",
        "                    its human form (--list is already a machine table)",
        "",
        "lane history (RG-27) — measured and persisted, never acted on here:",
        f"  store       <project>/{HISTORY_DIR_NAME}/{HISTORY_FILE_NAME} in the JUDGED",
        "              worktree — per (worktree x project), so two worktrees' gates",
        "              never contend; MUST be git-ignored (the tool refuses to write",
        "              and says so rather than dirtying the tree for the next lane)",
        "  read scope  the same one: `history --worktree B` reads B's store, never",
        "              the invoking checkout's, and names the tree in its output",
        f"  retention   [history] keep = <int>, default {HISTORY_KEEP_DEFAULT} commits per lane;",
        "              project [history] shadows the central one (R-09's rule)",
        "  eligibility  history keeps COMPLETED runs (passes AND fails) on a clean,",
        "              committed tree; aborted, errored, dirty-tree and mid-rebase",
        "              runs update `latest` only, with the exclusion reason recorded",
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
    parser.add_argument("--base", help="RG-26: the comparison base for a lane "
                        "that delegates it (assay judge.base_source = "
                        "\"request\"), and for conjunction lanes carrying a "
                        "{base} token")
    parser.add_argument("--json", action="store_true",
                        help="RG-27: `history` emits one machine-readable "
                             "JSON document instead of the human table")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the full execution plan and exit 0 — every "
                             "preflight is rehearsed and no JUDGED lane is "
                             "started; an assay lane's read-only `assay lanes "
                             "--json` inventory probe is a preflight and does "
                             "run, in a short container of its own")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args(argv)

    record = None  # RG-27: set once the lane resolves; see flush_run_record
    try:
        # Review fix (S1): `--json` is honored by `history` alone, so every
        # other invocation REFUSES it by name instead of accepting it and
        # emitting the plain table anyway. Same rule as RG-1's --worktree and
        # RG-26's --base: a flag the command cannot honor is a refusal, never
        # a silent no-op — a consumer piping `--list --json` into a parser
        # would otherwise get a TSV where it asked for JSON.
        if args.json and args.lane != "history":
            fail("--json is honored by the `history` verb only (run-gate.py "
                 "history [LANE] --json); `--list` is already a machine "
                 "table (name<TAB>kind<TAB>environment) and every other verb "
                 "prints human text")
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
        if args.lane == "history":
            # RG-27 query verb — reads the store, runs nothing, decides
            # nothing. Rigor/defer POLICY belongs to the controller reading
            # this, never to the tool producing it.
            #
            # Review fix (B1): the READ scope follows `--worktree` exactly as
            # the WRITE scope does (R-36f). The store is per (judged worktree
            # × project), so reading the invoking checkout's store while the
            # caller asked about tree B would answer with A's medians under
            # B's name — silently, which is the one thing this feature exists
            # to make impossible. Resolution happens ONLY when the flag is
            # given: an unflagged `history` stays git-free and keeps
            # answering in a checkout where git cannot.
            hist_dir, hist_scope = project_dir, None
            if args.worktree:
                # …and B1's ERROR path closes the same way. On the run path
                # an unresolvable --worktree dies downstream (git status in a
                # tree that is not there); a READ has no downstream, so an
                # unvalidated override would compute a store path under a
                # nonexistent tree and answer "(not written yet)" — the
                # invoking checkout's silence presented as tree B's answer.
                # resolve_repo_and_worktree() takes the override verbatim by
                # design (R-02), so the check belongs here.
                if not Path(args.worktree).is_dir():
                    fail(f"--worktree {args.worktree!r}: not a directory — "
                         f"`history` reports THAT tree's store, so it must "
                         f"name a real worktree")
                git_out("rev-parse", "--show-toplevel",
                        cwd=Path(args.worktree))  # refuses with git's own line
                _, hist_wt, hist_top = resolve_repo_and_worktree(
                    project_dir, args.worktree)
                hist_dir = effective_project_dir(project_dir, hist_top,
                                                 hist_wt)
                hist_scope = str(hist_wt)
            return cmd_history(lanes, hist_dir, cfg, cfg_path, central,
                               central_path, args.target, args.json,
                               hist_scope)
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
        # RG-26: resolve the comparison base BEFORE any admission or lock —
        # a refusal here is a configuration error, and making a caller wait
        # on a shared-infra lock to receive one is the fast-fail mistake
        # RG-20's review already corrected once.
        request_base, base_src = plan_comparison_base(
            lane, args.lane, args.base, env, lane_environment_name(lane),
            repo, worktree, env_source, eff_proj)
        if request_base:
            # R-05: mechanics are visible before execution, live AND dry.
            target = ("--request-base" if lane["kind"] == "assay"
                      else f"{BASE_TOKEN} in the lane argv")
            print(f"run-gate: comparison base {request_base} (from {base_src}) "
                  f"→ {target}", flush=True)
        # RG-27: from HERE on this is an INVOCATION with a result — a
        # clean-tree refusal, an admission refusal, a docker failure and a
        # Ctrl-C all count, and all land in `latest`. Everything BEFORE this
        # line is a configuration error that names no invocation to record
        # against. `--dry-run` records nothing at all: no lane started, so
        # nothing was measured and there is no result to be `latest`.
        if not args.dry_run:
            record = start_run_record(args.lane, worktree, repo)
            record["_project_dir"] = eff_proj
            record["_keep"] = resolve_history_keep(cfg, cfg_path, central,
                                                   central_path)[0]
        if lane.get("clean_tree", True) and not args.allow_dirty:
            check_clean_tree(worktree)
        # RG-20 resource-aware admission: slice-memory accounting FIRST
        # (fast-fail — review fix: a gate blocked on a held lock used to
        # wait before receiving an admission refusal it could have been
        # given instantly), THEN shared-infra serialization — the only
        # potentially-blocking step, in sorted-name order, released in
        # finally.
        slice_name = slice_src = None
        if env and env.get("mode") == "exec":
            # Review fix (R-05): exec lanes DISCLOSE their slice but never
            # memory-admit or cap: the persistent runner predates this
            # invocation (its placement was decided when CIU started it) and
            # `docker exec` can neither place nor cap work in a slice.
            if env.get("cgroup_slice"):
                print(f"run-gate: WARNING: cgroup_slice on exec environment "
                      f"{env_source} is naming-only ({env['cgroup_slice']!r}) "
                      f"— docker exec cannot enforce slice placement or caps; "
                      f"the runner is governed by how it was started",
                      flush=True)
                slice_name = env["cgroup_slice"]
                slice_src = f"declared {env_source}, naming-only"
            else:
                ambient = os.environ.get(CGROUP_ENV_VAR)
                if ambient:
                    slice_name = ambient
                    slice_src = f"${CGROUP_ENV_VAR}, naming-only"
                else:
                    slice_src = (f"no cgroup_slice declared and no "
                                 f"${CGROUP_ENV_VAR}")
            if lane.get("resources") or lane.get("memory"):
                print(f"run-gate: WARNING: lane {args.lane!r} declares "
                      f"resources/memory but its environment is exec-mode — "
                      f"resource admission and --memory caps apply to "
                      f"ephemeral container lanes only", flush=True)
        elif env:
            slice_name, slice_src = resolve_slice(env, env_source)
            check_slice_memory_admission(lane, args.lane, slice_name,
                                         slice_src)
        locks = acquire_shared_locks(lane, args.lane, args.dry_run)
        try:
            if not env:  # built-in 'host'
                code = run_host_lane(lane, args.lane, eff_proj, worktree,
                                     dry_run=args.dry_run,
                                     request_base=request_base)
            elif env.get("mode") == "exec":
                code = run_exec_lane(lane, args.lane, eff_proj, repo, worktree,
                                     env, env_source, lane_environment_name(lane),
                                     slice_name, slice_src,
                                     dry_run=args.dry_run,
                                     request_base=request_base)
            else:
                code = run_container_lane(lane, args.lane, eff_proj, repo,
                                          worktree, env, env_source,
                                          slice_name, slice_src,
                                          dry_run=args.dry_run,
                                          request_base=request_base)
            print(f"run-gate: lane {args.lane!r} exit {code}", flush=True)
        finally:
            for fd in locks:
                os.close(fd)  # releases the flock
        # RG-27: outside the shared-infra lock — telemetry never extends a
        # hold another gate is waiting on.
        flush_run_record(record, exit_code=code)
        return code
    except GateError as exc:
        flush_run_record(record, error=exc)
        print(f"{PROG}: {exc}", file=sys.stderr)
        return exc.exit_code
    except BaseException as exc:
        # Ctrl-C and friends (RG-27): `latest` records the abort, and the
        # exception continues on its way completely untouched. This also
        # catches SystemExit, deliberately harmless: flush_run_record is
        # at-most-once and re-raises nothing, so a `sys.exit()` raised from
        # anywhere under here still exits with its own code, having recorded
        # that the invocation ended.
        flush_run_record(record, error=exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
