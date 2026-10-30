#!/usr/bin/env python3
"""mdt devcontainer host bootstrap ("get.py").

Runs ON THE HOST (wired via devcontainer.json `initializeCommand`) BEFORE the
container is created. It first verifies the declared host cgroup slices are
loaded from installed unit files, then prepares every `$HOME` bind-mount source
with sane permissions. Docker's `--mount` form refuses a missing source; this
script applies MDT's configured policy before Docker starts.

Layout (fresh home; docs/HOME-PERSISTENCE.md):
- The complete home is one real directory per checkout (template default
  `~/mdt-cockpit-${localWorkspaceFolderBasename}-home/`, mode 0700), bind-mounted at
  `/home/<remoteUser>` (`/home/vscode`). Image-owned tools and defaults live outside this
  mount, so a new home starts EMPTY and the image fills in nothing but what the tools
  themselves create.
- `/tmp` is a separate host-backed directory (template default
  `~/mdt-cockpit-${localWorkspaceFolderBasename}-tmp/`, mode 1777), so worktrees survive
  rebuilds and are visible to the sibling test-runner container.
- Any further mount source is named `~/mdt-cockpit-<purpose>`.
- The host's native `~/.ssh` is read-only at `/run/mdt/host-ssh`, outside the home.

The ROLE of a source (home, tmp) comes from the mount's `target`, never from the
source's name, so a consumer that spells its own sources (for example
`~/mdt-cockpit-home/` -> `/home/vscode`, one home shared by every checkout) gets the same
modes and notices as the template defaults. The template spellings above, expanded for
the checkout this script runs in, are used only when the sibling devcontainer.json
cannot be read (see `fallback_bind_paths`).

Design: stdlib-only and idempotent. Reads the sibling `devcontainer.json` and inspects each `type=bind`
source under the host `$HOME`. Existing regular files and directories are handled according to their
actual type, regardless of their names or suffixes. For a missing source, the host config policy
defaults to `create-by-spelling`: a source ending in `/` creates a directory; a source without `/`
creates an empty regular file. Set `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` in the host config
(allowed values: `create-by-spelling`, `fail`) to require every `$HOME` source managed here to exist
already. `~/.ssh` gets 0700; the source mounted at `/tmp` gets 1777; the source mounted at the
container home gets 0700; everything else 0755.

No migration: this fork starts with an empty home and the user copies old content by hand.
When the previous split-mount state (`~/mdt--mounted-folders/`) still holds files and the new
home is absent or empty, `notify_legacy_split_state()` prints one informational line pointing at
the manual-copy section of docs/HOME-PERSISTENCE.md (printed as its full GitHub URL). It names the
source actually mounted as the home. It never changes the exit status.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Mount sources under the host $HOME, spelled `mdt-cockpit-<purpose>` (D2).
SOURCE_PREFIX = "mdt-cockpit-"
# The devcontainer variable the templates use to give every checkout its own home and
# /tmp (`vbpub` and `dstdns` on one host never share a home); initializeCommand runs in
# the workspace folder, so its basename is that value.
WORKSPACE_VAR = "${localWorkspaceFolderBasename}"
# The template spellings (templates/devcontainer.json, templates/host-folder/devcontainer.json)
# and their expansion for THIS checkout: the fallback names when the sibling
# devcontainer.json cannot be read, and the names the notices print.
HOME_SOURCE_SPELLING = SOURCE_PREFIX + WORKSPACE_VAR + "-home"
TMP_SOURCE_SPELLING = SOURCE_PREFIX + WORKSPACE_VAR + "-tmp"
HOME_SOURCE_NAME = HOME_SOURCE_SPELLING.replace(WORKSPACE_VAR, Path.cwd().name)
TMP_SOURCE_NAME = TMP_SOURCE_SPELLING.replace(WORKSPACE_VAR, Path.cwd().name)
TMP_MODE = 0o1777  # persisted host-backed /tmp: sticky + world-writable, like a normal /tmp
HOME_MODE = 0o700
# Roles a `$HOME` source can have, derived from the mount target (see module docstring).
ROLE_HOME = "home"
ROLE_TMP = "tmp"
TMP_TARGET = "/tmp"
# The mdt image has exactly one login user; devcontainer.json's `remoteUser` names it.
DEFAULT_REMOTE_USER = "vscode"
# ssh insists on a private directory, whoever created it.
MODE_OVERRIDES = {".ssh": 0o700}
DEFAULT_MODE = 0o755

# Canonical sources - used only if devcontainer.json can't be read.
FALLBACK = [HOME_SOURCE_NAME, TMP_SOURCE_NAME]
FALLBACK_FILES: list[str] = []
KNOWN_FILE_SOURCE_BASENAMES = {SOURCE_PREFIX + "gitconfig"}
KNOWN_DIRECTORY_SOURCE_NAMES = set(FALLBACK)

# The previous template's grouped parent. It exists in live code ONLY so the
# one-line notice can recognise it; nothing is ever created, read or migrated there.
LEGACY_PARENT_NAME = "mdt--mounted-folders"
# This file is vendored into the consumer's .devcontainer/, where no docs/ directory
# exists, so the notice must carry an absolute URL. The owner/repo equal the
# [github] table of the monorepo's cmru.orchestration.toml (a unit test pins that).
MANUAL_COPY_DOC = (
    "https://github.com/volkb79-2/vbpub/blob/main/mdt-cockpit/docs/HOME-PERSISTENCE.md"
    "#moving-from-modern-debian-tools-python-debug"
)

MISSING_SOURCE_POLICY_KEY = "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY"
DEFAULT_MISSING_SOURCE_POLICY = "create-by-spelling"
MISSING_SOURCE_POLICIES = {DEFAULT_MISSING_SOURCE_POLICY, "fail"}
HOST_CONFIG_DISPLAY = "/etc/mdt/host-setup.env"
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
BIND_RE = re.compile(r'source=([^,"]+),target=([^,"]+),type=bind')


def host_bind_mounts(dc_path: Path) -> list[tuple[str, str]]:
    """Return (source, target) bind-mount pairs from devcontainer.json (skipping // comment lines)."""
    mounts: list[tuple[str, str]] = []
    try:
        for line in dc_path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("//"):
                continue  # commented-out mount
            mounts.extend(BIND_RE.findall(line))
    except OSError:
        return []
    return mounts


def host_bind_sources(dc_path: Path) -> list:
    """Return bind-mount source strings from devcontainer.json (skipping // comment lines)."""
    return [source for source, _target in host_bind_mounts(dc_path)]


def container_home_target(dc_path: Path) -> str:
    """The in-container home directory, derived from devcontainer.json's `remoteUser`.

    A missing `remoteUser` means the image's single login user. An unreadable or
    malformed file is not guessed around: `main()` has already parsed the same file
    for the cgroup tiers and refused to continue if it was unusable.
    """
    document = _parse_jsonc_object(dc_path.read_text(encoding="utf-8"))
    user = document.get("remoteUser", DEFAULT_REMOTE_USER)
    if not isinstance(user, str) or re.fullmatch(r"[a-z_][a-z0-9_-]*", user) is None:
        raise ValueError(f"{dc_path} remoteUser must be a plain user name, got {user!r}")
    return f"/home/{user}"


def role_for_target(target: str, home_target: str) -> str | None:
    """Map a mount target to the role that decides its host-side mode, or None."""
    normalized = target.rstrip("/") or "/"
    if normalized == home_target:
        return ROLE_HOME
    if normalized == TMP_TARGET:
        return ROLE_TMP
    return None


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


def _expand_local_vars(source: str) -> str:
    """Expand the devcontainer variables a mount source may use, as the CLI does.

    initializeCommand runs in the local workspace folder, so its basename is
    `${localWorkspaceFolderBasename}`. Left unexpanded, the literal spelling was
    created as a host directory.
    """
    s = source.replace("${localEnv:HOME}", str(HOME))
    s = s.replace(WORKSPACE_VAR, Path.cwd().name)
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    return s


def to_home_dir(source: str):
    """Resolve a mount source to a host $HOME-relative path, or None to skip."""
    s = _expand_local_vars(source)
    p = Path(s)
    try:
        p.relative_to(HOME)
    except ValueError:
        return None  # not under HOME (docker.sock, /etc/letsencrypt, workspace repos)
    return p


def has_directory_marker(source: str) -> bool:
    """Whether the raw mount source ends in `/`, MDT's missing-dir hint."""
    return _expand_local_vars(source).endswith("/")


def _mode_for(p: Path, role: str | None = None) -> int:
    if role == ROLE_TMP:
        return TMP_MODE
    if role == ROLE_HOME:
        return HOME_MODE
    return MODE_OVERRIDES.get(p.name, DEFAULT_MODE)


def _is_known_directory_source(p: Path) -> bool:
    """Return whether a missing source is one of MDT's directory mounts."""
    if p == HOME / ".ssh":
        return True
    return p.parent == HOME and p.name in KNOWN_DIRECTORY_SOURCE_NAMES


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
        raise ValueError(
            f"{MISSING_SOURCE_POLICY_KEY} in {config_path} must be {_policy_choices(' or ')}; "
            f"found {policy!r}"
        )
    return policy


def _policy_choices(separator: str = ", ") -> str:
    return separator.join(sorted(MISSING_SOURCE_POLICIES))


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
                f"FragmentPath={fragment_path!r}); refusing to create the devcontainer. Docker would "
                "otherwise create an unlimited transient slice. Install the tiers from the Docker host with "
                "`sudo host-setup/install.sh` (mdt-cockpit), or correct the CGROUP_PARENT_DEV_* names in "
                "devcontainer.json containerEnv.",
                file=sys.stderr,
            )
            return False
    return True


