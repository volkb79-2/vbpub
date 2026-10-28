"""KI-78: an image-only release publishes a GitHub Release body (``oci-image-release``).

Real Git fixtures and the real ``GitHubReleases`` client run against an in-memory
GitHub REST fake (no network): the fake records every request, so a test can
prove what a refusal did NOT change and that a retry never touched an asset.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest

from cmru import changelog, cli, exit_codes, handlers, release, release_notes
from cmru.errors import RefusedBeforeChange
from tests.test_release_notes import CHANGES, _git, _project, _release_repo

API = "https://api.github.com/repos/o/r"
IMAGE = "ghcr.io/acme/demo"
TAG = "demo-v2.0.0"
OTHER_COMMIT = "f" * 40

SECTION = release_notes.extract_release_section(CHANGES, "2.0.0", source="CHANGES.md").text
EXPECTED_BODY = (
    f"**Image:** `{IMAGE}:2.0.0`\n\nPull: `docker pull {IMAGE}:2.0.0`\n\n{SECTION}"
)


class FakeGitHub:
    """The few REST routes ``GitHubReleases`` uses, with every call recorded."""

    def __init__(self) -> None:
        self.tags: dict[str, str] = {}
        self.releases: dict[str, dict] = {}
        self.assets: dict[int, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.on_create = None
        # (method, path, nth) -> action run just before the nth such request is served,
        # so a test can change GitHub state at one exact instant of the publish.
        self.before: dict[tuple[str, str, int], object] = {}
        # (method, path, nth) -> (status, body) served INSTEAD of the real answer, to
        # inject a malformed, missing or undecodable response at one exact request.
        self.respond: dict[tuple[str, str, int], tuple[int, str]] = {}
        # Like ``respond``, but the REAL handler runs first (the write IS applied) and the
        # answer is replaced afterwards: a write that landed whose response got lost.
        self.respond_after: dict[tuple[str, str, int], tuple[int, str]] = {}
        self._next_id = 100

    def seed_release(self, tag: str, *, body: str = "old body", target: str = "main",
                     assets: tuple[dict, ...] = ()) -> int:
        release_id = self._next_id
        self._next_id += 1
        self.releases[tag] = {
            "id": release_id, "tag_name": tag, "name": tag, "body": body,
            "target_commitish": target,
            "upload_url": f"https://uploads.example/{release_id}/assets{{?name,label}}",
        }
        self.assets[release_id] = [dict(item) for item in assets]
        return release_id

    @property
    def mutations(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "GET"]

    def handle(self, method: str, url: str, data: bytes | None) -> tuple[int, str]:
        assert url.startswith(API), f"unexpected request {method} {url}"
        path = url[len(API):]
        self.calls.append((method, path))
        nth = self.calls.count((method, path))
        action = self.before.get((method, path, nth))
        if action is not None:
            action()
        if (method, path, nth) in self.respond:
            return self.respond[(method, path, nth)]
        answer = self._serve(method, path, data)
        return self.respond_after.get((method, path, nth), answer)

    @staticmethod
    def _tag_segment(segment: str) -> str:
        """Decode a tag path segment, requiring the one-segment encoding GitHub routes."""
        tag = unquote(segment)
        assert quote(tag, safe="") == segment, f"tag segment is not fully encoded: {segment!r}"
        return tag

    def _serve(self, method: str, path: str, data: bytes | None) -> tuple[int, str]:
        payload = json.loads(data) if data else {}
        if method == "GET" and path.startswith("/commits/"):
            tag = self._tag_segment(path.removeprefix("/commits/"))
            if tag not in self.tags:
                return 404, "{}"
            return 200, json.dumps({"sha": self.tags[tag]})
        if method == "GET" and path.startswith("/releases/tags/"):
            record = self.releases.get(self._tag_segment(path.removeprefix("/releases/tags/")))
            return (200, json.dumps(record)) if record else (404, "{}")
        if method == "GET" and path.removeprefix("/releases/").isdigit():
            wanted = int(path.removeprefix("/releases/"))
            record = next((item for item in self.releases.values() if item["id"] == wanted), None)
            return (200, json.dumps(record)) if record else (404, "{}")
        if method == "POST" and path == "/releases":
            tag = payload["tag_name"]
            assert tag not in self.releases, "create over an existing release"
            self.tags.setdefault(tag, payload.get("target_commitish", ""))
            self.created.append(payload)
            release_id = self.seed_release(tag, body=payload["body"])
            self.releases[tag]["name"] = payload["name"]
            if self.on_create:
                self.on_create()
            return 201, json.dumps(self.releases[tag])
        if method == "PATCH" and path.startswith("/releases/"):
            release_id = int(path.removeprefix("/releases/"))
            record = next(item for item in self.releases.values() if item["id"] == release_id)
            record.update(name=payload["name"], body=payload["body"])
            return 200, json.dumps(record)
        raise AssertionError(f"unexpected GitHub call {method} {path}")


@pytest.fixture
def image_release(monkeypatch, tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-q", "-m", "release candidate")
    _git(tmp_path, "tag", TAG)
    commit = _git(tmp_path, "rev-parse", "HEAD")
    api = FakeGitHub()
    api.tags[TAG] = commit
    monkeypatch.setattr(
        release.GitHubReleases, "_request",
        lambda _gh, method, url, data=None, content_type=None: api.handle(method, url, data),
    )
    for key, value in (("GITHUB_PUSH_PAT", "t"), ("GITHUB_USERNAME", "o"), ("GITHUB_REPO", "r")):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    monkeypatch.setenv("CMRU_RELEASE_TAG", TAG)
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    # The read-back pauses between attempts go through ``release._pause``: record, never wait.
    pauses: list[float] = []
    monkeypatch.setattr(release, "_pause", pauses.append)
    return SimpleNamespace(
        project=project, root=tmp_path, commit=commit, api=api, pauses=pauses,
    )


def _publish(project, *, image: str = IMAGE, prefix: str = "demo") -> int:
    return handlers.main([
        "oci-image-release", "--prefix", prefix, "--image", image, "--cwd", str(project),
    ])


def test_get_tag_commit_uses_the_github_commits_ref_route(image_release):
    api = image_release.api
    client = release.GitHubReleases("o", "r", "t")

    assert client.get_tag_commit(TAG) == image_release.commit
    assert api.calls == [("GET", f"/commits/{TAG}")]


def test_tag_routes_encode_url_reserved_tag_characters_as_one_path_segment(image_release):
    api = image_release.api
    tag = "grp/de#m%o+x-v2.0.0"
    api.tags[tag] = image_release.commit
    api.seed_release(tag)
    client = release.GitHubReleases("o", "r", "t")
    segment = "grp%2Fde%23m%25o%2Bx-v2.0.0"

    assert client.get_tag_commit(tag) == image_release.commit
    assert client.get_release_by_tag(tag)["tag_name"] == tag
    assert api.calls == [("GET", f"/commits/{segment}"), ("GET", f"/releases/tags/{segment}")]


def test_get_tag_commit_keeps_non_404_errors_as_failures(image_release):
    api = image_release.api
    api.respond[("GET", f"/commits/{TAG}", 1)] = (500, "server error")
    with pytest.raises(SystemExit):
        release.GitHubReleases("o", "r", "t").get_tag_commit(TAG)


# --- `cmru publish --from-checkout --release-tag`: the metadata-only retry -----------

RETRY_TAG = "demo-v2.0.0"
PUSH_IMAGE = {"label": "push image", "argv": ["python3", "build-push.py", "--push"], "cwd": "."}


def _body_command(prefix: str) -> dict:
    return {
        "label": "release notes",
        "argv": ["cmru", "handler", "oci-image-release", "--prefix", prefix,
                 "--image", IMAGE, "--cwd", "."],
        "cwd": ".",
    }


@pytest.fixture
def retry_repo(tmp_path, monkeypatch):
    """A real tagged checkout with a bare origin; records handler runs and origin queries."""
    from cmru import release

    class _NoPublishedRelease:  # CMRU-SKIP-01: the retry consults the live body; none exists here
        def __init__(self, *_args):
            pass

        def get_release_by_tag(self, _tag):
            return None

    monkeypatch.setattr(release, "GitHubReleases", _NoPublishedRelease)

    def build(*, prefix: str = "demo", annotated: bool = False, push_tag: bool = True,
              step_env: dict | None = None, login: dict | None = None,
              clean_dirs: tuple[str, ...] = ()):
        from cmru.runner import StepConfig

        root = tmp_path / "repo"
        project_root = root / "demo"
        project_root.mkdir(parents=True)
        (project_root / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "t@t.invalid")
        _git(root, "config", "user.name", "t")
        _git(root, "add", "demo")
        _git(root, "commit", "-q", "-m", "release candidate")
        tag = f"{prefix}-v2.0.0"
        if annotated:
            _git(root, "tag", "-a", tag, "-m", "annotated release")
        else:
            _git(root, "tag", tag)
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", "-q", str(origin))
        _git(root, "remote", "add", "origin", str(origin))
        if push_tag:
            _git(root, "push", "-q", "origin", f"refs/tags/{tag}")
        body = _body_command(prefix)
        project = cli.ProjectConfig(
            name="demo", env={},
            steps={"push": [
                cli.Command(PUSH_IMAGE["label"], PUSH_IMAGE["argv"], project_root),
                cli.Command(body["label"], body["argv"], project_root),
            ]},
            prefix=f"{prefix}-v", cwd="demo", git_tag=True, changelog="CHANGES.md",
            project_root=project_root,
            runner_steps={"push": StepConfig(
                "push", [PUSH_IMAGE, body], None, [], None, list(clean_dirs), [], login,
                dict(step_env or {}), None,
            )},
            github_token="test-token",
        )
        return SimpleNamespace(
            root=root, project=project, tag=tag, body=body, origin=origin,
            commit=_git(root, "rev-parse", "HEAD"),
        )

    handler_runs: list = []
    origin_queries: list = []
    real_remote_git = cli.run_remote_git

    def remote_git(*args, **kwargs):
        origin_queries.append(args[1:])
        return real_remote_git(*args, **kwargs)

    monkeypatch.setattr(cli, "run_remote_git", remote_git)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_args: None)
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda candidate, step, repo, logs, **kwargs: handler_runs.append(
            (candidate, step, repo, kwargs.get("protected_env"))
        ),
    )
    return SimpleNamespace(build=build, handler_runs=handler_runs, origin_queries=origin_queries)


def _retry(repo, tag=None):
    return cli._run_oci_release_body_retry(
        repo.root, repo.project, tag or repo.tag, git_auth=None,
        github_config=cli.GitHubConfig("o", "r", "token", "user"),
        env_config=cli.ReleaseEnvConfig({}, None),
        gates_ran=True,  # CMRU-SKIP-01: nothing here records the gates fact, so declare it
    )


def test_release_body_retry_runs_only_the_declared_oci_handler_with_protected_identity(
    retry_repo,
):
    repo = retry_repo.build(
        step_env={"GITHUB_REPO": "declared-repo"},
        login={"registry": "ghcr.io"}, clean_dirs=("dist",),
    )

    assert _retry(repo) == (IMAGE, "2.0.0")

    assert len(retry_repo.handler_runs) == 1
    retry_project, step, ran_in, protected = retry_repo.handler_runs[0]
    assert step == "push" and ran_in == repo.root
    body_step = retry_project.runner_steps["push"]
    # Only the validated final command: the image push is never repeated.
    assert body_step.commands == [repo.body]
    # The push step's declared environment still applies to the body command, but the
    # image-push side effects (registry login, clean_dirs) are not repeated.
    assert body_step.step_env == {"GITHUB_REPO": "declared-repo"}
    assert body_step.login is None and body_step.clean_dirs == []
    assert protected == {"CMRU_RELEASE_TAG": repo.tag, "CMRU_RELEASE_COMMIT": repo.commit}
    # The declared project itself is untouched for any later caller.
    assert repo.project.runner_steps["push"].commands == [PUSH_IMAGE, repo.body]


def test_release_body_retry_peels_an_annotated_tag_with_url_reserved_prefix_characters(
    retry_repo,
):
    repo = retry_repo.build(prefix="grp/de#m%o", annotated=True)
    tag_object = _git(repo.root, "rev-parse", f"refs/tags/{repo.tag}")
    assert tag_object != repo.commit

    assert _retry(repo) == (IMAGE, "2.0.0")

    (_project, _step, _root, protected), = retry_repo.handler_runs
    assert protected == {"CMRU_RELEASE_TAG": repo.tag, "CMRU_RELEASE_COMMIT": repo.commit}
    assert len(retry_repo.origin_queries) == 1


@pytest.mark.parametrize("tag", [
    "other-v2.0.0", "demo-v2.0", "demo-v2.0.0+build", "demo-v2.0.0.dev1", "demo-v",
])
def test_release_body_retry_refuses_a_tag_that_is_not_this_projects_release(retry_repo, tag):
    repo = retry_repo.build()
    with pytest.raises(RefusedBeforeChange, match="--release-tag"):
        _retry(repo, tag)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_untracked_files_even_at_the_matching_tag(retry_repo):
    repo = retry_repo.build()
    (repo.root / "demo" / "stray.txt").write_text("not released\n", encoding="utf-8")

    with pytest.raises(RefusedBeforeChange, match="clean checkout"):
        _retry(repo)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_a_head_that_is_not_the_local_tag(retry_repo):
    repo = retry_repo.build()
    (repo.root / "demo" / "later.txt").write_text("after the release\n", encoding="utf-8")
    _git(repo.root, "add", "demo/later.txt")
    _git(repo.root, "commit", "-q", "-m", "after the release")

    with pytest.raises(RefusedBeforeChange, match="does not match local tag"):
        _retry(repo)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_an_absent_local_tag(retry_repo):
    repo = retry_repo.build()
    _git(repo.root, "tag", "-d", repo.tag)

    with pytest.raises(RefusedBeforeChange, match="local release tag .* is absent"):
        _retry(repo)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_a_moved_origin_tag_at_a_clean_head_before_running(
    retry_repo,
):
    repo = retry_repo.build(annotated=True)
    other = _git(repo.root, "commit-tree", "HEAD^{tree}", "-m", "other tag target")
    _git(repo.root, "push", "-q", "--force", "origin", f"{other}:refs/tags/{repo.tag}")

    with pytest.raises(RefusedBeforeChange, match=f"origin tag .* is {other[:12]}; expected"):
        _retry(repo)
    assert retry_repo.handler_runs == []
    assert _git(repo.root, "rev-parse", f"{repo.tag}^{{commit}}") == repo.commit


def test_release_body_retry_refuses_an_origin_without_the_tag_as_absent(retry_repo):
    repo = retry_repo.build(push_tag=False)

    with pytest.raises(RefusedBeforeChange, match="origin tag .* is absent"):
        _retry(repo)
    assert retry_repo.handler_runs == []
    assert len(retry_repo.origin_queries) == 1


def test_release_body_retry_never_reads_an_inaccessible_origin_as_absent(retry_repo):
    repo = retry_repo.build()
    _git(repo.root, "config", "protocol.file.allow", "never")
    _git(
        repo.root, "remote", "set-url", "origin",
        "file:///tmp/cmru-unreachable-origin.git",
    )

    with pytest.raises(RuntimeError, match="could not verify") as caught:
        _retry(repo)
    assert not isinstance(caught.value, RefusedBeforeChange)
    assert retry_repo.handler_runs == []


@pytest.mark.parametrize("advertised", [
    "not-a-ref-record\n",
    f"{'a' * 40}\trefs/tags/{RETRY_TAG}^{{}}\n",  # a peeled ref without its direct ref
    f"{'a' * 39}\trefs/tags/{RETRY_TAG}\n",
])
def test_release_body_retry_refuses_a_malformed_origin_advertisement(
    retry_repo, monkeypatch, advertised,
):
    repo = retry_repo.build()
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, advertised, ""),
    )
    with pytest.raises(RuntimeError, match="malformed|without its direct ref"):
        _retry(repo)
    assert retry_repo.handler_runs == []


@pytest.mark.parametrize("tag, fragment", [
    # A SemVer pre-release can still carry a local or dev segment: not a release.
    ("demo-v2.0.0-rc+build", "is not a released version"),
    ("demo-v2.0.0-rc.dev1", "is not a released version"),
    # A valid CMRU version that no OCI tag can carry.
    ("demo-v2.0.0-rc/1", "is not a valid OCI release version"),
    # A valid image tag that Git cannot store as a tag ref.
    ("demo-v2.0.0-rc..1", "is not a valid Git tag"),
])
def test_release_body_retry_refuses_a_version_no_release_tag_can_carry(retry_repo, tag, fragment):
    repo = retry_repo.build()
    with pytest.raises(RefusedBeforeChange, match=fragment):
        _retry(repo, tag)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_a_project_without_an_oci_release_command(retry_repo):
    from dataclasses import replace

    repo = retry_repo.build()
    push = repo.project.runner_steps["push"]
    repo.project.runner_steps["push"] = replace(push, commands=[PUSH_IMAGE])

    with pytest.raises(RefusedBeforeChange, match="requires a configured `oci-image-release`"):
        _retry(repo)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def _failing_local_git(monkeypatch, *, fails):
    """Fail the selected local Git calls; every other call runs for real."""
    real = cli.run_local_git

    def run(repo_root, *args, **kwargs):
        answer = fails(args)
        if answer is not None:
            return subprocess.CompletedProcess(("git", *args), *answer)
        return real(repo_root, *args, **kwargs)

    monkeypatch.setattr(cli, "run_local_git", run)


def test_release_body_retry_never_reads_an_uninspectable_checkout_as_clean(retry_repo, monkeypatch):
    repo = retry_repo.build()
    _failing_local_git(
        monkeypatch, fails=lambda args: (128, "", "index locked") if args[0] == "status" else None,
    )

    with pytest.raises(RuntimeError, match="cannot inspect checkout .*: index locked") as caught:
        _retry(repo)
    assert not isinstance(caught.value, RefusedBeforeChange)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_release_body_retry_refuses_a_malformed_local_tag_commit(retry_repo, monkeypatch):
    repo = retry_repo.build()
    _failing_local_git(
        monkeypatch,
        fails=lambda args: (0, "not-a-commit\n", "") if args[:2] == ("rev-parse", "--verify") else None,
    )

    with pytest.raises(RuntimeError, match="invalid commit id for release tag"):
        _retry(repo)
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def _origin_check_that(monkeypatch, repo, effect):
    """Answer origin's tag commit truthfully after applying ``effect`` to the checkout."""

    def origin_commit(repo_root, tag, *, git_auth):
        effect(repo_root)
        return repo.commit

    monkeypatch.setattr(cli, "_release_tag_origin_commit", origin_commit)


