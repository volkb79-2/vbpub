#!/usr/bin/env python3
"""Reusable CMRU command-library handlers.

A project opts in by putting one of these argv calls in its explicit required
``[steps.*]`` contract. They never synthesize a build or publication phase from
an artifact name. Invoke them through the installed module, for example:

    python3 -m cmru.handlers wheel-build --cwd .
    python3 -m cmru.handlers wheel-publish --prefix example --cwd .

The runner provides GITHUB_USERNAME, GITHUB_REPO, and GITHUB_PUSH_PAT from the
selected explicit credential contract. A direct caller must provide the same
environment; handlers do not read a convenience credentials file.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from cmru.release import (
    GitHubReleases,
    find_artifact,
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


def _wheel_glob(prefix: str) -> str:
    """Default wheel glob for a project prefix (PEP 503 dist-name normalisation)."""
    return f"{prefix.replace('-', '_')}-*.whl"


# ─── wheel commands ───────────────────────────────────────────────────────────
_WHEEL_BUILDER_IMAGE_ENV = "CMRU_WHEEL_BUILDER_IMAGE"
_DOCKER_CGROUP_PARENT_ENV = "CMRU_DOCKER_CGROUP_PARENT"


def _check_build_prerequisites() -> None:
    """Check the wheel-build path is usable before `cmd_wheel_build` invokes it.
    Exit 3 (PREREQ_MISSING) with an actionable message if not, rather than failing
    deep in a subprocess with a bare `No module named build` (direct mode) or a
    confusing docker error (container mode)."""
    from cmru import exit_codes

    if not (os.getenv(_WHEEL_BUILDER_IMAGE_ENV) or "").strip():
        print(
            f"[ERROR] ${_WHEEL_BUILDER_IMAGE_ENV} is required for wheel-build. "
            "Declare an immutable wheel-builder image in the project's cmru.toml [env]; "
            "CMRU refuses the non-reproducible local-Python build path.",
            file=sys.stderr,
        )
        raise SystemExit(exit_codes.PREREQ_MISSING)

    if shutil.which("docker") is None:
        print(
            f"[ERROR] ${_WHEEL_BUILDER_IMAGE_ENV} is set but docker is required "
            "and not found in PATH",
            file=sys.stderr,
        )
        raise SystemExit(exit_codes.PREREQ_MISSING)


def _docker_cgroup_parent() -> str:
    """Resolve the cgroup parent for a wheel-builder container.

    A wheel build is still a container workload. Refuse to let Docker place it
    in its ungoverned default when the caller has not supplied the estate's
    configured background tier.
    """
    parent = (
        os.getenv(_DOCKER_CGROUP_PARENT_ENV)
        or os.getenv("CGROUP_PARENT_DEV_BACKGROUND")
        or ""
    ).strip()
    if not parent:
        print(
            f"[ERROR] ${_DOCKER_CGROUP_PARENT_ENV} or "
            "$CGROUP_PARENT_DEV_BACKGROUND is required for wheel-build; "
            "refusing to launch an ungoverned Docker container",
            file=sys.stderr,
        )
        from cmru import exit_codes
        raise SystemExit(exit_codes.PREREQ_MISSING)
    return parent
def _host_bind_source(container_path: Path) -> str:
    """Resolve the real host-filesystem path backing a bind-mounted directory.

    A sibling `docker run` (e.g. the wheel-builder container) talks to the *host's*
    docker daemon (docker-outside-of-docker), so a `-v` source must be a host path —
    this container's own view (e.g. `/workspaces/vbpub`) may itself be a bind mount
    from a differently-named host directory. Reads `/proc/self/mountinfo` for the
    longest matching mount point and substitutes its root. A missing or
    unresolvable mapping is a configuration error: a sibling Docker daemon sees
    the host namespace, so guessing that the two paths are identical could mount
    an empty host directory and build the wrong source. On a real host the `/`
    mount is an explicit identity mapping, not a fallback. """
    path_str = str(container_path)
    best: Optional[tuple[str, str]] = None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(
            "Cannot resolve the host bind source: /proc/self/mountinfo is unavailable. "
            "CMRU refuses to guess a host path for a sibling Docker build."
        ) from exc
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        mount_root, mount_point = fields[3], fields[4]
        if path_str == mount_point or path_str.startswith(mount_point.rstrip("/") + "/"):
            if best is None or len(mount_point) > len(best[1]):
                best = (mount_root, mount_point)
    if best is None:
        raise RuntimeError(
            f"Cannot resolve host bind source for {container_path}; no matching mount exists."
        )
    mount_root, mount_point = best
    rel = path_str[len(mount_point):].lstrip("/")
    return f"{mount_root}/{rel}" if rel else mount_root


def _git_common_dir(cwd_parent: Path) -> Optional[Path]:
    """The actual git storage directory backing this checkout.

    For an ordinary checkout this is `<repo>/.git`, already covered by mounting
    `cwd_parent` alone. For a release worktree it lives OUTSIDE the worktree
    entirely — the worktree's own `.git` is just a file containing an absolute
    pointer there (`gitdir: <repo_root>/.git/worktrees/<name>`) — so a container
    with only the worktree bind-mounted cannot resolve the repository at all.
    `cmd_wheel_build` rejects that source tree before the builder is invoked:
    no static package version may stand in for Git-derived release evidence.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=str(cwd_parent),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    raw = Path(result.stdout.strip())
    return raw if raw.is_absolute() else (cwd_parent / raw).resolve()


