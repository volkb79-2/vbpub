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
import fnmatch
import glob
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
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


def _build_output_record(args: argparse.Namespace) -> Optional[dict]:
    raw_root = os.environ.get("CMRU_BUILD_OUTPUT_ROOT")
    if not raw_root:
        return None
    output_id = os.environ.get("CMRU_BUILD_OUTPUT_ID", "")
    project_name = os.environ.get("CMRU_BUILD_OUTPUT_PROJECT", "")
    if not output_id or not project_name:
        raise RuntimeError("CMRU build-output context is incomplete")
    artifact_root = Path(raw_root)
    if artifact_root.name != output_id:
        raise RuntimeError("CMRU_BUILD_OUTPUT_ROOT does not match CMRU_BUILD_OUTPUT_ID")
    from cmru.transaction import validate_build_output_tree
    record = validate_build_output_tree(artifact_root, project_name, output_id)
    manifest = record["manifest"]
    if os.environ.get("CMRU_BUILD_SOURCE_COMMIT") != manifest["source_commit"]:
        raise RuntimeError("CMRU_BUILD_SOURCE_COMMIT does not match the retained build manifest")
    if os.environ.get("CMRU_BUILD_SOURCE_DATE") != manifest["source_commit_date"]:
        raise RuntimeError("CMRU_BUILD_SOURCE_DATE does not match the retained build manifest")
    return record


def _build_output_files(
    args: argparse.Namespace, pattern: str, *, record: Optional[dict] = None,
) -> list[Path]:
    record = record if record is not None else _build_output_record(args)
    if record is None:
        raise RuntimeError("CMRU build-output context is not active")
    relative_pattern = Path(pattern)
    if relative_pattern.is_absolute() or ".." in relative_pattern.parts:
        raise RuntimeError(f"build-output asset selector must stay inside its record: {pattern!r}")
    raw_manifest = record["manifest"]
    matches: list[Path] = []
    for artifact in raw_manifest["artifacts"]:
        directory = artifact["directory"]
        for entry in artifact["files"]:
            coordinate = Path(directory) / entry["path"]
            selected = (
                fnmatch.fnmatchcase(coordinate.as_posix(), pattern)
                if "/" in pattern
                else fnmatch.fnmatchcase(coordinate.name, pattern)
            )
            if selected:
                matches.append(record["artifact_root"] / coordinate)
    return sorted(matches)


def _publish_versioned_artifacts(
    gh: GitHubReleases,
    *,
    prefix: str,
    version: str,
    asset_path: Path,
    notes: Optional[str],
    extra_assets: Optional[list[Path]],
    build_output: Optional[dict],
) -> dict:
    """Publish selected files, isolating retained records from publisher byproducts."""
    if build_output is None:
        return publish_versioned(
            gh, prefix=prefix, version=version, asset_path=asset_path,
            notes=notes, extra_assets=extra_assets, latest_pointer=True,
        )

    # The keystone writes checksum sidecars and latest.json beside its inputs.
    # Stage exact copies so publishing cannot mutate the immutable build record
    # or make its own digest inventory fail on a later retry.
    with tempfile.TemporaryDirectory(prefix="cmru-build-publish-") as temporary:
        staging = Path(temporary)
        staged_asset = staging / "asset" / asset_path.name
        staged_asset.parent.mkdir()
        shutil.copy2(asset_path, staged_asset)
        _verify_staged_build_output_file(build_output, asset_path, staged_asset)
        staged_extras: list[Path] = []
        for index, extra in enumerate(extra_assets or []):
            staged_extra = staging / f"extra-{index}" / extra.name
            staged_extra.parent.mkdir()
            shutil.copy2(extra, staged_extra)
            _verify_staged_build_output_file(build_output, extra, staged_extra)
            staged_extras.append(staged_extra)

        return publish_versioned(
            gh, prefix=prefix, version=version, asset_path=staged_asset,
            notes=notes, extra_assets=staged_extras or None, latest_pointer=True,
            require_existing_targets=True, latest_pointer_recreate=False,
            expected_tag_commit=build_output["manifest"]["source_commit"],
        )