def test_release_body_retry_refuses_a_head_that_moves_during_origin_verification(
    retry_repo, monkeypatch,
):
    repo = retry_repo.build()
    _origin_check_that(
        monkeypatch, repo,
        lambda root: _git(root, "commit", "-q", "--allow-empty", "-m", "concurrent commit"),
    )

    with pytest.raises(RefusedBeforeChange, match="HEAD changed during origin tag verification"):
        _retry(repo)
    assert retry_repo.handler_runs == []


def test_release_body_retry_refuses_a_checkout_dirtied_during_origin_verification(
    retry_repo, monkeypatch,
):
    repo = retry_repo.build()
    _origin_check_that(
        monkeypatch, repo,
        lambda root: (root / "demo" / "late.txt").write_text("concurrent edit\n", encoding="utf-8"),
    )

    with pytest.raises(RefusedBeforeChange, match="became dirty during origin tag verification"):
        _retry(repo)
    assert retry_repo.handler_runs == []


def test_release_body_retry_never_reads_an_unrecheckable_checkout_as_clean(retry_repo, monkeypatch):
    repo = retry_repo.build()
    origin_checked = []
    _origin_check_that(monkeypatch, repo, lambda _root: origin_checked.append(True))
    _failing_local_git(
        monkeypatch,
        fails=lambda args: (128, "", "") if args[0] == "status" and origin_checked else None,
    )

    with pytest.raises(RuntimeError, match="cannot recheck checkout .*: no diagnostic output") as caught:
        _retry(repo)
    assert not isinstance(caught.value, RefusedBeforeChange)
    assert origin_checked == [True] and retry_repo.handler_runs == []


