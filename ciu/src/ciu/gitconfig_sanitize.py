"""CIU-215: credential-free copies of a git common dir's config files.

A linked-worktree runner that mounts the git common dir must never see a
remote URL carrying a token. This module is a COPY AND ADAPTATION of
Run-Gate's sanitizer (``run_gate.py``: ``strip_url_userinfo``,
``sanitize_git_config_entries``, ``render_git_config``,
``_nested_git_config_files``, ``_MAX_GIT_OVERLAYS``), because ciu cannot import
run-gate at runtime. ``tests/tests/test_ciu_215_gitconfig_sanitize.py`` feeds
both implementations the same inputs, so a drift in either shows up as a
failing parity test.

Reading goes through ``git config --file <path> --list -z``: that form reads
exactly the one named file (no system/global config, no ``include``
following), so no environment hardening is needed here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import procutil

#: Bound on submodule and sibling-worktree config files that are sanitized.
MAX_GIT_OVERLAYS = 256

_URL_USERINFO_RE = re.compile(
    # No '.' in the scheme: a config KEY such as `url.https://tok@host/.insteadof`
    # must not be read as the scheme "url.https".
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+\-]*://)(?P<userinfo>[^/\s]*)@")
_WEB_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps"})
_DROP_GIT_CONFIG_KEY_RE = re.compile(
    # whole mail/IMAP sections carry credentials under many names
    r"^(credential\.|include\.|includeif\.|sendemail\.|imap\.)"
    r"|\.extraheader$"
    r"|\.cookiefile$"
    r"|^core\.(askpass|sshcommand)$"
    # a credential word as the SUFFIX of the last key segment
    r"|(pass|passwd|password|token|secret|apikey|api-key|api_key)$",
    re.IGNORECASE)
_URL_REWRITE_KEY_RE = re.compile(r"^url\..*\.(push)?insteadof$", re.IGNORECASE)
_GIT_DIR_BULK = frozenset({"objects", "refs", "logs", "hooks", "info"})


class GitConfigError(ValueError):
    """A git config file could not be read or sanitized safely."""


def strip_url_userinfo(text: str) -> str:
    """Remove embedded credentials from every URL inside *text*.

    http(s)/ftp userinfo is always a credential (a bare token is common); for
    other schemes only ``user:password@`` is, so ``ssh://git@host`` survives.
    """
    def repl(match: re.Match) -> str:
        # `git+https://`, `svn+http://`: any `<x>+http(s)` is a web scheme
        scheme = match.group("scheme")[:-3].lower().rsplit("+", 1)[-1]
        if scheme in _WEB_URL_SCHEMES or ":" in match.group("userinfo"):
            return match.group("scheme")
        return match.group(0)
    return _URL_USERINFO_RE.sub(repl, text)


def sanitize_git_config_entries(
        entries: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """The same config minus credentials (generic hygiene, no project
    knowledge): strips userinfo from every URL, drops credential helpers,
    ``http.*.extraheader``, password/token-like keys, ``include``s (they name
    host files) and ``url.*.insteadOf`` rewrites that embed credentials."""
    kept: list[tuple[str, str | None]] = []
    for key, value in entries:
        if _DROP_GIT_CONFIG_KEY_RE.search(key):
            continue
        clean_key = strip_url_userinfo(key)
        clean_value = None if value is None else strip_url_userinfo(value)
        if _URL_REWRITE_KEY_RE.match(key) and (
                clean_key != key or clean_value != value):
            continue
        kept.append((clean_key, clean_value))
    return kept


def _quote(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t"))


def render_git_config(entries: list[tuple[str, str | None]]) -> str:
    """Render ``git config --list -z`` style entries as a config file."""
    lines: list[str] = []
    current = None
    for key, value in entries:
        section, rest = key.split(".", 1)
        if "." in rest:
            subsection, name = rest.rsplit(".", 1)
        else:
            subsection, name = None, rest
        if (section, subsection) != current:
            current = (section, subsection)
            lines.append(f"[{section}]" if subsection is None
                         else f'[{section} "{_quote(subsection)}"]')
        lines.append(f"\t{name}" if value is None
                     else f'\t{name} = "{_quote(value)}"')
    return "\n".join(lines) + "\n"


def read_git_config_entries(path: Path) -> list[tuple[str, str | None]]:
    done = procutil.run_cmd(["git", "config", "--file", str(path), "--list", "-z"])
    if done.returncode != 0:
        detail = (done.stderr.strip().splitlines() or ["git failed"])[-1]
        raise GitConfigError(
            f"[CIU-215] cannot read git config {path} to sanitize it: {detail}")
    entries: list[tuple[str, str | None]] = []
    for item in done.stdout.split("\0"):
        if not item:
            continue
        key, sep, value = item.partition("\n")
        entries.append((key, value if sep else None))
    return entries


def sanitized_git_config_text(path: Path) -> str:
    return render_git_config(
        sanitize_git_config_entries(read_git_config_entries(path)))


def nested_git_dirs(modules: Path) -> list[Path]:
    """Every submodule git directory under ``<common>/modules`` (a directory
    holding ``HEAD``), by the same walk as :func:`nested_git_config_files`."""
    found: list[Path] = []
    for root, dirs, files in os.walk(modules):
        if "HEAD" in files:
            dirs[:] = [d for d in dirs if d not in _GIT_DIR_BULK]
            found.append(Path(root))
        dirs.sort()
    return found


def nested_git_config_files(modules: Path) -> list[Path]:
    """Every ``config`` / ``config.worktree`` anywhere under ``<common>/modules``
    (git names a submodule's dir by its PATH, so ``modules/libs/a/config`` and
    ``modules/libs/a/modules/b/config`` exist). The bulk dirs of a git dir
    (objects, refs, ...) are not descended into."""
    found: list[Path] = []
    for root, dirs, files in os.walk(modules):
        if "HEAD" in files:
            dirs[:] = [d for d in dirs if d not in _GIT_DIR_BULK]
        dirs.sort()
        found += [Path(root) / name for name in sorted(files)
                  if name in ("config", "config.worktree")]
    return found
