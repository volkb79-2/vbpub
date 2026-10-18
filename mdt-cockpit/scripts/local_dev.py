"""Explicit local-dev build mode for mdt-cockpit images (D9).

A local-dev build is an OPT-IN: ``build-push.py --build --local-dev <manifest.toml>``.
It exists so an image can be built and tested from artifacts that are not (yet)
published - a wheel built in a worktree, a PWMCP bundle whose release URL 404s.
It is never a fallback: a missing release does not turn a release build into one.

Everything here is pure policy so it can be tested without Docker or a network:

* the manifest lists every local artifact explicitly (name, path, sha256, version)
  and is verified in full before anything is built - a missing, unreadable or
  hash-mismatched entry refuses the build and names the entry;
* an unknown artifact name is an error, never silently ignored (a typo must not
  quietly fall back to the release artifact);
* the build is recorded in OCI labels and the image manifest;
* local-dev tags carry ``-dev-`` and a local-dev image is never publishable.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
MODE_RELEASE = "release"
MODE_LOCAL_DEV = "local-dev"
BUILD_MODE_LABEL = "net.volkb79.mdt.build-mode"
LOCAL_ARTIFACTS_LABEL = "net.volkb79.mdt.local-artifacts"
ENV_MANIFEST = "MDT_LOCAL_DEV_MANIFEST"
PWMCP_INSTALLER = "pwmcp-installer"
PWMCP_BUNDLE = "pwmcp-bundle"
PWMCP_NAMES = (PWMCP_INSTALLER, PWMCP_BUNDLE)
PROVENANCE_NAME = "provenance.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEV_TAG = re.compile(r"[A-Za-z0-9._-]+-dev-\d{8}(?:-\d+)?")
_DEV_TAG_LATEST = re.compile(r"[A-Za-z0-9._-]+-dev-latest")
_DEV_DATE = re.compile(r"dev-\d{8}(?:-\d+)?")


class LocalDevError(Exception):
    """The local-dev request cannot be honoured; the message names the cause."""


@dataclass(frozen=True)
class LocalArtifact:
    name: str
    path: Path
    sha256: str
    version: str

    @property
    def is_wheel(self) -> bool:
        return self.name not in PWMCP_NAMES


def normalize_dist(name: str) -> str:
    """PEP 503 normalisation, so ``cli_extended`` and ``cli-extended`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identity(filename: str) -> tuple[str, str]:
    """Return ``(normalised distribution, version)`` from a wheel file name."""
    if not filename.endswith(".whl"):
        raise LocalDevError(f"{filename!r} is not a wheel file name")
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        raise LocalDevError(f"{filename!r} is not a valid wheel file name")
    return normalize_dist(parts[0]), parts[1]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_names(wheels_list: Path) -> list[str]:
    """Names declared in ``pip/wheels.list`` ('#' starts a comment)."""
    names: list[str] = []
    for raw in wheels_list.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def load_manifest(
    manifest_path: Path,
    *,
    allowed_wheels: Sequence[str],
    pwmcp_version: str | None = None,
) -> list[LocalArtifact]:
    """Read and VERIFY a local-dev manifest; raise on the first unusable entry.

    ``allowed_wheels`` are the names in ``pip/wheels.list``; the PWMCP installer
    and bundle are always allowed. ``pwmcp_version`` (the pinned PWMCP release),
    when given, must equal the version declared for each PWMCP entry.
    """
    try:
        document = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LocalDevError(f"cannot read local-dev manifest {manifest_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LocalDevError(f"local-dev manifest {manifest_path} is not valid TOML: {exc}") from exc
    if document.get("schema_version") != SCHEMA_VERSION:
        raise LocalDevError(
            f"local-dev manifest {manifest_path} must declare schema_version = {SCHEMA_VERSION}"
        )
    unknown_keys = set(document) - {"schema_version", "artifact"}
    if unknown_keys:
        raise LocalDevError(
            f"local-dev manifest {manifest_path} has unknown top-level key(s): "
            + ", ".join(sorted(unknown_keys))
        )
    entries = document.get("artifact")
    if not isinstance(entries, list) or not entries:
        raise LocalDevError(f"local-dev manifest {manifest_path} lists no [[artifact]] entries")

    allowed = {name: normalize_dist(name) for name in allowed_wheels}
    known = sorted([*allowed, *PWMCP_NAMES])
    artifacts: list[LocalArtifact] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, 1):
        label = f"[[artifact]] #{index}"
        if not isinstance(entry, dict):
            raise LocalDevError(f"{label} must be a table")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise LocalDevError(f"{label} has no name")
        label = f"artifact {name!r}"
        extra = set(entry) - {"name", "path", "sha256", "version"}
        if extra:
            raise LocalDevError(f"{label} has unknown field(s): {', '.join(sorted(extra))}")
        if name not in allowed and name not in PWMCP_NAMES:
            raise LocalDevError(
                f"{label} is not a known artifact (known: {', '.join(known)}); "
                "refusing rather than silently using the release artifact instead"
            )
        if name in seen:
            raise LocalDevError(f"{label} is listed more than once")
        seen.add(name)
        for field in ("path", "sha256", "version"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise LocalDevError(f"{label} needs a non-empty string {field!r}")
        path = Path(entry["path"])
        if not path.is_absolute():
            raise LocalDevError(f"{label}: path must be absolute, got {entry['path']!r}")
        expected = entry["sha256"].lower()
        if not _SHA256.fullmatch(expected):
            raise LocalDevError(f"{label}: sha256 must be 64 hexadecimal digits")
        if not path.is_file():
            raise LocalDevError(f"{label}: {path} is missing or not a regular file")
        try:
            actual = sha256_of(path)
        except OSError as exc:
            raise LocalDevError(f"{label}: cannot read {path}: {exc}") from exc
        if actual != expected:
            raise LocalDevError(
                f"{label}: sha256 mismatch for {path}: manifest {expected}, file {actual}"
            )
        version = entry["version"]
        if name in allowed:
            dist, wheel_version = wheel_identity(path.name)
            if dist != allowed[name]:
                raise LocalDevError(
                    f"{label}: {path.name} is a wheel of {dist!r}, not {allowed[name]!r}"
                )
            if wheel_version != version:
                raise LocalDevError(
                    f"{label}: manifest version {version!r} but {path.name} is {wheel_version!r}"
                )
        elif pwmcp_version is not None and version != pwmcp_version:
            raise LocalDevError(
                f"{label}: version {version!r} does not match the pinned PWMCP release "
                f"{pwmcp_version!r}"
            )
        artifacts.append(LocalArtifact(name=name, path=path, sha256=actual, version=version))
    return artifacts


def provenance_records(artifacts: Iterable[LocalArtifact]) -> list[dict[str, str]]:
    """The fields recorded in labels and the image manifest (original path kept)."""
    return [
        {"name": a.name, "version": a.version, "sha256": a.sha256, "path": str(a.path)}
        for a in sorted(artifacts, key=lambda item: item.name)
    ]


def provenance_json(artifacts: Iterable[LocalArtifact]) -> str:
    return json.dumps(provenance_records(artifacts), separators=(",", ":"), sort_keys=True)


def _copy_verified(source: Path, destination: Path, expected_sha256: str, what: str) -> None:
    """Copy, hash THE COPY, compare with the recorded sha, then set readable modes.

    The source was hashed when the manifest was loaded, but the resolver (network) runs
    in between, so a rebuilt file would otherwise be baked under the old sha. Modes are
    set explicitly because an umask-077 checkout would leave the copy unreadable to the
    unprivileged build user.
    """
    shutil.copyfile(source, destination)
    actual = sha256_of(destination)
    if actual != expected_sha256:
        destination.unlink()
        raise LocalDevError(
            f"{what}: {source} changed after the manifest was verified "
            f"(recorded {expected_sha256}, copy hashes to {actual})"
        )
    destination.chmod(0o644)


def stage(artifacts: Sequence[LocalArtifact], *, staging_dir: Path) -> None:
    """Place verified artifacts where the Dockerfile consumes them.

    EVERYTHING goes into ``staging_dir`` (the ``mdt-local-dev`` named build context):
    wheels under ``wheels/``, the PWMCP files under ``pwmcp/``, and a ``provenance.json``
    that the in-image steps re-verify. Nothing is written into the shared, untracked
    ``pip/wheels/`` - a local wheel left there would be baked into the next RELEASE build.
    """
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    for sub in ("", "pwmcp", "wheels"):
        (staging_dir / sub).mkdir(parents=True, exist_ok=True)
        (staging_dir / sub).chmod(0o755)
    records: list[dict[str, str]] = []
    for artifact in artifacts:
        record = {
            "name": artifact.name,
            "version": artifact.version,
            "sha256": artifact.sha256,
            "path": str(artifact.path),
        }
        if artifact.is_wheel:
            target = f"wheels/{artifact.path.name}"
        elif artifact.name == PWMCP_INSTALLER:
            target = "pwmcp/get.py"
        else:
            target = "pwmcp/bundle.tar.xz"
        _copy_verified(artifact.path, staging_dir / target, artifact.sha256, artifact.name)
        record["staged"] = target
        records.append(record)
    provenance = staging_dir / PROVENANCE_NAME
    provenance.write_text(
        json.dumps({"schema": SCHEMA_VERSION, "artifacts": records}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    provenance.chmod(0o644)


def _split_ref(image_ref: str) -> tuple[str, str]:
    """``(repository, tag)`` tolerating a registry ``host:port`` before the tag."""
    last_segment = image_ref.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        raise LocalDevError(f"image reference has no tag: {image_ref!r}")
    head, _, tag = image_ref.rpartition(":")
    return head, tag


def check_local_dev_tags(tags: Sequence[str], build_date: str) -> list[str]:
    """Return the tags of one bake target after proving every one is local-only.

    In a local-dev build ``docker-bake.hcl`` emits only ``-dev-`` tags (the date
    coordinate ``build_date`` = ``dev-YYYYMMDD[-n]`` and the floating
    ``...-dev-latest``); bare ``latest``, ``latest-stable`` and the release
    ``...-latest`` aliases are dropped there by construction. This is the
    independent check on that: ANY tag that is not exactly
    ``<debian>-py<python>[-<flavor>]-dev-<YYYYMMDD>[-<n>]`` for this build or its
    ``...-dev-latest`` alias refuses the build, so a release tag can never be
    applied to a local-dev image by a bake-file mistake.
    """
    if not _DEV_DATE.fullmatch(build_date):
        raise LocalDevError(f"local-dev build date {build_date!r} must look like dev-YYYYMMDD[-n]")
    dated = 0
    for ref in tags:
        _, tag = _split_ref(ref)
        if tag.endswith(f"-{build_date}") and _DEV_TAG.fullmatch(tag):
            dated += 1
        elif not (tag.endswith("-dev-latest") and _DEV_TAG_LATEST.fullmatch(tag)):
            raise LocalDevError(
                f"tag {ref!r} is not a local-dev tag (expected ...-{build_date} or ...-dev-latest)"
            )
    if not dated:
        raise LocalDevError(f"none of {list(tags)!r} carries the local-dev coordinate -{build_date}")
    return list(tags)


def refuse_unpublishable(labels: Mapping[str, str] | None, subject: str) -> None:
    """Raise if an image's labels say it must not be published.

    A missing label is a release image built before the label existed; any value
    other than ``release`` is refused (an unknown mode is not a release).
    """
    mode = (labels or {}).get(BUILD_MODE_LABEL)
    if mode is None or mode == MODE_RELEASE:
        return
    raise LocalDevError(
        f"refusing to publish {subject}: its {BUILD_MODE_LABEL} label is {mode!r}; "
        "a local-dev image is for local testing only"
    )
