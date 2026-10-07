"""CMRU-CREDPASS: publisher credentials reach a release child over a private pipe.

No credential FILE is ever copied into, or read from, a release worktree. All
tokens below are synthetic fakes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import cmru
from cmru import cli, config, credential_handoff, transaction
from cmru.credential_handoff import CredentialHandoff

ROOT_TOKEN = "fake-root-token-AAAA1111"
ALPHA_TOKEN = "fake-alpha-token-BBBB2222"
BETA_TOKEN = "fake-beta-token-CCCC3333"
ENV_TOKEN = "fake-env-token-DDDD4444"
FILE_TOKEN = "fake-file-token-EEEE5555"
_ALL_TOKENS = (ROOT_TOKEN, ALPHA_TOKEN, BETA_TOKEN, ENV_TOKEN, FILE_TOKEN)

_ENV_NAMES = (
    credential_handoff.CHILD_ENV,
    credential_handoff.CREDENTIAL_FD_ENV,
    credential_handoff.CREDENTIAL_STATE_ENV,
    "GITHUB_PUSH_PAT",
    "GITHUB_TOKEN",
)


@pytest.fixture(autouse=True)
def clean_handoff_state(monkeypatch):
    for name in _ENV_NAMES:
        # setenv-then-delenv registers a restore, so values the code under test
        # writes straight into os.environ are undone after each test.
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    credential_handoff.reset_cache()
    yield
    credential_handoff.reset_cache()


def _pipe_with(payload: bytes) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return read_fd


def _as_child(monkeypatch) -> None:
    monkeypatch.setenv(credential_handoff.CHILD_ENV, "1")


def _secret_toml(token: str) -> str:
    return f'[github]\ntoken = "{token}"\n'


# --- payload codec ---------------------------------------------------------------


def test_payload_round_trips_root_and_per_project_tokens():
    handoff = CredentialHandoff(root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN, "beta": ""})
    assert credential_handoff.decode(credential_handoff.encode(handoff)) == handoff


def test_oversized_payload_is_refused_before_it_can_block_a_pipe_write():
    huge = CredentialHandoff(root="x" * (credential_handoff.MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(RuntimeError, match="too large"):
        credential_handoff.encode(huge)


@pytest.mark.parametrize("payload", [
    b"\xff\xfe",
    b"not json",
    b"[]",
    json.dumps({"schema_version": 1, "root": "r"}).encode(),
    json.dumps({"schema_version": 2, "root": "r", "projects": {}}).encode(),
    json.dumps({"schema_version": 1, "root": 7, "projects": {}}).encode(),
    json.dumps({"schema_version": 1, "root": "r", "projects": []}).encode(),
    json.dumps({"schema_version": 1, "root": "r", "projects": {"a": 1}}).encode(),
])
def test_malformed_payloads_are_refused(payload):
    with pytest.raises(RuntimeError, match="invalid credential handoff payload"):
        credential_handoff.decode(payload)


# --- child side ------------------------------------------------------------------


def test_a_process_that_is_not_a_transaction_child_has_no_handoff():
    assert credential_handoff.child_handoff() is None


def test_a_child_without_a_handoff_fails_closed(monkeypatch):
    _as_child(monkeypatch)
    with pytest.raises(RuntimeError, match="no credential handoff"):
        credential_handoff.child_handoff()


def test_a_child_consumes_the_pipe_once_and_caches_the_result(monkeypatch):
    _as_child(monkeypatch)
    read_fd = _pipe_with(credential_handoff.encode(
        CredentialHandoff(root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN}),
    ))
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, str(read_fd))

    first = credential_handoff.child_handoff()
    second = credential_handoff.child_handoff()

    assert first is second
    assert first == CredentialHandoff(root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN})
    assert credential_handoff.CREDENTIAL_FD_ENV not in os.environ
    assert os.environ[credential_handoff.CREDENTIAL_STATE_ENV] == "consumed"
    with pytest.raises(OSError):
        os.fstat(read_fd)  # the child closed its read end


def test_a_process_nested_below_a_consuming_child_gets_the_nested_marker(monkeypatch):
    _as_child(monkeypatch)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_STATE_ENV, "consumed")
    assert credential_handoff.child_handoff() == CredentialHandoff(nested=True)


def test_a_bogus_descriptor_value_is_refused(monkeypatch):
    _as_child(monkeypatch)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, "not-a-number")
    with pytest.raises(RuntimeError, match="invalid internal credential pipe"):
        credential_handoff.child_handoff()


@pytest.mark.parametrize("descriptor", ["0", "1", "2"])
def test_a_standard_descriptor_is_never_read_or_closed(monkeypatch, descriptor):
    _as_child(monkeypatch)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, descriptor)
    with pytest.raises(RuntimeError, match="invalid internal credential pipe"):
        credential_handoff.child_handoff()
    os.fstat(int(descriptor))  # still open


def test_a_regular_file_descriptor_is_not_accepted_as_the_pipe(monkeypatch, tmp_path):
    _as_child(monkeypatch)
    path = tmp_path / "payload"
    path.write_bytes(credential_handoff.encode(CredentialHandoff(root=ROOT_TOKEN)))
    fd = os.open(path, os.O_RDONLY)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, str(fd))
    with pytest.raises(RuntimeError, match="expected an inherited pipe"):
        credential_handoff.child_handoff()
    with pytest.raises(OSError):
        os.fstat(fd)  # the handoff reader closes what it was given, even when refusing it


def test_an_oversized_pipe_payload_is_refused(monkeypatch):
    _as_child(monkeypatch)
    read_fd = _pipe_with(b"x" * (credential_handoff.MAX_PAYLOAD_BYTES + 1))
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, str(read_fd))
    with pytest.raises(RuntimeError, match="payload is too large"):
        credential_handoff.child_handoff()


def test_a_closed_descriptor_is_refused(monkeypatch):
    _as_child(monkeypatch)
    read_fd = _pipe_with(b"{}")
    os.close(read_fd)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, str(read_fd))
    with pytest.raises(RuntimeError, match="invalid internal credential pipe"):
        credential_handoff.child_handoff()


# --- the config loader in a transaction child -------------------------------------


def _estate(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """A root with FILE secrets that a child must never read."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "cmru.secret.toml").write_text(_secret_toml(FILE_TOKEN), encoding="utf-8")
    (tmp_path / "alpha" / "cmru.secret.toml").write_text(
        _secret_toml(FILE_TOKEN), encoding="utf-8",
    )
    return tmp_path, {
        "alpha": tmp_path / "alpha" / "cmru.toml",
        "beta": tmp_path / "beta" / "cmru.toml",
    }


