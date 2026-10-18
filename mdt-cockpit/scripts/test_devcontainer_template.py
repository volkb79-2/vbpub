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
HOME_SOURCE = "${localEnv:HOME}/mdt-cockpit-home/"
TMP_SOURCE = "${localEnv:HOME}/mdt-cockpit-tmp/"
DOC_ANCHOR = "docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug"


def _template_mounts() -> list[tuple[str, str, str]]:
    mounts = []
    for line in TEMPLATE_TEXT.splitlines():
        match = MOUNT_RE.match(line)
        if match:
            mounts.append((match["spec"], match["source"], match["target"]))
    return mounts


def _bootstrap_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sources: list[str]) -> Path:
    """Point the bootstrap at a throwaway host $HOME with the given mount sources."""
    host_home = tmp_path / "host"
    host_home.mkdir(exist_ok=True)
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: sources)
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: True)
    return host_home


def test_template_mounts_the_complete_home_from_one_host_source() -> None:
    mounts = _template_mounts()
    home_mounts = [(source, target) for _, source, target in mounts if target == "/home/vscode"]
    assert home_mounts == [(HOME_SOURCE, "/home/vscode")]
    assert any(
        source == "${localEnv:HOME}/.ssh/" and target == "/run/mdt/host-ssh"
        for _, source, target in mounts
    )
    assert not any(
        target.startswith("/home/vscode/")
        for _, _, target in mounts
    )
    assert (TMP_SOURCE, "/tmp") in [(source, target) for _, source, target in mounts]


def test_template_names_every_home_source_mdt_cockpit_purpose_and_never_the_old_parent() -> None:
    home_sources = [
        source
        for _, source, _ in _template_mounts()
        if source.startswith("${localEnv:HOME}/") and source != "${localEnv:HOME}/.ssh/"
    ]
    assert home_sources == [HOME_SOURCE, TMP_SOURCE]
    assert all(
        source.removeprefix("${localEnv:HOME}/").startswith("mdt-cockpit-")
        for source in home_sources
    )
    # The old grouped parent survives in live code only inside the bootstrap's
    # one-line notice; the template itself never mounts or mentions it.
    assert "mdt--mounted-folders" not in TEMPLATE_TEXT
    assert "migrate_persistent_home" not in TEMPLATE_TEXT


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
    assert file_sources == []
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY" in TEMPLATE_TEXT
    assert "create-by-spelling" in TEMPLATE_TEXT
    assert "Docker's --mount" in TEMPLATE_TEXT
    assert "refuses a missing source" in TEMPLATE_TEXT
    assert "Docker creates them as root" not in TEMPLATE_TEXT


def test_template_keeps_codex_database_persistent_and_container_running() -> None:
    assert '"CODEX_SQLITE_HOME": "/home/vscode/.codex/sqlite-shared"' in TEMPLATE_TEXT
    assert '"shutdownAction": "none"' in TEMPLATE_TEXT


def test_template_exposes_the_pwmcp_endpoint_but_never_a_second_path_definition() -> None:
    document = bootstrap._parse_jsonc_object(TEMPLATE_TEXT)
    assert document["remoteEnv"] == {"PWMCP_MCP_URL": "http://pwmcp:8931/mcp"}
    # D5/D6: PATH and the npm prefix are defined once, by the image (Dockerfile
    # ENV). A second spelling in the template could only drift from it.
    for section in ("containerEnv", "remoteEnv"):
        assert "PATH" not in document[section], section
        assert "NPM_CONFIG_PREFIX" not in document[section], section
    assert "/home/vscode/.local" not in json.dumps(document["containerEnv"])


def test_image_defines_the_command_path_once_and_the_login_hook_only_restores_it() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    profile = (ROOT / "customization" / "profile.sh").read_text(encoding="utf-8")
    prefix = "/opt/mdt/bin:/opt/mdt/venv/bin:/opt/mdt/npm/bin:/srv/pwmcp-instance/bin"
    assert f'ENV MDT_PATH_PREFIX="{prefix}"' in dockerfile
    assert 'ENV MDT_PATH_SUFFIX="/home/vscode/.local/bin"' in dockerfile
    assert 'ENV PATH="${MDT_PATH_PREFIX}:${PATH}:${MDT_PATH_SUFFIX}"' in dockerfile
    assert "MDT_IMAGE_PATH" not in dockerfile, "an env snapshot does not exist in a cleared-environment login shell"
    assert dockerfile.count("ENV PATH=") == 1
    assert 'ENV VIRTUAL_ENV=' not in dockerfile
    assert "dstdns-venv.sh" not in dockerfile
    # profile.sh is sourced by interactive shells too, where rewriting PATH would
    # undo a user's activated venv: it must never touch PATH itself.
    assert not re.search(r"^\s*(export\s+)?PATH=", profile, re.MULTILINE)
    assert "printf '%s\\n' \"${PATH}\" > \"${share_dir}/path\"" in dockerfile
    assert 'PATH="$(cat /usr/local/share/mdt-cockpit/path)"' in dockerfile
    assert "path is missing; PATH left as /etc/profile set it" in dockerfile
    assert "/opt/mdt/venv/bin:/opt/mdt/npm/bin" not in profile


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

    assert derived == [
        host_home / ".ssh",
        host_home / "mdt-cockpit-home",
        host_home / "mdt-cockpit-tmp",
    ]


