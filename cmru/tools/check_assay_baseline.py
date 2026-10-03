#!/usr/bin/env python3
"""Require Assay R1 and the release mutation lane to use the same tag."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _previous_release_tag(assay_git, project_root: Path, tags_at_head: list[str]) -> str:
    args = ["describe", "--abbrev=0", "--tags", "--match", "cmru-v*"]
    for tag in tags_at_head:
        args.extend(("--exclude", tag))
    args.append("HEAD")
    return assay_git.run(project_root, *args).strip()


def _mutation_evidence_path() -> Path:
    return PROJECT_ROOT / ".assay" / "mutation-cmru.json"


def _evidence_parent() -> Path:
    parent_path = PROJECT_ROOT / ".assay"
    if parent_path.is_symlink():
        raise ValueError(f"mutation evidence parent must not be a symlink: {parent_path}")
    parent_path.mkdir(parents=True, exist_ok=True)
    if not parent_path.is_dir() or parent_path.is_symlink():
        raise ValueError(f"mutation skip evidence parent must be a real directory: {parent_path}")
    parent = parent_path.resolve()
    if not parent.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(f"mutation skip evidence parent escapes the project: {parent}")
    return parent


def _write_skip_evidence(nearest_tag: str, head_commit: str) -> None:
    evidence_path = _mutation_evidence_path()
    parent = _evidence_parent()
    path = parent / evidence_path.name
    if path.is_symlink():
        raise ValueError(f"mutation skip evidence must not be a symlink: {path}")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent,
            prefix=".mutation-skip-", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({
                "status": "skipped",
                "reason": "no-changed-source",
                "base": nearest_tag,
                "head": head_commit,
            }, stream, sort_keys=True)
            stream.write("\n")
        if path.is_symlink():
            raise ValueError(f"mutation skip evidence must not be a symlink: {path}")
        if temporary is None:
            raise OSError("temporary mutation skip evidence was not created")
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _remove_stale_skip_evidence() -> bool:
    parent_path = PROJECT_ROOT / ".assay"
    if parent_path.is_symlink():
        raise ValueError(f"mutation evidence parent must not be a symlink: {parent_path}")
    if not parent_path.exists():
        return False
    if not parent_path.is_dir():
        raise ValueError(f"mutation evidence parent must be a real directory: {parent_path}")
    parent = parent_path.resolve()
    if not parent.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(f"mutation evidence parent escapes the project: {parent}")
    path = parent / _mutation_evidence_path().name
    if path.is_symlink():
        raise ValueError(f"mutation evidence must not be a symlink: {path}")
    if not path.exists():
        return False
    if not path.is_file():
        raise ValueError(f"mutation evidence must be a regular file: {path}")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (
        isinstance(previous, dict)
        and previous.get("status") == "skipped"
        and previous.get("reason") == "no-changed-source"
    ):
        if path.is_symlink():
            raise ValueError(f"mutation evidence must not be a symlink: {path}")
        path.unlink()
        return True
    return False


def _source_pathspecs(assay_git, source_roots: list[str]) -> tuple[Path, list[str]]:
    repo_top = assay_git.repo_top(PROJECT_ROOT)
    project_prefix = PROJECT_ROOT.resolve().relative_to(repo_top)
    pathspecs = []
    for source_root in source_roots:
        relative_source = PurePosixPath(source_root)
        if (
            relative_source.is_absolute()
            or not relative_source.parts
            or ".." in relative_source.parts
        ):
            raise ValueError(f"unsafe Assay source root {source_root!r}")
        pathspecs.append(
            PurePosixPath(*project_prefix.parts, *relative_source.parts).as_posix()
        )
    return repo_top, pathspecs


def _host_remote_baseline(
    head_commit: str,
    nearest: str,
    tag_commit: str,
    released_tag: str | None,
    head_release_tags: list[str],
) -> tuple[str | None, dict[str, str]] | None:
    """Read the credential-free remote facts prepared by the host gate."""
    raw = os.environ.get("CMRU_ASSAY_BASELINE_FACTS", "")
    if not raw:
        print(
            "check-assay-baseline: host-prepared origin facts are missing; "
            "run the registered `./run-gate.py gate` lane",
            file=sys.stderr,
        )
        return None
    try:
        facts = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"check-assay-baseline: invalid host-prepared origin facts: {exc}", file=sys.stderr)
        return None
    expected_keys = {
        "schema_version", "head", "published_latest", "selected_tag",
        "selected_commit", "released_tag", "head_release_tags",
        "remote_tag_commits",
    }
    if not isinstance(facts, dict) or set(facts) != expected_keys:
        print("check-assay-baseline: host-prepared origin facts have an invalid shape", file=sys.stderr)
        return None
    if facts["schema_version"] != 2:
        print("check-assay-baseline: unsupported host-prepared origin facts version", file=sys.stderr)
        return None
    if facts["head"] != head_commit:
        print("check-assay-baseline: host-prepared origin facts refer to a different HEAD", file=sys.stderr)
        return None
    if facts["selected_tag"] != nearest or facts["selected_commit"] != tag_commit:
        print("check-assay-baseline: host-prepared origin facts select a different ancestor tag", file=sys.stderr)
        return None
    if facts["released_tag"] != released_tag:
        print("check-assay-baseline: host-prepared origin facts disagree about the release tag at HEAD", file=sys.stderr)
        return None
    prepared_head_tags = facts["head_release_tags"]
    if (
        not isinstance(prepared_head_tags, list)
        or any(not isinstance(tag, str) or not tag.startswith("cmru-v") for tag in prepared_head_tags)
        or prepared_head_tags != sorted(set(prepared_head_tags))
    ):
        print("check-assay-baseline: host-prepared CMRU tags at HEAD are malformed", file=sys.stderr)
        return None
    if prepared_head_tags != head_release_tags:
        print("check-assay-baseline: host-prepared facts disagree about local CMRU tags at HEAD", file=sys.stderr)
        return None

    published_latest = facts["published_latest"]
    if published_latest is not None and (
        not isinstance(published_latest, str) or not published_latest.startswith("cmru-v")
    ):
        print("check-assay-baseline: host-prepared latest published tag is malformed", file=sys.stderr)
        return None
    if not published_latest:
        print("check-assay-baseline: origin has no published CMRU release tag", file=sys.stderr)
        return None

    remote_tags = facts["remote_tag_commits"]
    if not isinstance(remote_tags, dict):
        print("check-assay-baseline: host-prepared remote tag commits are malformed", file=sys.stderr)
        return None
    for tag, commit in remote_tags.items():
        if (
            not isinstance(tag, str)
            or not tag.startswith("cmru-v")
            or not isinstance(commit, str)
            or len(commit) not in (40, 64)
            or any(char not in "0123456789abcdef" for char in commit)
        ):
            print("check-assay-baseline: host-prepared remote tag commit is malformed", file=sys.stderr)
            return None
    required_tags = {nearest, published_latest}
    required_tags.update(head_release_tags)
    if not required_tags <= remote_tags.keys():
        print("check-assay-baseline: host-prepared facts omit a required remote release tag", file=sys.stderr)
        return None
    return published_latest, remote_tags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-evidence",
        action="store_true",
        help="record an empty-source skip or remove an old skip record before mutation",
    )
    args = parser.parse_args(argv)
    removed_stale_skip = False
    if args.skip_evidence:
        try:
            removed_stale_skip = _remove_stale_skip_evidence()
        except (OSError, ValueError) as exc:
            print(
                f"check-assay-baseline: cannot clear stale mutation skip evidence: {exc}",
                file=sys.stderr,
            )
            return 1
        if removed_stale_skip:
            print(
                "check-assay-baseline: removed prior empty-source skip evidence "
                "before baseline verification",
                file=sys.stderr,
            )

    try:
        with (PROJECT_ROOT / "assay.toml").open("rb") as stream:
            config = tomllib.load(stream)
        judge = config["lanes"]["cmru"]["judge"]
        declared = judge["base"]
        source_roots = judge["source_roots"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        print(
            f"check-assay-baseline: cannot read CMRU Assay lane config: {exc}",
            file=sys.stderr,
        )
        return 1
    if not isinstance(declared, str) or not declared.strip():
        print("check-assay-baseline: Assay R1 base must be a non-empty tag", file=sys.stderr)
        return 1
    if not isinstance(source_roots, list) or not source_roots:
        print(
            "check-assay-baseline: Assay source_roots must be a non-empty list",
            file=sys.stderr,
        )
        return 1
    if any(not isinstance(root, str) or not root.strip() for root in source_roots):
        print(
            "check-assay-baseline: Assay source_roots must contain non-empty paths",
            file=sys.stderr,
        )
        return 1

    try:
        from assay import git as assay_git
        from assay.errors import AssayError
    except ImportError as exc:
        print(
            f"check-assay-baseline: cannot load Assay Git rules: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        repo_top, source_pathspecs = _source_pathspecs(assay_git, source_roots)
    except (AssayError, ValueError) as exc:
        print(
            f"check-assay-baseline: cannot resolve CMRU source paths: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        nearest = assay_git.run(
            PROJECT_ROOT, "describe", "--abbrev=0", "--tags", "--match", "cmru-v*",
        ).strip()
    except AssayError as exc:
        print(
            "check-assay-baseline: cannot resolve nearest ancestor CMRU release tag: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1
    if not nearest:
        print(
            "check-assay-baseline: git describe returned an empty CMRU release tag",
            file=sys.stderr,
        )
        return 1

    try:
        tag_commit = assay_git.run(
            PROJECT_ROOT,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"refs/tags/{nearest}^{{commit}}",
        ).strip()
        head_commit = assay_git.head_rev(PROJECT_ROOT)
        head_release_tags = sorted(set(
            tag for tag in assay_git.run(
                PROJECT_ROOT,
                "tag",
                "--list",
                "cmru-v*",
                "--points-at",
                head_commit,
            ).splitlines()
            if tag
        ))
    except AssayError as exc:
        print(
            f"check-assay-baseline: cannot resolve release tag and HEAD commits: {exc}",
            file=sys.stderr,
        )
        return 1

    released_tag: str | None = None
    if tag_commit == head_commit:
        released_tag = nearest
        try:
            # Keep the full ancestry search: the preceding release can be on
            # a merge's second parent, outside the tagged HEAD's first parent.
            nearest = _previous_release_tag(assay_git, PROJECT_ROOT, head_release_tags)
        except AssayError as exc:
            print(
                "check-assay-baseline: cannot resolve previous ancestor CMRU "
                f"release tag reachable from tagged HEAD {released_tag!r}: {exc}",
                file=sys.stderr,
            )
            return 1
        if not nearest:
            print(
                "check-assay-baseline: tagged HEAD has no previous ancestor "
                "CMRU release tag in its full ancestry",
                file=sys.stderr,
            )
            return 1
        try:
            tag_commit = assay_git.run(
                PROJECT_ROOT,
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"refs/tags/{nearest}^{{commit}}",
            ).strip()
        except AssayError as exc:
            print(
                f"check-assay-baseline: cannot resolve previous release tag commit: {exc}",
                file=sys.stderr,
            )
            return 1

    if bool(head_release_tags) != (released_tag is not None):
        print(
            "check-assay-baseline: git describe and git tag disagree about CMRU release tags at HEAD",
            file=sys.stderr,
        )
        return 1

    remote_baseline = _host_remote_baseline(
        head_commit, nearest, tag_commit, released_tag, head_release_tags,
    )
    if remote_baseline is None:
        return 1
    published_latest, remote_tag_commits = remote_baseline
    if released_tag is None:
        published_baseline_matches = nearest == published_latest
    else:
        # On a tagged release HEAD, the preceding ancestor tag remains the
        # R1/R2 base. The latest published tag may be any exact tag at HEAD;
        # git describe can prefer an older annotated tag over a newer lightweight
        # tag when both point at that commit.
        published_baseline_matches = (
            nearest == published_latest or published_latest in head_release_tags
        )
    if not published_baseline_matches:
        print(
            f"check-assay-baseline: selected ancestor tag {nearest!r} does not "
            f"match CMRU's verified published baseline {published_latest!r}",
            file=sys.stderr,
        )
        return 1

    remote_tag_commit = remote_tag_commits[nearest]
    if remote_tag_commit != tag_commit:
        print(
            f"check-assay-baseline: selected tag {nearest!r} resolves locally to "
            f"{tag_commit}, but origin publishes it at {remote_tag_commit}",
            file=sys.stderr,
        )
        return 1

    for head_tag in head_release_tags:
        remote_head_commit = remote_tag_commits[head_tag]
        if remote_head_commit != head_commit:
            print(
                f"check-assay-baseline: release tag at HEAD {head_tag!r} "
                f"resolves locally to {head_commit}, but origin publishes it at "
                f"{remote_head_commit}",
                file=sys.stderr,
            )
            return 1

    try:
        declared_commit = assay_git.run(
            PROJECT_ROOT,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{declared}^{{commit}}",
        ).strip()
        effective_base = assay_git.resolve_base(PROJECT_ROOT, declared)
        resolution = assay_git.base_resolution_mode(PROJECT_ROOT)
    except AssayError as exc:
        print(
            f"check-assay-baseline: cannot resolve Assay comparison commits: {exc}",
            file=sys.stderr,
        )
        return 1

    if not tag_commit or not declared_commit or not effective_base:
        print(
            "check-assay-baseline: Assay returned an empty comparison commit",
            file=sys.stderr,
        )
        return 1

    if declared != nearest:
        print(
            f"check-assay-baseline: Assay R1 base {declared!r} does not match "
            f"nearest ancestor CMRU release tag {nearest!r}",
            file=sys.stderr,
        )
        return 1
    if declared_commit != tag_commit:
        print(
            f"check-assay-baseline: Assay R1 base {declared!r} resolves to "
            f"{declared_commit}, not selected tag commit {tag_commit}",
            file=sys.stderr,
        )
        return 1
    if effective_base != tag_commit:
        try:
            source_delta = assay_git.run(
                PROJECT_ROOT,
                "diff",
                "--name-only",
                f"{tag_commit}..{effective_base}",
                "--",
                *source_pathspecs,
            )
        except AssayError as exc:
            print(
                "check-assay-baseline: cannot compare Assay's effective base "
                f"with selected release tag {nearest!r}: {exc}",
                file=sys.stderr,
            )
            return 1
        if source_delta.strip():
            print(
                f"check-assay-baseline: Assay's effective base is {effective_base!r} "
                f"({resolution}), not nearest ancestor release tag {nearest!r} "
                f"at {tag_commit}; source paths differ, so the R1 and mutation "
                "ranges would differ",
                file=sys.stderr,
            )
            return 1

    if args.skip_evidence:
        try:
            changed_paths = assay_git.run(
                PROJECT_ROOT,
                "diff",
                "--name-only",
                f"{tag_commit}..{head_commit}",
                "--",
                *source_pathspecs,
            )
        except (AssayError, ValueError) as exc:
            print(
                f"check-assay-baseline: cannot resolve changed CMRU source paths: {exc}",
                file=sys.stderr,
            )
            return 1
        if not changed_paths.strip():
            try:
                _write_skip_evidence(nearest, head_commit)
            except (OSError, ValueError) as exc:
                print(
                    f"check-assay-baseline: cannot write mutation skip evidence: {exc}",
                    file=sys.stderr,
                )
                return 1
            print(
                f"check-assay-baseline: no changed CMRU source since {nearest}; "
                "recorded explicit skip evidence",
                file=sys.stderr,
            )
            return 0
    print(
        "check-assay-baseline: Assay R1 base and effective comparison commit "
        f"match nearest ancestor tag {nearest}",
        file=sys.stderr,
    )
    print(nearest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