def _deliver(monkeypatch, handoff: CredentialHandoff) -> None:
    _as_child(monkeypatch)
    monkeypatch.setenv(
        credential_handoff.CREDENTIAL_FD_ENV, str(_pipe_with(credential_handoff.encode(handoff))),
    )


def test_a_child_uses_the_handed_tokens_and_never_reads_its_own_secret_files(
    monkeypatch, tmp_path,
):
    root, projects = _estate(tmp_path)
    _deliver(monkeypatch, CredentialHandoff(
        root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN},
    ))
    root_token, project_tokens = config._load_repository_secrets(root, projects)
    assert root_token == ROOT_TOKEN
    # alpha is routed to its own overlay token; beta (not in the handoff) gets the root.
    assert project_tokens == {"alpha": ALPHA_TOKEN, "beta": ROOT_TOKEN}
    assert FILE_TOKEN not in (root_token, *project_tokens.values())


def test_the_invocation_environment_token_does_not_override_the_handoff_in_a_child(
    monkeypatch, tmp_path,
):
    # The parent already applied "environment token wins" when it resolved the
    # handoff; a child must not second-guess it from a leaked ambient variable.
    root, projects = _estate(tmp_path)
    monkeypatch.setenv("GITHUB_PUSH_PAT", ENV_TOKEN)
    _deliver(monkeypatch, CredentialHandoff(root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN}))
    root_token, project_tokens = config._load_repository_secrets(root, projects)
    assert (root_token, project_tokens["alpha"]) == (ROOT_TOKEN, ALPHA_TOKEN)