def test_release_body_retry_never_runs_an_absent_declared_push_command(retry_repo, monkeypatch):
    """Fail closed if the validated image and the declared step ever disagree."""
    from dataclasses import replace

    from cmru.errors import StepUnavailable

    repo = retry_repo.build()
    push = repo.project.runner_steps["push"]
    repo.project.runner_steps["push"] = replace(push, commands=[])
    monkeypatch.setattr(cli, "_validated_oci_release_image", lambda *_args: IMAGE)

    with pytest.raises(StepUnavailable, match="declared push command is absent"):
        _retry(repo)
    assert retry_repo.handler_runs == []


def _dispatch_retry(monkeypatch, repo, *extra: str) -> int:
    loaded = (
        repo.root, {"demo": repo.project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    config_path = repo.root / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config_path)
    monkeypatch.setattr(cli, "load_config", lambda _path, **_kwargs: loaded)
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    return cli.main([
        "publish", "demo", "--from-checkout", "--release-tag", repo.tag,
        "--config", str(config_path), *extra,
    ])


def test_release_body_dry_run_with_origin_unavailable_shows_only_the_handler(
    retry_repo, monkeypatch, capsys,
):
    repo = retry_repo.build()
    _git(repo.root, "remote", "set-url", "origin", str(repo.origin.parent / "missing.git"))

    assert _dispatch_retry(monkeypatch, repo, "--dry-run") == 0

    out = capsys.readouterr().out
    assert retry_repo.origin_queries == [] and retry_repo.handler_runs == []
    assert "oci-image-release" in out and "build-push.py" not in out
    assert f"local tag commit {repo.commit[:12]}" in out


def test_release_body_dry_run_still_refuses_an_untracked_file(retry_repo, monkeypatch):
    repo = retry_repo.build()
    (repo.root / "demo" / "stray.txt").write_text("not released\n", encoding="utf-8")

    assert _dispatch_retry(monkeypatch, repo, "--dry-run") == exit_codes.REFUSED
    assert retry_repo.origin_queries == [] and retry_repo.handler_runs == []


def test_release_body_retry_through_publish_refuses_a_moved_origin_tag(retry_repo, monkeypatch):
    repo = retry_repo.build()
    other = _git(repo.root, "commit-tree", "HEAD^{tree}", "-m", "other tag target")
    _git(repo.root, "push", "-q", "--force", "origin", f"{other}:refs/tags/{repo.tag}")

    assert _dispatch_retry(monkeypatch, repo) == exit_codes.REFUSED
    assert retry_repo.handler_runs == [] and len(retry_repo.origin_queries) == 1


def test_publish_release_tag_refuses_more_than_one_selected_project(retry_repo, monkeypatch, capsys):
    repo = retry_repo.build()
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo", "other"])
    loaded = (
        repo.root, {"demo": repo.project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    config_path = repo.root / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config_path)
    monkeypatch.setattr(cli, "load_config", lambda _path, **_kwargs: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)

    assert cli.main([
        "publish", "demo", "--from-checkout", "--release-tag", repo.tag,
        "--config", str(config_path),
    ]) == 2

    captured = capsys.readouterr()
    assert "--release-tag requires exactly one selected project" in captured.out + captured.err
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


@pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["run", "dry-run"])
def test_publish_release_tag_refuses_a_project_spanning_more_than_one_git_family(
    retry_repo, monkeypatch, capsys, extra,
):
    repo = retry_repo.build()
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {repo.root: [repo.project], repo.origin: [repo.project]},
    )

    assert _dispatch_retry(monkeypatch, repo, *extra) == 1

    captured = capsys.readouterr()
    assert "could not determine exactly one Git family for --release-tag" in captured.out + captured.err
    assert retry_repo.handler_runs == [] and retry_repo.origin_queries == []


def test_publish_release_tag_uses_the_explicit_secret_file_and_retry_workflow(
    monkeypatch, tmp_path,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, prefix="demo-v", cwd="demo",
        project_root=project_root, runner_steps={}, github_token="token",
    )
    config_path = tmp_path / "cmru.orchestration.toml"
    secret_file = tmp_path / "selected.secret.toml"
    loaded = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    load_calls = []
    retry_calls = []
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config_path)
    monkeypatch.setattr(
        cli, "load_config",
        lambda path, **kwargs: load_calls.append((path, kwargs)) or loaded,
    )
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_args: None)
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(cli, "_configs_for_git_family", lambda *_args: {"demo": project})
    monkeypatch.setattr(
        cli, "_run_oci_release_body_retry",
        lambda root, selected, tag, **kwargs: retry_calls.append(
            (root, selected.name, tag, kwargs)
        ) or (IMAGE, "2.0.0"),
    )

    assert cli.main([
        "publish", "demo", "--from-checkout", "--release-tag", TAG,
        "--secret-file", str(secret_file), "--config", str(config_path),
    ]) == 0

    assert load_calls == [(config_path, {"secret_file": secret_file})]
    assert retry_calls == [(
        tmp_path, "demo", TAG, {
            "git_auth": cli.GitHubGitAuth("o", "r", "token"),
            "github_config": loaded[-2],
            "env_config": loaded[-1],
            "gates_skipped": None,
            "gates_ran": False,
        },
    )]


# --- oracle 1, 5: the exact body, created under the protected tag and commit ------


def test_a_valid_image_release_publishes_the_pull_coordinate_then_the_tagged_section_once(
    image_release,
):
    fixture = image_release
    # Mutable text must never leak in: a dirty working tree AND a later commit that
    # rewrites the notes, while the protected commit stays the tagged candidate.
    (fixture.project / "CHANGES.md").write_text(
        CHANGES.replace("Repaired the ordering.", "MUTABLE WORKING TREE"), encoding="utf-8",
    )
    _git(fixture.root, "add", "demo/CHANGES.md")
    _git(fixture.root, "commit", "-q", "-m", "later notes")
    (fixture.project / "CHANGES.md").write_text("# dirty\n", encoding="utf-8")

    assert _publish(fixture.project) == 0

    api = fixture.api
    assert api.releases[TAG]["body"] == EXPECTED_BODY
    assert EXPECTED_BODY.count("## [2.0.0] - 2026-10-13") == 1
    assert EXPECTED_BODY.startswith(
        f"**Image:** `{IMAGE}:2.0.0`\n\nPull: `docker pull {IMAGE}:2.0.0`\n\n## [2.0.0]"
    )
    for forbidden in ("MUTABLE", "Artifact", "SHA256", "sha256", "<artifact", "<sha256"):
        assert forbidden not in api.releases[TAG]["body"]
    # Created exactly once, under the protected tag and candidate commit, with the
    # tag checked before the write, the release AND the tag read back after it, and
    # nothing else mutated.
    assert api.created == [{
        "tag_name": TAG, "name": TAG, "body": EXPECTED_BODY, "target_commitish": fixture.commit,
    }]
    assert api.calls == [
        ("GET", f"/commits/{TAG}"),
        ("GET", f"/releases/tags/{TAG}"),
        ("POST", "/releases"),
        ("GET", "/releases/100"),  # the read-back is by the ID the create returned
        ("GET", f"/commits/{TAG}"),
    ]
    assert api.tags[TAG] == fixture.commit
    assert fixture.pauses == []  # visible at once: nothing waited


def test_a_project_without_a_changelog_publishes_only_the_image_coordinate(
    image_release, monkeypatch,
):
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "none")
    assert _publish(image_release.project) == 0
    assert image_release.api.releases[TAG]["body"] == (
        f"**Image:** `{IMAGE}:2.0.0`\n\nPull: `docker pull {IMAGE}:2.0.0`"
    )


def test_an_undeclared_notes_source_refuses_before_any_github_call(
    image_release, monkeypatch, capsys,
):
    monkeypatch.delenv("CMRU_RELEASE_NOTES_SOURCE")
    assert _publish(image_release.project) == exit_codes.REFUSED
    assert image_release.api.calls == []
    assert "CMRU_RELEASE_NOTES_SOURCE is not set" in capsys.readouterr().err


def test_a_missing_version_section_in_the_tagged_commit_refuses_before_github(
    image_release, monkeypatch, capsys,
):
    (image_release.project / "CHANGES.md").write_text(
        CHANGES.replace("## [2.0.0]", "## [2.0.1]"), encoding="utf-8",
    )
    _git(image_release.root, "add", "demo/CHANGES.md")
    _git(image_release.root, "commit", "-q", "-m", "wrong notes")
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", _git(image_release.root, "rev-parse", "HEAD"))
    assert _publish(image_release.project) == exit_codes.REFUSED
    assert image_release.api.calls == []
    assert "no `## [2.0.0] - YYYY-MM-DD` section" in capsys.readouterr().err


