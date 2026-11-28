# ciu-owned host-enrollment fragment (CIU S14.7 / KI-24).
#
# This file is RENDER INPUT, not an importable module: cmru's `[project.installer]
# extensions` mechanism inlines it verbatim into `ciu/get.py` at the
# `# @@EXTENSIONS@@` marker (cmru W1-CIU-ENROLL, decision O4). It is not shipped
# in the ciu wheel (see ciu/pyproject.toml: only `src/` is a package root).
#
# It may use only the template names listed in the rendered file's
# `EXTENSION_API` tuple plus the standard library; cmru checks that at render
# time. `[[PROJECT_NAME]]` is a cmru template placeholder, replaced after
# inlining.
#
# Hardening of this code (KI-49 / KI-50) is tracked as CIU-122 / CIU-123 in
# ciu/KNOWN_ISSUES_TODO_BACKLOG.md; the code below is the pre-move behaviour,
# moved unchanged.
import argparse
import base64
import binascii
import contextlib
import fcntl
import json
import glob as _glob
import grp
import hashlib
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


# ─── Host enrollment helpers (CIU S14.7 / cmru KI-24) ────────────────────────
#
# `enroll` turns a bare, untrusted host into one a controller CAN start trusting:
# install + a deploy user + ONE control-generated public key + the host-key
# fingerprints the operator confirms out of band. It deliberately does NOT
# generate keys, call anything back, open a listener, touch sshd_config, run any
# adapter verb, or install system packages — the token/callback/self-hosted
# download backend alternative was withdrawn; everything here is local state.

# Accepted public-key algorithms (KI-24): the two fixed names plus the two
# families. Anything else is a configuration error, not a "try it and see".
_SSH_KEY_TYPE_RE = re.compile(
    r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-[A-Za-z0-9@._-]+|sk-[A-Za-z0-9@._-]+)$"
)
_SSH_KEY_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,3}$")
_FLEETD_PATH_RE = re.compile(r"/[A-Za-z0-9._+/-]+\Z")
_FLEETD_GROUP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

# Printed where the operator must substitute a real value — never guessed.
_ENROLL_NAME_PLACEHOLDER = "<NAME>"
_ENROLL_ADDR_PLACEHOLDER = "<ADDRESS>"
_ENROLL_STATE_DIR = Path("/var/lib/ciu/enroll-state")
_ENROLL_LOCK_PATH = Path("/run/lock/ciu-enroll-authorized-keys.lock")


def _is_ssh_key_type(token: str) -> bool:
    return bool(_SSH_KEY_TYPE_RE.match(token))


