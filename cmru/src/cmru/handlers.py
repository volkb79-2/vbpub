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

from cmru import exit_codes
from cmru.release import (
    GitHubReleases,
    find_artifact,
    find_built_wheel,
    is_release_version,
    publish_versioned,
    read_wheel_version,
    validate_latest_release,
)
from cmru.release_notes import (
    NO_NOTES,
    ReleaseNotes,
    ReleaseNotesError,
    declared_source,
    load_release_notes,
)


def _notes_kwargs(release_notes: Optional[ReleaseNotes]) -> dict:
    """A project without curated notes publishes through the unchanged call shape."""
    return {} if release_notes is None else {"release_notes": release_notes}


def _curated_release_notes(
    cwd: Path, prefix: str, version: str, build_output: Optional[dict],
) -> Optional[ReleaseNotes]:
    """Resolve the curated section a release body must carry, before any network call.

    A release version REQUIRES its exact section when the project declares a
    changelog; a refusal here happens before authentication or upload, so a
    missing or malformed section can never publish a notes-less body. The text
    is read from the commit the artifact was built from (the tagged candidate
    or retained build source). An explicit ``publish --from-checkout`` resolves
    that source from the matching local release tag instead of mutable files.
    """
    if not is_release_version(version):
        return None  # a dev build publishes no immutable release body
    try:
        declaration = declared_source()
        if declaration == NO_NOTES:
            return None
        commit = os.environ.get("CMRU_RELEASE_COMMIT") or (
            build_output["manifest"]["source_commit"] if build_output else None
        )
        expected_tag = f"{prefix}-v{version}"
        declared_tag = os.environ.get("CMRU_RELEASE_TAG")
        if declared_tag and declared_tag != expected_tag:
            raise ReleaseNotesError(
                f"CMRU_RELEASE_TAG={declared_tag!r} does not match {expected_tag!r}"
            )
        if commit is None:
            tagged = subprocess.run(
                ["git", "rev-parse", f"{expected_tag}^{{commit}}"],
                cwd=cwd, capture_output=True, text=True, check=False,
            )
            if tagged.returncode:
                detail = tagged.stderr.strip() or "tag is absent from this checkout"
                raise ReleaseNotesError(
                    f"cannot bind release notes to {expected_tag}: {detail}"
                )
            commit = tagged.stdout.strip()
            # The notes are about to be bound to a LOCAL tag. Prove it is the
            # published tag, not a same-named hand-made one.
            _require_local_tag_is_published_tag(cwd, expected_tag)
        return load_release_notes(cwd, declaration, version, commit=commit)
    except ReleaseNotesError as exc:
        print(f"[ERROR] release notes: {exc}", file=sys.stderr)
        raise SystemExit(exit_codes.REFUSED) from None