# --- oracle 3: a retry updates the body and never touches an asset ------------------


def test_a_retry_updates_the_body_and_preserves_every_existing_asset(image_release):
    api = image_release.api
    assets = (
        {"id": 1, "name": "demo-2.0.0-py3-none-any.whl"},
        {"id": 2, "name": "demo-2.0.0-py3-none-any.whl.sha256"},
        {"id": 3, "name": TAG},  # a name the release itself uses
        {"id": 4, "name": "unrelated.txt"},
    )
    release_id = api.seed_release(TAG, body="stale body from a failed attempt", assets=assets)

    assert _publish(image_release.project) == 0
    assert _publish(image_release.project) == 0  # idempotent retry

    assert api.releases[TAG]["body"] == EXPECTED_BODY
    assert api.releases[TAG]["id"] == release_id  # updated in place, never recreated
    assert api.created == []
    assert api.mutations == [("PATCH", f"/releases/{release_id}")] * 2
    assert not any("assets" in path for _method, path in api.calls)  # never even listed
    assert api.assets[release_id] == [dict(item) for item in assets]


# --- oracle 4: identity mismatches refuse before any mutation -----------------------


def test_a_remote_tag_at_another_commit_refuses_before_any_mutation(image_release, capsys):
    api = image_release.api
    api.tags[TAG] = OTHER_COMMIT
    release_id = api.seed_release(TAG)
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert api.mutations == []
    assert api.releases[TAG]["body"] == "old body"
    assert api.releases[TAG]["id"] == release_id
    assert f"the remote tag points at {OTHER_COMMIT}" in capsys.readouterr().err


def test_a_protected_commit_that_is_not_the_remote_tags_commit_refuses_before_any_mutation(
    image_release, monkeypatch, capsys,
):
    (image_release.project / "CHANGES.md").write_text(CHANGES + "\n", encoding="utf-8")
    _git(image_release.root, "add", "demo/CHANGES.md")
    _git(image_release.root, "commit", "-q", "-m", "a later, real commit")
    later = _git(image_release.root, "rev-parse", "HEAD")
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", later)
    assert later != image_release.commit
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert image_release.api.mutations == []
    assert f"not the release commit {later}" in capsys.readouterr().err


def test_a_tag_the_remote_does_not_have_is_never_created_by_the_handler(image_release, capsys):
    image_release.api.tags.clear()
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert image_release.api.mutations == []
    error = capsys.readouterr().err
    assert "the remote has no such tag" in error and "never creates a tag itself" in error


def test_an_existing_release_that_targets_another_commit_refuses_before_any_mutation(
    image_release, capsys,
):
    api = image_release.api
    api.seed_release(TAG, target=OTHER_COMMIT)
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert api.mutations == []
    assert api.releases[TAG]["body"] == "old body"
    assert f"it targets commit {OTHER_COMMIT}" in capsys.readouterr().err


def test_a_tag_that_moves_during_create_is_reported_as_written_and_not_rolled_back(
    image_release, capsys,
):
    """The create cannot be made atomic with the tag check: the release stays, and says so."""
    api = image_release.api
    api.on_create = lambda: api.tags.__setitem__(TAG, OTHER_COMMIT)
    assert _publish(image_release.project) == exit_codes.FAILURE
    # The release WAS created and nothing undid it: the report must not claim otherwise.
    assert api.mutations == [("POST", "/releases")]
    assert api.releases[TAG]["body"] == EXPECTED_BODY
    error = capsys.readouterr().err
    assert "was written, but the remote tag now points at" in error
    assert "NOT rolled back" in error and "inspect the release and the tag before any retry" in error
    assert "refusing" not in error and "no mutation" not in error.lower()


def _replace_release(api: FakeGitHub):
    """An action that swaps the TAG's release for a different one (new release ID)."""
    return lambda: api.seed_release(TAG, body="a different release")


def test_a_release_id_change_before_the_patch_sends_no_mutation(image_release, capsys):
    api = image_release.api
    release_id = api.seed_release(TAG)
    # Calls 1 and 2 of GET /releases/tags are the lookup and the pre-PATCH re-fetch.
    api.before[("GET", f"/releases/tags/{TAG}", 2)] = _replace_release(api)
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert api.mutations == []  # no PATCH, no POST, no asset call
    assert api.releases[TAG]["body"] == "a different release"
    assert f"its release ID changed from {release_id} to {release_id + 1}" in capsys.readouterr().err


def test_a_tag_target_change_before_the_patch_sends_no_mutation(image_release, capsys):
    api = image_release.api
    api.seed_release(TAG)
    # Calls 1 and 2 of GET /commits/<tag> are the initial check and the pre-PATCH re-check.
    api.before[("GET", f"/commits/{TAG}", 2)] = (
        lambda: api.tags.__setitem__(TAG, OTHER_COMMIT)
    )
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert api.mutations == []
    assert api.releases[TAG]["body"] == "old body"
    error = capsys.readouterr().err
    assert f"its tag target changed from {image_release.commit} to '{OTHER_COMMIT}'" in error


def test_a_release_id_change_after_the_patch_is_reported_as_written(image_release, capsys):
    api = image_release.api
    release_id = api.seed_release(TAG)
    api.before[("GET", f"/releases/{release_id}", 1)] = _replace_release(api)  # the read-back by ID
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert len(api.mutations) == 1 and api.mutations[0][0] == "PATCH"  # the write happened
    error = capsys.readouterr().err
    assert "was written, but its release ID is now" in error and "NOT rolled back" in error


def test_a_tag_move_after_the_patch_is_reported_as_written(image_release, capsys):
    api = image_release.api
    api.seed_release(TAG)
    api.before[("GET", f"/commits/{TAG}", 3)] = (
        lambda: api.tags.__setitem__(TAG, OTHER_COMMIT)
    )
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert len(api.mutations) == 1 and api.mutations[0][0] == "PATCH"
    assert api.releases[TAG]["body"] == EXPECTED_BODY  # the new body is in place
    error = capsys.readouterr().err
    assert "was written, but the remote tag now points at" in error
    assert "NOT rolled back" in error and "refusing" not in error


# --- every write is read back: the INTENDED body must be what GitHub now holds ----------


def _release_get_after_the_write(api: FakeGitHub, *, existing: bool) -> tuple[str, str, int]:
    """Key of the FIRST post-write read: ``GET /releases/<id>`` by the ID the create is
    about to receive (the fake's next ID) or by the update's captured ID."""
    release_id = api.releases[TAG]["id"] if existing else api._next_id
    return ("GET", f"/releases/{release_id}", 1)


def _prepare_write(api: FakeGitHub, existing: bool) -> None:
    if existing:
        api.seed_release(TAG)


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_a_release_replaced_after_the_write_with_the_tag_unchanged_is_reported_as_written(
    image_release, capsys, existing,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.before[_release_get_after_the_write(api, existing=existing)] = (
        lambda: api.seed_release(TAG, body="an intruder's body")  # new release ID, same tag
    )

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert api.tags[TAG] == image_release.commit  # the Git tag never moved
    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]
    error = capsys.readouterr().err
    assert "was written, but its release ID is now" in error
    assert "NOT rolled back" in error and "refusing" not in error


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_a_body_overwritten_after_the_write_is_reported_even_with_id_and_tag_intact(
    image_release, capsys, existing,
):
    api = image_release.api
    _prepare_write(api, existing)

    def overwrite():
        api.releases[TAG]["body"] = "someone else's notes"  # ONLY the body changes

    api.before[_release_get_after_the_write(api, existing=existing)] = overwrite

    assert _publish(image_release.project) == exit_codes.FAILURE

    error = capsys.readouterr().err
    assert "was written, but its body is not the intended body" in error
    assert "its release ID" not in error and "remote tag now points" not in error
    assert "NOT rolled back" in error and "inspect the release and the tag before any retry" in error


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_a_post_write_transport_failure_says_the_release_was_written(
    image_release, capsys, existing,
):
    from urllib.error import URLError

    api = image_release.api
    _prepare_write(api, existing)

    def unreachable():
        raise URLError("connection reset")

    api.before[_release_get_after_the_write(api, existing=existing)] = unreachable

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]
    error = capsys.readouterr().err
    assert "WAS WRITTEN, but reading it back to verify it failed" in error
    assert "URLError" in error and "NOT rolled back" in error
    assert "Traceback" not in error and "refusing" not in error


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize("status, body, fragment", [
    (200, "not json {", "WAS WRITTEN, but reading it back to verify it failed"),
    (500, "upstream exploded", "WAS WRITTEN, but reading it back to verify it failed"),
    (200, "[]", "was written, but the release read-back is missing or malformed"),
    (200, '{"id": "x", "tag_name": "demo-v2.0.0", "body": "b"}', "its release ID is now 'x'"),
], ids=["undecodable", "http-500", "wrong-shape", "string-id"])
def test_a_missing_or_malformed_read_back_is_never_reported_as_success(
    image_release, capsys, existing, status, body, fragment,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.respond[_release_get_after_the_write(api, existing=existing)] = (status, body)

    assert _publish(image_release.project) == exit_codes.FAILURE

    error = capsys.readouterr().err
    assert fragment in error
    assert "NOT rolled back" in error
    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]  # no rollback call


