#!/usr/bin/env python3
"""mdt devcontainer host bootstrap ("get.py").

Runs ON THE HOST (wired via devcontainer.json `initializeCommand`) BEFORE the
container is created. It first verifies the declared host cgroup slices are
loaded from installed unit files, then prepares every `$HOME` bind-mount source
with sane permissions. Docker's `--mount` form refuses a missing source; this
script applies MDT's configured policy before Docker starts.

Layout (grouped persistence):
- Devcontainer-persisted state is grouped under `~/mdt--mounted-folders/` so a rebuild never
  wipes it and one `ls -la ~/mdt--mounted-folders/` shows the whole set. These are REAL dirs
  (NOT symlinks): `.claude .claudelink .codex .codex2 .config .gnupg .local .minisign .openclaw`
  `.pi .reasonix .ssh` plus `opencode-data` and `tmp`.
- `tmp` is the host-backed persisted `/tmp`: a REAL dir at mode 1777, so `/tmp` worktrees survive
  rebuilds and are visible to the sibling test-runner container (which bind-mounts the same host path).
- EXCEPTION: the host's NATIVE `~/.ssh` is also bind-mounted (readonly) at `/home/vscode/.ssh-host`,
  so the same host keys work both natively and inside the devcontainer (dual-use). That source is the
  host `~/.ssh`, not the grouped copy.

Design: stdlib-only and idempotent. Reads the sibling `devcontainer.json` and inspects each `type=bind`
source under the host `$HOME`. Existing regular files and directories are handled according to their
actual type, regardless of their names or suffixes. For a missing source, the host config policy
defaults to `create-by-spelling`: a source ending in `/` creates a directory; a source without `/`
creates an empty regular file. Set `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` in the host config
to require every `$HOME` source managed here to exist already. Secret dirs
(`.ssh`/`.gnupg`/`.minisign`) get 0700; `tmp` gets 1777; everything else 0755.

NOTE on data migration: this script only ENSURES the source paths EXIST — it does NOT copy your
existing `~/.claude`, `~/.gnupg`, `~/.minisign`, `~/.codex`, `~/.codex2`, `~/.reasonix`, `~/.openclaw`,
`~/.pi`, `~/.claudelink`, `~/.config`, or `~/.local/share/opencode` state into the grouped
parent. If you want that state to carry over, migrate it ONCE on the host before the first
rebuild, e.g.:  for d in .claude .claudelink .codex .config .gnupg .local .minisign .openclaw .pi .reasonix; do cp -a ~/$d/. ~/mdt--mounted-folders/$d/; done
(and copy `~/.local/share/opencode/.` to `~/mdt--mounted-folders/opencode-data/`)
(the grouped `.ssh` is independent of the readonly native `.ssh-host` mount).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Secret dirs whose tools require 0700 (ssh/gpg/minisign reject loose perms).
MODE_OVERRIDES = {".ssh": 0o700, ".gnupg": 0o700, ".minisign": 0o700}
DEFAULT_MODE = 0o755
TMP_MODE = 0o1777  # persisted host-backed /tmp: sticky + world-writable, like a normal /tmp

# Name of the grouped-persistence parent under $HOME.
PARENT_NAME = "mdt--mounted-folders"

# Canonical set under the parent — used only if devcontainer.json can't be read.
FALLBACK = [".claude", ".claudelink", ".codex", ".codex2", ".config", ".gnupg", ".local", ".minisign", ".openclaw", ".pi", ".reasonix", ".ssh", "opencode-data"]
# Standard file-level state mounts.
FALLBACK_FILES = [".claude.json", ".reasonix.toml"]
KNOWN_FILE_SOURCE_BASENAMES = set(FALLBACK_FILES) | {".gitconfig"}
KNOWN_DIRECTORY_SOURCE_NAMES = set(FALLBACK) | {"tmp"}

MISSING_SOURCE_POLICY_KEY = "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY"
DEFAULT_MISSING_SOURCE_POLICY = "create-by-spelling"
MISSING_SOURCE_POLICIES = {DEFAULT_MISSING_SOURCE_POLICY, "fail"}
HOST_CONFIG_PATH = Path("/etc/mdt/host-setup.env")
CGROUP_ENV_KEYS = (
    "CGROUP_PARENT_DEV_INTERACTIVE",
    "CGROUP_PARENT_DEV_BACKGROUND",
    "CGROUP_PARENT_DEV_GATES",
)

# Docker's `run` options. `runArgs` is passed as argv, so a string that looks
# like `--cgroup-parent` can be consumed as the value of an earlier option
# (for example `--label --cgroup-parent=...`). Keep the parser fail-closed for
# options it cannot classify rather than certifying a decoy token.
DOCKER_RUN_VALUE_OPTIONS = frozenset(
    {
        "--add-host", "--annotation", "--attach", "--blkio-weight",
        "--blkio-weight-device", "--cap-add", "--cap-drop", "--cgroup-parent",
        "--cgroupns", "--cidfile", "--cpu-period", "--cpu-quota",
        "--cpu-rt-period", "--cpu-rt-runtime", "--cpu-shares", "--cpus",
        "--cpuset-cpus", "--cpuset-mems", "--detach-keys", "--device",
        "--device-cgroup-rule", "--device-read-bps", "--device-read-iops",
        "--device-write-bps", "--device-write-iops", "--dns", "--dns-option",
        "--dns-search", "--domainname", "--entrypoint", "--env", "--env-file",
        "--expose", "--gpus", "--group-add", "--health-cmd",
        "--health-interval", "--health-retries", "--health-start-interval",
        "--health-start-period", "--health-timeout", "--hostname", "--ip",
        "--ip6", "--ipc", "--isolation", "--label", "--label-file", "--link",
        "--link-local-ip", "--log-driver", "--log-opt", "--mac-address",
        "--memory", "--memory-reservation", "--memory-swap",
        "--memory-swappiness", "--mount", "--name", "--network",
        "--network-alias", "--oom-score-adj", "--pid", "--pids-limit",
        "--platform", "--publish", "--pull", "--restart", "--runtime",
        "--security-opt", "--shm-size", "--stop-signal", "--stop-timeout",
        "--storage-opt", "--sysctl", "--tmpfs", "--ulimit", "--umask",
        "--user", "--userns", "--uts", "--volume", "--volume-driver",
        "--volumes-from", "--workdir",
    }
)
DOCKER_RUN_VALUE_SHORT_OPTIONS = frozenset({"a", "c", "e", "h", "l", "m", "p", "u", "v", "w"})
DOCKER_RUN_BOOLEAN_OPTIONS = frozenset(
    {
        "--detach", "--help", "--init", "--interactive", "--no-healthcheck",
        "--oom-kill-disable", "--privileged", "--publish-all", "--quiet",
        "--read-only", "--rm", "--sig-proxy", "--tty", "--use-api-socket",
    }
)
DOCKER_RUN_BOOLEAN_SHORT_OPTIONS = frozenset({"d", "i", "P", "q", "t"})

HOME = Path(os.path.expanduser("~"))
# Matches the devcontainer mount string: "source=...,target=...,type=bind[,...]"
BIND_RE = re.compile(r'source=([^,"]+),target=[^,"]+,type=bind')


def host_bind_sources(dc_path: Path) -> list:
    """Return bind-mount source strings from devcontainer.json (skipping // comment lines)."""
    sources = []
    try:
        for line in dc_path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("//"):
                continue  # commented-out mount
            sources.extend(BIND_RE.findall(line))
    except OSError:
        return []
    return sources


def host_cgroup_slices(dc_path: Path) -> tuple[str, ...]:
    """Derive required host tiers from the vendored devcontainer declaration."""
    try:
        text = dc_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read devcontainer template {dc_path}: {exc}") from exc

    try:
        document = _parse_jsonc_object(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not parse devcontainer template {dc_path}: {exc}") from exc

    environment = document.get("containerEnv")
    run_args = document.get("runArgs")
    if not isinstance(environment, dict):
        raise ValueError(f"{dc_path} must declare containerEnv as an object")
    if not isinstance(run_args, list) or any(not isinstance(arg, str) for arg in run_args):
        raise ValueError(f"{dc_path} must declare runArgs as an array of strings")
    environment = {
        key: value for key, value in environment.items() if key in CGROUP_ENV_KEYS
    }
    if any(not isinstance(value, str) for value in environment.values()):
        raise ValueError(f"{dc_path} cgroup environment values must be strings")

    run_parents = _docker_run_cgroup_parents(run_args, dc_path)

    if set(environment) != set(CGROUP_ENV_KEYS):
        raise ValueError(
            f"{dc_path} must declare exactly {', '.join(CGROUP_ENV_KEYS)} in containerEnv"
        )
    if len(run_parents) != 1:
        raise ValueError(f"{dc_path} must declare exactly one --cgroup-parent runArg")
    if run_parents[0] != environment["CGROUP_PARENT_DEV_INTERACTIVE"]:
        raise ValueError(
            f"{dc_path} --cgroup-parent runArg does not match "
            "CGROUP_PARENT_DEV_INTERACTIVE"
        )

    slices = tuple(environment[key] for key in CGROUP_ENV_KEYS)
    if len(set(slices)) != len(slices) or any(
        re.fullmatch(r"[A-Za-z0-9_.@-]+\.slice", item) is None for item in slices
    ):
        raise ValueError(f"{dc_path} has invalid or duplicate dev-tier slice names")
    return slices


def _docker_run_cgroup_parents(run_args: list[str], dc_path: Path) -> list[str]:
    """Read effective cgroup-parent values from Docker's run option argv."""
    parents: list[str] = []
    index = 0
    while index < len(run_args):
        argument = run_args[index]
        index += 1
        if argument == "--" or not argument.startswith("-") or argument == "-":
            raise ValueError(f"{dc_path} has an unrecognized positional runArg {argument!r}")

        if argument.startswith("--"):
            option, separator, inline_value = argument.partition("=")
            if option in DOCKER_RUN_VALUE_OPTIONS:
                if separator:
                    value = inline_value
                else:
                    if index >= len(run_args):
                        raise ValueError(f"{dc_path} has an incomplete {option} runArg")
                    value = run_args[index]
                    index += 1
                if not value:
                    raise ValueError(f"{dc_path} has an empty {option} runArg")
                if option == "--cgroup-parent":
                    parents.append(value)
                continue
            if option in DOCKER_RUN_BOOLEAN_OPTIONS:
                if separator and inline_value.lower() not in {"1", "0", "true", "false"}:
                    raise ValueError(f"{dc_path} has an invalid boolean {option} runArg")
                continue
            raise ValueError(f"{dc_path} has an unsupported Docker runArg {argument!r}")

        # Docker's short options may be bundled (for example `-it`) or may
        # carry a value directly after the option (`-lkey=value`).
        short_options = argument[1:]
        short_index = 0
        while short_index < len(short_options):
            option = short_options[short_index]
            short_index += 1
            if option in DOCKER_RUN_VALUE_SHORT_OPTIONS:
                value = short_options[short_index:]
                if value.startswith("="):
                    value = value[1:]
                if not value:
                    if index >= len(run_args):
                        raise ValueError(f"{dc_path} has an incomplete -{option} runArg")
                    value = run_args[index]
                    index += 1
                if not value:
                    raise ValueError(f"{dc_path} has an empty -{option} runArg")
                break
            if option not in DOCKER_RUN_BOOLEAN_SHORT_OPTIONS:
                raise ValueError(f"{dc_path} has an unsupported Docker runArg {argument!r}")
    return parents


def _parse_jsonc_object(text: str) -> dict:
    """Parse JSONC while rejecting duplicate keys and preserving string contents."""
    uncommented = _remove_jsonc_comments(text)
    cleaned = _remove_jsonc_trailing_commas(uncommented)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSONC object key {key!r}")
            result[key] = value
        return result

    document = json.loads(cleaned, object_pairs_hook=unique_object)
    if not isinstance(document, dict):
        raise ValueError("devcontainer root must be an object")
    return document


def _remove_jsonc_comments(text: str) -> str:
    """Replace JSONC comments with whitespace without touching quoted text."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            while index < len(text) and text[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if text.startswith("/*", index):
            output.extend((" ", " "))
            index += 2
            while index < len(text) and not text.startswith("*/", index):
                output.append("\n" if text[index] == "\n" else " ")
                index += 1
            if index >= len(text):
                raise ValueError("unterminated JSONC block comment")
            output.extend((" ", " "))
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _remove_jsonc_trailing_commas(text: str) -> str:
    """Remove trailing commas outside JSON strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def to_home_dir(source: str):
    """Resolve a mount source to a host $HOME-relative path, or None to skip."""
    s = source.replace("${localEnv:HOME}", str(HOME))
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    p = Path(s)
    try:
        p.relative_to(HOME)
    except ValueError:
        return None  # not under HOME (docker.sock, /etc/letsencrypt, workspace repos)
    return p


def has_directory_marker(source: str) -> bool:
    """Whether the raw mount source ends in `/`, MDT's missing-dir hint."""
    s = source.replace("${localEnv:HOME}", str(HOME))
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    return s.endswith("/")


def _mode_for(p: Path) -> int:
    if p.name == "tmp" and p.parent.name == PARENT_NAME:
        return TMP_MODE
    return MODE_OVERRIDES.get(p.name, DEFAULT_MODE)


def _is_known_directory_source(p: Path) -> bool:
    """Return whether a missing source is one of MDT's directory mounts."""
    if p == HOME / ".ssh":
        return True
    return p.parent == HOME / PARENT_NAME and p.name in KNOWN_DIRECTORY_SOURCE_NAMES


def load_missing_source_policy(config_path: Path | None = None) -> str:
    """Read the optional bootstrap policy without sourcing the shell config.

    A host with no MDT config uses the shipped default. If the config exists but
    this newly introduced key is absent, the shipped default also applies until
    the operator runs the wizard. Duplicate or invalid declared values fail closed.
    """
    config_path = config_path or HOST_CONFIG_PATH
    try:
        config_path.lstat()
    except FileNotFoundError:
        return DEFAULT_MISSING_SOURCE_POLICY
    except OSError as exc:
        raise ValueError(f"could not inspect host config {config_path}: {exc}") from exc
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"could not read host config {config_path}: {exc}") from exc

    declared: list[str] = []
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if key.strip() != MISSING_SOURCE_POLICY_KEY:
            continue
        if not separator:
            raise ValueError(
                f"invalid {MISSING_SOURCE_POLICY_KEY} assignment on line {line_number} of {config_path}"
            )
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
            value = value[1:-1]
        declared.append(value)

    if len(declared) > 1:
        raise ValueError(f"duplicate {MISSING_SOURCE_POLICY_KEY} in {config_path}")
    policy = declared[0] if declared else DEFAULT_MISSING_SOURCE_POLICY
    if policy not in MISSING_SOURCE_POLICIES:
        choices = " or ".join(sorted(MISSING_SOURCE_POLICIES))
        raise ValueError(
            f"{MISSING_SOURCE_POLICY_KEY} in {config_path} must be {choices}; found {policy!r}"
        )
    return policy


def verify_host_cgroup_slices(slices: tuple[str, ...], *, runner=None) -> bool:
    """Require the template's declared host slices before Docker creates a cockpit.

    systemd accepts an unknown Docker cgroup parent by creating a transient,
    unlimited slice. This host-side initializeCommand runs before that first
    container exists, so it is the only safe point to prove these units are
    already loaded from installed unit files.
    """
    run = subprocess.run if runner is None else runner
    for unit in slices:
        try:
            result = run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=Id,LoadState,FragmentPath",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(
                f"[mdt-bootstrap] ERROR cannot verify host cgroup slice {unit}: {exc}",
                file=sys.stderr,
            )
            return False

        facts: dict[str, str] = {}
        malformed = False
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in {"Id", "LoadState", "FragmentPath"} or key in facts:
                malformed = True
                break
            facts[key] = value
        unit_id = facts.get("Id", "")
        load_state = facts.get("LoadState", "")
        fragment_path = facts.get("FragmentPath", "")
        if (
            result.returncode != 0
            or malformed
            or unit_id != unit
            or load_state != "loaded"
            or not fragment_path.startswith("/")
            or fragment_path.startswith("/run/systemd/")
            or not Path(fragment_path).is_file()
        ):
            print(
                f"[mdt-bootstrap] ERROR host cgroup slice {unit} must be loaded from an installed unit "
                f"(exit={result.returncode}, Id={unit_id!r}, LoadState={load_state!r}, "
                f"FragmentPath={fragment_path!r}); refusing to create the devcontainer.",
                file=sys.stderr,
            )
            return False
    return True


