"""CIU-176: a linked worktree of a standalone-root repo must be able to start a stack.

Docker-free. The primary image-map comparison may only render the PRIMARY's
template from the primary's own generated facts; when those are absent it must
refuse naming the primary and the command, never emit the misleading
"Missing required environment values in <primary template>". A missing
gitignored ``.env*`` value must say where it comes from, and the devcontainer
attach must use the container this process really runs in.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, deploy, workspace_env  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


_REAL_OWN_CONTAINER_ID = workspace_env._own_container_id  # before the autouse stub


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    assert _git(["add", "ciu.global.defaults.toml.j2"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


class TestPrimaryWithoutGeneratedFacts:
    def test_refuses_naming_primary_root_and_command(self, tmp_repo):
        linked = tmp_repo / ".worktrees" / "wt"
        assert _git(["worktree", "add", "-b", "wt", str(linked), "main"], tmp_repo).returncode == 0
        with pytest.raises(ValueError) as excinfo:
            deploy.resolve_primary_image_references(linked)
        message = str(excinfo.value)
        assert "[CIU-176]" in message
        assert str(tmp_repo.resolve()) in message
        assert "cd " in message and "ciu env generate" in message
        assert "Missing required environment" not in message


    def test_present_generated_facts_pass(self, tmp_path):
        (tmp_path / "ciu.instance.generated.toml").write_text("", encoding="utf-8")
        assert deploy._require_primary_generated_facts(tmp_path) is None

    def test_bake_surfaces_the_refusal_without_a_doubled_prefix(
        self, monkeypatch, tmp_path, capsys
    ):
        from ciu import cli, dev, image_isolation, worktree

        repo = tmp_path / "repo"
        primary = tmp_path / "primary"
        repo.mkdir()
        primary.mkdir()
        monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: repo)
        monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: "id")
        monkeypatch.setattr(worktree, "primary_ciu_root", lambda _root: primary)
        monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: {})
        monkeypatch.setattr(
            cli.subprocess, "run",
            lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "{}", ""),
        )
        monkeypatch.setattr(image_isolation, "bake_scoped_tags", lambda *_a, **_kw: {"app:dev-id"})
        assert cli._bake(["app"]) == 2
        err = capsys.readouterr().err
        assert "[CIU-176]" in err and "ciu env generate" in err
        assert "[CIU-117] [CIU-176]" not in err

    def test_deploy_surfaces_the_refusal(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from ciu import worktree

        primary = tmp_path / "primary"
        primary.mkdir()
        profile = SimpleNamespace(name="test", config={}, compose_profiles=[])
        entry = {
            "path": "tools/app", "name": "app",
            "service": {"profiles": [], "env_overrides": {}},
        }
        monkeypatch.setattr(worktree, "primary_ciu_root", lambda _root: primary)
        monkeypatch.setattr(deploy, "profile_env", lambda _profile: {})
        monkeypatch.setattr(deploy, "group_by_phase", lambda _selection: [("1", [entry])])
        monkeypatch.setattr(deploy.profiles_pkg, "render_ciu_context", lambda *_a: {})
        monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: False)
        seen: list[str] = []

        def run_stack(*_args, primary_image_references, **_kwargs):
            with pytest.raises(ValueError, match=r"\[CIU-176\]") as excinfo:
                primary_image_references()
            seen.append(str(excinfo.value))
            return True

        monkeypatch.setattr(deploy, "_run_stack", run_stack)
        assert deploy.action_deploy(
            tmp_path, profile, [entry], dry_run=False, ignore_errors=False,
            health_after_phase=False, update_cert_permission=False, no_preflight=True,
        ) == 0
        assert len(seen) == 1


class TestMissingEnvHint:
    @staticmethod
    def _expand(template: Path) -> str:
        with pytest.raises(ValueError) as excinfo:
            config_model.expand_env_vars_or_fail(
                "key = \"$NAF_GUARD_USER\"\n", str(template), environ={}
            )
        return str(excinfo.value)

    def test_linked_worktree_names_primary_env_files_it_lacks(self, tmp_path):
        primary = tmp_path / "primary"
        linked = tmp_path / "linked"
        (primary / ".git" / "worktrees" / "wt").mkdir(parents=True)
        linked.mkdir()
        (linked / ".git").write_text(
            f"gitdir: {primary / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
        )
        (primary / ".env.guard").write_text("X=1\n", encoding="utf-8")
        (primary / ".env.example").write_text("X=\n", encoding="utf-8")
        (primary / ".env.shared").write_text("X=1\n", encoding="utf-8")
        (linked / ".env.shared").write_text("X=1\n", encoding="utf-8")
        (primary / ".env.dir").mkdir()
        message = self._expand(linked / "sub" / "t.j2")
        assert "NAF_GUARD_USER" in message
        assert "linked worktree" in message and str(primary) in message
        assert ".env.guard" in message
        assert ".env.example" not in message and ".env.shared" not in message
        assert ".env.dir" not in message

    def test_nothing_missing_adds_no_hint(self, tmp_path):
        primary = tmp_path / "primary"
        linked = tmp_path / "linked"
        (primary / ".git" / "worktrees" / "wt").mkdir(parents=True)
        linked.mkdir()
        (linked / ".git").write_text(
            f"gitdir: {primary / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
        )
        assert "linked worktree" not in self._expand(linked / "t.j2")

    def test_plain_checkout_and_no_git_add_no_hint(self, tmp_path):
        plain = tmp_path / "plain"
        (plain / ".git").mkdir(parents=True)
        assert "linked worktree" not in self._expand(plain / "t.j2")
        assert "linked worktree" not in self._expand(tmp_path / "nogit" / "t.j2")

    @pytest.mark.parametrize("content", ["", "not a gitdir line\n", "gitdir: /x/y/z\n"])
    def test_malformed_git_file_adds_no_hint(self, tmp_path, content):
        linked = tmp_path / "linked"
        linked.mkdir()
        (linked / ".git").write_text(content, encoding="utf-8")
        assert "linked worktree" not in self._expand(linked / "t.j2")

    def test_gitdir_pointing_at_own_directory_adds_no_hint(self, tmp_path):
        # <dir>/.git/worktrees/x written into <dir>/.git (a file): primary == dir
        checkout = tmp_path / "c"
        checkout.mkdir()
        (checkout / ".git").write_text(
            f"gitdir: {checkout / '.git' / 'worktrees' / 'x'}\n", encoding="utf-8"
        )
        assert "linked worktree" not in self._expand(checkout / "t.j2")

    def test_unreadable_primary_never_replaces_the_refusal(self, tmp_path, monkeypatch):
        primary = tmp_path / "primary"
        linked = tmp_path / "linked"
        (primary / ".git" / "worktrees" / "wt").mkdir(parents=True)
        linked.mkdir()
        (linked / ".git").write_text(
            f"gitdir: {primary / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
        )

        def denied(self, pattern):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "glob", denied)
        assert "NAF_GUARD_USER" in self._expand(linked / "t.j2")

    def test_relative_gitdir_pointer_resolves_against_the_checkout(
        self, tmp_path, monkeypatch
    ):
        primary = tmp_path / "primary"
        linked = tmp_path / "linked"
        (primary / ".git" / "worktrees" / "wt").mkdir(parents=True)
        linked.mkdir()
        (linked / ".git").write_text(
            "gitdir: ../primary/.git/worktrees/wt\n", encoding="utf-8"
        )
        (primary / ".env.guard").write_text("X=1\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path / "primary" / ".git")  # an unrelated cwd
        message = self._expand(linked / "t.j2")
        assert ".env.guard" in message and str(primary) in message

    def test_sub_root_env_files_are_named_and_the_command_is_quoted(self, tmp_path):
        primary = tmp_path / "my primary"
        linked = tmp_path / "linked"
        (primary / ".git" / "worktrees" / "wt").mkdir(parents=True)
        (primary / "sub").mkdir()
        (linked / "sub").mkdir(parents=True)
        (linked / ".git").write_text(
            f"gitdir: {primary / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
        )
        (primary / "sub" / ".env.guard").write_text("X=1\n", encoding="utf-8")
        message = self._expand(linked / "sub" / "t.j2")
        assert "sub/.env.guard" in message
        assert f"'{primary}/sub/.env.guard'" in message

    def test_missing_primary_directory_adds_no_hint(self, tmp_path):
        linked = tmp_path / "linked"
        linked.mkdir()
        (linked / ".git").write_text(
            f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'wt'}\n",
            encoding="utf-8",
        )
        assert "linked worktree" not in self._expand(linked / "t.j2")


class TestOwnContainerAttach:
    def test_own_container_id_parsed_from_mountinfo(self, monkeypatch):
        cid = "ab" * 32
        line = f"1 2 0:3 / /etc/hosts rw - ext4 /var/lib/docker/containers/{cid}/hosts rw\n"
        real_read = Path.read_text

        def fake_read(self, *a, **k):
            if str(self) == "/proc/self/mountinfo":
                return line
            return real_read(self, *a, **k)

        monkeypatch.setattr(workspace_env, "_own_container_id", _REAL_OWN_CONTAINER_ID)
        monkeypatch.setattr(Path, "read_text", fake_read)
        assert workspace_env._own_container_id() == cid

    def test_own_container_id_empty_when_unreadable_or_absent(self, monkeypatch):
        def boom(self, *a, **k):
            raise OSError("no proc")

        monkeypatch.setattr(workspace_env, "_own_container_id", _REAL_OWN_CONTAINER_ID)
        monkeypatch.setattr(Path, "read_text", boom)
        assert workspace_env._own_container_id() == ""
        monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: "no container here\n")
        assert workspace_env._own_container_id() == ""

    def test_stale_configured_name_is_replaced_by_the_real_container(
        self, monkeypatch, capsys
    ):
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "/naf-cockpit\n", "")

        monkeypatch.setattr(workspace_env, "_own_container_id", lambda: "c" * 64)
        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)
        assert (
            workspace_env._resolve_own_container_name("dstdns-devcontainer-vb")
            == "naf-cockpit"
        )
        assert calls == [["docker", "inspect", "c" * 64, "--format", "{{.Name}}"]]
        assert "naf-cockpit" in capsys.readouterr().out

    def test_matching_or_unknown_own_id_keeps_configured_name_silently(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(workspace_env, "_own_container_id", lambda: "")
        assert workspace_env._resolve_own_container_name("x") == "x"
        monkeypatch.setattr(workspace_env, "_own_container_id", lambda: "c" * 64)
        monkeypatch.setattr(
            workspace_env.subprocess, "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 0, "/x\n", ""),
        )
        assert workspace_env._resolve_own_container_name("x") == "x"
        assert capsys.readouterr().out == ""

    def test_short_id_hostname_fallback_resolves_to_the_name_without_warning(
        self, monkeypatch, capsys
    ):
        own = "abcdef012345" + "0" * 52
        monkeypatch.setattr(workspace_env, "_own_container_id", lambda: own)
        monkeypatch.setattr(
            workspace_env.subprocess, "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 0, "/naf-cockpit\n", ""),
        )
        assert workspace_env._resolve_own_container_name("abcdef012345") == "naf-cockpit"
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize(
        "result",
        [
            subprocess.CompletedProcess([], 1, "", "permission denied on containers/json"),
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "\n", ""),
        ],
    )
    def test_failed_inspect_warns_and_keeps_the_configured_name(
        self, monkeypatch, capsys, result
    ):
        monkeypatch.setattr(workspace_env, "_own_container_id", lambda: "c" * 64)
        monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_a, **_k: result)
        assert workspace_env._resolve_own_container_name("x") == "x"
        out = capsys.readouterr().out
        assert "could not inspect this process's own container" in out
        assert "'x' unverified" in out

    @pytest.mark.usefixtures("real_network_side_effects")
    def test_connect_uses_the_resolved_real_container(self, monkeypatch):
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setenv("DEVCONTAINER_NAME", "dstdns-devcontainer-vb")
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
        monkeypatch.setattr(
            workspace_env, "_resolve_own_container_name", lambda _configured: "naf-cockpit"
        )
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)
        workspace_env._connect_devcontainer_to_network("net")
        assert calls[-1] == ["docker", "network", "connect", "net", "naf-cockpit"]