def _verify_staged_build_output_file(
    record: dict, source_path: Path, staged_path: Path,
) -> None:
    """Bind a staged upload to the validated manifest, closing copy-time races."""
    artifact_root = Path(record["artifact_root"]).absolute()
    try:
        coordinate = source_path.absolute().relative_to(artifact_root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            f"retained publisher input is outside its build record: {source_path}"
        ) from exc
    expected = None
    for artifact in record["manifest"]["artifacts"]:
        directory = artifact["directory"]
        for entry in artifact["files"]:
            if f"{directory}/{entry['path']}" == coordinate:
                expected = (entry["sha256"], int(entry["bytes"]))
                break
        if expected is not None:
            break
    if expected is None:
        raise RuntimeError(
            f"retained publisher input is not present in build.json: {source_path}"
        )

    metadata = staged_path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"staged retained artifact is not a regular file: {staged_path}")
    digest = hashlib.sha256()
    with staged_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = (digest.hexdigest(), metadata.st_size)
    if actual != expected:
        raise RuntimeError(
            f"staged retained artifact differs from build.json: {source_path}"
        )


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
            # ``>=``: on equal-length mount points the LAST mountinfo entry is
            # the visible one (an earlier entry at the same point is shadowed).
            if best is None or len(mount_point) >= len(best[1]):
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
    from cmru.transaction import _common_git_dir, _shared_worktree

    shared = _shared_worktree()
    try:
        return _common_git_dir(cwd_parent)
    except shared.WorkspaceError:
        return None


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


def _git_toplevel(cwd: Path) -> Optional[Path]:
    """The worktree root containing ``cwd`` (``git rev-parse --show-toplevel``)."""
    from cmru.transaction import _shared_worktree

    shared = _shared_worktree()
    try:
        return Path(shared.discover_git_root(cwd)[0])
    except shared.WorkspaceError:
        return None


def _wheel_builder_mount_root(cwd: Path) -> Path:
    """Directory bind-mounted into the wheel-builder container.

    Normally ``cwd.parent`` (``-w`` must exist). A project nested deeper than
    one level (``libraries/pkg`` with ``root = "../.."``) needs the whole
    worktree root so git discovery and setuptools-scm see the repository; the
    top-level is mounted whenever it contains ``cwd.parent``."""
    top = _git_toplevel(cwd)
    if top is None or top == cwd:
        return cwd.parent
    try:
        cwd.parent.relative_to(top)
    except ValueError:
        return cwd.parent
    return top


def _wheel_builder_env_args() -> list[str]:
    """Name-only ``-e`` forwards for reproducible-build and pretend-version vars."""
    names = sorted(
        name for name in os.environ
        if name == "SOURCE_DATE_EPOCH" or name.startswith("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_")
    )
    args: list[str] = []
    for name in names:
        args += ["-e", name]
    return args


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
    mount_root = _wheel_builder_mount_root(cwd)
    host_mount = _host_bind_source(mount_root)
    subprocess.run(
        [
            "docker", "run", "--rm", "--cgroup-parent", cgroup_parent,
            "-v", f"{host_mount}:{mount_root}",
            *_wheel_builder_git_mount_args(cwd, mount_root=mount_root),
            *_wheel_builder_env_args(),
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

    pattern = args.glob or _wheel_glob(args.prefix)
    build_output = _build_output_record(args)
    if build_output is not None:
        matches = _build_output_files(args, pattern, record=build_output)
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one retained wheel matching {pattern!r}, found {len(matches)}"
            )
        wheel = matches[0]
    else:
        wheel = find_built_wheel(cwd / "dist", pattern)
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
        if os.environ.get("CMRU_BUILD_OUTPUT_ROOT"):
            files = _build_output_files(args, pattern, record=build_output)
        else:
            matched = sorted(Path(item).resolve() for item in glob.glob(pattern))
            files = [item for item in matched if item.is_file()]
        if not files:
            raise SystemExit(
                f"--extra-asset {pattern!r} matched no existing file"
            )
        extras.extend(files)

    gh = GitHubReleases(owner, repo, token)
    result = _publish_versioned_artifacts(
        gh, prefix=args.prefix, version=version, asset_path=wheel,
        notes=notes, extra_assets=extras or None, build_output=build_output,
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
    build_output = _build_output_record(args)

    if args.version_file and build_output is not None:
        matches = _build_output_files(args, args.version_file, record=build_output)
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one retained version file {args.version_file!r}, found {len(matches)}"
            )
        version_path = matches[0]
        version = version_path.read_text(encoding="utf-8").strip()
    elif args.version_file:
        version_path = cwd / args.version_file
        version = version_path.read_text(encoding="utf-8").strip()
    else:
        version = _require_env(args.version_env)

    if build_output is not None:
        matches = _build_output_files(args, args.glob, record=build_output)
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one retained tarball matching {args.glob!r}, found {len(matches)}"
            )
        art = matches[0]
    else:
        art = find_artifact(cwd / "dist", args.glob)
    notes = (os.getenv(args.notes_env) if args.notes_env else None) or None

    gh = GitHubReleases(owner, repo, token)
    result = _publish_versioned_artifacts(
        gh, prefix=args.prefix, version=version, asset_path=art,
        notes=notes, extra_assets=None, build_output=build_output,
    )
    print(f"[INFO] Published {args.prefix} {version}")
    print(result)


