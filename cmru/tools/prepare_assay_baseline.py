#!/usr/bin/env python3
"""Prepare token-free origin baseline facts for CMRU's tester-unified lane."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _previous_release_tag(assay_git, project_root: Path, tags_at_head: list[str]) -> str:
    args = ["describe", "--abbrev=0", "--tags", "--match", "cmru-v*"]
    for tag in tags_at_head:
        args.extend(("--exclude", tag))
    args.append("HEAD")
    return assay_git.run(project_root, *args).strip()


def _remote_tag_commit(repo_root: Path, tag: str, git_auth) -> str:
    from cmru.git_auth import run_remote_git

    result = run_remote_git(
        repo_root,
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
        auth=git_auth,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            f"cannot verify CMRU release tag {tag!r} on origin "
            f"(git ls-remote exited {result.returncode}): {detail}"
        )
    plain_commit = peeled_commit = None
    for line in result.stdout.splitlines():
        object_id, separator, ref = line.partition("\t")
        if not separator or not _OID_RE.fullmatch(object_id):
            continue
        if ref == f"refs/tags/{tag}^{{}}":
            peeled_commit = object_id
        elif ref == f"refs/tags/{tag}":
            plain_commit = object_id
    remote_commit = peeled_commit or plain_commit
    if not remote_commit:
        raise RuntimeError(f"origin returned no usable object for CMRU release tag {tag!r}")
    return remote_commit


def build_facts(
    repo_root: Path,
    project_root: Path = PROJECT_ROOT,
    *,
    git_auth=None,
    assay_git=None,
) -> dict[str, object]:
    """Resolve local ancestry and query only its relevant published tags."""
    if assay_git is None:
        from assay import git as assay_git
    from cmru.release import _semver_key
    from cmru.version import _highest_remote_tag_for_prefix

    nearest = assay_git.run(
        project_root, "describe", "--abbrev=0", "--tags", "--match", "cmru-v*",
    ).strip()
    if not nearest:
        raise RuntimeError("git describe returned an empty CMRU release tag")
    tag_commit = assay_git.run(
        project_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"refs/tags/{nearest}^{{commit}}",
    ).strip()
    head_commit = assay_git.head_rev(project_root)
    head_release_tags = sorted(set(
        tag for tag in assay_git.run(
            project_root,
            "tag",
            "--list",
            "cmru-v*",
            "--points-at",
            head_commit,
        ).splitlines()
        if tag
    ))

    released_tag: str | None = None
    if tag_commit == head_commit:
        released_tag = nearest
        nearest = _previous_release_tag(assay_git, project_root, head_release_tags)
        if not nearest:
            raise RuntimeError(
                "tagged HEAD has no previous CMRU release tag in its full ancestry"
            )
        tag_commit = assay_git.run(
            project_root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"refs/tags/{nearest}^{{commit}}",
        ).strip()

    if bool(head_release_tags) != (released_tag is not None):
        raise RuntimeError(
            "git describe and git tag disagree about CMRU release tags at HEAD"
        )

    latest_tag = _highest_remote_tag_for_prefix(
        repo_root,
        "cmru-v",
        lambda tag: _semver_key(tag[len("cmru-v"):]),
        git_auth=git_auth,
    )
    relevant_tags = {nearest}
    if latest_tag is not None:
        relevant_tags.add(latest_tag)
    relevant_tags.update(head_release_tags)
    remote_tags = {
        tag: _remote_tag_commit(repo_root, tag, git_auth)
        for tag in sorted(relevant_tags)
    }
    return {
        "schema_version": 2,
        "head": head_commit,
        "published_latest": latest_tag,
        "selected_tag": nearest,
        "selected_commit": tag_commit,
        "released_tag": released_tag,
        "head_release_tags": head_release_tags,
        "remote_tag_commits": remote_tags,
    }


def _repository_git_auth(repo_root: Path):
    from cmru.cli import load_config
    from cmru.git_auth import GitHubGitAuth

    config_path = repo_root / "cmru.orchestration.toml"
    loaded = load_config(config_path, validate_dependencies=False)
    github = loaded[-2]
    return GitHubGitAuth(
        owner=github.owner,
        repo=github.repo,
        token=github.token,
    )


def main() -> int:
    try:
        from assay import git as assay_git

        repo_root = assay_git.repo_top(PROJECT_ROOT)
        auth = _repository_git_auth(repo_root)
        facts = build_facts(repo_root, PROJECT_ROOT, git_auth=auth, assay_git=assay_git)
    except Exception as exc:
        print(f"prepare-assay-baseline: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(facts, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
