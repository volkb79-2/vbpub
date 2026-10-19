"""KI-78: an image-only release publishes a GitHub Release body (``oci-image-release``).

Real Git fixtures and the real ``GitHubReleases`` client run against an in-memory
GitHub REST fake (no network): the fake records every request, so a test can
prove what a refusal did NOT change and that a retry never touched an asset.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

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
        payload = json.loads(data) if data else {}
        if method == "GET" and path.startswith("/commits/tags/"):
            tag = path.removeprefix("/commits/tags/")
            if tag not in self.tags:
                return 404, "{}"
            return 200, json.dumps({"sha": self.tags[tag]})
        if method == "GET" and path.startswith("/releases/tags/"):
            record = self.releases.get(path.removeprefix("/releases/tags/"))
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
    return SimpleNamespace(project=project, root=tmp_path, commit=commit, api=api)


def _publish(project, *, image: str = IMAGE, prefix: str = "demo") -> int:
    return handlers.main([
        "oci-image-release", "--prefix", prefix, "--image", image, "--cwd", str(project),
    ])


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
    # tag checked before the write and again after it, and nothing else mutated.
    assert api.created == [{
        "tag_name": TAG, "name": TAG, "body": EXPECTED_BODY, "target_commitish": fixture.commit,
    }]
    assert api.calls == [
        ("GET", f"/commits/tags/{TAG}"),
        ("GET", f"/releases/tags/{TAG}"),
        ("POST", "/releases"),
        ("GET", f"/commits/tags/{TAG}"),
    ]
    assert api.tags[TAG] == fixture.commit


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
    # Calls 1 and 2 of GET /commits/tags are the initial check and the pre-PATCH re-check.
    api.before[("GET", f"/commits/tags/{TAG}", 2)] = (
        lambda: api.tags.__setitem__(TAG, OTHER_COMMIT)
    )
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert api.mutations == []
    assert api.releases[TAG]["body"] == "old body"
    error = capsys.readouterr().err
    assert f"its tag target changed from {image_release.commit} to '{OTHER_COMMIT}'" in error


def test_a_release_id_change_after_the_patch_is_reported_as_written(image_release, capsys):
    api = image_release.api
    api.seed_release(TAG)
    api.before[("GET", f"/releases/tags/{TAG}", 3)] = _replace_release(api)
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert len(api.mutations) == 1 and api.mutations[0][0] == "PATCH"  # the write happened
    error = capsys.readouterr().err
    assert "was written, but its release ID is now" in error and "NOT rolled back" in error


def test_a_tag_move_after_the_patch_is_reported_as_written(image_release, capsys):
    api = image_release.api
    api.seed_release(TAG)
    api.before[("GET", f"/commits/tags/{TAG}", 3)] = (
        lambda: api.tags.__setitem__(TAG, OTHER_COMMIT)
    )
    assert _publish(image_release.project) == exit_codes.FAILURE
    assert len(api.mutations) == 1 and api.mutations[0][0] == "PATCH"
    assert api.releases[TAG]["body"] == EXPECTED_BODY  # the new body is in place
    error = capsys.readouterr().err
    assert "was written, but the remote tag now points at" in error
    assert "NOT rolled back" in error and "refusing" not in error


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
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "..")],
                 "is not the project root 'demo'", id="cwd-parent"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "src")],
                 "is not the project root 'demo'", id="cwd-subdirectory"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", ".", cwd="sub")],
                 "is not the project root 'demo'", id="command-cwd-moves-the-root"),
    pytest.param([_oci("--prefix", "demo", "--image", IMAGE, "--cwd", "/abs/demo")],
                 "must be relative", id="absolute-cwd"),
    pytest.param([_oci(*GOOD, cwd="/abs/demo")], "must be relative", id="absolute-command-cwd"),
    pytest.param([_oci(*GOOD), _command(["cmru", "handler", *PUSH_ARGV], label="push image")],
                 "must be the LAST command of the push step", id="image-push-after-release"),
    pytest.param([_oci(*GOOD), _command(["echo", "done"], label="announce")],
                 "starting with announce", id="any-command-after-release"),
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