def cmd_bundle_manifest(args: argparse.Namespace) -> None:
    """Write the installer manifest (`files` = sha256/size/mode of every regular file) into
    a staged bundle directory, before it is tarred. The hardened get.py refuses bundles
    whose members the manifest does not list, so every tarball project needs this step."""
    from cmru import exit_codes
    from cmru.manifest import (
        bundle_tag_problem, build_bundle_manifest, manifest_sha256, write_manifest,
    )

    problem = bundle_tag_problem(args.tag)
    if problem:
        print(f"[ERROR] {problem}", file=sys.stderr)
        raise SystemExit(exit_codes.CONFIG_ERROR)
    root = Path(args.root).resolve()
    name = args.manifest_name  # the file name inside `root` (not the project --name)
    try:
        manifest = build_bundle_manifest(
            project=args.name, tag=args.tag, bundle_root=root,
            exclude=(name, name + ".minisig"),
        )
    except ValueError as exc:
        print(f"[ERROR] bundle-manifest: {exc}", file=sys.stderr)
        raise SystemExit(exit_codes.FAILURE) from None
    except RuntimeError as exc:
        # SOURCE_DATE_EPOCH unset: a missing prerequisite, not a crash (found by the
        # W2-PKG5 surface review: it used to escape as a traceback).
        print(f"[ERROR] bundle-manifest: {exc}", file=sys.stderr)
        raise SystemExit(exit_codes.PREREQ_MISSING) from None
    out = write_manifest(manifest, root / name)
    print(f"[INFO] Wrote {out} ({len(manifest['files'])} files, "
          f"sha256 {manifest_sha256(out)})")


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

# OCI repack (`--repack`) was removed from the handler grammar: it always
# failed closed while KI-02 is open. The option returns when KI-02 is fixed.

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
    """Build an OCI image using docker buildx bake."""
    cwd = Path(args.cwd).resolve()
    bake_file = args.bake_file
    target = args.bake_target

    print(f"[INFO] cmru handler: building OCI image in {cwd}")
    print(f"[INFO]   bake_file={bake_file}  target={target}")

    _check_prerequisites()
    _docker_login()

    subprocess.run(
        ["docker", "buildx", "bake", "-f", bake_file, target, "--load"],
        cwd=str(cwd), check=True,
    )

    print("[INFO] OCI image build complete")


def cmd_oci_image_push(args: argparse.Namespace) -> None:
    """Push an OCI image with ``docker buildx bake --push``."""
    cwd = Path(args.cwd).resolve()
    bake_file = args.bake_file
    target = args.bake_target

    print(f"[INFO] cmru handler: pushing OCI image in {cwd}")
    _docker_login()

    subprocess.run(
        ["docker", "buildx", "bake", "-f", bake_file, target, "--push"],
        cwd=str(cwd), check=True,
    )
    print("[INFO] OCI image push complete")