def ensure(
    p: Path,
    *,
    expected_type: str | None = None,
    missing_policy: str = DEFAULT_MISSING_SOURCE_POLICY,
    role: str | None = None,
) -> bool:
    """Accept existing sources by actual type and prepare missing ones by policy.

    For missing paths, `expected_type` comes from the raw source spelling: a
    trailing `/` means directory and no trailing `/` means file. Existing
    custom sources are classified from the host filesystem instead. `role`
    (home or tmp, from the mount target) selects the directory mode.
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
        created_type = expected_type or (
            "directory" if _is_known_directory_source(p) else "file"
        )
        if missing_policy == "fail":
            create_command = (
                f"mkdir -p -m {_mode_for(p, role):04o} {p}" if created_type == "directory" else f"touch {p}"
            )
            print(
                f"[mdt-bootstrap] ERROR missing bind source {p}; {MISSING_SOURCE_POLICY_KEY}=fail "
                f"({HOST_CONFIG_DISPLAY}), so no path was created. Either create the intended host "
                f"source yourself (`{create_command}`), or set {MISSING_SOURCE_POLICY_KEY}="
                f"{DEFAULT_MISSING_SOURCE_POLICY} (allowed values: {_policy_choices()}) in "
                f"{HOST_CONFIG_DISPLAY} with `install.sh --wizard` on the host.",
                file=sys.stderr,
            )
            return False
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

    mode = _mode_for(p, role)
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
    return (
        [(HOME / ".ssh", "directory")]
        + [(HOME / name, "directory") for name in FALLBACK]
        + [(HOME / name, "file") for name in FALLBACK_FILES]
    )


def fallback_roles() -> dict[Path, str]:
    """Roles of the canonical sources, used with `fallback_bind_paths` only."""
    return {HOME / HOME_SOURCE_NAME: ROLE_HOME, HOME / TMP_SOURCE_NAME: ROLE_TMP}


def _holds_file_or_symlink(path: Path) -> bool | None:
    """Whether a tree holds any file, symlink or special file; None if unreadable.

    Empty directories do not count. Symlinks are never followed: a symlink is
    content in itself. `None` means the tree could not be fully inspected, which
    is not the same as "empty" - the caller must not report it as empty.
    """
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    return True
                nested = _holds_file_or_symlink(Path(entry.path))
                if nested is not False:
                    return nested
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return False


def _is_absent_or_empty(path: Path) -> bool | None:
    """True when `path` is absent or an empty directory; None if it can't be told."""
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except FileNotFoundError:
        return True
    except NotADirectoryError:
        return False
    except OSError:
        return None