def test_a_child_without_a_handoff_refuses_instead_of_reading_its_root_secret(
    monkeypatch, tmp_path, capsys,
):
    root, projects = _estate(tmp_path)
    _as_child(monkeypatch)
    with pytest.raises(SystemExit):
        config._load_repository_secrets(root, projects)
    err = capsys.readouterr().err
    assert "no credential handoff" in err
    assert FILE_TOKEN not in err


def test_a_nested_process_sees_only_the_environment_token_never_a_file(monkeypatch, tmp_path):
    root, projects = _estate(tmp_path)
    _as_child(monkeypatch)
    monkeypatch.setenv(credential_handoff.CREDENTIAL_STATE_ENV, "consumed")
    assert config._load_repository_secrets(root, projects) == (
        "", {"alpha": "", "beta": ""},
    )
    monkeypatch.setenv("GITHUB_TOKEN", ENV_TOKEN)
    assert config._load_repository_secrets(root, projects) == (
        ENV_TOKEN, {"alpha": ENV_TOKEN, "beta": ENV_TOKEN},
    )


# --- parent side: resolution is unchanged, then handed over -----------------------


def _handoff_for(root: Path, projects: dict[str, Path]) -> CredentialHandoff:
    root_token, project_tokens = config._load_repository_secrets(root, projects)
    return cli._credential_handoff(
        SimpleNamespace(token=root_token),
        {name: SimpleNamespace(github_token=token) for name, token in project_tokens.items()},
        list(projects),
    )


def test_the_parent_resolves_root_and_per_project_overlay_tokens_from_its_checkout(tmp_path):
    root, projects = _estate(tmp_path)
    (root / "alpha" / "cmru.secret.toml").write_text(_secret_toml(ALPHA_TOKEN), encoding="utf-8")
    (root / "cmru.secret.toml").write_text(_secret_toml(ROOT_TOKEN), encoding="utf-8")
    assert _handoff_for(root, projects) == CredentialHandoff(
        root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN, "beta": ROOT_TOKEN},
    )


def test_the_parent_environment_token_wins_over_every_secret_file(monkeypatch, tmp_path):
    root, projects = _estate(tmp_path)
    monkeypatch.setenv("GITHUB_PUSH_PAT", ENV_TOKEN)
    assert _handoff_for(root, projects) == CredentialHandoff(
        root=ENV_TOKEN, projects={"alpha": ENV_TOKEN, "beta": ENV_TOKEN},
    )


def test_an_absent_credential_is_handed_over_as_empty_strings():
    handoff = cli._credential_handoff(
        SimpleNamespace(token=None), {"alpha": SimpleNamespace(github_token=None)}, ["alpha"],
    )
    assert handoff == CredentialHandoff(root="", projects={"alpha": ""})


# --- run_child: a real child process --------------------------------------------