def ensure(
    p: Path,
    *,
    expected_type: str | None = None,
    missing_policy: str = DEFAULT_MISSING_SOURCE_POLICY,
) -> bool:
    """Accept existing sources by actual type and prepare missing ones by policy.

    For missing paths, `expected_type` comes from the raw source spelling: a
    trailing `/` means directory and no trailing `/` means file. Existing
    custom sources are classified from the host filesystem instead.
    """
    if expected_type not in (None, "file", "directory"):
        raise ValueError(f"unsupported bind-source type {expected_type!r}")
    if missing_policy not in MISSING_SOURCE_POLICIES:
        raise ValueError(f"unsupported missing bind-source policy {missing_policy!r}")

    expects_directory = expected_type == "directory"
    if expected_type == "directory" and p.name in KNOWN_FILE_SOURCE_BASENAMES:
        print(
            f"[mdt-bootstrap] ERROR {p.name} is a file-level mount source and cannot use the "
            f"directory `/` marker: {p}. No path was created.",
            file=sys.stderr,
        )
        return False
    if p.is_file():
        if expects_directory:
            print(
                f"[mdt-bootstrap] ERROR expected a directory bind source at {p}; found a file. "
                "No path was changed. Correct the mount source and intended type.",
                file=sys.stderr,
            )
            return False
        print(f"[mdt-bootstrap] exists  {p} (file)")
        return True
    if p.is_dir():
        if p.name in KNOWN_FILE_SOURCE_BASENAMES:
            print(
                f"[mdt-bootstrap] ERROR expected a file bind source at {p}; found a directory. "
                "No path was changed. Inspect the directory, then provide the intended regular "
                "file or remove the file mount from devcontainer.json.",
                file=sys.stderr,
            )
            return False
        if expects_directory:
            target = p
        else:
            print(f"[mdt-bootstrap] exists  {p} (dir; existing host type accepted)")
            return True
    elif p.exists():
        # Sockets and other special sources are valid bind mounts too. They already
        # have a concrete type, so leave them unchanged.
        if expects_directory:
            print(
                f"[mdt-bootstrap] ERROR expected a directory bind source at {p}; found a special "
                "filesystem object. No path was changed.",
                file=sys.stderr,
            )
            return False
        if p.name in KNOWN_FILE_SOURCE_BASENAMES:
            print(
                f"[mdt-bootstrap] ERROR expected a regular file bind source at {p}; found a special "
                "filesystem object. No path was changed.",
                file=sys.stderr,
            )
            return False
        print(f"[mdt-bootstrap] exists  {p} (special source; unchanged)")
        return True
    else:
        if missing_policy == "fail":
            print(
                f"[mdt-bootstrap] ERROR missing bind source {p}; policy is `fail`, so no path was "
                "created. Create the intended host source or choose "
                "`create-by-spelling` in the MDT host-setup wizard.",
                file=sys.stderr,
            )
            return False
        created_type = expected_type or (
            "directory" if _is_known_directory_source(p) else "file"
        )
        if created_type == "file":
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            except FileExistsError:
                if p.is_file():
                    print(f"[mdt-bootstrap] exists  {p} (file)")
                    return True
                print(
                    f"[mdt-bootstrap] ERROR expected a regular file bind source at {p}; "
                    "another filesystem object appeared. No path was changed.",
                    file=sys.stderr,
                )
                return False
            except OSError as exc:
                print(f"[mdt-bootstrap] ERROR could not prepare {p}: {exc}", file=sys.stderr)
                return False
            print(f"[mdt-bootstrap] created {p} (mode 0o600) (empty file)")
            return True
        target = p

    mode = _mode_for(p)
    is_tmp = mode == TMP_MODE
    try:
        if target.exists():
            # Re-assert 1777 on tmp every run (worktree tooling + other users rely on it); leave
            # other dirs' modes alone so we never fight perms the user set deliberately.
            if is_tmp:
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass
            print(f"[mdt-bootstrap] exists  {target} (dir)")
            return True
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, mode)
        except OSError:
            pass
        print(f"[mdt-bootstrap] created {target} (mode {oct(mode)}) (dir)")
    except OSError as exc:
        print(f"[mdt-bootstrap] ERROR could not prepare {target}: {exc}", file=sys.stderr)
        return False
    return True