def _decode_ssh_key_blob(
    key_type: str, key_b64: str, *, require_canonical: bool,
) -> bytes:
    if not _is_ssh_key_type(key_type) or _SSH_KEY_B64_RE.fullmatch(key_b64) is None:
        raise ValueError("unsupported key type or malformed base64 token")
    try:
        blob = base64.b64decode(key_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError("malformed strict base64") from exc
    if not blob:
        raise ValueError("empty SSH key blob")
    if require_canonical and base64.b64encode(blob).decode("ascii") != key_b64:
        raise ValueError("noncanonical base64 encoding")
    if len(blob) < 4:
        raise ValueError("SSH key blob has no embedded type")
    type_length = int.from_bytes(blob[:4], "big")
    if type_length == 0 or type_length > len(blob) - 4:
        raise ValueError("SSH key blob has an invalid embedded type length")
    try:
        embedded_type = blob[4:4 + type_length].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SSH key blob type is not ASCII") from exc
    if embedded_type != key_type:
        raise ValueError("declared key type does not match the embedded SSH key type")
    return blob


def _try_decode_ssh_key_blob(key_type, key_b64) -> Optional[bytes]:
    try:
        if isinstance(key_type, bytes):
            key_type = key_type.decode("ascii")
        if isinstance(key_b64, bytes):
            key_b64 = key_b64.decode("ascii")
        if not _is_ssh_key_type(key_type) or _SSH_KEY_B64_RE.fullmatch(key_b64) is None:
            return None
        return base64.b64decode(key_b64.encode("ascii"), validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return None


def _parse_authorized_key(raw: Optional[str]) -> Tuple[str, str, str]:
    """Parse ``<type> <base64>[ <comment>]`` → (type, base64, comment).

    Refuses anything else with EXIT_CONFIG (KI-24 step 1). An options field
    (``from="..."``,``command="..."``) is NOT accepted here: options are this
    subcommand's own business (``--from``), so a key line that already carries
    them is ambiguous about which restriction actually applies.
    """
    text = (raw or "").strip()
    if not text:
        fatal("--authorized-key is empty; pass the control-generated public key line.",
              EXIT_CONFIG)
    if "\n" in text or "\r" in text:
        fatal("--authorized-key must be a SINGLE line; got embedded newline(s).",
              EXIT_CONFIG)
    parts = text.split()
    if len(parts) < 2:
        fatal(
            f"--authorized-key is not a public key line: {text!r}. "
            "Expected '<type> <base64> [comment]'.",
            EXIT_CONFIG,
        )
    ktype, b64 = parts[0], parts[1]
    comment = " ".join(parts[2:])
    if not _is_ssh_key_type(ktype):
        if any(ch in ktype for ch in '=,"'):
            fatal(
                f"--authorized-key starts with an options field ({ktype!r}); pass "
                "the bare '<type> <base64> [comment]' line and express the "
                "restriction with --from instead.",
                EXIT_CONFIG,
            )
        fatal(
            f"Unsupported key type {ktype!r}. Expected one of: ssh-ed25519, "
            "ecdsa-sha2-*, sk-*, ssh-rsa.",
            EXIT_CONFIG,
        )
    if not _SSH_KEY_B64_RE.match(b64):
        fatal(
            f"Key material for {ktype} is not base64: {b64!r}. "
            "Did an options field (from=\"...\") get pasted in? Use --from instead.",
            EXIT_CONFIG,
        )
    return ktype, b64, comment


def _ak_split_line(line: str) -> Optional[Tuple[str, str, str, str]]:
    """Split one authorized_keys line into (options, type, base64, comment).

    Returns None for blank/comment lines and for lines carrying no recognisable
    key type. Splitting is quote-aware because an OpenSSH options field may hold
    whitespace inside double quotes (``from="1.2.3.4",command="a b"``) — a plain
    ``str.split()`` would tear such a line in half and mis-read the key.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    tokens: List[str] = []
    buf: List[str] = []
    in_quote = False
    escaped = False
    for ch in stripped:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_quote:
            buf.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch.isspace() and not in_quote:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    if not tokens:
        return None
    if _is_ssh_key_type(tokens[0]):
        options, rest = "", tokens
    else:
        options, rest = tokens[0], tokens[1:]
        if not rest or not _is_ssh_key_type(rest[0]):
            # Tolerate the malformed `from="…",<type> <b64>` shape on READ (see
            # _build_key_line): sshd refuses such a line, but if one is already
            # in the file we must still recognise the key material in it — a
            # conflict we cannot see is a conflict we silently append beside.
            head, sep, tail = options.rpartition(",")
            if sep and _is_ssh_key_type(tail) and tokens[1:]:
                options, rest = head, [tail] + tokens[1:]
    if len(rest) < 2 or not _is_ssh_key_type(rest[0]):
        return None
    return options, rest[0], rest[1], " ".join(rest[2:])


def _build_key_line(ktype: str, b64: str, comment: str, from_pattern: Optional[str]) -> str:
    """Render the exact line to append: ``from="P" <type> <b64> <comment>``.

    NOTE (deviation from KI-24's literal text, which spells the restricted form
    ``from="PATTERN",<type> …``): OpenSSH separates the options field from the
    key with WHITESPACE, not a comma. sshd's reader advances past the options to
    the first unquoted whitespace, so a comma-joined line makes ``ssh-ed25519``
    look like a second option name and the key is never read — the entry parses
    as garbage and public-key auth fails. Measured, not reasoned: with
    ``from="*",<key>`` in authorized_keys a real sshd answered "Permission
    denied (publickey)"; with ``from="*" <key>`` the same key authenticated. An
    installer that wrote the comma form would report a successful enrollment and
    leave a host the controller can never log into.
    """
    body = f"{ktype} {b64}" + (f" {comment}" if comment else "")
    if from_pattern:
        return f'from="{from_pattern}" {body}'
    return body


def _find_sshd() -> Optional[str]:
    """Locate an SSH server binary: PATH first, then the usual sbin location."""
    found = shutil.which("sshd")
    if found:
        return found
    fallback = Path("/usr/sbin/sshd")
    return str(fallback) if fallback.exists() else None


def _enroll_check_prerequisites(args: argparse.Namespace) -> Tuple[str, str, str]:
    """KI-24 step 1 — every check that must pass BEFORE any network I/O.

    Linux is already enforced globally in ``main()`` before dispatch. Returns the
    parsed key so the caller never re-parses it.
    """
    if os.geteuid() != 0:
        fatal("enroll must run as root (sudo): it creates a user and writes "
              "into that user's ~/.ssh.", EXIT_PREREQ)

    sshd = _find_sshd()
    if not sshd:
        fatal(
            "No SSH server on this host (sshd is not on PATH and /usr/sbin/sshd "
            "does not exist). Install openssh-server and re-run — this installer "
            "never installs system packages itself.",
            EXIT_PREREQ,
        )
    ok(f"SSH server present: {sshd}")

    _forced_command_from_args(args)
    parsed = _parse_authorized_key(getattr(args, "authorized_key", None))
    ok(f"Authorized key parsed ({parsed[0]}).")
    return parsed


def _enroll_ensure_user(user: str, want_docker: bool) -> "pwd.struct_passwd":
    """KI-24 step 3 — create the deploy user when absent, never modify it when present."""
    if want_docker:
        # Checked BEFORE useradd so a missing group never leaves a half-enrolled host.
        try:
            grp.getgrnam("docker")
        except KeyError:
            fatal(
                "--docker was requested but this host has no 'docker' group. "
                "Install Docker (which creates it) and re-run; enroll will not "
                "create system groups.",
                EXIT_PREREQ,
            )

    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        entry = None

    if entry is None:
        info(f"Creating deploy user {user} ...")
        result = subprocess.run(
            ["useradd", "--create-home", "--shell", "/bin/bash", user],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            fatal(
                f"useradd {user} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}",
                EXIT_FAIL,
            )
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            fatal(f"useradd {user} reported success but the user is still unknown.",
                  EXIT_FAIL)
        ok(f"User {user} created (home {entry.pw_dir}, shell {entry.pw_shell}).")
    else:
        ok(f"User {user} already exists — left untouched "
           f"(home {entry.pw_dir}, shell {entry.pw_shell}).")

    if want_docker:
        result = subprocess.run(
            ["usermod", "--append", "--groups", "docker", user],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            fatal(
                f"Adding {user} to the docker group failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}",
                EXIT_FAIL,
            )
        ok(f"User {user} is in the docker group.")

    return entry


def _enroll_install_key_unlocked(
    entry: "pwd.struct_passwd",
    parsed: Tuple[str, str, str],
    from_pattern: Optional[str],
) -> Path:
    """KI-24 step 4 — ~USER/.ssh 0700, authorized_keys 0600, key appended ONCE.

    Idempotency is decided on KEY MATERIAL, not on the rendered line: an
    identical entry is reported and not duplicated, while the same key under
    DIFFERENT options (a changed ``from=``, or one with and one without) is a
    real conflict and is refused with EXIT_CONFIG rather than quietly appended
    beside the old one.
    """
    ktype, b64, comment = parsed
    want_options = f'from="{from_pattern}"' if from_pattern else ""
    line = _build_key_line(ktype, b64, comment, from_pattern)

    ssh_dir = Path(entry.pw_dir) / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    os.chown(ssh_dir, entry.pw_uid, entry.pw_gid)

    authorized = ssh_dir / "authorized_keys"
    existing = (
        authorized.read_text(encoding="utf-8", errors="replace")
        if authorized.exists() else ""
    )
    supplied_blob = _try_decode_ssh_key_blob(ktype, b64)
    if supplied_blob is not None:
        existing_bytes = authorized.read_bytes() if authorized.exists() else b""
        restricted_lines = _recorded_restricted_key_lines(entry, existing_bytes, supplied_blob)
        if restricted_lines:
            fatal(
                f"{authorized} already contains this key blob in a CIU-restricted "
                f"entry on line {', '.join(str(number) for number in restricted_lines)}; "
                "refusing an unrestricted authorized_keys form.", EXIT_CONFIG,
            )

    already_present = False
    other_key_comments: List[str] = []
    for raw_line in existing.splitlines():
        parsed_line = _ak_split_line(raw_line)
        if parsed_line is None:
            continue
        have_options, have_type, have_b64, have_comment = parsed_line
        if (have_type, have_b64) != (ktype, b64):
            # A DIFFERENT, already-recognized key for this same user — not a
            # conflict (KI-24 never restricts a user to one key), but a
            # genuine key rotation must not silently leave two valid
            # identities with no visibility (adversarial review finding,
            # cmru-ki24): note it so it can be warned about below.
            other_key_comments.append(have_comment or have_type)
            continue
        if have_options != want_options:
            fatal(
                f"{authorized} already carries this key under DIFFERENT options.\n"
                f"  present: {have_options or '(none)'}\n"
                f"  wanted:  {want_options or '(none)'}\n"
                "Refusing to append a second entry for the same key material — "
                "resolve the conflict by hand and re-run.",
                EXIT_CONFIG,
            )
        already_present = True
        if have_comment != comment:
            warn(f"Key already present with a different comment "
                 f"({have_comment!r} vs {comment!r}); leaving it as-is.")

    if already_present:
        ok(f"Key already present in {authorized} — not duplicated.")
    else:
        if other_key_comments:
            warn(
                f"{authorized} already has {len(other_key_comments)} other "
                f"key(s) for this user ({', '.join(other_key_comments)}) — "
                "appending this one ALONGSIDE them, not replacing. If this "
                "was meant to be a rotation, remove the old key by hand."
            )
        _enroll_before_authorized_keys_commit(authorized, existing)
        with open(authorized, "a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(line + "\n")
        ok(f"Key appended to {authorized}.")

    os.chmod(authorized, 0o600)
    os.chown(authorized, entry.pw_uid, entry.pw_gid)
    return authorized


def _enroll_install_key(
    entry: "pwd.struct_passwd",
    parsed: Tuple[str, str, str],
    from_pattern: Optional[str],
) -> Path:
    with _authorized_keys_lock():
        return _enroll_install_key_unlocked(entry, parsed, from_pattern)


def _agent_path_is_normalized(value: object) -> bool:
    if not isinstance(value, str) or _FLEETD_PATH_RE.fullmatch(value) is None:
        return False
    parts = value.split("/")
    return (
        value != "/"
        and not value.endswith("/")
        and all(part not in ("", ".", "..") for part in parts[1:])
    )


def _forced_command_from_args(args: argparse.Namespace) -> Optional[str]:
    """Build only fleetd-agent's fixed argv; CIU never accepts a command string."""
    enabled = bool(getattr(args, "forced_command", False))
    agent_path = getattr(args, "agent_path", None)
    group = getattr(args, "fleetd_group", None)
    policy = getattr(args, "agent_policy", None)
    if not enabled:
        if agent_path is not None or group is not None or policy is not None:
            fatal("--agent-path, --fleetd-group, and --agent-policy require "
                  "--forced-command.", EXIT_CONFIG)
        return None
    if agent_path is None or group is None:
        fatal("--forced-command requires explicit --agent-path and --fleetd-group "
              "values.", EXIT_CONFIG)
    if getattr(args, "from_pattern", None) is not None:
        fatal("--forced-command cannot be combined with --from; the restricted "
              "entry has one fixed authorized_keys command option.", EXIT_CONFIG)
    if not _agent_path_is_normalized(agent_path):
        fatal("--agent-path must be an absolute normalized path using only "
              "letters, digits, '.', '_', '+', '-', and '/'.", EXIT_CONFIG)
    if not isinstance(group, str) or _FLEETD_GROUP_RE.fullmatch(group) is None:
        fatal("--fleetd-group must match fleetd's group grammar "
              "([A-Za-z0-9][A-Za-z0-9._-]{0,127}).", EXIT_CONFIG)
    if policy is not None and not _agent_path_is_normalized(policy):
        fatal("--agent-policy must be an absolute normalized path using only "
              "letters, digits, '.', '_', '+', '-', and '/'.", EXIT_CONFIG)
    if not getattr(args, "name", None):
        fatal("--forced-command requires --name so CIU can identify this entry "
              "for rotation and removal.", EXIT_CONFIG)
    command = f"{agent_path} forced-command --group {group}"
    if policy is not None:
        command += f" --policy {policy}"
    return command


def _restricted_marker(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return f"ciu-restricted-v1:{digest}"


@contextlib.contextmanager
def _authorized_keys_lock():
    """Serialize every CIU authorized_keys writer with one protected lock."""
    path = Path(_ENROLL_LOCK_PATH)
    if not path.is_absolute():
        fatal("CIU authorized_keys lock path must be absolute.", EXIT_PREREQ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    except OSError as exc:
        fatal(f"Cannot prepare CIU authorized_keys lock directory {path.parent}: "
              f"{exc.strerror or exc}.", EXIT_CONFIG)
    for parent in reversed(path.parents):
        if parent == Path("/"):
            continue
        try:
            info = parent.lstat()
        except OSError as exc:
            fatal(f"Cannot inspect CIU lock directory {parent}: {exc.strerror or exc}.",
                  EXIT_CONFIG)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            fatal(f"CIU authorized_keys lock parent {parent} must be a real directory.",
                  EXIT_CONFIG)
        if (
            path == Path("/run/lock/ciu-enroll-authorized-keys.lock")
            and (info.st_uid != 0 or info.st_mode & 0o022)
        ):
            fatal(f"CIU lock directory {parent} must be root-owned and not writable "
                  "by other users.", EXIT_CONFIG)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        fatal(f"Cannot open CIU authorized_keys lock {path}: {exc.strerror or exc}.",
              EXIT_CONFIG)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077
        ):
            fatal(f"CIU authorized_keys lock {path} must be a single-link regular file "
                  "owned by the installer with mode 0600.", EXIT_CONFIG)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as exc:
        fatal(f"CIU authorized_keys lock operation failed: {exc.strerror or exc}.",
              EXIT_FAIL)
    finally:
        os.close(fd)


def _enroll_state_filename(entry: "pwd.struct_passwd", name: str) -> str:
    identity = f"{entry.pw_uid}\0{name}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest() + ".json"


def _enroll_before_authorized_keys_commit(authorized: Path, expected_data) -> None:
    """Test seam immediately before a writer commits its already-read view."""


def _verify_authorized_keys_unchanged(
    ssh_fd: int, expected_fd: Optional[int], expected_data: bytes, authorized: Path,
) -> None:
    try:
        path_info = os.stat("authorized_keys", dir_fd=ssh_fd, follow_symlinks=False)
    except FileNotFoundError:
        if expected_fd is None:
            return
        fatal(f"{authorized} disappeared after CIU read it; refusing to replace it.",
              EXIT_CONFIG)
    except OSError as exc:
        fatal(f"Cannot recheck {authorized} before replacement: {exc.strerror or exc}.",
              EXIT_CONFIG)
    if expected_fd is None:
        fatal(f"{authorized} appeared after CIU read it; refusing to replace it.",
              EXIT_CONFIG)
    original_info = os.fstat(expected_fd)
    if (
        not stat.S_ISREG(path_info.st_mode) or path_info.st_nlink != 1
        or (path_info.st_dev, path_info.st_ino)
        != (original_info.st_dev, original_info.st_ino)
    ):
        fatal(f"{authorized} changed identity after CIU read it; refusing to replace it.",
              EXIT_CONFIG)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
    try:
        current_fd = os.open("authorized_keys", flags, dir_fd=ssh_fd)
    except OSError as exc:
        fatal(f"Cannot reopen {authorized} before replacement: {exc.strerror or exc}.",
              EXIT_CONFIG)
    try:
        current_info = os.fstat(current_fd)
        chunks = []
        while True:
            chunk = os.read(current_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        if (
            (current_info.st_dev, current_info.st_ino)
            != (original_info.st_dev, original_info.st_ino)
            or b"".join(chunks) != expected_data
        ):
            fatal(f"{authorized} contents changed after CIU read it; refusing to replace it.",
                  EXIT_CONFIG)
    finally:
        os.close(current_fd)


def _open_enroll_state_dir(*, create: bool) -> Optional[int]:
    """Open CIU's protected ownership directory without following symlinks."""
    path = Path(_ENROLL_STATE_DIR)
    if not path.is_absolute():
        fatal("CIU enrollment state directory must be absolute.", EXIT_PREREQ)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        except FileExistsError:
            pass
    try:
        final_info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(final_info.st_mode) or not stat.S_ISDIR(final_info.st_mode):
        fatal(f"CIU enrollment state path {path} must be a real directory.", EXIT_CONFIG)
    if final_info.st_uid != os.geteuid() or final_info.st_mode & 0o077:
        fatal(
            f"CIU enrollment state directory {path} must be owned by the installer "
            "and have mode 0700.", EXIT_CONFIG,
        )
    # The production path and each parent are root-controlled. The temporary
    # path override is an in-process test seam and is never user-configurable.
    if path == Path("/var/lib/ciu/enroll-state"):
        for parent in path.parents:
            if parent == Path("/"):
                break
            parent_info = parent.lstat()
            if (
                stat.S_ISLNK(parent_info.st_mode)
                or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0
                or parent_info.st_mode & 0o022
            ):
                fatal(
                    f"CIU enrollment state parent {parent} must be a root-owned "
                    "directory not writable by other users.", EXIT_CONFIG,
                )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        fatal(f"Cannot open CIU enrollment state directory {path}: "
              f"{exc.strerror or exc}.", EXIT_CONFIG)


def _state_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate state field {key!r}")
        result[key] = value
    return result


def _load_enroll_state(entry: "pwd.struct_passwd", name: str) -> Optional[dict]:
    directory_fd = _open_enroll_state_dir(create=False)
    if directory_fd is None:
        return None
    filename = _enroll_state_filename(entry, name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
    try:
        try:
            fd = os.open(filename, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077
            ):
                fatal(
                    f"CIU enrollment state file {filename} must be a single-link "
                    "installer-owned regular file with mode 0600.", EXIT_CONFIG,
                )
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            try:
                state = json.loads(
                    b"".join(chunks).decode("utf-8"), object_pairs_hook=_state_json_object,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                fatal(f"CIU enrollment state file {filename} is malformed: {exc}.",
                      EXIT_CONFIG)
            if (
                not isinstance(state, dict) or state.get("version") != 1
                or state.get("uid") != entry.pw_uid or state.get("name") != name
            ):
                fatal(f"CIU enrollment state file {filename} has the wrong identity.",
                      EXIT_CONFIG)
            return state
        finally:
            os.close(fd)
    except OSError as exc:
        fatal(f"Cannot read CIU enrollment state file {filename}: "
              f"{exc.strerror or exc}.", EXIT_CONFIG)
    finally:
        os.close(directory_fd)


def _list_enroll_states_for_user(entry: "pwd.struct_passwd") -> List[dict]:
    directory_fd = _open_enroll_state_dir(create=False)
    if directory_fd is None:
        return []
    result = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
    try:
        for filename in os.listdir(directory_fd):
            if re.fullmatch(r"[0-9a-f]{64}\.json", filename) is None:
                continue
            try:
                fd = os.open(filename, flags, dir_fd=directory_fd)
            except OSError:
                continue
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077
                ):
                    continue
                chunks = []
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                try:
                    state = json.loads(
                        b"".join(chunks).decode("utf-8"),
                        object_pairs_hook=_state_json_object,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(state, dict) or state.get("version") != 1:
                    continue
                if state.get("uid") != entry.pw_uid:
                    continue
                name = state.get("name")
                if (
                    not isinstance(name, str)
                    or filename != _enroll_state_filename(entry, name)
                ):
                    continue
                result.append(state)
            finally:
                os.close(fd)
        return result
    finally:
        os.close(directory_fd)


def _recorded_restricted_key_lines(
    entry: "pwd.struct_passwd", existing: bytes, key_blob: bytes,
) -> List[int]:
    wanted_key_hash = hashlib.sha256(key_blob).hexdigest()
    has_recorded_key = False
    for state in _list_enroll_states_for_user(entry):
        status = state.get("status")
        candidates = []
        if status in ("committed", "pending-remove"):
            candidates = [state.get("record")]
        elif status == "prepared":
            candidates = [state.get("old"), state.get("new")]
        else:
            continue
        for record in candidates:
            if isinstance(record, dict) and record.get("key_sha256") == wanted_key_hash:
                has_recorded_key = True
    if not has_recorded_key:
        return []

    matching_lines = []
    for line_number, (_whole, body) in enumerate(_authorized_key_records(existing), 1):
        parsed = _parse_authorized_key_record(body)
        if parsed is not None:
            _options, key_type, key_b64, _comment = parsed
            if _try_decode_ssh_key_blob(key_type, key_b64) == key_blob:
                matching_lines.append(line_number)
            continue
        if body.lstrip().startswith(b"#"):
            continue
        tokens = body.split()
        if any(
            _try_decode_ssh_key_blob(tokens[index], tokens[index + 1]) == key_blob
            for index in range(len(tokens) - 1)
        ):
            matching_lines.append(line_number)
    return matching_lines


def _save_enroll_state(
    entry: "pwd.struct_passwd", name: str, state: Optional[dict],
) -> None:
    directory_fd = _open_enroll_state_dir(create=state is not None)
    if directory_fd is None:
        return
    filename = _enroll_state_filename(entry, name)
    try:
        if state is None:
            try:
                os.unlink(filename, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.fsync(directory_fd)
            return
        payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        temp_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
            getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            os.fchmod(temp_fd, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("CIU enrollment state write made no progress")
                view = view[written:]
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        try:
            os.replace(temp_name, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    except OSError as exc:
        fatal(f"Cannot update CIU enrollment state for '{name}': "
              f"{exc.strerror or exc}.", EXIT_FAIL)
    finally:
        os.close(directory_fd)


def _state_record(
    whole_line: bytes, key_type: str, key_b64: str,
    prefix: bytes, separator_added: bool,
) -> dict:
    key_blob = _decode_ssh_key_blob(key_type, key_b64, require_canonical=True)
    return {
        "line_sha256": hashlib.sha256(whole_line).hexdigest(),
        "key_sha256": hashlib.sha256(key_blob).hexdigest(),
        "separator_added": separator_added,
        "prefix_length": len(prefix),
        "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
    }


def _state_hash(record: dict) -> str:
    value = record.get("line_sha256") if isinstance(record, dict) else None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        fatal("CIU enrollment state contains an invalid line hash.", EXIT_CONFIG)
    return value


def _line_hash(whole: bytes) -> str:
    return hashlib.sha256(whole).hexdigest()


def _record_line_numbers(
    records: List[Tuple[bytes, bytes]], record: dict,
) -> List[int]:
    wanted = _state_hash(record)
    return [number for number, (whole, _body) in enumerate(records, 1)
            if _line_hash(whole) == wanted]


def _has_restricted_marker(body: bytes) -> bool:
    return any(token.startswith(b"ciu-restricted-v1:") for token in body.split())


def _refuse_unrecorded_markers(
    records: List[Tuple[bytes, bytes]], allowed_hashes: set[str], authorized: Path,
) -> None:
    for line_number, (whole, body) in enumerate(records, 1):
        if _has_restricted_marker(body) and _line_hash(whole) not in allowed_hashes:
            fatal(
                f"{authorized} contains an unrecorded CIU restricted marker on line "
                f"{line_number}; refusing to rewrite it. Restore trusted CIU state "
                "or inspect and recover this entry manually.", EXIT_CONFIG,
            )


def _is_restricted_command_shape(
    parsed: Optional[Tuple[str, str, str, str]],
) -> bool:
    """Recognize the fixed safe fleetd command form without claiming ownership."""
    if parsed is None:
        return False
    options, key_type, key_b64, _comment = parsed
    if not _is_ssh_key_type(key_type) or _SSH_KEY_B64_RE.fullmatch(key_b64) is None:
        return False
    prefix = 'restrict,command="'
    if not options.startswith(prefix) or not options.endswith('"'):
        return False
    command = options[len(prefix):-1]
    words = command.split(" ")
    if any(not word for word in words):
        return False
    if len(words) not in (4, 6) or words[1:3] != ["forced-command", "--group"]:
        return False
    if not _agent_path_is_normalized(words[0]) or _FLEETD_GROUP_RE.fullmatch(words[3]) is None:
        return False
    return len(words) == 4 or (
        words[4] == "--policy" and _agent_path_is_normalized(words[5])
    )


def _refuse_unrecorded_restricted_shapes(
    records: List[Tuple[bytes, bytes]], allowed_hashes: set[str], authorized: Path,
) -> None:
    for line_number, (whole, body) in enumerate(records, 1):
        if (
            _is_restricted_command_shape(_parse_authorized_key_record(body))
            and _line_hash(whole) not in allowed_hashes
        ):
            fatal(
                f"{authorized} contains an unrecorded restricted forced-command "
                f"shape on line {line_number}; refusing to rewrite it. Restore "
                "trusted CIU state or inspect and recover this entry manually.",
                EXIT_CONFIG,
            )


def _recorded_restricted_line_hashes_for_user(
    entry: "pwd.struct_passwd",
) -> set[str]:
    hashes: set[str] = set()
    for state in _list_enroll_states_for_user(entry):
        status = state.get("status")
        if status in ("committed", "pending-remove"):
            candidates = [state.get("record")]
        elif status == "prepared":
            candidates = [state.get("old"), state.get("new")]
        else:
            continue
        for record in candidates:
            if isinstance(record, dict):
                hashes.add(_state_hash(record))
    return hashes


def _make_enroll_state(entry: "pwd.struct_passwd", name: str, status: str, **fields) -> dict:
    return {
        "version": 1,
        "uid": entry.pw_uid,
        "name": name,
        "status": status,
        **fields,
    }


def _recover_enroll_state(
    entry: "pwd.struct_passwd", name: str, authorized: Path,
    records: List[Tuple[bytes, bytes]],
) -> Optional[dict]:
    state = _load_enroll_state(entry, name)
    if state is None:
        allowed_hashes = _recorded_restricted_line_hashes_for_user(entry)
        _refuse_unrecorded_markers(records, allowed_hashes, authorized)
        _refuse_unrecorded_restricted_shapes(records, allowed_hashes, authorized)
        return None
    status = state.get("status")
    allowed_hashes: set[str] = set()
    if status == "prepared":
        old_record = state.get("old")
        new_record = state.get("new")
        if not isinstance(new_record, dict):
            fatal(f"CIU enrollment state for '{name}' lacks its prepared line record.",
                  EXIT_CONFIG)
        new_lines = _record_line_numbers(records, new_record)
        old_lines = _record_line_numbers(records, old_record) if old_record else []
        if old_record is None and not new_lines:
            _save_enroll_state(entry, name, None)
            return None
        if old_record is not None and len(old_lines) == 1 and not new_lines:
            state = _make_enroll_state(entry, name, "committed", record=old_record)
            _save_enroll_state(entry, name, state)
        elif not old_lines and len(new_lines) == 1:
            state = _make_enroll_state(entry, name, "committed", record=new_record)
            _save_enroll_state(entry, name, state)
        else:
            lines = sorted(set(old_lines + new_lines))
            where = ", ".join(str(number) for number in lines) or "(recorded line missing)"
            fatal(
                f"{authorized} has an ambiguous interrupted CIU update for '{name}' "
                f"at line {where}; refusing to remove or rewrite any entry.", EXIT_CONFIG,
            )
        allowed_hashes = {_state_hash(state["record"])}
    elif status == "pending-remove":
        record = state.get("record")
        if not isinstance(record, dict):
            fatal(f"CIU enrollment state for '{name}' lacks its removal record.",
                  EXIT_CONFIG)
        lines = _record_line_numbers(records, record)
        if not lines:
            _save_enroll_state(entry, name, None)
            return None
        if len(lines) != 1:
            fatal(
                f"{authorized} matches the recorded restricted entry for '{name}' on "
                f"lines {', '.join(str(number) for number in lines)}; refusing removal.",
                EXIT_CONFIG,
            )
        state = _make_enroll_state(entry, name, "committed", record=record)
        _save_enroll_state(entry, name, state)
        allowed_hashes = {_state_hash(record)}
    elif status == "committed":
        record = state.get("record")
        if not isinstance(record, dict):
            fatal(f"CIU enrollment state for '{name}' lacks its committed line record.",
                  EXIT_CONFIG)
        lines = _record_line_numbers(records, record)
        if len(lines) != 1:
            where = ", ".join(str(number) for number in lines) or "(recorded line missing)"
            fatal(
                f"{authorized} does not contain the recorded restricted entry for "
                f"'{name}' exactly once (lines {where}); refusing to rewrite it.",
                EXIT_CONFIG,
            )
        allowed_hashes = {_state_hash(record)}
    else:
        fatal(f"CIU enrollment state for '{name}' has unknown status {status!r}.",
              EXIT_CONFIG)
    recorded_hashes = allowed_hashes | _recorded_restricted_line_hashes_for_user(entry)
    _refuse_unrecorded_markers(records, recorded_hashes, authorized)
    _refuse_unrecorded_restricted_shapes(records, recorded_hashes, authorized)
    return state


def _authorized_key_records(data: bytes) -> List[Tuple[bytes, bytes]]:
    """Return (whole line, line body) pairs, splitting only on LF bytes."""
    result: List[Tuple[bytes, bytes]] = []
    start = 0
    while start < len(data):
        end = data.find(b"\n", start)
        if end < 0:
            whole = data[start:]
            body = whole
            start = len(data)
        else:
            whole = data[start:end + 1]
            body = data[start:end]
            start = end + 1
        if body.endswith(b"\r"):
            body = body[:-1]
        result.append((whole, body))
    return result


def _parse_authorized_key_record(body: bytes) -> Optional[Tuple[str, str, str, str]]:
    return _ak_split_line(body.decode("utf-8", errors="replace"))


def _managed_restricted_entry(
    parsed: Optional[Tuple[str, str, str, str]], marker: str,
) -> bool:
    """Require both CIU's marker and its exact safe forced-command structure."""
    if parsed is None:
        return False
    _options, _key_type, _key_b64, comment = parsed
    if not _is_restricted_command_shape(parsed):
        return False
    return bool(comment.split()) and comment.split()[-1] == marker


def _open_restricted_ssh_dir(entry: "pwd.struct_passwd", *, create: bool):
    """Open home and .ssh without following a target-controlled final symlink."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        fatal("This platform cannot open authorized_keys paths with O_NOFOLLOW.",
              EXIT_PREREQ)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        home_fd = os.open(entry.pw_dir, flags)
    except OSError as exc:
        fatal(f"Cannot open the deploy user's home without following symlinks: "
              f"{exc.strerror or exc}.", EXIT_CONFIG)
    try:
        if not stat.S_ISDIR(os.fstat(home_fd).st_mode):
            fatal("The deploy user's home is not a directory.", EXIT_CONFIG)
        try:
            ssh_fd = os.open(".ssh", flags, dir_fd=home_fd)
        except FileNotFoundError:
            if not create:
                return home_fd, None
            try:
                os.mkdir(".ssh", 0o700, dir_fd=home_fd)
            except FileExistsError:
                pass
            try:
                ssh_fd = os.open(".ssh", flags, dir_fd=home_fd)
            except OSError as exc:
                fatal(f"Cannot open the deploy user's .ssh directory without "
                      f"following symlinks: {exc.strerror or exc}.", EXIT_CONFIG)
        except OSError as exc:
            fatal(f"Cannot open the deploy user's .ssh directory without "
                  f"following symlinks: {exc.strerror or exc}.", EXIT_CONFIG)
        if not stat.S_ISDIR(os.fstat(ssh_fd).st_mode):
            os.close(ssh_fd)
            fatal("The deploy user's .ssh path is not a directory.", EXIT_CONFIG)
        if create:
            try:
                os.fchown(ssh_fd, entry.pw_uid, entry.pw_gid)
                os.fchmod(ssh_fd, 0o700)
            except OSError as exc:
                os.close(ssh_fd)
                fatal(f"Cannot set ownership or mode on the deploy user's .ssh "
                      f"directory: {exc.strerror or exc}.", EXIT_CONFIG)
        return home_fd, ssh_fd
    except BaseException:
        os.close(home_fd)
        raise


def _read_restricted_authorized_keys(ssh_fd: int, path: Path) -> Tuple[Optional[int], bytes]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
    try:
        fd = os.open("authorized_keys", flags, dir_fd=ssh_fd)
    except FileNotFoundError:
        return None, b""
    except OSError as exc:
        fatal(f"Cannot open {path} without following symlinks: "
              f"{exc.strerror or exc}.", EXIT_CONFIG)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fatal(f"{path} must be a regular, single-link file.", EXIT_CONFIG)
        chunks: List[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return fd, b"".join(chunks)
    except BaseException:
        os.close(fd)
        raise


def _write_restricted_authorized_keys(
    ssh_fd: int, entry: "pwd.struct_passwd", data: bytes,
    *, expected_fd: Optional[int], expected_data: bytes,
) -> None:
    temp_name = None
    temp_fd = None
    for _attempt in range(32):
        candidate = f".authorized_keys.ciu-{secrets.token_hex(12)}"
        try:
            temp_fd = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=ssh_fd,
            )
            temp_name = candidate
            break
        except FileExistsError:
            continue
        except OSError as exc:
            fatal(f"Cannot create an atomic authorized_keys replacement: "
                  f"{exc.strerror or exc}.", EXIT_FAIL)
    if temp_fd is None or temp_name is None:
        fatal("Could not allocate an atomic authorized_keys replacement.", EXIT_FAIL)
    try:
        os.fchown(temp_fd, entry.pw_uid, entry.pw_gid)
        os.fchmod(temp_fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("authorized_keys write made no progress")
            view = view[written:]
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
        _enroll_before_authorized_keys_commit(authorized, expected_data)
        _verify_authorized_keys_unchanged(ssh_fd, expected_fd, expected_data, authorized)
        os.replace(
            temp_name, "authorized_keys", src_dir_fd=ssh_fd, dst_dir_fd=ssh_fd,
        )
        os.fsync(ssh_fd)
    except OSError as exc:
        fatal(f"Atomic authorized_keys update failed: {exc.strerror or exc}.", EXIT_FAIL)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=ssh_fd)
            except FileNotFoundError:
                pass


def _remove_recorded_line(
    existing: bytes, records: List[Tuple[bytes, bytes]], record: dict,
    authorized: Path,
) -> bytes:
    line_numbers = _record_line_numbers(records, record)
    if len(line_numbers) != 1:
        where = ", ".join(str(number) for number in line_numbers) or "(recorded line missing)"
        fatal(
            f"{authorized} must contain the exact recorded restricted line once "
            f"(matches at {where}); refusing to remove or rotate it.", EXIT_CONFIG,
        )
    index = line_numbers[0] - 1
    start = sum(len(whole) for whole, _body in records[:index])
    end = start + len(records[index][0])
    prefix_length = record.get("prefix_length")
    prefix_hash = record.get("prefix_sha256")
    can_restore_prefix = (
        record.get("separator_added") is True
        and type(prefix_length) is int
        and 0 <= prefix_length < len(existing)
        and start == prefix_length + 1
        and existing[prefix_length:prefix_length + 1] == b"\n"
        and end == len(existing)
        and hashlib.sha256(existing[:prefix_length]).hexdigest() == prefix_hash
    )
    if can_restore_prefix:
        return existing[:prefix_length]
    return existing[:start] + existing[end:]


def _enroll_install_restricted_key_locked(
    entry: "pwd.struct_passwd",
    parsed: Tuple[str, str, str],
    name: str,
    command: str,
) -> Path:
    """Install one state-recorded `restrict,command` entry atomically and no-follow."""
    key_type, key_b64, comment = parsed
    try:
        key_blob = _decode_ssh_key_blob(key_type, key_b64, require_canonical=True)
    except ValueError:
        fatal(
            "Restricted --authorized-key must use strict canonical base64 and its "
            "embedded SSH key type must match the declared type.", EXIT_CONFIG,
        )
    marker = _restricted_marker(name)
    options = f'restrict,command="{command}"'
    marked_comment = " ".join(part for part in (comment, marker) if part)
    line = (f'{options} {key_type} {key_b64} {marked_comment}'.rstrip() + "\n").encode()
    authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    home_fd, ssh_fd = _open_restricted_ssh_dir(entry, create=True)
    assert ssh_fd is not None
    authorized_fd = None
    try:
        authorized_fd, existing = _read_restricted_authorized_keys(ssh_fd, authorized)
        records = _authorized_key_records(existing)
        state = _recover_enroll_state(entry, name, authorized, records)
        old_record = state.get("record") if state is not None else None
        old_hash = _state_hash(old_record) if old_record is not None else None
        old_line_number = None
        if old_record is not None:
            old_lines = _record_line_numbers(records, old_record)
            if len(old_lines) != 1:
                fatal(f"{authorized} must contain the recorded restricted line exactly "
                      "once before rotation.", EXIT_CONFIG)
            old_line_number = old_lines[0]

        for line_number, (whole, body) in enumerate(records, 1):
            if old_hash is not None and _line_hash(whole) == old_hash:
                continue
            parsed_line = _parse_authorized_key_record(body)
            if parsed_line is None:
                tokens = [] if body.lstrip().startswith(b"#") else body.split()
                collision = any(
                    _try_decode_ssh_key_blob(tokens[index], tokens[index + 1]) == key_blob
                    for index in range(len(tokens) - 1)
                )
                if collision:
                    fatal(f"{authorized} already contains this key blob on line "
                          f"{line_number}; refusing a second authorized_keys form.",
                          EXIT_CONFIG)
                continue

            _have_options, have_type, have_b64, _have_comment = parsed_line
            have_blob = _try_decode_ssh_key_blob(have_type, have_b64)
            if have_blob == key_blob:
                fatal(f"{authorized} already contains this key blob on line "
                      f"{line_number} outside the recorded CIU restricted entry; "
                      "refusing a second authorized_keys form.", EXIT_CONFIG)

        if old_record is not None and old_line_number is not None:
            old_whole = records[old_line_number - 1][0]
            if old_whole == line:
                assert authorized_fd is not None
                try:
                    os.fchown(authorized_fd, entry.pw_uid, entry.pw_gid)
                    os.fchmod(authorized_fd, 0o600)
                except OSError as exc:
                    fatal(f"Cannot set ownership or mode on {authorized}: "
                          f"{exc.strerror or exc}.", EXIT_CONFIG)
                ok(f"Restricted key already present in {authorized} — not duplicated.")
                return authorized
            prefix_data = _remove_recorded_line(existing, records, old_record, authorized)
        else:
            prefix_data = existing

        separator_added = bool(prefix_data and not prefix_data.endswith(b"\n"))
        new_record = _state_record(line, key_type, key_b64, prefix_data, separator_added)
        new_data = prefix_data + (b"\n" if separator_added else b"") + line
        prepared = _make_enroll_state(
            entry, name, "prepared", old=old_record, new=new_record,
        )
        _save_enroll_state(entry, name, prepared)
        _write_restricted_authorized_keys(
            ssh_fd, entry, new_data, expected_fd=authorized_fd, expected_data=existing,
        )
        _save_enroll_state(
            entry, name, _make_enroll_state(entry, name, "committed", record=new_record),
        )
        ok(f"Restricted key installed in {authorized}.")
        return authorized
    finally:
        if authorized_fd is not None:
            os.close(authorized_fd)
        os.close(ssh_fd)
        os.close(home_fd)


def _enroll_install_restricted_key(
    entry: "pwd.struct_passwd",
    parsed: Tuple[str, str, str],
    name: str,
    command: str,
) -> Path:
    with _authorized_keys_lock():
        return _enroll_install_restricted_key_locked(entry, parsed, name, command)


def _enroll_remove_restricted_keys_locked(entry: "pwd.struct_passwd", name: str) -> int:
    """Remove exactly one restricted entry only when root state records its bytes."""
    authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    home_fd, ssh_fd = _open_restricted_ssh_dir(entry, create=False)
    if ssh_fd is None:
        os.close(home_fd)
        return 0
    authorized_fd = None
    try:
        authorized_fd, existing = _read_restricted_authorized_keys(ssh_fd, authorized)
        records = _authorized_key_records(existing)
        state = _recover_enroll_state(entry, name, authorized, records)
        if state is None:
            return 0
        record = state["record"]
        new_data = _remove_recorded_line(existing, records, record, authorized)
        _save_enroll_state(
            entry, name, _make_enroll_state(entry, name, "pending-remove", record=record),
        )
        _write_restricted_authorized_keys(
            ssh_fd, entry, new_data, expected_fd=authorized_fd, expected_data=existing,
        )
        _save_enroll_state(entry, name, None)
        return 1
    finally:
        if authorized_fd is not None:
            os.close(authorized_fd)
        os.close(ssh_fd)
        os.close(home_fd)


def _enroll_remove_restricted_keys(entry: "pwd.struct_passwd", name: str) -> int:
    with _authorized_keys_lock():
        return _enroll_remove_restricted_keys_locked(entry, name)


def _remove_restricted_command(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        fatal("remove-restricted must run as root (sudo).", EXIT_PREREQ)
    name = getattr(args, "name", None)
    user = getattr(args, "user", None) or "ciu"
    if not name:
        fatal("remove-restricted requires the original --name.", EXIT_CONFIG)
    if any((
        getattr(args, "authorized_key", None), getattr(args, "controller", None),
        getattr(args, "from_pattern", None), getattr(args, "docker", False),
        getattr(args, "forced_command", False), getattr(args, "agent_path", None),
        getattr(args, "fleetd_group", None), getattr(args, "agent_policy", None),
        getattr(args, "no_install", False), getattr(args, "scope", "system") != "system",
        getattr(args, "version", None), getattr(args, "variant", None),
        getattr(args, "manifest_pubkey", None), getattr(args, "config", None),
    )):
        fatal("remove-restricted accepts only --name and --user.", EXIT_CONFIG)
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        fatal(f"Cannot remove the restricted key: user {user!r} does not exist.",
              EXIT_PREREQ)
    removed = _enroll_remove_restricted_keys(entry, name)
    count_word = "entry" if removed == 1 else "entries"
    ok(f"Removed {removed} CIU-managed restricted key {count_word} for {name}.")


def _host_key_fingerprints() -> List[Tuple[str, str, str]]:
    """(path, full ssh-keygen line, SHA256:… token) for every host public key.

    Shells out to ``ssh-keygen -lf`` — the fingerprint is OpenSSH's to compute,
    never this script's.
    """
    results: List[Tuple[str, str, str]] = []
    for path in sorted(_glob.glob("/etc/ssh/ssh_host_*_key.pub")):
        result = subprocess.run(
            ["ssh-keygen", "-lf", path], capture_output=True, text=True
        )
        if result.returncode != 0:
            warn(f"ssh-keygen -lf {path} failed (exit {result.returncode}): "
                 f"{result.stderr.strip()}")
            continue
        text = result.stdout.strip()
        token = next(
            (tok for tok in text.split() if tok.startswith("SHA256:")), ""
        )
        results.append((path, text, token))
    return results


def _host_addresses() -> List[str]:
    """Addresses from ``hostname -I`` — reported UNCONFIRMED, never chosen for the operator."""
    if not shutil.which("hostname"):
        warn("hostname(1) not found; cannot list this host's addresses.")
        return []
    result = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
    if result.returncode != 0:
        warn(f"hostname -I failed (exit {result.returncode}): {result.stderr.strip()}")
        return []
    return result.stdout.split()


def do_enroll(args: argparse.Namespace, token: Optional[str]) -> None:
    """Enrol this host for a controller (CIU S14.7 / cmru KI-24).

    Strict order, fail-fast, idempotent on re-run:
      1. prerequisites — BEFORE any network I/O;
      2. install (skipped by --no-install);
      3. deploy user;
      4. ~USER/.ssh + authorized_keys;
      5. print the fingerprints, addresses and completion command.
    """
    if getattr(args, "remove_restricted", False):
        _remove_restricted_command(args)
        return

    scope = getattr(args, "scope", "system")
    user = getattr(args, "user", None) or "ciu"
    controller = getattr(args, "controller", "")

    hr()
    print(f"  {_c('BLD', '[[PROJECT_NAME]]')}  enroll  user={user}  "
          f"controller={controller}  scope={scope}")
    hr()

    # 1. Prerequisites — nothing below this line may run before these pass.
    restricted_command = _forced_command_from_args(args)
    parsed = _enroll_check_prerequisites(args)

    # 2. Install, verbatim: do_install owns the transaction/manifest logic.
    if getattr(args, "no_install", False):
        info("--no-install: skipping the install step.")
    else:
        install_args = argparse.Namespace(
            scope=scope,
            version=getattr(args, "version", None),
            variant=getattr(args, "variant", None),
            manifest_pubkey=getattr(args, "manifest_pubkey", None),
            config=getattr(args, "config", None),
        )
        do_install(install_args, token)

    # 3. Deploy user.
    entry = _enroll_ensure_user(user, bool(getattr(args, "docker", False)))

    # 4. Authorized key.
    if restricted_command is None:
        authorized = _enroll_install_key(entry, parsed, getattr(args, "from_pattern", None))
    else:
        authorized = _enroll_install_restricted_key(
            entry, parsed, getattr(args, "name"), restricted_command,
        )

    # 5. Report. Nothing here changes state; it is what the operator confirms.
    root = _root_dir(scope)
    installed = _current_version(root) or "(not installed)"
    fingerprints = _host_key_fingerprints()
    addresses = _host_addresses()
    ed25519_fp = next(
        (token_ for path, _text, token_ in fingerprints
         if path.endswith("ssh_host_ed25519_key.pub") and token_),
        "",
    )
    name = getattr(args, "name", None) or _ENROLL_NAME_PLACEHOLDER
    address = addresses[0] if addresses else _ENROLL_ADDR_PLACEHOLDER

    hr()
    print("SSH host key fingerprints (confirm these out of band):")
    if fingerprints:
        for path, text, _token in fingerprints:
            print(f"  {path}: {text}")
    else:
        warn("No /etc/ssh/ssh_host_*_key.pub found — is the SSH server configured?")

    print("Addresses (UNCONFIRMED — this host cannot know which one the "
          "controller reaches it on):")
    if addresses:
        for addr in addresses:
            print(f"  {addr}")
    else:
        print(f"  {_ENROLL_ADDR_PLACEHOLDER}")

    print(f"User:      {user}")
    print(f"Home:      {entry.pw_dir}")
    print(f"Keys:      {authorized}")
    print(f"Installed: {installed}")
    print(f"Controller: {controller}")
    hr()
    print("Finish enrollment on the CONTROL host with:")
    print(f"  ciu host enroll {name} --ssh-host {address} "
          f"--fingerprint {ed25519_fp or 'SHA256:<ed25519 fingerprint>'}")
    if name == _ENROLL_NAME_PLACEHOLDER:
        print(f"  (replace {_ENROLL_NAME_PLACEHOLDER} — enroll does not invent a host name; "
              "pass --name to have it filled in)")
    if address == _ENROLL_ADDR_PLACEHOLDER:
        print(f"  (replace {_ENROLL_ADDR_PLACEHOLDER} with the address the controller "
              "will actually use)")
    hr()
    ok(f"Host enrolled for {controller}.")


def _register_enroll(subparsers):
    """Add the `enroll` subcommand. The flag set is normative: ciu's
    `host enroll` prints the exact one-liner an admin runs on a bare host."""
    p_enroll = subparsers.add_parser(
        "enroll",
        help="Enrol this host: install + deploy user + authorized key + host-key fingerprints",
    )
    p_enroll.add_argument("--authorized-key", metavar="KEY", required=True,
                          help="Control-generated public key line: '<type> <base64> [comment]'")
    p_enroll.add_argument("--controller", metavar="FQDN", required=True,
                          help="FQDN of the controller this host is being enrolled for")
    p_enroll.add_argument("--user", metavar="USER", default="ciu",
                          help="Deploy user to create or confirm (default: ciu)")
    p_enroll.add_argument("--name", metavar="NAME",
                          help="Host name for the printed completion command (default: a placeholder)")
    p_enroll.add_argument("--from", dest="from_pattern", metavar="PATTERN",
                          help='Restrict the key with from="PATTERN" (source-address restriction)')
    p_enroll.add_argument("--forced-command", action="store_true",
                          help="Install an opt-in fleetd forced-command key")
    p_enroll.add_argument("--agent-path", metavar="PATH",
                          help="Absolute normalized fleetd-agent executable path")
    p_enroll.add_argument("--fleetd-group", metavar="GROUP",
                          help="Explicit fleetd forced-command group")
    p_enroll.add_argument("--agent-policy", metavar="PATH",
                          help="Optional absolute normalized fleetd policy path")
    p_enroll.add_argument("--docker", action="store_true",
                          help="Add the deploy user to the docker group (refused when absent)")
    p_enroll.add_argument("--no-install", action="store_true",
                          help="Skip the install step (user + key + fingerprints only)")
    p_enroll.add_argument("--scope", choices=["system", "user"], default="system",
                          help="Install scope for the install step (default: system)")
    p_remove = subparsers.add_parser(
        "remove-restricted",
        help="Remove CIU-managed fleetd restricted enrollment entries",
    )
    p_remove.add_argument("--name", metavar="NAME", required=True,
                          help="Host name used by the original restricted enrollment")
    p_remove.add_argument("--user", metavar="USER", default="ciu",
                          help="Deploy user whose restricted entry is removed (default: ciu)")
    p_remove.set_defaults(remove_restricted=True)
    return {"enroll": do_enroll, "remove-restricted": do_enroll}


_EXTENSIONS.append(_register_enroll)