def _display_home_relative(path: Path) -> str:
    """`~/name/` for a path under the host $HOME, so the notice names the real source."""
    try:
        return f"~/{path.relative_to(HOME)}/"
    except ValueError:
        return f"{path}/"


def notify_legacy_split_state(new_home: Path | None = None) -> bool:
    """Print the one-line manual-copy pointer when old split state is left behind.

    Informational only (D1): the exit status never depends on it. Fires when the
    previous template's `~/mdt--mounted-folders/` holds files/symlinks AND the new
    home is absent or empty. `new_home` is the source actually mounted as the container
    home (the template default for this checkout when omitted). Returns whether the line was printed.
    """
    legacy = HOME / LEGACY_PARENT_NAME
    legacy_state = _holds_file_or_symlink(legacy)
    if legacy_state is False:
        return False
    new_home = new_home if new_home is not None else HOME / HOME_SOURCE_NAME
    new_home_empty = _is_absent_or_empty(new_home)
    if new_home_empty is False:
        return False
    legacy_text = "holds files" if legacy_state else "could not be fully inspected"
    home_text = "is empty" if new_home_empty else "could not be inspected"
    print(
        f"[mdt-bootstrap] NOTE the previous split state ~/{LEGACY_PARENT_NAME}/ {legacy_text} "
        f"and the new home {_display_home_relative(new_home)} {home_text}: this template starts with an empty "
        f"home and migrates nothing - copy what you need by hand, see {MANUAL_COPY_DOC}.",
        file=sys.stderr,
    )
    return True


