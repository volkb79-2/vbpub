#!/usr/bin/env python3
"""Built-in profile step handlers for cmru (S-REL "batteries-included" profiles).

These implement the *default* build / publish / validate behaviour for standard
artifact profiles, so a project can declare ``artifacts = ["wheel"]`` and OMIT the
matching ``[steps.*]`` entirely — cmru runs these instead of a per-project script.

They are invoked as subprocess steps by the orchestrator, by absolute path:

    <python> <…>/cmru/handlers.py wheel-build   --cwd <project-dir>
    <python> <…>/cmru/handlers.py wheel-publish --prefix <bare> --cwd <project-dir> [--notes-env VAR]
    <python> <…>/cmru/handlers.py wheel-validate --prefix <bare>

The orchestrator exports GITHUB_USERNAME / GITHUB_REPO / GITHUB_PUSH_PAT into the
environment before running steps (see cli.apply_release_env, SPEC S2.4); these handlers
read that same contract — identical to the hand-written scripts they replace.

Any explicit ``[project.X.steps.<step>]`` overrides the built-in — the escape hatch for
projects with non-standard needs (multi-wheel, bespoke validation, extra assets).

Stdlib only. Works whether cmru is pip-installed or run from a checkout (the sys.path
fallback below makes ``import cmru.release`` resolve when invoked by file path).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from cmru.release import (
        GitHubReleases,
        find_built_wheel,
        publish_versioned,
        read_wheel_version,
        validate_latest_release,
    )
except ModuleNotFoundError:  # invoked by file path from a checkout without install
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cmru.release import (  # noqa: E402
        GitHubReleases,
        find_built_wheel,
        publish_versioned,
        read_wheel_version,
        validate_latest_release,
    )


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        print(f"[ERROR] {name} is required", file=sys.stderr)
        raise SystemExit(1)
    return value


def _load_env_file(path: str | None) -> None:
    """Optional standalone convenience: seed GITHUB_* etc. from a KEY=VALUE .env file.

    ``setdefault`` semantics — a value already in the environment WINS (SPEC S2.4),
    so this only fills gaps when the script is run outside cmru orchestration. No-op if
    the path is unset or absent."""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _wheel_glob(prefix: str) -> str:
    """Default wheel glob for a project prefix (PEP 503 dist-name normalisation)."""
    return f"{prefix.replace('-', '_')}-*.whl"


# ─── wheel profile ────────────────────────────────────────────────────────────
def cmd_wheel_build(args: argparse.Namespace) -> None:
    """Clean stale wheels + `python -m build --wheel --outdir dist` in the project."""
    cwd = Path(args.cwd).resolve()
    dist = cwd / "dist"
    if dist.exists():
        for stale in dist.glob("*.whl"):
            stale.unlink()
    print(f"[INFO] cmru built-in: building wheel in {cwd}")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", "dist"],
        cwd=str(cwd), check=True,
    )


def cmd_wheel_publish(args: argparse.Namespace) -> None:
    """Find the built wheel, read its METADATA version, publish via the keystone."""
    _load_env_file(getattr(args, "env_file", None))
    cwd = Path(args.cwd).resolve()
    token = _require_env("GITHUB_PUSH_PAT")
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")

    wheel = find_built_wheel(cwd / "dist", args.glob or _wheel_glob(args.prefix))
    version = read_wheel_version(wheel)
    notes = (os.getenv(args.notes_env) if args.notes_env else None) or f"{args.prefix} {version}"

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh, prefix=args.prefix, version=version, asset_path=wheel,
        notes=notes, latest_pointer=True,
    )
    print(f"[INFO] Published {args.prefix} {version}")
    print(f"[INFO] {args.prefix.upper()}_WHEEL_SHA256={result['sha256']}")
    if result.get("asset_url"):
        print(f"[INFO] {args.prefix.upper()}_WHEEL_ASSET_URL={result['asset_url']}")


def cmd_wheel_validate(args: argparse.Namespace) -> None:
    """Assert the resolved latest <prefix>-v* release carries a wheel + .sha256."""
    _load_env_file(getattr(args, "env_file", None))
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_PUSH_PAT") or ""

    gh = GitHubReleases(owner, repo, token)
    info = validate_latest_release(gh, args.prefix, artifact_suffix=".whl")
    print(f"[INFO] {args.prefix} latest: {info['version']} "
          f"(resolved from highest {args.prefix}-v* release)")
    print(f"[INFO] {args.prefix.upper()}_WHEEL_NAME={info['asset']}")
    print(f"[INFO] {args.prefix.upper()}_WHEEL_LATEST_URL={info['url']}")
    if info.get("sha256_url"):
        print(f"[INFO] {args.prefix.upper()}_WHEEL_SHA256_URL={info['sha256_url']}")
        print(f"[INFO] Verify: curl -LO {info['url']} && curl -LO {info['sha256_url']} "
              f"&& sha256sum -c {info['asset']}.sha256")


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cmru.handlers",
        description="cmru built-in profile step handlers (invoked by the orchestrator).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("wheel-build", help="build the project's wheel into dist/")
    p_build.add_argument("--cwd", required=True, help="project directory (holds pyproject.toml)")
    p_build.set_defaults(func=cmd_wheel_build)

    p_pub = sub.add_parser("wheel-publish", help="publish the built wheel to GitHub Releases")
    p_pub.add_argument("--prefix", required=True, help="release prefix, e.g. 'ciu' (no -v)")
    p_pub.add_argument("--cwd", required=True, help="project directory (dist/ holds the wheel)")
    p_pub.add_argument("--glob", help="wheel glob (default: <prefix>-*.whl)")
    p_pub.add_argument("--notes-env", dest="notes_env",
                       help="env var holding release notes (default notes: '<prefix> <version>')")
    p_pub.add_argument("--env-file", dest="env_file",
                       help="optional .env to seed GITHUB_* when run standalone (env wins)")
    p_pub.set_defaults(func=cmd_wheel_publish)

    p_val = sub.add_parser("wheel-validate", help="validate the resolved latest wheel release")
    p_val.add_argument("--prefix", required=True, help="release prefix, e.g. 'ciu' (no -v)")
    p_val.add_argument("--env-file", dest="env_file",
                       help="optional .env to seed GITHUB_* when run standalone (env wins)")
    p_val.set_defaults(func=cmd_wheel_validate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