_STUB = """#!{python}
import hashlib, json, os, sys
from pathlib import Path
from cmru import credential_handoff

result = {{"argv": sys.argv, "cwd_secret_files": [
    str(p) for p in Path.cwd().rglob("cmru.secret.toml")
]}}
try:
    handoff = credential_handoff.child_handoff()
except RuntimeError as exc:
    result["error"] = str(exc)
else:
    tokens = [handoff.root, *handoff.projects.values()]
    cmdline = Path("/proc/self/cmdline").read_bytes()
    environment = "\\n".join(os.environ.values())
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    result.update(
        root_sha=sha(handoff.root),
        project_shas={{name: sha(token) for name, token in handoff.projects.items()}},
        token_in_cmdline=any(t and t.encode() in cmdline for t in tokens),
        token_in_environment=any(t and t in environment for t in tokens),
        fd_env=os.environ.get(credential_handoff.CREDENTIAL_FD_ENV),
        state=os.environ.get(credential_handoff.CREDENTIAL_STATE_ENV),
    )
Path(os.environ["CREDPASS_RESULT"]).write_text(json.dumps(result))
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def stub_child(monkeypatch, tmp_path):
    """Run transaction.run_child against a real subprocess standing in for cmru."""
    script = tmp_path / "stub-cmru"
    script.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    result_path = tmp_path / "result.json"
    worktree = tmp_path / "release-worktree"
    worktree.mkdir()
    monkeypatch.setattr(transaction, "internal_launcher", lambda _root: str(script))
    monkeypatch.setenv("CREDPASS_RESULT", str(result_path))
    monkeypatch.setenv("PYTHONPATH", str(Path(cmru.__file__).resolve().parent.parent))
    workspace = transaction.ReleaseWorkspace(
        tmp_path, worktree, "cmru/release/credpass", "a" * 40,
    )

    def run(credentials, args=("--config", "cmru.orchestration.toml")):
        code = transaction.run_child(
            workspace, list(args), project_names=["alpha", "beta"], credentials=credentials,
        )
        return code, json.loads(result_path.read_text(encoding="utf-8"))

    run.tmp_path = tmp_path
    return run


def test_the_child_receives_per_project_tokens_over_the_pipe_only(stub_child):
    code, result = stub_child(
        CredentialHandoff(root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN, "beta": BETA_TOKEN}),
    )
    assert code == 0
    assert result["root_sha"] == _sha(ROOT_TOKEN)
    assert result["project_shas"] == {"alpha": _sha(ALPHA_TOKEN), "beta": _sha(BETA_TOKEN)}
    assert result["token_in_cmdline"] is False
    assert result["token_in_environment"] is False
    # The descriptor NUMBER is consumed from the env; only the marker remains.
    assert result["fd_env"] is None and result["state"] == "consumed"
    assert result["cwd_secret_files"] == []


def test_the_token_never_appears_in_argv_logs_or_anything_left_on_disk(stub_child, capfd):
    handoff = CredentialHandoff(
        root=ROOT_TOKEN, projects={"alpha": ALPHA_TOKEN, "beta": BETA_TOKEN},
    )
    code, result = stub_child(handoff)
    assert code == 0
    assert not any(token in " ".join(result["argv"]) for token in _ALL_TOKENS)
    captured = capfd.readouterr()
    assert not any(token in captured.out + captured.err for token in _ALL_TOKENS)
    # Scan every byte retained anywhere under the scratch tree (worktree, logs,
    # artifacts, the stub's own result) for any token.
    for path in stub_child.tmp_path.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not any(token.encode() in data for token in _ALL_TOKENS), path


def test_a_child_launched_without_credentials_fails_closed(stub_child):
    code, result = stub_child(None)
    assert code == 0  # the stub itself reports the refusal it observed
    assert "no credential handoff" in result["error"]


def test_a_stale_ambient_descriptor_variable_never_leaks_into_the_child(
    stub_child, monkeypatch,
):
    monkeypatch.setenv(credential_handoff.CREDENTIAL_FD_ENV, "999")
    monkeypatch.setenv(credential_handoff.CREDENTIAL_STATE_ENV, "consumed")
    code, result = stub_child(None)
    assert "no credential handoff" in result["error"]


def test_a_short_pipe_write_is_refused_and_leaks_no_descriptor(stub_child, monkeypatch):
    opened: list[int] = []
    real_pipe = os.pipe

    def recording_pipe():
        pair = real_pipe()
        opened.extend(pair)
        return pair

    monkeypatch.setattr(transaction.os, "pipe", recording_pipe)
    monkeypatch.setattr(transaction.os, "write", lambda _fd, _payload: 1)
    with pytest.raises(RuntimeError, match="short write"):
        stub_child(CredentialHandoff(root=ROOT_TOKEN))
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


# --- the launcher: no secret file in the release worktree, ever -------------------


@pytest.fixture
def release_launcher(monkeypatch, tmp_path):
    """cmru.main('release', ...) with Git/transaction plumbing replaced by stubs.

    The caller's checkout holds real-looking secret files; the release worktree
    is a real directory, so a stray copy into it is observable.
    """
    (tmp_path / "demo").mkdir()
    (tmp_path / "cmru.secret.toml").write_text(_secret_toml(FILE_TOKEN), encoding="utf-8")
    (tmp_path / "demo" / "cmru.secret.toml").write_text(
        _secret_toml(FILE_TOKEN), encoding="utf-8",
    )
    project = cli.ProjectConfig(
        "demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token=ALPHA_TOKEN,
    )
    loaded = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", ROOT_TOKEN, "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    worktree = tmp_path / "release"
    worktree.mkdir()
    workspace = transaction.ReleaseWorkspace(tmp_path, worktree, "cmru/release/x", "a" * 40)
    seen: dict[str, list] = {"handed": [], "files_during": [], "files_after": []}

    def secret_files():
        return sorted(str(p) for p in worktree.rglob("cmru.secret.toml"))

    def fake_run_child(_workspace, _args, **kwargs):
        seen["handed"].append(kwargs.get("credentials"))
        seen["files_during"].append(secret_files())  # gate time
        return 0

    def fake_remove_workspace(_workspace):
        seen["files_after"].append(secret_files())  # publish complete, pre-cleanup

    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli.transaction, "project_git_family_groups",
                        lambda root, projects: {root: list(projects)})
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_a, **_k: {})
    monkeypatch.setattr(cli.transaction, "write_release_tag_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(cli, "_project_git_tag_policy_at_snapshot",
                        lambda _root, _base, _project, **_kw: False)
    monkeypatch.setattr(cli, "_project_config_paths_at_snapshot",
                        lambda _root, _base, _config, _configs, names: {
                            name: Path(name) / "cmru.toml" for name in names})
    monkeypatch.setattr(cli, "_project_config_paths_in_candidate",
                        lambda _s, _c, _cfg, _configs, names: {
                            name: Path(name) / "cmru.toml" for name in names})
    monkeypatch.setattr(cli, "_project_release_policy_in_candidate",
                        lambda _candidate, name, _config: (f"{name}-v", False))
    monkeypatch.setattr(cli, "_assert_resume_candidate_is_safe_to_replay", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_a: {})
    monkeypatch.setattr(cli.transaction, "clear_plan_refused", lambda *_a: None)
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_a, **_k: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_a, **_k: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *_a, **_k: workspace)
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *_a, **_k: workspace)
    monkeypatch.setattr(cli.transaction, "read_release_scope_for_path", lambda _p: ["demo"])
    monkeypatch.setattr(cli.transaction, "assert_resume_workspace_committed", lambda _p: None)
    monkeypatch.setattr(cli.transaction, "run_child", fake_run_child)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", fake_remove_workspace)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *_a, **_k: transaction._SyncLocalMainResult(True),
    )

    def launch(*extra):
        return cli.main([
            "release", "demo", "--config", str(tmp_path / "cmru.toml"),
            "--discard", "logs", "--discard", "artifacts", *extra,
        ])

    launch.seen = seen
    launch.worktree = worktree
    return launch


def test_a_release_hands_the_credential_to_the_child_and_copies_no_secret_file(
    release_launcher,
):
    assert release_launcher() == 0
    expected = CredentialHandoff(root=ROOT_TOKEN, projects={"demo": ALPHA_TOKEN})
    assert release_launcher.seen["handed"] == [expected]
    # no secret file at gate time, and none by the time publishing has finished
    assert release_launcher.seen["files_during"] == [[]]
    assert release_launcher.seen["files_after"] == [[]]


def test_a_resumed_release_hands_the_credential_to_the_child_the_same_way(release_launcher):
    assert release_launcher("--resume", str(release_launcher.worktree)) == 0
    expected = CredentialHandoff(root=ROOT_TOKEN, projects={"demo": ALPHA_TOKEN})
    assert release_launcher.seen["handed"] == [expected]
    assert release_launcher.seen["files_during"] == [[]]
    assert release_launcher.seen["files_after"] == [[]]