def fallback_bind_paths() -> list[tuple[Path, str]]:
    """Return typed canonical sources when the sibling template is unavailable."""
    parent = HOME / PARENT_NAME
    return (
        [(HOME / ".ssh", "directory")]
        + [(parent / name, "directory") for name in FALLBACK]
        + [(parent / "tmp", "directory")]
        + [(parent / name, "file") for name in FALLBACK_FILES]
    )


def main() -> int:
    try:
        missing_policy = load_missing_source_policy(HOST_CONFIG_PATH)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1
    print(f"[mdt-bootstrap] missing bind-source policy: {missing_policy}")
    dc = Path(__file__).resolve().parent / "devcontainer.json"
    try:
        cgroup_slices = host_cgroup_slices(dc)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1
    if not verify_host_cgroup_slices(cgroup_slices):
        return 1

    requested: dict[Path, str] = {}
    conflicted: set[Path] = set()
    failed = False
    found_home_source = False
    sources = host_bind_sources(dc)
    for source in sources:
        path = to_home_dir(source)
        if path is not None:
            found_home_source = True
            source_type = "directory" if has_directory_marker(source) else "file"
            previous_type = requested.get(path)
            if previous_type is not None and previous_type != source_type:
                print(
                    f"[mdt-bootstrap] ERROR conflicting file/directory source spellings for {path}; "
                    "no source was prepared.",
                    file=sys.stderr,
                )
                conflicted.add(path)
                requested.pop(path, None)
                failed = True
            elif path not in conflicted:
                requested[path] = source_type
    if not found_home_source:
        print("[mdt-bootstrap] no parseable $HOME bind mounts; using fallback set", file=sys.stderr)
        requested = dict(fallback_bind_paths())
    for p, expected_type in requested.items():
        result = ensure(
            p,
            expected_type=expected_type,
            missing_policy=missing_policy,
        )
        if result is False:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