def main() -> int:
    try:
        missing_policy = load_missing_source_policy(HOST_CONFIG_PATH)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1
    print(
        f"[mdt-bootstrap] missing bind-source policy: {missing_policy} "
        f"({MISSING_SOURCE_POLICY_KEY}; allowed values: {_policy_choices()})"
    )
    dc = Path(__file__).resolve().parent / "devcontainer.json"
    try:
        cgroup_slices = host_cgroup_slices(dc)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1
    if not verify_host_cgroup_slices(cgroup_slices):
        return 1
    try:
        home_target = container_home_target(dc)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1

    requested: dict[Path, str] = {}
    roles: dict[Path, str] = {}
    conflicted: set[Path] = set()
    failed = False
    found_home_source = False
    for source, target in host_bind_mounts(dc):
        path = to_home_dir(source)
        if path is not None:
            found_home_source = True
            role = role_for_target(target, home_target)
            if role is not None and roles.get(path, role) != role:
                print(
                    f"[mdt-bootstrap] ERROR {path} is mounted both as the container home ({home_target}) "
                    "and as /tmp in devcontainer.json; the two need different modes (0700 and 1777). "
                    "Give each mount its own host directory. No source was prepared.",
                    file=sys.stderr,
                )
                conflicted.add(path)
                requested.pop(path, None)
                failed = True
                continue
            if role is not None:
                roles[path] = role
            source_type = "directory" if has_directory_marker(source) else "file"
            previous_type = requested.get(path)
            if previous_type is not None and previous_type != source_type:
                print(
                    f"[mdt-bootstrap] ERROR conflicting file/directory source spellings for {path} in "
                    "devcontainer.json: one mount ends the source in `/` (directory), another does not (file). "
                    "Spell every mount of this source the same way. No source was prepared.",
                    file=sys.stderr,
                )
                conflicted.add(path)
                requested.pop(path, None)
                failed = True
            elif path not in conflicted:
                requested[path] = source_type
    if not found_home_source:
        print(
            f"[mdt-bootstrap] no parseable $HOME bind mounts in {dc}; using the canonical fallback set "
            f"(~/{HOME_SOURCE_NAME}, ~/{TMP_SOURCE_NAME}, ~/.ssh)",
            file=sys.stderr,
        )
        requested = dict(fallback_bind_paths())
        roles = fallback_roles()
    notify_legacy_split_state(next((p for p, r in roles.items() if r == ROLE_HOME), None))
    for p, expected_type in requested.items():
        result = ensure(
            p,
            expected_type=expected_type,
            missing_policy=missing_policy,
            role=roles.get(p),
        )
        if result is False:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