def test_a_write_that_fails_in_transit_is_reported_as_unknown_not_as_refused(
    image_release, capsys,
):
    from urllib.error import URLError

    api = image_release.api
    api.seed_release(TAG)

    def drop_the_patch():
        raise URLError("timed out")

    api.before[("PATCH", f"/releases/{api.releases[TAG]['id']}", 1)] = drop_the_patch

    assert _publish(image_release.project) == exit_codes.FAILURE

    error = capsys.readouterr().err
    assert "MAY OR MAY NOT have been written" in error and "nothing was rolled back" in error
    assert "Traceback" not in error


def test_a_commit_target_that_changes_between_the_two_release_reads_sends_no_patch(
    image_release, capsys,
):
    """ID and tag stay put; only target_commitish becomes a different full SHA."""
    api = image_release.api
    release_id = api.seed_release(TAG)  # target "main": not a commit claim, so the first read passes

    def retarget():
        api.releases[TAG]["target_commitish"] = OTHER_COMMIT

    api.before[("GET", f"/releases/tags/{TAG}", 2)] = retarget  # the pre-PATCH re-fetch

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert api.mutations == []
    assert api.releases[TAG]["id"] == release_id and api.tags[TAG] == image_release.commit
    assert api.releases[TAG]["body"] == "old body"
    assert f"it now targets commit {OTHER_COMMIT}, not the release commit {image_release.commit}" in (
        capsys.readouterr().err
    )


def test_a_branch_name_as_the_commit_target_is_not_a_commit_claim(image_release):
    api = image_release.api
    api.seed_release(TAG, target="main")
    assert _publish(image_release.project) == 0
    assert api.releases[TAG]["body"] == EXPECTED_BODY


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_a_release_that_reads_back_under_another_tag_is_reported_as_written(
    image_release, capsys, existing,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.before[_release_get_after_the_write(api, existing=existing)] = (
        lambda: api.releases[TAG].update(tag_name="demo-v9.9.9")  # same ID and body, other tag
    )

    assert _publish(image_release.project) == exit_codes.FAILURE

    error = capsys.readouterr().err
    assert "was written, but it now reports tag 'demo-v9.9.9'" in error
    assert "NOT rolled back" in error and "refusing" not in error
    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]


def test_a_release_commit_that_is_not_a_full_sha_is_refused_before_any_request(
    image_release, capsys,
):
    client = release.GitHubReleases("o", "r", "t")

    for bad in ("abc123", "A" * 40, "", None):
        with pytest.raises(SystemExit):
            client.publish_release_body(TAG, TAG, "body", tag_commit=bad)

    assert image_release.api.calls == []  # not even the tag lookup happened
    assert capsys.readouterr().err.count("no valid 40-hex release commit was supplied") == 4


@pytest.mark.parametrize("answer, fragment", [
    ("[]", "the returned record is malformed"),
    ('{"id": 0, "tag_name": "demo-v2.0.0"}', "the returned release ID is malformed"),
    ('{"id": true, "tag_name": "demo-v2.0.0"}', "the returned release ID is malformed"),
    ('{"id": 7, "tag_name": "demo-v9.9.9"}', "the returned record is for tag 'demo-v9.9.9'"),
], ids=["not-a-record", "zero-id", "boolean-id", "other-tag"])
def test_a_malformed_existing_release_record_refuses_before_any_mutation(
    image_release, capsys, answer, fragment,
):
    api = image_release.api
    api.respond[("GET", f"/releases/tags/{TAG}", 1)] = (200, answer)

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert api.mutations == []
    error = capsys.readouterr().err
    assert f"refusing to publish GitHub Release {TAG}: {fragment}" in error


def test_release_response_id_must_match_the_expected_id():
    assert release.GitHubReleases._usable_response_id(
        TAG, {"id": 7, "tag_name": TAG}, expected=8,
    ) is None


def test_readback_by_tag_refuses_a_malformed_release_id(monkeypatch, capsys):
    client = release.GitHubReleases("o", "r", "t")
    monkeypatch.setattr(client, "get_release_by_tag", lambda _tag: {
        "id": True, "tag_name": TAG, "body": "notes", "target_commitish": "main",
    })
    monkeypatch.setattr(client, "get_tag_commit", lambda _tag: OTHER_COMMIT)

    with pytest.raises(SystemExit):
        client._confirm_write(
            TAG, OTHER_COMMIT, "notes", release_id=None,
            cause=None, attempts=1,
        )

    assert "release ID is malformed (True)" in capsys.readouterr().err


def test_release_readback_pause_delegates_to_sleep(monkeypatch):
    pauses = []
    monkeypatch.setattr(release.time, "sleep", pauses.append)

    release._pause(0.25)

    assert pauses == [0.25]


# --- a write that was SENT may have been applied: read back, never resend, never a clean refusal ---

#: Answers a write can give that are NOT a usable success record. Each one, after the
#: POST/PATCH was sent, means the write MAY have landed.
UNUSABLE_ANSWERS = [
    (500, "upstream exploded"),
    (502, ""),
    (201, "not json {"),
    (201, "[]"),
    (201, '{"id": 0, "tag_name": "demo-v2.0.0"}'),
    (201, '{"id": true, "tag_name": "demo-v2.0.0"}'),
    (201, '{"id": -3, "tag_name": "demo-v2.0.0"}'),
    (201, '{"id": "7", "tag_name": "demo-v2.0.0"}'),
    (201, '{"id": 9, "tag_name": "demo-v9.9.9"}'),
]
UNUSABLE_IDS = [
    "http-500", "http-502-empty", "undecodable", "not-a-record", "zero-id", "boolean-id",
    "negative-id", "string-id", "other-tag",
]


def _write_key(api: FakeGitHub, *, existing: bool) -> tuple[str, str, int]:
    return ("PATCH", f"/releases/{api.releases[TAG]['id']}", 1) if existing else ("POST", "/releases", 1)


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize("status, body", UNUSABLE_ANSWERS, ids=UNUSABLE_IDS)
def test_an_applied_write_with_an_unusable_answer_is_verified_by_read_back_and_never_resent(
    image_release, capsys, existing, status, body,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.respond_after[_write_key(api, existing=existing)] = (status, body)

    assert _publish(image_release.project) == 0  # the exact release is there: success

    assert api.releases[TAG]["body"] == EXPECTED_BODY
    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]  # sent ONCE
    error = capsys.readouterr().err
    assert "got no usable answer" in error and "it was NOT resent" in error
    assert "refusing" not in error and "UNKNOWN" not in error


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize("status, body", UNUSABLE_ANSWERS, ids=UNUSABLE_IDS)
def test_an_unusable_answer_whose_write_cannot_be_confirmed_is_unknown_not_refused_or_retriable(
    image_release, capsys, existing, status, body,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.respond[_write_key(api, existing=existing)] = (status, body)  # the fake never applies it

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]  # never resent
    error = capsys.readouterr().err
    assert "has an UNKNOWN outcome" in error
    assert "MAY OR MAY NOT have been written and nothing was rolled back" in error
    assert "CMRU never resends a create or an update" in error
    assert "inspect the release and the tag before any retry" in error
    assert "refusing" not in error and "Traceback" not in error
    # The read-back is by the captured ID for an update; a create with no usable ID falls
    # back to the tag, and one with a usable ID reads that ID.
    if existing:
        assert ("GET", f"/releases/{api.releases[TAG]['id']}") in api.calls
        assert api.releases[TAG]["body"] == "old body"
    else:
        assert TAG not in api.releases


def test_a_create_with_a_lost_answer_reads_the_release_by_tag_and_verifies_it(image_release):
    api = image_release.api
    api.respond_after[("POST", "/releases", 1)] = (500, "lost")

    assert _publish(image_release.project) == 0

    # No usable ID came back, so the read-back was by TAG (never by an invented ID).
    assert ("GET", f"/releases/tags/{TAG}") in api.calls[2:]
    assert not any(path.removeprefix("/releases/").isdigit() for _m, path in api.calls)


def test_a_create_with_a_lost_answer_and_a_changed_body_is_unknown_not_success(image_release, capsys):
    api = image_release.api
    api.respond_after[("POST", "/releases", 1)] = (500, "lost")
    api.before[("GET", f"/releases/tags/{TAG}", 2)] = (
        lambda: api.releases[TAG].update(body="someone else's notes")
    )

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert [m for m, _p in api.mutations] == ["POST"]
    error = capsys.readouterr().err
    assert "has an UNKNOWN outcome: GitHub answered HTTP 500 (lost)" in error
    assert "the read-back found that its body is not the intended body" in error


def test_a_create_with_a_lost_answer_and_an_unreadable_read_back_is_unknown(image_release, capsys):
    api = image_release.api
    api.respond_after[("POST", "/releases", 1)] = (500, "lost")
    api.respond[("GET", f"/releases/tags/{TAG}", 2)] = (500, "read failed too")

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert [m for m, _p in api.mutations] == ["POST"]
    error = capsys.readouterr().err
    assert "has an UNKNOWN outcome" in error and "reading it back failed" in error
    assert "CMRU never resends" in error


def test_a_create_with_a_lost_answer_waits_for_visibility_by_tag_then_verifies(image_release):
    api = image_release.api
    api.respond_after[("POST", "/releases", 1)] = (500, "lost")
    api.respond[("GET", f"/releases/tags/{TAG}", 2)] = (404, "{}")  # not served yet

    assert _publish(image_release.project) == 0

    assert [m for m, _p in api.mutations] == ["POST"]
    assert len(image_release.pauses) == 1  # one deterministic pause between two reads


