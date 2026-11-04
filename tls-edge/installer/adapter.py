#!/usr/bin/env python3
"""tls-edge installer adapter: prepare the stable CIU root <root>/stack/.

Run by get.py with the release venv's python BEFORE the ``current`` swap and before the
``stable_dir`` sync (SPEC S6.18), so it only creates what the sync does not own:

* ``<root>/stack/ciu-stack/ciu.toml.j2``  seeded ONCE from the wizard answers (the site
  override; later edits are the operator's and are never overwritten),
* ``<root>/stack/consumer.env``           (re)written every run: ``TLS_EDGE_DOMAIN=...``,
* ``<root>/stack/.git``                   ``git init`` + one empty commit, because ciu
  derives its identity from git and cannot run in a non-git root (ciu backlog CIU-163).

Everything under ``stack/`` that the bundle ships (``ciu-stack/*.j2``, ``.gitignore``, the
``ciu`` stub, ...) is written by the installer's own sync afterwards.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

STACK_DIR = "stack"
OVERRIDE = Path("ciu-stack") / "ciu.toml.j2"
CONSUMER_ENV = "consumer.env"
TLS_MODES = ("acme-tls", "acme-http", "acme-dns", "static", "dev")

_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_PROVIDER = re.compile(r"[a-z0-9]{2,40}")


def _release_root(raw: str) -> Path:
    release_root = Path(raw).resolve()
    if (
        release_root.name != "tree"
        or release_root.parent.parent.name != "releases"
        or len(release_root.parents) < 3
    ):
        raise ValueError(f"unexpected CMRU release tree path: {release_root}")
    return release_root


def _answers() -> dict:
    """The flat wizard answers; {} when none were collected (rollback)."""
    raw = os.environ.get("CMRU_INSTALLER_ANSWERS", "")
    if not raw or not Path(raw).is_file():
        return {}
    with open(raw, "rb") as stream:
        return tomllib.load(stream)


def _need(answers: dict, key: str, pattern: re.Pattern, what: str, *, required: bool) -> str:
    value = answers.get(key, "")
    if value in ("", None):
        if required:
            raise ValueError(f"answer {key!r} ({what}) is required")
        return ""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"answer {key!r} is not a valid {what}: {value!r}")
    return value


def seed_text(answers: dict) -> str:
    """The sparse site override rendered from the wizard answers (validated, quote-safe)."""
    domain = _need(answers, "domain", _DOMAIN, "domain name", required=True)
    email = _need(answers, "acme_email", _EMAIL, "email address", required=False)
    provider = _need(answers, "dns_provider", _PROVIDER, "DNS provider name", required=False)
    mode = answers.get("tls_mode", "acme-tls")
    if mode not in TLS_MODES:
        raise ValueError(f"answer 'tls_mode' must be one of {list(TLS_MODES)}: {mode!r}")
    calls = answers.get("calls", True)
    if not isinstance(calls, bool):
        raise ValueError("answer 'calls' must be true or false")

    lines = [
        "# tls-edge site override: seeded ONCE by the installer from your answers; this",
        "# file is yours (updates never rewrite it). Defaults live in ciu.defaults.toml.j2.",
        "[tls_edge]",
        f'domain = "{domain}"',
        "# Migration from a hand-made deployment (docs/INSTALL.md): adopt what exists.",
        "# ingress_external = true                  # reuse the existing ingress_public network",
        "",
        "[tls_edge.tls]",
        f'mode = "{mode}"',
        "",
        "[tls_edge.acme]",
        f'email = "{email}"',
        '# volume_name = "edge-proxy_acme-data"     # adopt an existing acme.json volume',
        "",
    ]
    if mode == "acme-dns":
        lines += [
            "[tls_edge.acme.dns]",
            f'provider = "{provider or "cloudflare"}"',
            '# wildcard_main = "example.test"          # one wildcard cert instead of per-host',
            "# The provider token is a CIU secret, never a literal here (docs/INSTALL.md):",
            "# [tls_edge.secrets]",
            '# dns_api_token = { directive = "ASK_EXTERNAL:CF_DNS_API_TOKEN", mode = "0444" }',
            "# [tls_edge.acme.dns.environment]",
            '# CF_DNS_API_TOKEN_FILE = "/run/secrets/dns_api_token"',
            "",
        ]
    lines += [
        "[tls_edge.calls]",
        f"enabled = {'true' if calls else 'false'}",
        "",
        "# [tls_edge.runtime]",
        '# cgroup_parent = "edge.slice"',
        "",
    ]
    return "\n".join(lines)


def _write_new(path: Path, data: str, mode: int) -> None:
    """Create `path` exclusively (never replaces an operator's file)."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _replace(path: Path, data: str, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    if os.path.lexists(tmp):
        tmp.unlink()
    _write_new(tmp, data, mode)
    os.replace(tmp, path)


def _ensure_git(stack: Path) -> None:
    """ciu needs a git repository with a commit (CIU-163); create it once."""
    if (stack / ".git").exists():
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "tls-edge-installer",
        "GIT_AUTHOR_EMAIL": "tls-edge-installer@localhost",
        "GIT_COMMITTER_NAME": "tls-edge-installer",
        "GIT_COMMITTER_EMAIL": "tls-edge-installer@localhost",
    })
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "commit", "-q", "--allow-empty", "-m", "tls-edge instance root (installer)"],
    ):
        result = subprocess.run(argv, cwd=stack, env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"{' '.join(argv)} failed: {(result.stdout + result.stderr).strip()}")


def prepare(instance_root: Path, answers: dict) -> None:
    stack = instance_root / STACK_DIR
    stack.mkdir(mode=0o755, exist_ok=True)
    if stack.is_symlink() or not stack.is_dir():
        raise FileExistsError(f"{stack} must be a plain directory")
    (stack / OVERRIDE.parent).mkdir(mode=0o755, exist_ok=True)

    override = stack / OVERRIDE
    if not os.path.lexists(override):
        _write_new(override, seed_text(answers), 0o644)
    if answers:
        domain = _need(answers, "domain", _DOMAIN, "domain name", required=True)
        _replace(stack / CONSUMER_ENV, f"TLS_EDGE_DOMAIN={domain}\n", 0o644)
    _ensure_git(stack)


def check(instance_root: Path) -> None:
    stack = instance_root / STACK_DIR
    for rel in (OVERRIDE, Path(CONSUMER_ENV), Path(".git"), Path("ciu"), Path(".gitignore")):
        if not os.path.lexists(stack / rel):
            raise RuntimeError(f"instance is incomplete: {stack / rel} is missing")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "apply", "health", "rollback"))
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)

    try:
        instance_root = _release_root(args.release_root).parents[2]
        if args.action == "health":
            check(instance_root)
        else:
            prepare(instance_root, _answers())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] tls-edge adapter {args.action}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