def _wheel_builder_git_mount_args(
    source_dir: Path, *, mount_root: Optional[Path] = None,
) -> list[str]:
    """Extra `-v` args so the wheel-builder container can resolve git history.

    ``source_dir`` is the project whose version is being built. ``mount_root``
    is the directory the sibling container already receives; callers building
    from a parent directory set it explicitly. This distinction makes a copied
    one-project repository work just like an in-tree monorepo project. The
    extra mount is omitted (not just harmless-duplicate) when the common git
    dir is already inside ``mount_root``. A
    wheel build is source-derived evidence, so a non-Git directory is rejected
    instead of allowing setuptools-scm to fabricate its fallback version."""
    mount_root = mount_root or source_dir
    common_dir = _git_common_dir(source_dir)
    if common_dir is None:
        raise RuntimeError(
            f"Wheel build requires a Git worktree: cannot resolve git common directory for {source_dir}."
        )
    try:
        common_dir.relative_to(mount_root)
        return []  # already covered by the cwd_parent mount
    except ValueError:
        pass
    host_common_dir = _host_bind_source(common_dir)
    return ["-v", f"{host_common_dir}:{common_dir}"]


def cmd_wheel_build(args: argparse.Namespace) -> None:
    """Clean stale wheels + `python -m build --wheel --outdir dist` in the project."""
    _check_build_prerequisites()
    cwd = Path(args.cwd).resolve()
    if _git_common_dir(cwd) is None:
        raise RuntimeError(
            f"Wheel build requires a Git worktree: cannot resolve git common directory for {cwd}."
        )
    dist = cwd / "dist"
    if dist.exists():
        for stale in dist.glob("*.whl"):
            stale.unlink()
    print(f"[INFO] cmru handler: building wheel in {cwd}")

    # _check_build_prerequisites() has already rejected an absent image.  Read it
    # again rather than carrying ambient state in a module global: each command
    # invocation remains self-contained and a caller which mutates its environment
    # between the check and launch still fails loudly.
    image = (os.getenv(_WHEEL_BUILDER_IMAGE_ENV) or "").strip()
    if not image:
        raise RuntimeError(
            "wheel-builder image disappeared after prerequisite validation; refusing build"
        )
    cgroup_parent = _docker_cgroup_parent()
    # Run from the parent directory with the project dir as positional source;
    # the image's venv replaces the retired local-Python build path.
    host_parent = _host_bind_source(cwd.parent)
    subprocess.run(
        [
            "docker", "run", "--rm", "--cgroup-parent", cgroup_parent,
            "-v", f"{host_parent}:{cwd.parent}",
            *_wheel_builder_git_mount_args(cwd, mount_root=cwd.parent),
            "-w", str(cwd.parent),
            image,
            "/opt/wheel-builder-venv/bin/python", "-m", "build",
            "--wheel", "--outdir", str(dist), str(cwd),
        ],
        check=True,
    )


def cmd_wheel_publish(args: argparse.Namespace) -> None:
    """Find the built wheel, read its METADATA version, publish via the keystone."""
    cwd = Path(args.cwd).resolve()
    token = _require_env("GITHUB_PUSH_PAT")
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")

    wheel = find_built_wheel(cwd / "dist", args.glob or _wheel_glob(args.prefix))
    version = read_wheel_version(wheel)
    notes = (os.getenv(args.notes_env) if args.notes_env else None) or f"{args.prefix} {version}"

    # `--extra-asset` (repeatable, default none): additional files to attach to
    # the SAME release. `publish_versioned` has always accepted `extra_assets`;
    # this handler simply never exposed it, so a wheel project could not publish
    # a companion artifact without reimplementing the release call. assay needs
    # it for its zipapp and its hash-bound release manifest. Purely additive --
    # every existing project passes no such flag and is byte-for-byte unaffected.
    # Each value is a path OR a glob, because a companion artifact's filename
    # carries the version the release is being cut at (`assay-1.2.3.pyz`) and the
    # step declaring it cannot know that string. Zero matches is a hard error, not
    # a silent skip: publishing a release whose notes advertise a companion that
    # was never uploaded is worse than not publishing.
    extras: list[Path] = []
    for pattern in getattr(args, "extra_asset", None) or []:
        matched = sorted(Path(item).resolve() for item in glob.glob(pattern))
        files = [item for item in matched if item.is_file()]
        if not files:
            raise SystemExit(
                f"[ERROR] --extra-asset {pattern!r} matched no existing file"
            )
        extras.extend(files)

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh, prefix=args.prefix, version=version, asset_path=wheel,
        notes=notes, extra_assets=extras or None, latest_pointer=True,
    )
    print(f"[INFO] Published {args.prefix} {version}")
    for item in extras:
        print(f"[INFO] {args.prefix.upper()}_EXTRA_ASSET={item.name}")
    print(f"[INFO] {args.prefix.upper()}_WHEEL_SHA256={result['sha256']}")
    if result.get("asset_url"):
        print(f"[INFO] {args.prefix.upper()}_WHEEL_ASSET_URL={result['asset_url']}")