def test_a_create_with_a_lost_answer_that_never_becomes_visible_is_unknown_after_bounded_reads(
    image_release, capsys,
):
    api = image_release.api
    api.respond[("POST", "/releases", 1)] = (500, "lost")  # the fake never applies it

    assert _publish(image_release.project) == exit_codes.FAILURE

    tag_reads = [call for call in api.calls if call == ("GET", f"/releases/tags/{TAG}")]
    assert len(tag_reads) == 1 + release.READBACK_ATTEMPTS  # the lookup, then finite reads
    assert len(image_release.pauses) == release.READBACK_ATTEMPTS - 1
    assert [m for m, _p in api.mutations] == ["POST"]
    assert f"no release for the tag was visible after {release.READBACK_ATTEMPTS} read(s)" in (
        capsys.readouterr().err
    )


# --- a create reads its release BY ID with bounded retries for "not yet visible" ---------


def test_a_just_created_release_that_is_not_yet_visible_is_waited_for_not_called_replaced(
    image_release,
):
    api = image_release.api
    api.respond[("GET", "/releases/100", 1)] = (404, "{}")
    api.respond[("GET", "/releases/100", 2)] = (404, "{}")

    assert _publish(image_release.project) == 0

    assert [m for m, _p in api.mutations] == ["POST"]  # never created twice
    assert len(api.created) == 1
    assert len(image_release.pauses) == 2  # two misses, two pauses, then it was served
    assert api.releases[TAG]["body"] == EXPECTED_BODY


def test_a_created_release_that_stays_invisible_is_reported_after_a_finite_bounded_read(
    image_release, capsys,
):
    api = image_release.api
    for attempt in range(1, release.READBACK_ATTEMPTS + 1):
        api.respond[("GET", "/releases/100", attempt)] = (404, "{}")

    assert _publish(image_release.project) == exit_codes.FAILURE

    reads = [call for call in api.calls if call == ("GET", "/releases/100")]
    assert len(reads) == release.READBACK_ATTEMPTS  # finite
    assert len(image_release.pauses) == release.READBACK_ATTEMPTS - 1
    assert [m for m, _p in api.mutations] == ["POST"]  # no second create, no PATCH
    error = capsys.readouterr().err
    assert f"was written, but release 100 was not visible after {release.READBACK_ATTEMPTS} read(s)" in error
    assert "not yet visible, or removed" in error
    assert "its release ID is now" not in error  # invisible is NOT "replaced"
    assert "NOT rolled back" in error and "inspect the release and the tag before any retry" in error


def test_the_create_backoff_is_finite_and_the_update_read_is_a_single_attempt(image_release):
    assert release.READBACK_ATTEMPTS == 5
    assert len(release._READBACK_PAUSES) >= release.READBACK_ATTEMPTS - 1
    api = image_release.api
    release_id = api.seed_release(TAG)
    api.respond[("GET", f"/releases/{release_id}", 1)] = (404, "{}")

    with pytest.raises(SystemExit):
        release.GitHubReleases("o", "r", "t").publish_release_body(
            TAG, TAG, EXPECTED_BODY, tag_commit=image_release.commit,
        )

    assert image_release.pauses == []  # an existing release is not waited for
    assert [call for call in api.calls if call == ("GET", f"/releases/{release_id}")] == [
        ("GET", f"/releases/{release_id}")
    ]


# --- target_commitish is verified after BOTH the create and the PATCH ---------------------


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_a_release_retargeted_to_another_commit_after_the_write_is_reported_as_written(
    image_release, capsys, existing,
):
    api = image_release.api
    _prepare_write(api, existing)
    api.before[_release_get_after_the_write(api, existing=existing)] = (
        lambda: api.releases[TAG].update(target_commitish=OTHER_COMMIT)  # ID, tag, body intact
    )

    assert _publish(image_release.project) == exit_codes.FAILURE

    assert [m for m, _p in api.mutations] == ["PATCH" if existing else "POST"]
    error = capsys.readouterr().err
    assert f"was written, but it now targets commit {OTHER_COMMIT}, not the release commit {image_release.commit}" in error
    assert "NOT rolled back" in error and "refusing" not in error
    assert "its release ID" not in error and "its body" not in error


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize("target", ["main", "", "refs/heads/release"], ids=["branch", "empty", "ref"])
def test_a_non_commit_target_after_the_write_is_not_a_commit_claim(image_release, existing, target):
    api = image_release.api
    _prepare_write(api, existing)
    api.before[_release_get_after_the_write(api, existing=existing)] = (
        lambda: api.releases[TAG].update(target_commitish=target)
    )

    assert _publish(image_release.project) == 0


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_the_exact_release_commit_as_the_target_after_the_write_is_accepted(image_release, existing):
    api = image_release.api
    _prepare_write(api, existing)
    api.before[_release_get_after_the_write(api, existing=existing)] = (
        lambda: api.releases[TAG].update(target_commitish=image_release.commit)
    )

    assert _publish(image_release.project) == 0


# --- the unknown-write recovery is stated, and the POST/PATCH is never resent ---------------


def test_an_http_error_after_the_patch_is_never_a_clean_refusal(image_release, capsys):
    api = image_release.api
    api.seed_release(TAG)
    api.respond[("PATCH", f"/releases/{api.releases[TAG]['id']}", 1)] = (500, "boom")

    assert _publish(image_release.project) == exit_codes.FAILURE

    error = capsys.readouterr().err
    assert "[ERROR] update release" not in error  # the generic failure line is not used
    assert "HTTP 500: boom" not in error
    assert "GitHub answered HTTP 500 (boom)" in error and "UNKNOWN outcome" in error
    assert len([m for m, _p in api.mutations]) == 1


# --- the registry must be explicit: Docker reads `registry/team` as Docker Hub ------------


@pytest.mark.parametrize("image", [
    "ghcr.io/volkb79-2/cgprofile",          # the registered CGProfile image
    "ghcr.io/acme/demo",
    "docker.io/library/nginx",               # Docker Hub, spelled explicitly
    "registry.example.com/team/sub/app",
    "myhost:5000/app",                        # a port makes it a registry
    "127.0.0.1:5000/app",
    "localhost/app",                          # `localhost` is the one bare-word registry
    "localhost:5000/team/app",
])
def test_an_explicit_registry_reference_is_accepted(image):
    assert release.validate_oci_image_repository(image) == image
    assert release.oci_image_reference(image, "1.2.3") == f"{image}:1.2.3"


@pytest.mark.parametrize("image", ["registry/team", "myorg/app", "team/sub/app", "Registry/team"])
def test_a_first_component_docker_would_read_as_a_hub_namespace_is_refused(image):
    with pytest.raises(ValueError) as refusal:
        release.validate_oci_image_repository(image)
    message = str(refusal.value)
    assert "Docker Hub namespace" in message and f"ghcr.io/{image}" in message
    assert f"docker.io/{image}" in message  # names how to spell Docker Hub deliberately


@pytest.mark.parametrize("image", [
    f"{IMAGE}:1.0", "localhost:5000/app:1.0", f"{IMAGE}@sha256:{'a' * 64}", "ghcr.io", "ghcr.io/",
    "ghcr.io/Acme/Demo", "[::1]:5000/app",
])
def test_tag_digest_and_malformed_references_stay_refused(image):
    with pytest.raises(ValueError):
        release.validate_oci_image_repository(image)


@pytest.mark.parametrize("name, value, fragment", [
    ("CMRU_RELEASE_TAG", None, "CMRU_RELEASE_TAG not set"),
    ("CMRU_RELEASE_COMMIT", None, "CMRU_RELEASE_COMMIT not set"),
    ("CMRU_RELEASE_COMMIT", "abc123", "not a full 40-hex commit id"),
    ("CMRU_RELEASE_COMMIT", "F" * 40, "not a full 40-hex commit id"),
    ("CMRU_RELEASE_TAG", "other-v2.0.0", "not a demo-v<version> tag"),
    ("CMRU_RELEASE_TAG", "demo-v", "not a demo-v<version> tag"),
    ("CMRU_RELEASE_TAG", "demo-v2.0.0.dev1", "non-release version"),
    ("CMRU_RELEASE_TAG", "demo-v2.0.0+local", "non-release version"),
])
def test_absent_or_malformed_release_identity_refuses_before_any_github_call(
    image_release, monkeypatch, capsys, name, value, fragment,
):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    assert _publish(image_release.project) == exit_codes.REFUSED
    assert image_release.api.calls == []
    error = capsys.readouterr().err
    assert fragment in error
    if value is None:
        assert "cmru release" in error  # the remedy names the supported entry point


def test_a_commit_the_checkout_does_not_have_refuses_before_any_github_call(
    image_release, monkeypatch, capsys,
):
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", "1" * 40)
    assert _publish(image_release.project) == exit_codes.REFUSED
    assert image_release.api.calls == []
    assert "cannot read release notes source" in capsys.readouterr().err


@pytest.mark.parametrize("image", [
    f"{IMAGE}:2.0.0",
    f"{IMAGE}@sha256:{'a' * 64}",
    "demo",
    "ghcr.io//demo",
    "GHCR.io/Acme/Demo",
    "registry/team",       # Docker would read this as a Docker Hub namespace
    f" {IMAGE}",
])
def test_an_image_reference_with_a_tag_digest_or_bad_shape_refuses_before_github(
    image_release, capsys, image,
):
    assert _publish(image_release.project, image=image) == exit_codes.REFUSED
    assert image_release.api.calls == []
    assert "oci-image-release:" in capsys.readouterr().err


# --- oracle 2, 6: the dry run shows the publisher's body ----------------------------


