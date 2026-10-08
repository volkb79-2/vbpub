"""Regression tests for the vendored devcontainer persistence contract."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "devcontainer.json"
TEMPLATE_TEXT = TEMPLATE.read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "templates"))
import initialize_container_environment as bootstrap  # noqa: E402


MOUNT_RE = re.compile(
    r'^\s*"(?P<spec>source=(?P<source>[^,"]+),target=(?P<target>[^,"]+),'
    r'type=(?P<type>[^,"]+)(?:,[^"]*)?)",?\s*$'
)
PERSISTENCE_PREFIX = "${localEnv:HOME}/mdt--mounted-folders/"


def _template_mounts() -> list[tuple[str, str, str]]:
    mounts = []
    for line in TEMPLATE_TEXT.splitlines():
        match = MOUNT_RE.match(line)
        if match:
            mounts.append((match["spec"], match["source"], match["target"]))
    return mounts


def test_template_contains_source_backed_pi_claudelink_and_opencode_mounts() -> None:
    mounts = {source: target for _, source, target in _template_mounts()}
    expected = {
        f"{PERSISTENCE_PREFIX}.pi/": "/home/vscode/.pi",
        f"{PERSISTENCE_PREFIX}.claudelink/": "/home/vscode/.claudelink",
        f"{PERSISTENCE_PREFIX}opencode-data/": "/home/vscode/.local/share/opencode",
    }

    assert {source: mounts[source] for source in expected} == expected
    for source, target in expected.items():
        spec = f"source={source},target={target},type=bind,consistency=cached"
        assert f'"{spec}"' in TEMPLATE_TEXT


def test_persistent_directory_mounts_remain_alphabetically_ordered() -> None:
    directory_sources = [
        source
        for _, source, _ in _template_mounts()
        if source.startswith(PERSISTENCE_PREFIX)
        and not source.endswith((".claude.json", ".reasonix.toml"))
    ]
    assert all(source.endswith("/") for source in directory_sources)

    names = [
        source.removeprefix(PERSISTENCE_PREFIX).removesuffix("/")
        for source in directory_sources
        if source.removeprefix(PERSISTENCE_PREFIX).removesuffix("/") != "tmp"
    ]

    assert names == sorted(names)
    assert names[-1] == "opencode-data"


def test_template_marks_home_directories_and_files_by_source_spelling() -> None:
    home_sources = [
        source
        for _, source, _ in _template_mounts()
        if source.startswith("${localEnv:HOME}/")
    ]
    directory_sources = [source for source in home_sources if not source.endswith(".claude.json")]
    file_sources = [source for source in home_sources if not source.endswith("/")]

    assert directory_sources
    assert all(source.endswith("/") for source in directory_sources)
    assert file_sources == [f"{PERSISTENCE_PREFIX}.claude.json"]
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY" in TEMPLATE_TEXT
    assert "create-by-spelling" in TEMPLATE_TEXT
    assert "no `/` marks it as an empty" in TEMPLATE_TEXT
    assert "Docker's --mount" in TEMPLATE_TEXT
    assert "refuses a missing source" in TEMPLATE_TEXT
    assert "Docker creates them as root" not in TEMPLATE_TEXT


def test_template_keeps_codex_database_persistent_and_container_running() -> None:
    assert '"CODEX_SQLITE_HOME": "/home/vscode/.codex/sqlite-shared"' in TEMPLATE_TEXT
    assert '"shutdownAction": "none"' in TEMPLATE_TEXT


def test_bootstrap_derives_every_active_home_bind_source() -> None:
    host_home = Path("/host/home")
    original_home = bootstrap.HOME
    bootstrap.HOME = host_home
    try:
        derived = [
            path
            for source in bootstrap.host_bind_sources(TEMPLATE)
            if (path := bootstrap.to_home_dir(source)) is not None
        ]
    finally:
        bootstrap.HOME = original_home

    grouped = host_home / bootstrap.PARENT_NAME
    expected = [
        host_home / ".ssh",
        grouped / ".claude",
        grouped / ".claudelink",
        grouped / ".codex",
        grouped / ".codex2",
        grouped / ".config",
        grouped / ".gnupg",
        grouped / ".local",
        grouped / ".minisign",
        grouped / ".openclaw",
        grouped / ".pi",
        grouped / ".reasonix",
        grouped / ".ssh",
        grouped / "opencode-data",
        grouped / ".claude.json",
        grouped / "tmp",
    ]
    assert derived == expected


def test_bootstrap_accepts_existing_extensionless_and_arbitrary_file_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = host_home / "mdt--mounted-folders" / ".npmrc"
    source.parent.mkdir(parents=True)
    source.write_text("registry=https://registry.example/\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "HOME", host_home)

    assert bootstrap.ensure(source) is True
    assert "(file)" in capsys.readouterr().out
    assert source.is_file()


def test_bootstrap_defaults_missing_unmarked_custom_source_to_empty_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = "${localEnv:HOME}/mdt--mounted-folders/.custom-state"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [source])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)

    assert bootstrap.main() == 0
    created = host_home / "mdt--mounted-folders" / ".custom-state"
    assert created.is_file()
    assert created.read_bytes() == b""
    assert created.stat().st_mode & 0o777 == 0o600
    assert "empty file" in capsys.readouterr().out


def test_bootstrap_reads_strict_missing_source_policy_from_host_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = "${localEnv:HOME}/mdt--mounted-folders/.strict-state"
    config_path = tmp_path / "host-setup.env"
    config_path.write_text(
        'DEVCONTAINER_MISSING_BIND_SOURCE_POLICY="fail"\n', encoding="utf-8"
    )
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", config_path)
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [source])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)

    assert bootstrap.main() == 1
    assert not (host_home / "mdt--mounted-folders" / ".strict-state").exists()
    assert "policy is `fail`" in capsys.readouterr().err


def test_bootstrap_rejects_invalid_missing_source_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "host-setup.env"
    config_path.write_text(
        "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=guess\n", encoding="utf-8"
    )
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", config_path)
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)

    assert bootstrap.main() == 1
    assert "must be create-by-spelling or fail" in capsys.readouterr().err


def test_bootstrap_refuses_conflicting_source_markers_without_fallback_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    base = "${localEnv:HOME}/mdt--mounted-folders/.conflicted"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(
        bootstrap,
        "host_bind_sources",
        lambda _path: [base, base + "/"],
    )
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)

    assert bootstrap.main() == 1
    assert not (host_home / "mdt--mounted-folders" / ".conflicted").exists()
    assert not (host_home / "mdt--mounted-folders" / ".claude").exists()
    assert "conflicting file/directory source spellings" in capsys.readouterr().err


def test_bootstrap_creates_missing_custom_directory_with_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_home = tmp_path / "host"
    source = "${localEnv:HOME}/mdt--mounted-folders/.custom-cache/"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [source])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)

    assert bootstrap.main() == 0
    assert (host_home / "mdt--mounted-folders" / ".custom-cache").is_dir()


def test_bootstrap_treats_gitconfig_as_a_file_and_rejects_a_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = host_home / "mdt--mounted-folders" / ".gitconfig"
    source.parent.mkdir(parents=True)
    source.write_text("[user]\n\tname = Example\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "HOME", host_home)

    assert bootstrap.ensure(source) is True
    assert "(file)" in capsys.readouterr().out

    source.unlink()
    source.mkdir()
    assert bootstrap.ensure(source) is False
    assert source.is_dir()
    assert "expected a file bind source" in capsys.readouterr().err

    source.rmdir()
    assert bootstrap.ensure(source) is True
    assert source.is_file()
    assert source.read_bytes() == b""

    assert bootstrap.ensure(source, expected_type="directory") is False
    assert source.is_file()
    assert "cannot use the directory `/` marker" in capsys.readouterr().err


def test_bootstrap_main_uses_complete_fallback_when_template_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_home = tmp_path / "host"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)
    ensured: list[tuple[Path, str, str]] = []

    def record(path: Path, *, expected_type: str, missing_policy: str) -> None:
        ensured.append((path, expected_type, missing_policy))

    monkeypatch.setattr(bootstrap, "ensure", record)

    assert bootstrap.main() == 0
    assert ensured == [
        (path, expected_type, "create-by-spelling")
        for path, expected_type in bootstrap.fallback_bind_paths()
    ]
    assert (host_home / bootstrap.PARENT_NAME / ".pi", "directory", "create-by-spelling") in ensured
    assert (host_home / bootstrap.PARENT_NAME / ".claudelink", "directory", "create-by-spelling") in ensured
    assert (host_home / bootstrap.PARENT_NAME / ".local", "directory", "create-by-spelling") in ensured
    assert (host_home / bootstrap.PARENT_NAME / "opencode-data", "directory", "create-by-spelling") in ensured


def test_host_cgroup_slices_must_be_loaded_from_installed_units(tmp_path, capsys) -> None:
    calls: list[list[str]] = []
    unit_root = tmp_path / "etc/systemd/system"
    unit_root.mkdir(parents=True)
    for unit in ("dev-interactive.slice", "dev-background.slice", "dev-gates.slice"):
        (unit_root / unit).write_text("[Slice]\n", encoding="utf-8")

    def runner(argv, **kwargs):
        calls.append(argv)
        unit = argv[2]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                f"Id={unit}\nLoadState=loaded\n"
                f"FragmentPath={unit_root / unit}\n"
            ),
            stderr="",
        )

    slices = bootstrap.host_cgroup_slices(TEMPLATE)
    assert slices == (
        "dev-interactive.slice",
        "dev-background.slice",
        "dev-gates.slice",
    )
    assert bootstrap.verify_host_cgroup_slices(slices, runner=runner)
    assert [call[2] for call in calls] == list(slices)
    assert all("--property=Id,LoadState,FragmentPath" in call for call in calls)


def test_host_cgroup_preflight_docs_and_consumer_example_stay_in_sync() -> None:
    top_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    template_readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")

    section = consumers.split("## Consumer devcontainer", 1)[1]
    example_text = section.split("```jsonc", 1)[1].split("```", 1)[0]
    example = json.loads(example_text)
    slices = bootstrap.host_cgroup_slices(TEMPLATE)
    assert example["initializeCommand"] == (
        "python3 .devcontainer/initialize_container_environment.py"
    )
    assert example["runArgs"] == [f"--cgroup-parent={slices[0]}"]
    assert "docs/DESIGN-GUIDE.md#host-cgroup-preflight" in top_readme
    assert "CONSUMERS.md#host-cgroup-preflight" in design
    assert "DESIGN-GUIDE.md#host-cgroup-preflight" in consumers
    assert "docs/CONSUMERS.md#host-cgroup-preflight" in template_readme
    assert "preflight design](docs/DESIGN-GUIDE.md#host-cgroup-preflight)" in lifecycle
    for unit in slices:
        assert unit in top_readme
        assert unit in design
        assert unit in consumers
    assert "safe for every consumer" not in lifecycle


@pytest.mark.parametrize(
    ("interactive", "run_args"),
    [
        ("dev-other.slice", ("dev-interactive.slice",)),
        ("dev-interactive.slice", ("dev-interactive.slice", "dev-background.slice")),
        ("dev-interactive.slice", ()),
    ],
)
def test_host_cgroup_slice_source_must_match_runarg(tmp_path, interactive, run_args) -> None:
    config = tmp_path / "devcontainer.json"
    config.write_text(
        json.dumps(
            {
                "containerEnv": {
                    "CGROUP_PARENT_DEV_INTERACTIVE": interactive,
                    "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
                    "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
                },
                "runArgs": [f"--cgroup-parent={parent}" for parent in run_args],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="--cgroup-parent"):
        bootstrap.host_cgroup_slices(config)


def test_host_cgroup_slices_parse_effective_jsonc_runargs_not_decoy_lines(
    tmp_path,
) -> None:
    config = tmp_path / "devcontainer.json"
    config.write_text(
        '''{
          // A nested decoy must not stand in for Docker's effective runArgs.
          "notes": ["--cgroup-parent=dev-interactive.slice"],
          "comment": "https://example.invalid/a//b",
          "containerEnv": {
            "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
            "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
            "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
          },
          "runArgs": ["--cgroup-parent=dev-other.slice",],
        }
        ''',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        bootstrap.host_cgroup_slices(config)


def test_host_cgroup_slices_accept_two_token_parent_and_reject_duplicate_json_keys(
    tmp_path,
) -> None:
    config = tmp_path / "devcontainer.json"
    config.write_text(
        json.dumps(
            {
                "containerEnv": {
                    "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
                    "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
                    "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
                },
                "runArgs": ["--cgroup-parent", "dev-interactive.slice"],
            }
        ),
        encoding="utf-8",
    )
    assert bootstrap.host_cgroup_slices(config) == (
        "dev-interactive.slice",
        "dev-background.slice",
        "dev-gates.slice",
    )

    config.write_text(
        '{"containerEnv": {}, "containerEnv": {}, "runArgs": []}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSONC object key"):
        bootstrap.host_cgroup_slices(config)


@pytest.mark.parametrize("label_option", ["--label", "-l"])
def test_host_cgroup_parent_value_cannot_be_hidden_as_a_label_value(
    tmp_path, label_option
) -> None:
    config = tmp_path / "devcontainer.json"
    template = {
        "containerEnv": {
            "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
            "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
            "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
        },
        "runArgs": [label_option, "--cgroup-parent=dev-interactive.slice"],
    }
    config.write_text(json.dumps(template), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one --cgroup-parent"):
        bootstrap.host_cgroup_slices(config)


def test_host_cgroup_parent_after_a_label_value_is_the_effective_option(
    tmp_path,
) -> None:
    config = tmp_path / "devcontainer.json"
    config.write_text(
        json.dumps(
            {
                "containerEnv": {
                    "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
                    "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
                    "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
                },
                "runArgs": [
                    "--label",
                    "--cgroup-parent=dev-decoy.slice",
                    "--cgroup-parent=dev-interactive.slice",
                ],
            }
        ),
        encoding="utf-8",
    )

    assert bootstrap.host_cgroup_slices(config) == (
        "dev-interactive.slice",
        "dev-background.slice",
        "dev-gates.slice",
    )


def test_host_cgroup_preflight_refuses_unclassified_docker_run_options(tmp_path) -> None:
    config = tmp_path / "devcontainer.json"
    config.write_text(
        json.dumps(
            {
                "containerEnv": {
                    "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
                    "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
                    "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
                },
                "runArgs": ["--future-option", "value", "--cgroup-parent=dev-interactive.slice"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported Docker runArg"):
        bootstrap.host_cgroup_slices(config)


@pytest.mark.parametrize(
    "facts",
    [
        "Id=dev-interactive.slice\nLoadState=not-found\nFragmentPath=\n",
        "Id=dev-interactive.slice\nLoadState=loaded\nFragmentPath=\n",
        "Id=dev-interactive.slice\nLoadState=loaded\n"
        "FragmentPath=/run/systemd/transient/dev-interactive.slice\n",
    ],
)
def test_host_cgroup_slice_refusal_is_fail_closed(capsys, facts) -> None:
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=facts, stderr="")

    assert not bootstrap.verify_host_cgroup_slices(
        ("dev-interactive.slice",), runner=runner
    )
    assert "refusing to create the devcontainer" in capsys.readouterr().err


def test_host_cgroup_slice_query_failure_is_refused(capsys) -> None:
    def runner(argv, **kwargs):
        raise FileNotFoundError("systemctl")

    assert not bootstrap.verify_host_cgroup_slices(
        ("dev-interactive.slice",), runner=runner
    )
    assert "cannot verify host cgroup slice" in capsys.readouterr().err


def test_host_cgroup_slice_query_timeout_is_refused(capsys) -> None:
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=10)

    assert not bootstrap.verify_host_cgroup_slices(
        ("dev-interactive.slice",), runner=runner
    )
    assert "cannot verify host cgroup slice" in capsys.readouterr().err


def test_loaded_host_cgroup_unit_must_have_an_installed_file(capsys) -> None:
    def runner(argv, **kwargs):
        unit = argv[2]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                f"Id={unit}\nLoadState=loaded\n"
                f"FragmentPath=/etc/systemd/system/{unit}\n"
            ),
            stderr="",
        )

    assert not bootstrap.verify_host_cgroup_slices(
        ("dev-interactive.slice",), runner=runner
    )
    assert "refusing to create the devcontainer" in capsys.readouterr().err


def test_bootstrap_checks_slices_before_preparing_host_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = "${localEnv:HOME}/mdt--mounted-folders/.must-not-create"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [source])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: False)

    assert bootstrap.main() == 1
    assert not (host_home / bootstrap.PARENT_NAME / ".must-not-create").exists()


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "templates" / "README.md",
        ROOT / "DEVCONTAINER-LIFECYCLE.md",
        ROOT / "docs" / "CONSUMERS.md",
        ROOT / "USAGE.md",
        ROOT / "README.md",
    ],
)
def test_user_facing_docs_match_persistence_targets(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in (
        "/home/vscode/.pi",
        "/home/vscode/.claudelink",
        "/home/vscode/.local/share/opencode",
    ):
        assert target in text, path
    assert "opencode-data" in text, path
    assert ".pi/agent/sessions" in text, path
    assert ".claudelink" in text, path


def test_persistence_docs_expose_migration_recipe_and_no_stale_opencode_path() -> None:
    template_readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    usage = (ROOT / "USAGE.md").read_text(encoding="utf-8")
    top_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for text in (template_readme, lifecycle, consumers):
        assert "cp -a" in text
    for text in (template_readme, lifecycle, consumers, usage):
        assert ".claudelink" in text
        assert ".pi" in text
        assert ".local/share/opencode" in text
        assert "mdt--mounted-folders/opencode-data" in text
        assert "docker cp" in text
        assert "WAL" in text
    assert "`~/.opencode`" not in template_readme
    assert "`~/tmp`" not in template_readme
    assert "DEVCONTAINER-LIFECYCLE.md#migrating-a-running-devcontainer-before-adopting-the-mounts" in top_readme
    assert "templates/README.md#migrate-existing-pi-claudelink-and-opencode-state-once" in top_readme


def test_bind_source_type_policy_is_explained_and_cross_linked() -> None:
    top_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    template_readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")

    assert "## Bootstrap uses the source filesystem type" in design
    assert "docs/DESIGN-GUIDE.md#bootstrap-uses-the-source-filesystem-type" in top_readme
    for text in (top_readme, design, template_readme, lifecycle):
        assert "docs/CONSUMERS.md#optional-git-config-mount" in text or (
            "CONSUMERS.md#optional-git-config-mount" in text
        )
    assert "source=${localEnv:HOME}/mdt--mounted-folders/.my-cache/" in consumers
    assert "append `/`" in consumers
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=create-by-spelling" in consumers
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail" in consumers
    assert "create-by-spelling" in design and "no trailing `/`" in design
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY" in template_readme
    assert "empty regular file (`0600`)" in lifecycle
    assert ".gitconfig" in consumers
    for text in (top_readme, design, consumers):
        assert "CODEX_SQLITE_HOME" in text
        assert 'shutdownAction: "none"' in text or 'shutdownAction` to `none' in text
    assert "docs/CONSUMERS.md#codex-profile-state-and-container-lifetime" in top_readme
    assert "CONSUMERS.md#codex-profile-state-and-container-lifetime" in design


def test_running_container_recipe_quiesces_before_copying_sqlite_state() -> None:
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")

    runbook_heading = "## Migrating a running devcontainer before adopting the mounts"
    stopped_check = 'test "$(docker inspect --format \'{{.State.Running}}\' "$container_name")" = "false"'
    claudelink_copy = 'docker cp "$container_name:/home/vscode/.claudelink/." "$host_state/.claudelink/"'
    assert runbook_heading in lifecycle
    assert "set -eu" in lifecycle
    assert 'docker stop --timeout 30 "$container_name"' in lifecycle
    assert 'docker exec "$container_name" test -d /home/vscode/.claudelink' in lifecycle
    assert stopped_check in lifecycle
    assert claudelink_copy in lifecycle
    assert lifecycle.index("docker stop --timeout 30") < lifecycle.index(stopped_check)
    assert lifecycle.index(stopped_check) < lifecycle.index(claudelink_copy)
    for sidecar in ("nexus.db", "nexus.db-wal", "nexus.db-shm"):
        assert sidecar in lifecycle
    assert "Never copy only" in lifecycle
