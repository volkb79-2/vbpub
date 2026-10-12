"""CMRU release tags are the authority for OCI image version coordinates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib import version


def _project_root(tmp_path: Path, prefix: str = "cgprofile-v") -> Path:
    (tmp_path / "cmru.toml").write_text(
        f'[project]\nprefix = "{prefix}"\n', encoding="utf-8",
    )
    return tmp_path


def _tagged_git_project(tmp_path: Path, tag: str = "cgprofile-v1.2.3"):
    root = tmp_path / "repo"
    project = root / "scripts" / "cgroup-profiler"
    project.mkdir(parents=True)
    (project / "cmru.toml").write_text(
        '[project]\nprefix = "cgprofile-v"\n', encoding="utf-8",
    )
    (project / "source.py").write_text("release = True\n", encoding="utf-8")

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", ".")
    git("commit", "-q", "-m", "release candidate")
    commit = git("rev-parse", "HEAD")
    git("tag", "-a", tag, "-m", "release")
    return root, project, commit, tag, git


def _git_tags(
    monkeypatch,
    *,
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
    status_stdout: str = "",
    status_returncode: int = 0,
    status_stderr: str = "",
    head: str = "a" * 40,
    tagged_commit: str | None = None,
):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "tag"]:
            result_stdout = stdout
            result_stderr = stderr
            result_code = returncode
        elif argv == ["git", "rev-parse", "--verify", "HEAD^{commit}"]:
            result_stdout = head
            result_stderr = ""
            result_code = 0
        elif len(argv) == 4 and argv[:3] == ["git", "rev-parse", "--verify"]:
            result_stdout = tagged_commit or head
            result_stderr = ""
            result_code = 0
        elif argv == ["git", "rev-parse", "--show-toplevel"]:
            result_stdout = str(kwargs["cwd"])
            result_stderr = ""
            result_code = 0
        elif argv == ["git", "status", "--porcelain", "--untracked-files=all"]:
            result_stdout = status_stdout
            result_stderr = status_stderr
            result_code = status_returncode
        else:
            raise AssertionError(f"unexpected git command: {argv!r}")
        captured = kwargs.get("capture_output", False)
        result_stdout = result_stdout if captured else None
        result_stderr = result_stderr if captured else None
        if captured and not kwargs.get("text", False):
            result_stdout = result_stdout.encode()
            result_stderr = result_stderr.encode()
        if result_code and kwargs.get("check", False):
            raise subprocess.CalledProcessError(
                result_code, argv, output=result_stdout, stderr=result_stderr,
            )
        return subprocess.CompletedProcess(
            argv, result_code, result_stdout, result_stderr,
        )

    monkeypatch.setattr(version.subprocess, "run", fake_run)


def test_runtime_version_uses_source_fallback_when_environment_is_absent(monkeypatch):
    monkeypatch.delenv(version.VERSION_ENV, raising=False)
    assert version.runtime_version("1.1.0") == "1.1.0"


def test_runtime_version_refuses_present_but_empty_environment(monkeypatch):
    monkeypatch.setenv(version.VERSION_ENV, "  ")
    with pytest.raises(RuntimeError, match="CGPROFILE_VERSION must be a semantic version"):
        version.runtime_version("1.1.0")


def test_runtime_version_uses_embedded_release_identity(monkeypatch):
    monkeypatch.setenv(version.VERSION_ENV, " 1.2.0 ")
    assert version.runtime_version("1.1.0") == "1.2.0"


@pytest.mark.parametrize("bad", ["latest", "1.1.0-01"])
def test_runtime_version_refuses_invalid_embedded_identity(monkeypatch, bad):
    monkeypatch.setenv(version.VERSION_ENV, bad)
    with pytest.raises(RuntimeError, match="CGPROFILE_VERSION must be a semantic version"):
        version.runtime_version("1.1.0")


def test_exact_release_tag_is_authoritative_over_stale_environment(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="assay-v7.0.0\n\ncgprofile-v1.1.0\n")
    monkeypatch.setenv(version.VERSION_ENV, "9.9.9")
    assert version.resolve_build_version(
        root, require_release_tag=True,
    ) == "1.1.0"


def test_cmru_release_identity_binds_annotated_tag_to_exact_clean_commit(
    tmp_path, monkeypatch,
):
    _root, project, commit, tag, _git = _tagged_git_project(tmp_path)
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", tag)

    assert version.resolve_cmru_release_identity(project) == ("1.2.3", commit, tag)


def test_cmru_release_identity_rejects_a_moved_local_tag(tmp_path, monkeypatch):
    _root, project, commit, tag, git = _tagged_git_project(tmp_path)
    other = git(
        "commit-tree", git("rev-parse", "HEAD^{tree}"), "-p", commit,
        "-m", "different source identity",
    )
    git("tag", "--force", tag, other)
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", tag)

    with pytest.raises(RuntimeError, match="points at .* expected"):
        version.resolve_cmru_release_identity(project)


def test_cmru_release_identity_rejects_a_checkout_that_moved_after_gating(
    tmp_path, monkeypatch,
):
    root, project, commit, tag, git = _tagged_git_project(tmp_path)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "later checkout"],
        cwd=root, check=True,
    )
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", tag)

    with pytest.raises(RuntimeError, match="does not match checkout HEAD"):
        version.resolve_cmru_release_identity(project)


def test_cmru_release_identity_rejects_dirty_tagged_source(tmp_path, monkeypatch):
    _root, project, commit, tag, _git = _tagged_git_project(tmp_path)
    (project / "source.py").write_text("release = False\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", tag)

    with pytest.raises(RuntimeError, match="tagged build tree has tracked or untracked changes"):
        version.resolve_cmru_release_identity(project)


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"CMRU_RELEASE_COMMIT": "a" * 40},
        {"CMRU_RELEASE_TAG": "cgprofile-v1.2.3"},
        {"CMRU_RELEASE_COMMIT": "unknown", "CMRU_RELEASE_TAG": "cgprofile-v1.2.3"},
    ],
)
def test_cmru_release_identity_requires_complete_valid_protected_inputs(
    tmp_path, monkeypatch, identity,
):
    _root, project, _commit, _tag, _git = _tagged_git_project(tmp_path)
    monkeypatch.delenv("CMRU_RELEASE_COMMIT", raising=False)
    monkeypatch.delenv("CMRU_RELEASE_TAG", raising=False)
    for key, value in identity.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match="CMRU_RELEASE_COMMIT|incomplete CMRU"):
        version.resolve_cmru_release_identity(project)


def test_dirty_checkout_at_release_tag_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(
        monkeypatch,
        stdout="cgprofile-v1.1.0\n",
        status_stdout=" M scripts/cgroup-profiler/lib/serve.py\n",
    )

    with pytest.raises(RuntimeError, match="tracked or untracked changes"):
        version.resolve_build_version(root, require_release_tag=True)


def test_release_tag_is_refused_when_cleanliness_cannot_be_verified(
    tmp_path, monkeypatch,
):
    root = _project_root(tmp_path)
    _git_tags(
        monkeypatch,
        stdout="cgprofile-v1.1.0\n",
        status_returncode=128,
        status_stderr="fatal: could not read index",
    )

    with pytest.raises(RuntimeError, match="cannot verify tagged build tree cleanliness"):
        version.resolve_build_version(root, require_release_tag=True)


def test_multiple_project_tags_at_head_are_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="cgprofile-v1.0.0\ncgprofile-v1.1.0\n")
    with pytest.raises(RuntimeError, match="multiple .* release tags"):
        version.resolve_build_version(root, require_release_tag=True)


def test_malformed_project_tag_at_head_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="cgprofile-vlatest\n")
    with pytest.raises(RuntimeError, match="release tag .* semantic version"):
        version.resolve_build_version(root, require_release_tag=True)


def test_untagged_local_build_uses_dev_identity_despite_ambient_override(
    tmp_path, monkeypatch,
):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    monkeypatch.setenv(version.VERSION_ENV, "2.3.4")
    assert version.resolve_build_version(root, require_release_tag=False) == "0.0.0-dev"


def test_publish_refuses_ambient_override_without_exact_tag(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    monkeypatch.setenv(version.VERSION_ENV, "2.3.4")
    with pytest.raises(RuntimeError, match="refuse publication without an exact release tag"):
        version.resolve_build_version(root, require_release_tag=True)


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "detail"),
    [
        (
            128, "unexpected output", "fatal: not a git repository",
            "fatal: not a git repository",
        ),
        (128, "unexpected output", "", "exit 128"),
        (0, "  ", "", "exit 0"),
    ],
)
def test_git_output_refuses_failed_or_empty_resolution(
    tmp_path, monkeypatch, returncode, stdout, stderr, detail,
):
    def fake_run(argv, **kwargs):
        assert argv == ["git", "rev-parse", "HEAD"]
        assert kwargs == {
            "cwd": str(tmp_path), "capture_output": True, "text": True,
            "check": False,
        }
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(version.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=f"cannot resolve release source.*{detail}"):
        version._git_output(tmp_path, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "detail"),
    [
        (128, "/repo", "fatal: not a git repository", "fatal: not a git repository"),
        (128, "/repo", "", "exit 128"),
        (0, "  ", "", "exit 0"),
    ],
)
def test_tagged_tree_refuses_when_repository_root_cannot_be_read(
    tmp_path, monkeypatch, returncode, stdout, stderr, detail,
):
    def fake_run(argv, **kwargs):
        assert argv == ["git", "rev-parse", "--show-toplevel"]
        assert kwargs == {
            "cwd": str(tmp_path), "capture_output": True, "text": True,
            "check": False,
        }
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(version.subprocess, "run", fake_run)
    with pytest.raises(
        RuntimeError,
        match=f"cannot verify tagged build tree cleanliness: {detail}",
    ):
        version._require_clean_tagged_tree(tmp_path)


def test_cmru_release_identity_requires_a_configured_non_empty_prefix(
    tmp_path, monkeypatch,
):
    _root, project, commit, tag, _git = _tagged_git_project(tmp_path)
    (project / "cmru.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", tag)

    with pytest.raises(RuntimeError, match="cmru.toml must declare a non-empty project.prefix"):
        version.resolve_cmru_release_identity(project)


def test_cmru_release_identity_refuses_an_empty_configured_prefix(
    tmp_path, monkeypatch,
):
    root, project, _commit, old_tag, git = _tagged_git_project(tmp_path)
    (project / "cmru.toml").write_text(
        '[project]\nprefix = ""\n', encoding="utf-8",
    )
    git("add", "scripts/cgroup-profiler/cmru.toml")
    git("commit", "-q", "-m", "empty project prefix")
    commit = git("rev-parse", "HEAD")
    git("tag", "--delete", old_tag)
    git("tag", "-a", "1.2.3", "-m", "release")
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", "1.2.3")

    with pytest.raises(RuntimeError, match="cmru.toml must declare a non-empty project.prefix"):
        version.resolve_cmru_release_identity(project)


def test_cmru_release_identity_rejects_tag_with_wrong_project_prefix(
    tmp_path, monkeypatch,
):
    _root, project, commit, _tag, _git = _tagged_git_project(tmp_path)
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", "assay-v1.2.3")

    with pytest.raises(RuntimeError, match="does not use configured prefix"):
        version.resolve_cmru_release_identity(project)


def test_release_tag_must_point_at_the_build_head(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    head = "a" * 40
    tag = "cgprofile-v1.1.0"
    tagged_commit = "b" * 40

    def fake_run(argv, **kwargs):
        assert kwargs == {
            "cwd": str(root), "capture_output": True, "text": True,
            "check": False,
        }
        if argv == ["git", "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(argv, 0, head, "")
        if argv == ["git", "tag", "--points-at", head, "--list", "cgprofile-v*"]:
            return subprocess.CompletedProcess(argv, 0, f"{tag}\n", "")
        if argv == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(argv, 0, str(root), "")
        if argv == ["git", "status", "--porcelain", "--untracked-files=all"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == [
            "git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}",
        ]:
            return subprocess.CompletedProcess(argv, 0, tagged_commit, "")
        raise AssertionError(f"unexpected git command: {argv!r}")

    monkeypatch.setattr(version.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="points at .* not checkout HEAD"):
        version.resolve_build_version(root, require_release_tag=True)


def test_dockerfile_requires_an_explicit_image_version_before_package_install():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "ARG CGPROFILE_VERSION\n" in dockerfile
    assert "ARG CGPROFILE_VERSION=" not in dockerfile
    version_arg = dockerfile.index("ARG CGPROFILE_VERSION\n")
    first_from = dockerfile.index("FROM ")
    builder_arg = dockerfile.index("ARG CGPROFILE_VERSION\n", first_from)
    version_guard = dockerfile.index("CGPROFILE_VERSION build arg is required")
    first_package_install = dockerfile.index("\nRUN apt-get update")
    assert version_arg < first_from < builder_arg < version_guard < first_package_install


def test_direct_consumer_build_recipe_uses_development_identity():
    consumers = (
        Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"
    ).read_text(encoding="utf-8")
    recipe = consumers.split(
        "For a direct source build, use the development identity", 1
    )[1].split("The Dockerfile refuses", 1)[0]

    assert "--build-arg CGPROFILE_VERSION=0.0.0-dev" in recipe
    assert "-t cgprofile:local ." in recipe
    assert "CGPROFILE_VERSION=1.0.0" not in recipe


def test_all_public_docs_explain_tag_only_release_identity():
    docs = Path(__file__).resolve().parents[1]
    public_docs = (
        docs / "README.md",
        docs / "docs" / "DESIGN-GUIDE.md",
        docs / "docs" / "CONSUMERS.md",
    )
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        assert "0.0.0-dev" in text
        assert "CGPROFILE_VERSION" in text
        assert "CMRU_RELEASE_COMMIT" in text
        assert "CMRU_RELEASE_TAG" in text
    consumers = public_docs[-1].read_text(encoding="utf-8")
    assert "a `--push` without CMRU's protected" in consumers
    assert "Use `cmru release` to publish." in consumers
    assert "validated `CGPROFILE_VERSION`" not in consumers
    assert "local tag both point at" in consumers
    assert "bare `docker buildx bake` ignores an ambient" in consumers
    design = public_docs[1].read_text(encoding="utf-8")
    assert "Ambient `CGPROFILE_VERSION` never creates release authority" in design
    assert "temporary full-repository worktree" in design
    assert "literal development defaults" in design


def test_publish_without_exact_tag_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    monkeypatch.setenv(version.VERSION_ENV, "1.0.0")
    with pytest.raises(RuntimeError, match="no cgprofile-v<version> tag points at HEAD"):
        version.resolve_build_version(root, require_release_tag=True)


@pytest.mark.parametrize(
    ("stderr", "expected_detail"),
    [("not a git repository", "not a git repository"), ("", "exit 128")],
)
def test_git_tag_probe_failure_preserves_diagnostic_or_exit_fallback(
    tmp_path, monkeypatch, stderr, expected_detail,
):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, returncode=128, stderr=stderr)
    with pytest.raises(RuntimeError) as exc_info:
        version.resolve_build_version(root, require_release_tag=False)
    assert "cannot resolve release tag at HEAD" in str(exc_info.value)
    assert expected_detail in str(exc_info.value)


@pytest.mark.parametrize("prefix", ['""', "42"])
def test_empty_or_non_string_project_prefix_is_refused(tmp_path, monkeypatch, prefix):
    (tmp_path / "cmru.toml").write_text(
        f"[project]\nprefix = {prefix}\n", encoding="utf-8",
    )
    _git_tags(monkeypatch)
    with pytest.raises(RuntimeError, match="must declare a non-empty project.prefix"):
        version.resolve_build_version(tmp_path, require_release_tag=False)


def test_project_prefix_is_read_from_cmru_contract(tmp_path, monkeypatch):
    root = _project_root(tmp_path, prefix="profile-v")
    _git_tags(monkeypatch, stdout="profile-v3.2.1\n")
    assert version.resolve_build_version(root, require_release_tag=True) == "3.2.1"


def test_missing_project_prefix_is_refused(tmp_path):
    (tmp_path / "cmru.toml").write_text("[project]\nid = 'cgroup-profiler'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must declare a non-empty project.prefix"):
        version.resolve_build_version(tmp_path, require_release_tag=False)


def test_non_table_project_is_refused(tmp_path):
    (tmp_path / "cmru.toml").write_text("project = 'not a table'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must declare a non-empty project.prefix"):
        version.resolve_build_version(tmp_path, require_release_tag=False)


def test_daemon_version_uses_embedded_release_identity_in_fresh_process():
    import os
    import sys

    env = dict(os.environ, CGPROFILE_VERSION="1.2.0")
    result = subprocess.run(
        [sys.executable, "-c", "from lib.serve import CGPROFILE_VERSION; print(CGPROFILE_VERSION)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        check=False, env=env,
    )
    assert result.returncode == 0
    assert result.stdout == "1.2.0\n"
    assert result.stderr == ""


def test_untagged_source_daemon_reports_development_identity():
    import os
    import sys

    env = dict(os.environ)
    env.pop("CGPROFILE_VERSION", None)
    result = subprocess.run(
        [sys.executable, "-c", "from lib.serve import CGPROFILE_VERSION; print(CGPROFILE_VERSION)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        check=False, env=env,
    )
    assert result.returncode == 0
    assert result.stdout == "0.0.0-dev\n"
    assert result.stderr == ""