def test_the_publisher_and_the_dry_run_share_one_body_builder():
    assert handlers.build_oci_image_release_body is release.build_oci_image_release_body
    notes = release_notes.ReleaseNotes(version="2.0.0", source="x", text=SECTION)
    assert release.build_oci_image_release_body(IMAGE, "2.0.0", release_notes=notes) == EXPECTED_BODY
    assert release.build_oci_image_release_body(IMAGE, "2.0.0") == EXPECTED_BODY.split("\n\n## ")[0]
    with pytest.raises(ValueError, match="carries a tag"):
        release.build_oci_image_release_body(f"{IMAGE}:1", "2.0.0")
    with pytest.raises(ValueError, match="not a valid image tag"):
        release.build_oci_image_release_body(IMAGE, "2.0.0+local")


def _command(argv: list[str], cwd: str = ".", label: str | None = None) -> dict:
    """A push-step command table as the shipped config loader produces it."""
    return {"label": label or argv[0], "argv": argv, "cwd": cwd}


def _project_with_commands(*commands: dict, **step_fields) -> SimpleNamespace:
    project = _project()
    project.runner_steps = {"push": SimpleNamespace(commands=list(commands), **step_fields)}
    return project


def _image_project(*push_argvs: list[str]) -> SimpleNamespace:
    return _project_with_commands(
        *(_command(["cmru", "handler", *argv], label=argv[0]) for argv in push_argvs),
    )


OCI_ARGV = ["oci-image-release", "--prefix", "demo", "--image", IMAGE, "--cwd", "."]
PUSH_ARGV = ["oci-image-push", "--cwd", ".", "--bake-file", "b.hcl", "--bake-target", "t"]


@pytest.mark.real_release_notes_preflight
def test_the_image_dry_run_prints_the_publishers_body_without_artifact_placeholders(
    tmp_path, capsys, monkeypatch,
):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    before = changes.read_bytes()
    calls = []
    real = release.build_oci_image_release_body

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(release, "build_oci_image_release_body", spy)
    cli._preview_release_bodies(repo, {"demo": _image_project(OCI_ARGV)}, ["demo"])
    assert changes.read_bytes() == before  # the preview generated and restored the notes

    output = capsys.readouterr().out
    shown = output.split("----- begin release body -----\n", 1)[1].split(
        "\n----- end release body -----", 1,
    )[0]
    assert calls and calls[0][0] == (IMAGE, "1.0.1")
    notes = calls[0][1]["release_notes"]
    assert shown == real(IMAGE, "1.0.1", release_notes=notes)
    assert shown.startswith(
        f"**Image:** `{IMAGE}:1.0.1`\n\nPull: `docker pull {IMAGE}:1.0.1`\n\n## [1.0.1]"
    )
    assert shown.endswith(notes.text) and "fix(demo): repair" in shown
    for placeholder in (release_notes.ARTIFACT_PLACEHOLDER, release_notes.SHA256_PLACEHOLDER):
        assert placeholder not in output
    assert "**Artifact:**" not in output and "**SHA256:**" not in output
    assert f"image {IMAGE}:1.0.1" in output


@pytest.mark.real_release_notes_preflight
def test_an_image_project_with_no_build_previews_the_section_without_a_release_body(
    tmp_path, capsys,
):
    repo = _release_repo(tmp_path)
    cli._preview_release_bodies(repo, {"demo": _image_project(OCI_ARGV)}, ["demo"], no_build=True)
    output = capsys.readouterr().out
    assert "--no-build skips publication" in output
    assert "begin release body" not in output and "fix(demo): repair" in output


@pytest.mark.real_release_notes_preflight
def test_a_body_the_builder_rejects_is_a_typed_dry_run_refusal_not_a_traceback(
    tmp_path, monkeypatch,
):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    before = changes.read_bytes()

    def rejects(*_args, **_kwargs):
        raise ValueError("release version '1.0.1+local' is not a valid image tag")

    monkeypatch.setattr(release, "build_oci_image_release_body", rejects)

    with pytest.raises(RefusedBeforeChange, match="demo: oci-image-release: release version '1.0.1"):
        cli._preview_release_bodies(repo, {"demo": _image_project(OCI_ARGV)}, ["demo"])

    assert changes.read_bytes() == before  # the preview still restored the candidate's bytes


# --- the release-plan validator: one function for prepare, dry-run and the plan -------


def _oci(*options: str, cwd: str = ".") -> dict:
    return _command(["cmru", "handler", "oci-image-release", *options], cwd=cwd, label="release notes")


GOOD = ("--prefix", "demo", "--image", IMAGE, "--cwd", ".")

#: (push-step commands, the fragment the refusal must name). Every entry is invalid config
#: that must be refused before any side effect, by prepare, by dry-run and by the plan.
BAD_CONFIGS = [
    pytest.param([_oci("--prefix", "demo", "--cwd", ".")], "missing required option(s): --image",
                 id="missing-image"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE)], "missing required option(s): --cwd",
                 id="missing-cwd"),
    pytest.param([_oci("--image", IMAGE, "--cwd", ".")], "missing required option(s): --prefix",
                 id="missing-prefix"),
    pytest.param([_oci(*GOOD, "--image", IMAGE)], "--image is given more than once",
                 id="duplicate-image"),
    pytest.param([_oci(*GOOD, "--cwd", ".")], "--cwd is given more than once", id="duplicate-cwd"),
    pytest.param([_oci(*GOOD, "--prefix=demo")], "--prefix is given more than once",
                 id="duplicate-prefix-equals-form"),
    pytest.param([_oci(*GOOD, "--dry-run")], "unexpected argument '--dry-run'", id="unknown-flag"),
    pytest.param([_oci(*GOOD, "stray")], "unexpected argument 'stray'", id="stray-positional"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd")], "--cwd has no value",
                 id="flag-without-value"),
    pytest.param([_oci("--prefix", "demo", "--image", "", "--cwd", ".")], "--image has an empty value",
                 id="empty-value"),
    pytest.param([_oci("--prefix", "--image", IMAGE, "--cwd", ".")], "looks like an option",
                 id="flag-eats-flag"),
    pytest.param([_oci("--prefix", "other", "--image", IMAGE, "--cwd", ".")],
                 "must be this project's release prefix without -v, 'demo'", id="wrong-prefix"),
    pytest.param([_oci("--prefix", "demo-v", "--image", IMAGE, "--cwd", ".")],
                 "must be this project's release prefix without -v", id="prefix-with-v"),
    pytest.param([_oci("--prefix", "demo", "--image", f"{IMAGE}:1.0", "--cwd", ".")],
                 "carries a tag", id="image-with-tag"),
    pytest.param([_oci("--prefix", "demo", "--image", f"{IMAGE}@sha256:{'a' * 64}", "--cwd", ".")],
                 "carries a digest", id="image-with-digest"),
    pytest.param([_oci("--prefix", "demo", "--image", "demo", "--cwd", ".")],
                 "must be <registry>/<repository>", id="image-without-registry"),
    pytest.param([_oci("--prefix", "demo", "--image", "registry/team", "--cwd", ".")],
                 "Docker Hub namespace", id="image-with-ambiguous-registry"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "..")],
                 "is not the project root 'demo'", id="cwd-parent"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "src")],
                 "is not the project root 'demo'", id="cwd-subdirectory"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", ".", cwd="sub")],
                 "is not the project root 'demo'", id="command-cwd-moves-the-root"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "/abs/demo")],
                 "must be relative", id="absolute-cwd"),
    pytest.param([_oci(*GOOD, cwd="/abs/demo")], "must be relative", id="absolute-command-cwd"),
    pytest.param([_oci(*GOOD, cwd="  ")], "declares no working directory", id="blank-command-cwd"),
    pytest.param(
        [{"label": "release notes", "argv": ["cmru", "handler", "oci-image-release", *GOOD]}],
        "declares no working directory", id="missing-command-cwd",
    ),
    pytest.param([_oci(*GOOD), _command(["cmru", "handler", *PUSH_ARGV], label="push image")],
                 "must be the LAST command of the push step", id="image-push-after-release"),
    pytest.param([_oci(*GOOD), _command(["echo", "done"], label="announce")],
                 "starting with 'announce'", id="any-command-after-release"),
    pytest.param([_oci(*GOOD), _oci(*GOOD)], "would overwrite the earlier body",
                 id="two-release-handlers"),
    pytest.param([_command(["cmru", "handler", "wheel-publish", "--prefix", "demo", "--cwd", "."]),
                  _oci(*GOOD)], "would overwrite the earlier body", id="wheel-publish-conflict"),
]


def _bad_project(commands: list[dict]) -> SimpleNamespace:
    return _project_with_commands(*commands)


def test_a_valid_push_step_puts_the_release_handler_last_and_resolves_its_root(tmp_path):
    (tmp_path / "demo").mkdir()
    push = _command(["cmru", "handler", *PUSH_ARGV], label="push image")
    release_last = _project_with_commands(push, _oci(*GOOD))
    assert cli._validated_oci_release_image(release_last, tmp_path) == IMAGE
    # The handler alone is also last; the `--flag=value` spelling parses the same way.
    alone = _project_with_commands(_oci("--prefix=demo", f"--image={IMAGE}", "--cwd=."))
    assert cli._validated_oci_release_image(alone, tmp_path) == IMAGE
    # A command run from a subdirectory reaches the project root with a relative --cwd:
    # nothing about the candidate's absolute location is baked into the configuration.
    from_sub = _project_with_commands(
        push, _oci("--prefix", "demo", "--image", IMAGE, "--cwd", "..", cwd="sub"),
    )
    assert cli._validated_oci_release_image(from_sub, tmp_path) == IMAGE
    # CGProfile's registered shape: a NON-handler image-push command first, then the handler.
    cgprofile = _project_with_commands(
        _command(["python3", "build-push.py", "--push"], label="push image"), _oci(*GOOD),
    )
    assert cli._validated_oci_release_image(cgprofile, tmp_path) == IMAGE
    # A project that opted out of a changelog still validates (its body is the coordinate alone).
    unlogged = _project_with_commands(push, _oci(*GOOD))
    unlogged.changelog = None
    assert cli._validated_oci_release_image(unlogged, tmp_path) == IMAGE
    # No release-body handler, or a bare image push, is simply "not applicable".
    assert cli._validated_oci_release_image(_image_project(PUSH_ARGV), tmp_path) is None
    assert cli._validated_oci_release_image(_project(), tmp_path) is None