def _require_local_tag_is_published_tag(cwd: Path, tag: str) -> None:
    """Refuse unless origin's *tag* points at the very commit the local one does.

    ``publish --from-checkout`` resolves release prose from a local tag, so a
    local tag that merely shares the published name (moved, re-created, or
    never pushed) would put another commit's notes into an immutable release
    body. This reuses CMRU's exact origin comparison
    (:func:`cmru.version._tag_pushed_to_origin`: one ``ls-remote`` over the
    direct and peeled refs, so annotated and lightweight tags both compare by
    the commit they target) with the same repository credential the publish
    itself uses. A tag absent from origin, a disagreement, and an origin that
    cannot be queried are three distinct refusals; none is read as "nothing
    published yet".
    """
    from cmru.git_auth import GitHubGitAuth
    from cmru.version import _tag_pushed_to_origin

    auth = GitHubGitAuth(
        owner=_require_env("GITHUB_USERNAME"),
        repo=_require_env("GITHUB_REPO"),
        token=_require_env("GITHUB_PUSH_PAT"),
    )
    try:
        published = _tag_pushed_to_origin(cwd, tag, git_auth=auth)
    except (RuntimeError, OSError) as exc:
        raise ReleaseNotesError(
            f"cannot bind release notes to {tag}: the local tag could not be "
            f"verified as the published one: {exc}"
        ) from exc
    if not published:
        raise ReleaseNotesError(
            f"cannot bind release notes to {tag}: origin has no {tag!r} tag, so the "
            "local tag is not the published release tag; push the reviewed tag first"
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
    release_notes: Optional[ReleaseNotes] = None,
) -> dict:
    """Publish selected files, isolating retained records from publisher byproducts."""
    # A project that declares no curated notes publishes exactly the generated
    # body, so the keystone is called without the argument.
    curated = _notes_kwargs(release_notes)
    if build_output is None:
        return publish_versioned(
            gh, prefix=prefix, version=version, asset_path=asset_path,
            notes=notes, extra_assets=extra_assets, latest_pointer=True,
            **curated,
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
            **curated,
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


def _configured_wheel_builder_image(cwd: Path) -> str:
    """Read the wheel-builder image from CMRU config enclosing *cwd*."""
    return _configured_env_value(cwd, _WHEEL_BUILDER_IMAGE_ENV)


def _configured_env_value(cwd: Path, name: str) -> str:
    """Read one ``[env]`` value from the CMRU config enclosing *cwd*.

    A direct handler invocation does not run through the release-step
    environment injector. For registered estate projects, the nearest
    orchestration document is authoritative; for a standalone project, its
    own ``cmru.toml`` is authoritative. No value is invented here.
    """
    from cmru.config import (
        ORCHESTRATION_CONFIG_FILENAME,
        PROJECT_CONFIG_FILENAME,
        load_forge_config,
    )

    ancestors = (cwd, *cwd.parents)
    orchestration_path = next(
        (directory / ORCHESTRATION_CONFIG_FILENAME for directory in ancestors
         if (directory / ORCHESTRATION_CONFIG_FILENAME).exists()),
        None,
    )
    project_path = next(
        (directory / PROJECT_CONFIG_FILENAME for directory in ancestors
         if (directory / PROJECT_CONFIG_FILENAME).exists()),
        None,
    )
    config_path = orchestration_path or project_path
    if config_path is None:
        return ""

    forge = load_forge_config(config_path)
    values = dict(forge.env)
    if forge.orchestration is None:
        raise RuntimeError(
            f"{config_path}: strict CMRU config loader returned no project registry"
        )
    matches: list[tuple[int, str]] = []
    for name, path in forge.orchestration.project_configs.items():
        project_root = path.resolve().parent
        try:
            cwd.relative_to(project_root)
        except ValueError:
            continue
        matches.append((len(project_root.parts), name))
    if not matches:
        raise ValueError(
            f"{cwd} is not inside a project registered by {config_path}"
        )
    project_name = max(matches, key=lambda item: item[0])[1]
    values.update(forge.projects[project_name].env)

    return str(values.get(name, "") or "").strip()


def _resolve_wheel_builder_image(cwd: Path | None = None) -> str:
    if _WHEEL_BUILDER_IMAGE_ENV in os.environ:
        return os.environ[_WHEEL_BUILDER_IMAGE_ENV].strip()
    return _configured_wheel_builder_image(cwd) if cwd is not None else ""


def _check_build_prerequisites(cwd: Path | None = None) -> str:
    """Check the wheel-build path is usable before `cmd_wheel_build` invokes it.
    Exit 3 (PREREQ_MISSING) with an actionable message if not, rather than failing
    deep in a subprocess with a bare `No module named build` (direct mode) or a
    confusing docker error (container mode)."""
    from cmru import exit_codes

    image = _resolve_wheel_builder_image(cwd)
    if not image:
        print(
            f"[ERROR] ${_WHEEL_BUILDER_IMAGE_ENV} is required for wheel-build. "
            "Set it explicitly or declare it in the enclosing CMRU configuration; "
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
    return image


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
        raise SystemExit(exit_codes.PREREQ_MISSING)
    return parent


def _declared_input(cwd: Path, name: str) -> str:
    """An explicit environment value wins; otherwise the enclosing CMRU config."""
    if name in os.environ:
        return os.environ[name].strip()
    return _configured_env_value(cwd, name)


def require_installed_cgroup_parent(cgroup_parent: str, cwd: Path) -> None:
    """Prove *cgroup_parent* is a real, configured host systemd slice, or refuse.

    systemd auto-vivifies a ``.slice`` for ANY name, so Docker given a typo'd
    ``--cgroup-parent`` starts the container normally in an unlimited
    transient slice next to production. The check therefore runs BEFORE the
    wheel-builder ``docker run`` (and before the bootstrap script's Buildx),
    through the same host-side probe contract ``tester-gate`` uses
    (:func:`cmru.tester_gate.check_slice_unit`: ``LoadState=loaded`` and a
    non-empty ``FragmentPath``, read from the Docker HOST's systemd via a
    pinned privileged ``nsenter`` container; never a local ``systemctl`` from a
    cockpit that has no view of the host's systemd).

    The probe container is placed in the declared gates tier
    (``CMRU_TESTER_CGROUP_PARENT``); the probe image is the declared
    digest-pinned ``CMRU_TESTER_CGROUP_PROBE_IMAGE``. That probe parent is
    verified FIRST with ``tester-gate``'s own self-check contract
    (``check_slice_unit(parent, image, parent)``: the short, read-only probe
    runs in the slice it is asking about, and a typo'd name is still caught
    because it reads an empty ``FragmentPath``). Only a probe parent that
    answers affirmatively is then used to probe the build slice, so a typo'd
    gates tier is reported instead of silently hosting every probe. Either
    input missing, or any uncertainty (no docker, probe failure, unknown
    LoadState) for either slice, refuses.
    """
    from cmru import tester_gate

    probe_image_name = tester_gate._SLICE_PROBE_IMAGE_ENV
    probe_parent_name = tester_gate._CGROUP_PARENT_ENV
    probe_image = _declared_input(cwd, probe_image_name)
    probe_parent = _declared_input(cwd, probe_parent_name)
    missing = [
        name for name, value in (
            (probe_image_name, probe_image), (probe_parent_name, probe_parent),
        ) if not value
    ]
    if missing:
        print(
            f"[ERROR] cannot verify cgroup parent {cgroup_parent!r} before "
            f"wheel-build: {', '.join(missing)} is required (normally declared in "
            "cmru.orchestration.toml [env]); refusing to launch a Docker container "
            "into an unverified slice",
            file=sys.stderr,
        )
        raise SystemExit(exit_codes.PREREQ_MISSING)
    pinned_image = tester_gate.require_digest_pinned(probe_image, "cgroup probe image")
    parent_ok, parent_note = tester_gate.check_slice_unit(
        probe_parent, pinned_image, probe_parent,
    )
    if parent_ok is not True:
        print(
            f"[ERROR] wheel-build refuses to launch: {probe_parent_name}={probe_parent!r} "
            f"is not a verified host slice, so it cannot host the probe that checks "
            f"{cgroup_parent!r} ({parent_note})",
            file=sys.stderr,
        )
        raise SystemExit(exit_codes.PREREQ_MISSING)
    exists, note = tester_gate.check_slice_unit(cgroup_parent, pinned_image, probe_parent)
    if exists is not True:
        print(
            f"[ERROR] wheel-build refuses to launch: cgroup parent {cgroup_parent!r} "
            f"is not a verified host slice ({note})",
            file=sys.stderr,
        )
        raise SystemExit(exit_codes.PREREQ_MISSING)


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
    cwd = Path(args.cwd).resolve()
    image = _check_build_prerequisites(cwd)
    if _git_common_dir(cwd) is None:
        raise RuntimeError(
            f"Wheel build requires a Git worktree: cannot resolve git common directory for {cwd}."
        )
    # Verify the placement before anything is deleted or launched.
    cgroup_parent = _docker_cgroup_parent()
    require_installed_cgroup_parent(cgroup_parent, cwd)
    dist = cwd / "dist"
    if dist.exists():
        for stale in dist.glob("*.whl"):
            stale.unlink()
    print(f"[INFO] cmru handler: building wheel in {cwd}")

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
    # Supplementary opening text only; the curated section below is mandatory
    # for a project that declares a changelog and cannot be bypassed by it.
    notes = (os.getenv(args.notes_env) if args.notes_env else None) or f"{args.prefix} {version}"
    release_notes = _curated_release_notes(cwd, args.prefix, version, build_output)

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
        **_notes_kwargs(release_notes),
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
    release_notes = _curated_release_notes(cwd, args.prefix, version, build_output)

    gh = GitHubReleases(owner, repo, token)
    result = _publish_versioned_artifacts(
        gh, prefix=args.prefix, version=version, asset_path=art,
        notes=notes, extra_assets=None, build_output=build_output,
        **_notes_kwargs(release_notes),
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