def cmd_wheel_validate(args: argparse.Namespace) -> None:
    """Assert the resolved latest <prefix>-v* release carries a wheel + .sha256."""
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")
    token = os.getenv("GITHUB_PUSH_PAT") or ""

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


def cmd_tarball_publish(args: argparse.Namespace) -> None:
    """Find the built tarball, read the version, publish via the keystone."""
    cwd = Path(args.cwd).resolve()
    token = _require_env("GITHUB_PUSH_PAT")
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")

    if args.version_file:
        version_path = cwd / args.version_file
        version = version_path.read_text(encoding="utf-8").strip()
    else:
        version = _require_env(args.version_env)

    art = find_artifact(cwd / "dist", args.glob)
    notes = (os.getenv(args.notes_env) if args.notes_env else None) or None

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh, prefix=args.prefix, version=version, asset_path=art,
        notes=notes, latest_pointer=True,
    )
    print(f"[INFO] Published {args.prefix} {version}")
    print(result)


def cmd_tarball_validate(args: argparse.Namespace) -> None:
    """Assert the resolved latest <prefix>-v* release carries a tarball + .sha256."""
    owner = _require_env("GITHUB_USERNAME")
    repo = _require_env("GITHUB_REPO")
    token = os.getenv("GITHUB_PUSH_PAT") or ""

    artifact_suffix = getattr(args, "artifact_suffix", None) or ".tar.xz"
    gh = GitHubReleases(owner, repo, token)
    info = validate_latest_release(gh, args.prefix, artifact_suffix=artifact_suffix)
    print(f"[INFO] {args.prefix} latest: {info['version']} "
          f"(resolved from highest {args.prefix}-v* release)")
    print(f"[INFO] {args.prefix.upper()}_TARBALL_NAME={info['asset']}")
    print(f"[INFO] {args.prefix.upper()}_TARBALL_LATEST_URL={info['url']}")
    if info.get("sha256_url"):
        print(f"[INFO] {args.prefix.upper()}_TARBALL_SHA256_URL={info['sha256_url']}")
        print(f"[INFO] Verify: curl -LO {info['url']} && curl -LO {info['sha256_url']} "
              f"&& sha256sum -c {info['asset']}.sha256")


# ─── OCI image commands ───────────────────────────────────────────────────────

_OCI_REPACK_DISABLED = (
    "cmru OCI repack is experimental and not production-ready; "
    "the path is disabled until its production-equivalence requirements are met"
)


def _reject_experimental_repack(repack: bool) -> None:
    """Fail closed before auth, Docker, or filesystem state can be mutated."""
    if not repack:
        return
    from cmru import exit_codes

    print(f"[ERROR] {_OCI_REPACK_DISABLED}", file=sys.stderr)
    raise SystemExit(exit_codes.CONFIG_ERROR)