@pytest.mark.parametrize("commands, fragment", BAD_CONFIGS)
def test_the_validator_refuses_invalid_handler_config(tmp_path, commands, fragment):
    (tmp_path / "demo").mkdir()
    with pytest.raises(RefusedBeforeChange, match="demo: .*") as refusal:
        cli._validated_oci_release_image(_bad_project(commands), tmp_path)
    assert fragment in str(refusal.value)


def test_an_untagged_project_or_a_step_that_appends_arguments_is_refused(tmp_path):
    (tmp_path / "demo").mkdir()
    untagged = _project_with_commands(_oci(*GOOD))
    untagged.git_tag = False
    with pytest.raises(RefusedBeforeChange, match="no protected release tag or commit"):
        cli._validated_oci_release_image(untagged, tmp_path)
    for fields in (
        {"no_cache_env": "NO_CACHE"},
        {"bake_set_prefix": "t.args.", "bake_set_vars": ["VERSION"]},
    ):
        appended = _project_with_commands(_oci(*GOOD), **fields)
        with pytest.raises(RefusedBeforeChange, match="append arguments to every command"):
            cli._validated_oci_release_image(appended, tmp_path)


@pytest.mark.parametrize("commands, fragment", BAD_CONFIGS)
def test_prepare_refuses_invalid_handler_config_before_the_prepare_step_runs(
    tmp_path, monkeypatch, commands, fragment,
):
    repo = _release_repo(tmp_path)
    project = _bad_project(commands)
    project.steps = {"prepare": []}  # a declared prepare step that must NOT run
    ran = []
    monkeypatch.setattr(cli, "run_project_step", lambda *a, **k: ran.append(a[1]))
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *a, **k: ran.append("changelog"))
    with pytest.raises(RefusedBeforeChange) as refusal:
        cli._prepare_release_projects(repo, {"demo": project}, ["demo"])
    assert fragment in str(refusal.value)
    assert ran == []  # neither prepare nor changelog generation was reached


@pytest.mark.real_release_notes_preflight
@pytest.mark.parametrize("commands, fragment", BAD_CONFIGS)
def test_the_dry_run_gives_the_same_refusal_and_leaves_the_changelog_bytes(
    tmp_path, commands, fragment,
):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    before = changes.read_bytes()
    with pytest.raises(RefusedBeforeChange) as refusal:
        cli._preview_release_bodies(repo, {"demo": _bad_project(commands)}, ["demo"])
    assert fragment in str(refusal.value)
    assert changes.read_bytes() == before


@pytest.mark.real_release_notes_preflight
def test_the_dry_run_and_the_release_plan_validate_with_the_same_function(tmp_path, monkeypatch):
    repo = _release_repo(tmp_path)
    seen = []
    real = cli._validated_oci_release_image
    monkeypatch.setattr(
        cli, "_validated_oci_release_image",
        lambda project, root: seen.append((project.name, root)) or real(project, root),
    )
    project = _image_project(PUSH_ARGV, OCI_ARGV)
    cli._preview_release_bodies(repo, {"demo": project}, ["demo"])
    cli._validate_release_body_handlers(repo, {"demo": project}, ["demo"])
    assert seen == [("demo", repo), ("demo", repo)]


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_release_plan_validates_every_shipped_project_before_any_cycle(
    monkeypatch, tmp_path, dry_run,
):
    """A bad handler in a LATER project must refuse before the first project is touched."""
    from cmru import transaction

    (tmp_path / "demo").mkdir()
    first = SimpleNamespace(name="first", prefix="first-v", git_tag=True, runner_steps={})
    bad = _bad_project([_oci(*GOOD), _command(["cmru", "handler", *PUSH_ARGV], label="push image")])
    configs = {"first": first, "demo": bad}
    marked, reached = [], []

    def forbidden(name):
        return lambda *a, **k: reached.append(name)

    monkeypatch.setattr("cmru.version.detect_changed_projects", lambda *a, **k: [
        ("first", first, "first-v1.0.0", "patch"), ("demo", bad, "demo-v1.0.0", "patch"),
    ])
    monkeypatch.setattr("cmru.version.release_cmd", forbidden("release_cmd"))
    monkeypatch.setattr(cli, "_known_project_scope_names", lambda _c: frozenset())
    monkeypatch.setattr(cli, "_release_child_controller_identity", lambda *_a: {
        "version": "6.2.0", "wheel_sha256": "a" * 64, "distribution_sha256": "b" * 64,
    })
    monkeypatch.setattr(cli, "_prepare_dry_run_external_versions", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_check_release_tool_dependencies", forbidden("tool-deps"))
    monkeypatch.setattr(cli, "_preview_release_bodies", forbidden("preview"))
    monkeypatch.setattr(cli, "_release_projects_sequentially", forbidden("sequential"))
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: "workspace")
    monkeypatch.setattr(
        transaction, "mark_plan_refused", lambda _root, workspace: marked.append(workspace),
    )
    vargs = SimpleNamespace(
        dry_run=dry_run, allow_tag_ahead_of_head=False, allow_stale_tool_deps=False,
        minor=False, major=False, set_version=None, no_build=False,
    )

    with pytest.raises(SystemExit) as exited:
        cli._release_child(
            vargs, tmp_path, configs, configs, ["first", "demo"], ["first", "demo"],
            SimpleNamespace(), SimpleNamespace(), None,
        )

    assert exited.value.code == exit_codes.REFUSED
    assert marked == ["workspace"] and reached == []  # nothing past the validator ran


def _external_version_project(handler: dict) -> SimpleNamespace:
    """An `external:` version project with a declared `prepare` step."""
    project = _project_with_commands(
        _command(["cmru", "handler", *PUSH_ARGV], label="push image"), handler,
    )
    project.version = SimpleNamespace(strategy="external:DEMO_VERSION", bump="conventional")
    project.steps = {"prepare": [{"label": "discover version", "argv": ["x"], "cwd": "."}]}
    return project


def test_an_invalid_handler_is_refused_before_the_dry_run_prepare_step_writes_anything(
    monkeypatch, tmp_path,
):
    """`external:` versions make a dry run execute `prepare` (project code that may write
    files). A malformed handler in ANY selected project must refuse before that."""
    from cmru import transaction
    from cmru.version import ReleasePlanRefused

    (tmp_path / "demo").mkdir()
    marker = tmp_path / "prepare-ran.marker"
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda project, step, root, logs, **kw: marker.write_text(step, encoding="utf-8"),
    )
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_known_project_scope_names", lambda _c: frozenset())
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: "workspace")
    marked = []
    monkeypatch.setattr(transaction, "mark_plan_refused", lambda _r, w: marked.append(w))

    def stop_after_prepare(*_a, **_k):
        raise ReleasePlanRefused("the plan is not under test")  # ends the run past `prepare`

    monkeypatch.setattr("cmru.version.detect_changed_projects", stop_after_prepare)
    vargs = SimpleNamespace(
        dry_run=True, allow_tag_ahead_of_head=False, allow_stale_tool_deps=False,
        minor=False, major=False, set_version=None, no_build=False,
    )

    def run(handler: dict) -> int:
        project = _external_version_project(handler)
        configs = {"demo": project}
        with pytest.raises(SystemExit) as exited:
            cli._release_child(
                vargs, tmp_path, configs, configs, ["demo"], ["demo"],
                SimpleNamespace(), SimpleNamespace(), None,
            )
        return exited.value.code

    # Control: with a VALID handler the same fixture really does run `prepare` first
    # (and then stops at the stubbed plan), so the absence below is the validator's doing.
    assert run(_oci(*GOOD)) == exit_codes.REFUSED
    assert marker.read_text(encoding="utf-8") == "prepare"
    marker.unlink()
    marked.clear()

    assert run(_oci("--prefix", "demo", "--image", "registry/team", "--cwd", ".")) == exit_codes.REFUSED
    assert not marker.exists()  # the prepare step never ran
    assert marked == ["workspace"]  # refused as a plan refusal, so no candidate is retained


# --- oracle 6: detection, and the single-body-owner rule ----------------------------


def test_the_push_step_handler_is_recognised_as_a_release_body():
    assert "oci-image-release" in cli._RELEASE_BODY_HANDLERS
    assert cli._has_cmru_github_release_body(_image_project(OCI_ARGV)) is True
    assert cli._has_cmru_github_release_body(
        _image_project(["oci-image-push", "--cwd", ".", "--bake-file", "b.hcl", "--bake-target", "t"]),
    ) is False  # pushing an image alone never claims a published body


@pytest.mark.parametrize("other", [
    ["wheel-publish", "--prefix", "demo", "--cwd", "."],
    ["tarball-publish", "--prefix", "demo", "--cwd", ".", "--glob", "*.xz"],
    OCI_ARGV,
])
def test_an_image_release_cannot_share_a_push_step_with_another_body_handler(other):
    with pytest.raises(RefusedBeforeChange, match="would overwrite the earlier body"):
        cli._release_body_handler_argv(_image_project(OCI_ARGV, other))
    with pytest.raises(RefusedBeforeChange, match="would overwrite the earlier body"):
        cli._release_body_handler_argv(_image_project(other, OCI_ARGV))