def test_bootstrap_creates_a_private_home_and_a_sticky_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_home = _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE, TMP_SOURCE])

    assert bootstrap.main() == 0

    assert (host_home / "mdt-cockpit-home").stat().st_mode & 0o7777 == 0o700
    assert (host_home / "mdt-cockpit-tmp").stat().st_mode & 0o7777 == 0o1777


def test_bootstrap_notes_old_split_state_but_never_fails_or_migrates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE])
    old_codex = host_home / "mdt--mounted-folders" / ".codex"
    old_codex.mkdir(parents=True)
    (old_codex / "auth.json").write_text("secret", encoding="utf-8")

    assert bootstrap.main() == 0

    captured = capsys.readouterr()
    notes = [line for line in captured.err.splitlines() if "NOTE" in line]
    assert len(notes) == 1
    assert DOC_ANCHOR in notes[0]
    assert "mdt-cockpit-home" in notes[0]
    # Nothing was read into, copied to, or removed from either place.
    assert list((host_home / "mdt-cockpit-home").iterdir()) == []
    assert (old_codex / "auth.json").read_text(encoding="utf-8") == "secret"


@pytest.mark.parametrize(
    "make_content",
    [
        lambda parent: (parent / ".reasonix.toml").touch(),
        lambda parent: (parent / ".local").mkdir() or (parent / ".local" / "bin").symlink_to("/nowhere"),
    ],
    ids=["zero-byte-file", "dangling-symlink"],
)
def test_bootstrap_counts_any_file_or_symlink_as_old_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], make_content
) -> None:
    host_home = _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE])
    parent = host_home / "mdt--mounted-folders"
    parent.mkdir()
    make_content(parent)

    assert bootstrap.main() == 0

    assert "NOTE" in capsys.readouterr().err


def test_bootstrap_stays_silent_when_old_state_is_only_empty_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE])
    parent = host_home / "mdt--mounted-folders"
    (parent / ".claude").mkdir(parents=True)
    (parent / ".codex").mkdir()

    assert bootstrap.main() == 0

    assert "NOTE" not in capsys.readouterr().err


def test_bootstrap_stays_silent_once_the_new_home_has_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE])
    (host_home / "mdt--mounted-folders").mkdir()
    (host_home / "mdt--mounted-folders" / "leftover").write_text("old", encoding="utf-8")
    (host_home / "mdt-cockpit-home").mkdir()
    (host_home / "mdt-cockpit-home" / ".bash_history").write_text("ls\n", encoding="utf-8")

    assert bootstrap.main() == 0

    assert "NOTE" not in capsys.readouterr().err


def test_bootstrap_without_old_state_prints_no_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _bootstrap_home(monkeypatch, tmp_path, [HOME_SOURCE])

    assert bootstrap.main() == 0

    assert "NOTE" not in capsys.readouterr().err