def handlers_cli():
    from cli_extended import OptionSpec, VerbGroup, VerbSpec
    from cmru.cli_support import cmru_registry

    registry = cmru_registry("cmru handler", "Explicit project-step command library.")
    required_path = lambda flag, desc: OptionSpec(
        (flag,), desc, metavar="PATH", parser_kwargs={"required": True},
    )
    required_name = lambda flag, desc, metavar="NAME": OptionSpec(
        (flag,), desc, metavar=metavar, parser_kwargs={"required": True},
    )
    commands = (
        ("wheel-build", "Build the project's wheel into dist/.", cmd_wheel_build, (
            required_path("--cwd", "project directory (holds pyproject.toml)"),
        )),
        ("wheel-publish", "Publish the built wheel to GitHub Releases.", cmd_wheel_publish, (
            required_name("--prefix", "release prefix without -v", "PREFIX"),
            required_path("--cwd", "project directory (dist/ holds the wheel)"),
            OptionSpec(("--glob",), "wheel glob (default: <prefix>-*.whl)", metavar="GLOB", parser_kwargs={"default": None}),
            OptionSpec(("--notes-env",), "environment variable holding release notes", metavar="NAME", parser_kwargs={"dest": "notes_env", "default": None}),
            OptionSpec(("--extra-asset",), "additional file to attach; repeatable", metavar="PATH", parser_kwargs={"action": "append", "default": []}),
        )),
        ("wheel-validate", "Validate the resolved latest wheel release.", cmd_wheel_validate, (
            required_name("--prefix", "release prefix without -v", "PREFIX"),
        )),
        ("tarball-publish", "Publish the built tarball to GitHub Releases.", cmd_tarball_publish, (
            required_name("--prefix", "release prefix without -v", "PREFIX"),
            required_path("--cwd", "project directory (dist/ holds the tarball)"),
            required_name("--glob", "tarball glob", "GLOB"),
            OptionSpec(("--version-file",), "version file relative to --cwd", metavar="PATH", parser_kwargs={"dest": "version_file", "default": None}, mutually_exclusive_group="version-source", mutually_exclusive_required=True),
            OptionSpec(("--version-env",), "environment variable containing the version", metavar="NAME", parser_kwargs={"dest": "version_env", "default": None}, mutually_exclusive_group="version-source", mutually_exclusive_required=True),
            OptionSpec(("--notes-env",), "environment variable holding optional release notes", metavar="NAME", parser_kwargs={"dest": "notes_env", "default": None}),
        )),
        ("bundle-manifest", "Write the installer manifest (files + sha256) into a staged bundle dir.", cmd_bundle_manifest, (
            required_name("--name", "project name (as in cmru.toml)", "NAME"),
            required_name("--tag", "full release tag, e.g. tls-edge-v1.2.3", "TAG"),
            required_path("--root", "the staged bundle directory (the tarball's top-level dir)"),
            OptionSpec(("--manifest-name",), "manifest file name (default manifest.json)", metavar="NAME", parser_kwargs={"dest": "manifest_name", "default": "manifest.json"}),
        )),
        ("tarball-validate", "Validate the resolved latest tarball release.", cmd_tarball_validate, (
            required_name("--prefix", "release prefix without -v", "PREFIX"),
            OptionSpec(("--artifact-suffix",), "expected artifact file extension", metavar="SUFFIX", parser_kwargs={"dest": "artifact_suffix", "default": ".tar.xz"}),
        )),
        ("oci-image-build", "Build an OCI image with docker buildx bake.", cmd_oci_image_build, (
            required_path("--cwd", "project directory (holds bake file)"),
            required_path("--bake-file", "path to bake HCL file"),
            required_name("--bake-target", "bake target name (not a project target)", "NAME"),
        )),
        ("oci-image-push", "Push an OCI image to its registry.", cmd_oci_image_push, (
            required_path("--cwd", "project directory (holds bake file)"),
            required_path("--bake-file", "path to bake HCL file"),
            required_name("--bake-target", "bake target name (not a project target)", "NAME"),
        )),
    )
    for name, description, handler, options in commands:
        mutating = name not in {"wheel-validate", "tarball-validate"}

        def dispatch(args, runtime, fn=handler, command=name):
            if runtime.dry_run:
                details = {
                    key: value for key, value in vars(args).items()
                    if key != "dry_run" and "token" not in key.lower()
                }
                print(f"[DRY RUN] Would run cmru handler {command} with {details}")
                return None
            return fn(args)

        registry.register(VerbSpec(
            name,
            description=description,
            group=(
                VerbGroup.MODIFICATION.value
                if mutating else VerbGroup.EXPLORATION.value
            ),
            mutating=mutating,
            dry_run=mutating,
            include_confirmation=False,
            options=options,
            include_json=False,
            include_progress=False,
            handler=dispatch,
        ))
    return registry.build()


def main(argv: list | None = None) -> int:
    """``python -m cmru.handlers`` entry; the SAME builder as ``cmru handler``."""
    from cli_extended import VersionLookupError

    from cmru import exit_codes

    try:
        cli = handlers_cli()
    except VersionLookupError:
        print(
            "cmru is not installed as a distribution; install the wheel (see README)",
            file=sys.stderr,
        )
        return exit_codes.PREREQ_MISSING
    return cli.run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
