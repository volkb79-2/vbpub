#!/usr/bin/env python3
"""Prepare token-free origin baseline facts for CMRU's tester-unified lane."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_TAG_REF_RE = re.compile(
    r"refs/tags/(?P<tag>cmru-v[0-9][A-Za-z0-9.+-]*)(?P<peeled>\^\{\})?\Z"
)


def _remote_tag_commits(repo_root: Path, git_auth) -> dict[str, str]:
    """Read every published CMRU tag and its commit in one authenticated query."""
    from cmru.git_auth import run_remote_git

    result = run_remote_git(
        repo_root,
        "ls-remote",
        "--tags",
        "origin",
        "refs/tags/cmru-v*",
        auth=git_auth,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            "cannot list CMRU release tags on origin "
            f"(git ls-remote exited {result.returncode}): {detail}"
        )
    plain: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            raise RuntimeError("origin returned an empty CMRU release tag record")
        object_id, separator, ref = line.partition("\t")
        if not separator:
            raise RuntimeError("origin returned a malformed CMRU release tag record")
        parsed_ref = _TAG_REF_RE.fullmatch(ref)
        if parsed_ref is None:
            raise RuntimeError(f"origin returned a malformed CMRU release tag ref {ref!r}")
        if not _OID_RE.fullmatch(object_id):
            raise RuntimeError(f"origin returned a malformed object id for ref {ref!r}")
        tag = parsed_ref.group("tag")
        if parsed_ref.group("peeled"):
            previous = peeled.get(tag)
            if previous is not None and previous != object_id:
                raise RuntimeError(f"origin returned conflicting peeled refs for tag {tag!r}")
            peeled[tag] = object_id
        else:
            tag = ref[len("refs/tags/"):]
            previous = plain.get(tag)
            if previous is not None and previous != object_id:
                raise RuntimeError(f"origin returned conflicting refs for tag {tag!r}")
            plain[tag] = object_id
    if not set(peeled) <= plain.keys():
        raise RuntimeError("origin returned a peeled CMRU tag without its tag ref")
    return {tag: peeled.get(tag, object_id) for tag, object_id in plain.items()}


def build_facts(
    repo_root: Path,
    project_root: Path = PROJECT_ROOT,
    *,
    git_auth=None,
    assay_git=None,
) -> dict[str, object]:
    """Resolve the latest published ancestor and prepare token-free origin facts."""
    if assay_git is None:
        from assay import git as assay_git
    from assay_baseline_tags import (
        latest_ancestor_release_tag,
        latest_published_release_tag,
    )

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

    released_tag: str | None = nearest if tag_commit == head_commit else None

    if bool(head_release_tags) != (released_tag is not None):
        raise RuntimeError(
            "git describe and git tag disagree about CMRU release tags at HEAD"
        )

    remote_tags = _remote_tag_commits(repo_root, git_auth)
    if not remote_tags:
        raise RuntimeError("origin has no published CMRU release tags")
    latest_tag = latest_published_release_tag(remote_tags)
    if latest_tag is None:
        raise RuntimeError("origin has no published CMRU release tags")
    nearest = latest_ancestor_release_tag(
        assay_git,
        project_root,
        head_commit,
        remote_tags,
        exclude_tags=set(head_release_tags),
    )
    if nearest is None:
        raise RuntimeError(
            "candidate HEAD has no previous published CMRU release tag in its full ancestry"
        )
    tag_commit = assay_git.run(
        project_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"refs/tags/{nearest}^{{commit}}",
    ).strip()
    if remote_tags[nearest] != tag_commit:
        raise RuntimeError(
            f"selected CMRU release tag {nearest!r} resolves locally to {tag_commit}, "
            f"but origin publishes it at {remote_tags[nearest]}"
        )
    return {
        "schema_version": 3,
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
