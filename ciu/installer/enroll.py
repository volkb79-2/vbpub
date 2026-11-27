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


def _is_ssh_key_type(token: str) -> bool:
    return bool(_SSH_KEY_TYPE_RE.match(token))


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


def _enroll_install_key(
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

    already_present = False
    other_key_comments: List[str] = []
    for line_number, raw_line in enumerate(existing.splitlines(), 1):
        parsed_line = _ak_split_line(raw_line)
        if parsed_line is None:
            if not raw_line.lstrip().startswith("#"):
                tokens = raw_line.split()
                collision = any(
                    tokens[index] == ktype and tokens[index + 1] == b64
                    for index in range(len(tokens) - 1)
                )
                if collision:
                    fatal(
                        f"{authorized} already contains this key blob on line "
                        f"{line_number}; refusing a second authorized_keys form.",
                        EXIT_CONFIG,
                    )
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
            line_detail = f"  conflicting line {line_number}\n"
            fatal(
                f"{authorized} already carries this key under DIFFERENT options.\n"
                f"{line_detail}"
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
        with open(authorized, "a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(line + "\n")
        ok(f"Key appended to {authorized}.")

    os.chmod(authorized, 0o600)
    os.chown(authorized, entry.pw_uid, entry.pw_gid)
    return authorized


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
    options, key_type, key_b64, comment = parsed
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
    if not _agent_path_is_normalized(words[0]):
        return False
    if _FLEETD_GROUP_RE.fullmatch(words[3]) is None:
        return False
    if len(words) == 6:
        if words[4] != "--policy" or not _agent_path_is_normalized(words[5]):
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


def _enroll_install_restricted_key(
    entry: "pwd.struct_passwd",
    parsed: Tuple[str, str, str],
    name: str,
    command: str,
) -> Path:
    """Install one CIU-owned `restrict,command` record atomically and no-follow."""
    key_type, key_b64, comment = parsed
    marker = _restricted_marker(name)
    options = f'restrict,command="{command}"'
    marked_comment = " ".join(part for part in (comment, marker) if part)
    line = f'{options} {key_type} {key_b64} {marked_comment}'.rstrip()
    authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    home_fd, ssh_fd = _open_restricted_ssh_dir(entry, create=True)
    assert ssh_fd is not None
    authorized_fd = None
    try:
        authorized_fd, existing = _read_restricted_authorized_keys(ssh_fd, authorized)
        records = _authorized_key_records(existing)
        kept: List[bytes] = []
        prior_managed = 0
        already_present = False
        for line_number, (whole, body) in enumerate(records, 1):
            parsed_line = _parse_authorized_key_record(body)
            managed = _managed_restricted_entry(parsed_line, marker)
            if parsed_line is None:
                tokens = [] if body.lstrip().startswith(b"#") else body.split()
                collision = any(
                    tokens[index] == key_type.encode("ascii")
                    and tokens[index + 1] == key_b64.encode("ascii")
                    for index in range(len(tokens) - 1)
                )
                if collision:
                    fatal(f"{authorized} already contains this key blob on line "
                          f"{line_number}; refusing a second authorized_keys form.",
                          EXIT_CONFIG)
                kept.append(whole)
                continue

            have_options, have_type, have_b64, _have_comment = parsed_line
            same_key = (have_type, have_b64) == (key_type, key_b64)
            exact_entry = managed and same_key and have_options == options
            if same_key and not exact_entry and not managed:
                fatal(f"{authorized} already contains this key blob on line "
                      f"{line_number} under different options or ownership; "
                      "refusing a second authorized_keys form.", EXIT_CONFIG)
            if managed:
                if exact_entry:
                    already_present = True
                    kept.append(whole)
                else:
                    prior_managed += 1
                continue
            kept.append(whole)

        new_data = b"".join(kept)
        if already_present:
            if prior_managed == 0 and new_data == existing:
                assert authorized_fd is not None
                try:
                    os.fchown(authorized_fd, entry.pw_uid, entry.pw_gid)
                    os.fchmod(authorized_fd, 0o600)
                except OSError as exc:
                    fatal(f"Cannot set ownership or mode on {authorized}: "
                          f"{exc.strerror or exc}.", EXIT_CONFIG)
                ok(f"Restricted key already present in {authorized} — not duplicated.")
                return authorized
        else:
            if new_data and not new_data.endswith(b"\n"):
                new_data += b"\n"
            new_data += line.encode("utf-8") + b"\n"

        _write_restricted_authorized_keys(ssh_fd, entry, new_data)
        ok(f"Restricted key installed in {authorized}.")
        return authorized
    finally:
        if authorized_fd is not None:
            os.close(authorized_fd)
        os.close(ssh_fd)
        os.close(home_fd)


def _enroll_remove_restricted_keys(entry: "pwd.struct_passwd", name: str) -> int:
    """Remove only lines with CIU's marker and validated fixed-command structure."""
    marker = _restricted_marker(name)
    authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    home_fd, ssh_fd = _open_restricted_ssh_dir(entry, create=False)
    if ssh_fd is None:
        os.close(home_fd)
        return 0
    authorized_fd = None
    try:
        authorized_fd, existing = _read_restricted_authorized_keys(ssh_fd, authorized)
        if authorized_fd is None:
            return 0
        kept: List[bytes] = []
        removed = 0
        for whole, body in _authorized_key_records(existing):
            if _managed_restricted_entry(_parse_authorized_key_record(body), marker):
                removed += 1
            else:
                kept.append(whole)
        if removed:
            _write_restricted_authorized_keys(ssh_fd, entry, b"".join(kept))
        return removed
    finally:
        if authorized_fd is not None:
            os.close(authorized_fd)
        os.close(ssh_fd)
        os.close(home_fd)


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