def test_bootstrap_accepts_existing_extensionless_and_arbitrary_file_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = host_home / "mdt-cockpit-npmrc"
    source.parent.mkdir(parents=True)
    source.write_text("registry=https://registry.example/\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "HOME", host_home)

    assert bootstrap.ensure(source) is True
    assert "(file)" in capsys.readouterr().out
    assert source.is_file()


def test_bootstrap_defaults_missing_unmarked_custom_source_to_empty_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = _bootstrap_home(
        monkeypatch, tmp_path, ["${localEnv:HOME}/mdt-cockpit-custom-state"]
    )

    assert bootstrap.main() == 0
    created = host_home / "mdt-cockpit-custom-state"
    assert created.is_file()
    assert created.read_bytes() == b""
    assert created.stat().st_mode & 0o777 == 0o600
    assert "empty file" in capsys.readouterr().out


def test_bootstrap_reads_strict_missing_source_policy_from_host_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = _bootstrap_home(
        monkeypatch, tmp_path, ["${localEnv:HOME}/mdt-cockpit-strict-state"]
    )
    config_path = tmp_path / "host-setup.env"
    config_path.write_text(
        'DEVCONTAINER_MISSING_BIND_SOURCE_POLICY="fail"\n', encoding="utf-8"
    )
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", config_path)

    assert bootstrap.main() == 1
    assert not (host_home / "mdt-cockpit-strict-state").exists()
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
    base = "${localEnv:HOME}/mdt-cockpit-conflicted"
    host_home = _bootstrap_home(monkeypatch, tmp_path, [base, base + "/"])

    assert bootstrap.main() == 1
    assert not (host_home / "mdt-cockpit-conflicted").exists()
    assert not (host_home / "mdt-cockpit-home").exists()
    assert "conflicting file/directory source spellings" in capsys.readouterr().err


def test_bootstrap_creates_missing_custom_directory_with_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_home = _bootstrap_home(
        monkeypatch, tmp_path, ["${localEnv:HOME}/mdt-cockpit-cache/"]
    )

    assert bootstrap.main() == 0
    assert (host_home / "mdt-cockpit-cache").is_dir()


def test_bootstrap_treats_the_gitconfig_source_as_a_file_and_rejects_a_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_home = tmp_path / "host"
    source = host_home / "mdt-cockpit-gitconfig"
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
    host_home = _bootstrap_home(monkeypatch, tmp_path, [])
    ensured: list[tuple[Path, str, str]] = []

    def record(path: Path, *, expected_type: str, missing_policy: str) -> None:
        ensured.append((path, expected_type, missing_policy))

    monkeypatch.setattr(bootstrap, "ensure", record)

    assert bootstrap.main() == 0
    assert ensured == [
        (path, expected_type, "create-by-spelling")
        for path, expected_type in bootstrap.fallback_bind_paths()
    ]
    assert (host_home / "mdt-cockpit-home", "directory", "create-by-spelling") in ensured
    assert (host_home / "mdt-cockpit-tmp", "directory", "create-by-spelling") in ensured
    assert (host_home / ".ssh", "directory", "create-by-spelling") in ensured


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
    source = "${localEnv:HOME}/mdt-cockpit-must-not-create/"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "HOST_CONFIG_PATH", tmp_path / "no-mdt-config")
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [source])
    monkeypatch.setattr(bootstrap, "verify_host_cgroup_slices", lambda *_: False)

    assert bootstrap.main() == 1
    assert not (host_home / "mdt-cockpit-must-not-create").exists()


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
    assert "mdt-cockpit-home" in text, path
    assert "complete" in text.lower(), path
    assert "home-vscode" not in text, path
    # The old parent may be NAMED (to explain what to copy from), but no doc may
    # still show it as a mount source.
    assert "localEnv:HOME}/mdt--mounted-folders" not in text, path


def test_manual_copy_replaces_the_retired_migration_in_every_user_doc() -> None:
    template_readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    persistence = (ROOT / "docs" / "HOME-PERSISTENCE.md").read_text(encoding="utf-8")
    usage = (ROOT / "USAGE.md").read_text(encoding="utf-8")
    top_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for text in (template_readme, lifecycle, consumers, usage, top_readme, persistence):
        assert "migrate_persistent_home" not in text
        assert ".mdt-home-migration" not in text
    assert "## Moving from modern-debian-tools-python-debug" in persistence
    for name in (".claude/", ".claude.json", ".codex*/", ".ssh/", ".gnupg/", ".minisign/",
                 ".claudelink/", ".pi/", ".reasonix/", ".openclaw/", ".local/share/opencode/"):
        assert name in persistence, name
    assert "cp -a" in persistence
    for never_copy in ("~/.venv*", "~/.oh-my-zsh", "~/.local/bin", "~/.local/lib/node_modules",
                       "~/.codex/packages/standalone"):
        assert never_copy in persistence, never_copy
    for text in (template_readme, lifecycle, consumers, usage, top_readme):
        assert "HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug" in text
    assert "docs/DESIGN-GUIDE.md#the-home-is-user-state-image-assets-live-outside-it" in top_readme


def test_vm_acceptance_runner_copies_the_mdt_cockpit_fork() -> None:
    runner = (ROOT / "host-setup" / "tests" / "run-vm-acceptance.sh").read_text(
        encoding="utf-8"
    )
    assert 'GUEST_ROOT="/home/tester/vbpub/mdt-cockpit"' in runner
    assert 'copy "$RUN" /source/mdt-cockpit/ "$GUEST_ROOT/"' in runner


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
    assert "source=${localEnv:HOME}/mdt-cockpit-cache/" in consumers
    assert "append `/`" in consumers
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=create-by-spelling" in consumers
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail" in consumers
    assert "create-by-spelling" in design and "no trailing `/`" in design
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY" in template_readme
    assert "empty regular file (`0600`)" in lifecycle
    assert "mdt-cockpit-gitconfig" in consumers
    for text in (top_readme, design, consumers):
        assert "CODEX_SQLITE_HOME" in text
        assert 'shutdownAction: "none"' in text or 'shutdownAction` to `none' in text
    assert "docs/CONSUMERS.md#codex-profile-state-and-container-lifetime" in top_readme
    assert "CONSUMERS.md#codex-profile-state-and-container-lifetime" in design