def _check_prerequisites() -> None:
    """Check that required CLI tools are available. Exit 3 (PREREQ_MISSING) if not."""
    from cmru import exit_codes

    if shutil.which("docker") is None:
        print("[ERROR] docker is required but not found in PATH", file=sys.stderr)
        raise SystemExit(exit_codes.PREREQ_MISSING)

    # docker buildx is a docker CLI plugin; verify it responds.
    try:
        subprocess.run(
            ["docker", "buildx", "version"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[ERROR] docker buildx is required but not available", file=sys.stderr)
        raise SystemExit(exit_codes.PREREQ_MISSING)

def _docker_login() -> None:
    """Login to the container registry using GITHUB_USERNAME / GITHUB_PUSH_PAT / REGISTRY env."""
    registry = _require_env("REGISTRY")
    username = _require_env("GITHUB_USERNAME")
    token = _require_env("GITHUB_PUSH_PAT")
    print(f"[INFO] Logging into {registry} as {username}")
    subprocess.run(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input=f"{token}\n",
        text=True,
        check=True,
    )


def cmd_oci_image_build(args: argparse.Namespace) -> None:
    """Build an OCI image using docker buildx bake, with optional repack."""
    cwd = Path(args.cwd).resolve()
    bake_file = args.bake_file
    target = args.target
    repack = args.repack

    _reject_experimental_repack(repack)

    print(f"[INFO] cmru handler: building OCI image in {cwd}")
    print(f"[INFO]   bake_file={bake_file}  target={target}  repack={repack}")

    _check_prerequisites()
    _docker_login()

    subprocess.run(
        ["docker", "buildx", "bake", "-f", bake_file, target, "--load"],
        cwd=str(cwd), check=True,
    )

    print("[INFO] OCI image build complete")


def cmd_oci_image_push(args: argparse.Namespace) -> None:
    """Push an OCI image. Uses the OCI layout from the build step (repack mode)
    or runs ``docker buildx bake --push`` (non-repack mode)."""
    cwd = Path(args.cwd).resolve()
    bake_file = args.bake_file
    target = args.target

    _reject_experimental_repack(args.repack)

    print(f"[INFO] cmru handler: pushing OCI image in {cwd}")
    _docker_login()

    subprocess.run(
        ["docker", "buildx", "bake", "-f", bake_file, target, "--push"],
        cwd=str(cwd), check=True,
    )
    print("[INFO] OCI image push complete")


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cmru.handlers",
        description="cmru explicit project-step command library",
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
    p_pub.add_argument("--extra-asset", dest="extra_asset", action="append", default=[],
                       metavar="PATH",
                       help="additional file to attach to the same release; repeatable")
    p_pub.set_defaults(func=cmd_wheel_publish)

    p_val = sub.add_parser("wheel-validate", help="validate the resolved latest wheel release")
    p_val.add_argument("--prefix", required=True, help="release prefix, e.g. 'ciu' (no -v)")
    p_val.set_defaults(func=cmd_wheel_validate)

    p_tpub = sub.add_parser("tarball-publish", help="publish the built tarball to GitHub Releases")
    p_tpub.add_argument("--prefix", required=True, help="release prefix, e.g. 'tls-edge' (no -v)")
    p_tpub.add_argument("--cwd", required=True, help="project directory (dist/ holds the tarball)")
    p_tpub.add_argument("--glob", required=True, help="tarball glob, e.g. 'tls-edge-v*.tar.xz'")
    _tver = p_tpub.add_mutually_exclusive_group(required=True)
    _tver.add_argument("--version-file", dest="version_file",
                       help="path relative to --cwd holding the version string (e.g. VERSION)")
    _tver.add_argument("--version-env", dest="version_env",
                       help="env var holding the version string")
    p_tpub.add_argument("--notes-env", dest="notes_env",
                        help="env var holding release notes (optional)")
    p_tpub.set_defaults(func=cmd_tarball_publish)

    p_tval = sub.add_parser("tarball-validate", help="validate the resolved latest tarball release")
    p_tval.add_argument("--prefix", required=True, help="release prefix, e.g. 'tls-edge' (no -v)")
    p_tval.add_argument("--artifact-suffix", dest="artifact_suffix", default=".tar.xz",
                        help="expected artifact file extension (default: .tar.xz)")
    p_tval.set_defaults(func=cmd_tarball_validate)

    # ── oci-image subcommands ──────────────────────────────────────────────
    p_ocib = sub.add_parser("oci-image-build",
                            help="build OCI image with docker buildx bake (optional repack)")
    p_ocib.add_argument("--cwd", required=True, help="project directory (holds bake file)")
    p_ocib.add_argument("--bake-file", required=True, help="path to bake HCL file")
    p_ocib.add_argument("--target", required=True, help="bake target name")
    p_ocib.add_argument("--repack", action="store_true", help="enable OCI repack")
    p_ocib.add_argument("--repack-target-size", default="2GB",
                        help="target size per layer for repack (default: 2GB)")
    p_ocib.add_argument("--repack-compression", type=int, default=9,
                        help="compression level 1-22 for repack (default: 9)")
    p_ocib.set_defaults(func=cmd_oci_image_build)

    p_ocip = sub.add_parser("oci-image-push",
                            help="push OCI image to registry")
    p_ocip.add_argument("--cwd", required=True, help="project directory (holds bake file)")
    p_ocip.add_argument("--bake-file", required=True, help="path to bake HCL file")
    p_ocip.add_argument("--target", required=True, help="bake target name")
    p_ocip.add_argument("--repack", action="store_true",
                        help="repack mode (push already done in build step)")
    p_ocip.set_defaults(func=cmd_oci_image_push)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
