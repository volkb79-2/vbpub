"""Behavior tests linked to the reviewed CLI cases in docs/cli-review.toml.

Every `cli_case` marker names a catalog case; the catalog lists the node id of
each test that proves it. The handlers run for real through `main()`; only the
host-touching `Installer` is replaced by a recorder, so no test can partition a
disk, write swap, or touch systemd. Every file lives under tmp_path.
"""

from __future__ import annotations

import json
import shlex
import stat
import sys
from types import ModuleType, SimpleNamespace

import pytest
from cli_extended import CliOutput

import debian_install_v2.bootstrap as product
import debian_install_v2.wizard as wizard_module
from debian_install_v2.actions import PlannedAction
from debian_install_v2.bootstrap import main
from debian_install_v2.config import load_config

ROUTE = "route:entrypoint:debian-install-v2"
TOKEN = "123456:SECRET-TOKEN-VALUE"
PLAN = {
    "release": "trixie",
    "root_device": "/dev/vda1",
    "new_root_size_sectors": 1000,
    "swap_partitions": [{"device": "/dev/vda2", "start": 2048, "sectors": 4096}],
    "sfdisk_plan": "label: dos\n",
}
CONFIG_VERBS = {
    "install": ["--dry-run"],
    "status": [],
    "verify": [],
    "plan": [],
    "disable-stage2": ["--dry-run"],
    "build-customscript": [],
}


def case_min(verb):
    return f"case:{ROUTE}/{verb}/minimum"


def case_spelling(verb, flag):
    return f"case:{ROUTE}/{verb}/option-spelling/option:{ROUTE}/{verb}/{flag}/{flag}"


def case_member(verb, flag):
    return (
        f"case:{ROUTE}/{verb}/exclusive-member/configuration-source/"
        f"option:{ROUTE}/{verb}/{flag}"
    )


def case_conflict(verb):
    return (
        f"case:{ROUTE}/{verb}/exclusive-conflict/configuration-source/"
        f"option:{ROUTE}/{verb}/--config/option:{ROUTE}/{verb}/--config-json"
    )


def cases(*case_ids):
    return [pytest.mark.cli_case(case_id) for case_id in case_ids]


class FakeInstaller:
    """Records what the handlers ask for; performs nothing on the host."""

    instances: list[FakeInstaller] = []

    def __init__(self, config, actions, *, inspect_host=True):
        self.config = config
        self.actions = actions
        self.inspect_host = inspect_host
        self.calls: list[str] = []
        FakeInstaller.instances.append(self)

    def _record(self, name):
        self.calls.append(name)
        if self.actions.dry_run:
            self.actions.planned.append(PlannedAction(("/usr/bin/true", name), f"fake {name}"))

    def show_plan(self):
        self.calls.append("show_plan")
        return PLAN

    def install(self):
        self._record("install")

    def resume(self):
        self._record("resume")

    def disable_stage2(self):
        self._record("disable_stage2")

    def verify(self):
        self.calls.append("verify")

    def status(self):
        self.calls.append("status")
        return {
            "status": "running",
            "phase": "stage1",
            "run_id": "run-1",
            "started_at": "2026-10-05T00:00:00Z",
            "dry_run": False,
            "planned_action_count": 0,
            "last_error": f"notification failed for {self.config.telegram_bot_token}",
        }


@pytest.fixture(autouse=True)
def host_free(monkeypatch, tmp_path):
    FakeInstaller.instances = []
    monkeypatch.setattr(product, "Installer", FakeInstaller)
    monkeypatch.setattr(product, "_stage2_config", lambda state_dir: product.Config())
    monkeypatch.setenv("VBPUB_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def write_config(tmp_path, **overrides):
    data = {
        "schema_version": 1,
        "fresh_install": True,
        "swap_disk_total_gb": 32,
        "swap_file_count": 3,
        "state_dir": str(tmp_path / "state"),
        "log_dir": str(tmp_path / "logs"),
        "never_reboot": True,
        "auto_reboot_after_stage1": False,
        "credential_mode": "systemd",
        **overrides,
    }
    path = tmp_path / "install.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path), json.dumps(data)


def run(capsys, argv):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_loaded_swap_file_count(verb, out):
    """The configuration source reached the handler: swap_file_count 3 is ours."""
    if verb == "build-customscript":
        assert json.loads(out)["config"]["swap_file_count"] == 3
    else:
        assert [item.config.swap_file_count for item in FakeInstaller.instances] == [3]


# --- configuration source: --config / --config-json (exactly one) ----------


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param(
            verb,
            marks=cases(case_min(verb), case_member(verb, "--config"), case_spelling(verb, "--config")),
        )
        for verb in CONFIG_VERBS
    ],
)
def test_config_file_source_is_read(verb, tmp_path, capsys):
    path, _ = write_config(tmp_path)
    code, out, err = run(capsys, [verb, "--config", path, *CONFIG_VERBS[verb]])
    assert code == 0, err
    assert_loaded_swap_file_count(verb, out)


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param(
            verb,
            marks=cases(
                case_member(verb, "--config-json"), case_spelling(verb, "--config-json")
            ),
        )
        for verb in CONFIG_VERBS
    ],
)
def test_inline_json_source_is_read(verb, tmp_path, capsys):
    _, inline = write_config(tmp_path)
    code, out, err = run(capsys, [verb, "--config-json", inline, *CONFIG_VERBS[verb]])
    assert code == 0, err
    assert_loaded_swap_file_count(verb, out)


@pytest.mark.parametrize(
    "verb",
    [pytest.param(verb, marks=cases(case_conflict(verb))) for verb in CONFIG_VERBS],
)
def test_both_configuration_sources_are_refused(verb, tmp_path, capsys):
    path, inline = write_config(tmp_path)
    code, out, err = run(capsys, [verb, "--config", path, "--config-json", inline])
    assert code == 2
    assert out == ""
    assert "argument --config-json: not allowed with argument --config" in err
    assert FakeInstaller.instances == []


# --- --dry-run and --yes on the mutating verbs -----------------------------


def dry_run_argv(verb, tmp_path):
    return ["resume"] if verb == "resume" else [verb, "--config", write_config(tmp_path)[0]]


@pytest.mark.parametrize(
    ("verb", "step"),
    [
        pytest.param(
            "install", "install", id="install", marks=cases(case_spelling("install", "--dry-run"))
        ),
        pytest.param(
            "resume", "resume", id="resume", marks=cases(case_spelling("resume", "--dry-run"))
        ),
        pytest.param(
            "disable-stage2",
            "disable_stage2",
            id="disable-stage2",
            marks=cases(case_spelling("disable-stage2", "--dry-run")),
        ),
    ],
)
def test_dry_run_prints_the_planned_actions_and_asks_nothing(verb, step, tmp_path, capsys):
    code, out, err = run(capsys, [*dry_run_argv(verb, tmp_path), "--dry-run"])
    assert code == 0, err
    assert json.loads(out) == {
        "result": "planned",
        "actions": [
            {"argv": ["/usr/bin/true", step], "description": f"fake {step}", "dangerous": False}
        ],
    }
    assert "[y/N]" not in err
    assert "Confirmation accepted" not in err
    (installer,) = FakeInstaller.instances
    assert installer.actions.dry_run is True
    assert step in installer.calls


@pytest.mark.parametrize(
    ("verb", "step", "done"),
    [
        pytest.param(
            "install",
            "install",
            "Stage-one installation completed",
            id="install",
            marks=cases(case_spelling("install", "--yes")),
        ),
        pytest.param(
            "resume",
            "resume",
            "Stage-two installation completed.",
            id="resume",
            marks=cases(case_min("resume"), case_spelling("resume", "--yes")),
        ),
        pytest.param(
            "disable-stage2",
            "disable_stage2",
            "Stage-two service disabled and completion marker recorded.",
            id="disable-stage2",
            marks=cases(case_spelling("disable-stage2", "--yes")),
        ),
    ],
)
def test_yes_skips_only_the_confirmation_and_runs_the_step(verb, step, done, tmp_path, capsys):
    code, out, err = run(capsys, [*dry_run_argv(verb, tmp_path), "--yes"])
    assert code == 0, err
    assert "Confirmation accepted via --yes." in err
    assert done in err
    (installer,) = FakeInstaller.instances
    assert installer.actions.dry_run is False
    assert step in installer.calls


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param(verb, marks=cases(case_spelling(verb, "--yes")))
        for verb in ("install", "resume", "disable-stage2")
    ],
)
def test_without_yes_a_non_interactive_run_refuses_before_acting(verb, tmp_path, capsys):
    code, out, err = run(capsys, dry_run_argv(verb, tmp_path))
    assert code == 2
    assert "confirmation is required, but stdin is not interactive" in err
    assert all(
        step not in item.calls
        for item in FakeInstaller.instances
        for step in ("install", "resume", "disable_stage2")
    )


def test_resume_needs_an_absolute_state_directory(monkeypatch, capsys):
    monkeypatch.setenv("VBPUB_STATE_DIR", "relative/state")
    code, out, err = run(capsys, ["resume", "--yes"])
    assert code == 2
    assert "VBPUB_STATE_DIR must be set to an absolute path for resume" in err
    assert FakeInstaller.instances == []


# --- --json on the exploration verbs ---------------------------------------


@pytest.mark.parametrize(
    ("verb", "expected_json", "text_prefix"),
    [
        pytest.param(
            "status",
            {
                "status": "running",
                "phase": "stage1",
                "run_id": "run-1",
                "started_at": "2026-10-05T00:00:00Z",
                "dry_run": False,
                "planned_action_count": 0,
                "last_error": "notification failed for ",
            },
            "status: running\nphase: stage1\n",
            id="status",
            marks=cases(case_spelling("status", "--json")),
        ),
        pytest.param(
            "plan",
            PLAN,
            "Release: trixie\n",
            id="plan",
            marks=cases(case_spelling("plan", "--json")),
        ),
    ],
)
def test_json_replaces_the_text_rendering(verb, expected_json, text_prefix, tmp_path, capsys):
    path, _ = write_config(tmp_path)
    code, out, err = run(capsys, [verb, "--config", path, "--json"])
    assert code == 0, err
    assert json.loads(out) == expected_json
    code, out, err = run(capsys, [verb, "--config", path])
    assert code == 0, err
    assert out.startswith(text_prefix)


# --- --debug-raw: redaction is on unless asked otherwise --------------------


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param(verb, marks=cases(case_spelling(verb, "--debug-raw")))
        for verb in ("install", "verify", "plan", "disable-stage2", "resume", "wizard")
    ],
)
def test_debug_raw_only_adds_the_warning_and_debug_lines_without_a_secret(verb, tmp_path, capsys):
    if verb in ("resume", "wizard"):
        base = ["resume", "--dry-run"] if verb == "resume" else ["wizard", "--output", str(tmp_path / "w.json")]
    else:
        base = [verb, "--config", write_config(tmp_path)[0], *CONFIG_VERBS[verb]]
    plain_code, plain_out, plain_err = run(capsys, base)
    raw_code, raw_out, raw_err = run(capsys, [*base, "--debug-raw"])
    assert plain_code == (2 if verb == "wizard" else 0)
    assert (raw_code, raw_out) == (plain_code, plain_out)
    assert raw_err.startswith("[WARN] --debug-raw is active: credentials, tokens, passwords")
    kept = "".join(
        line
        for line in raw_err.splitlines(keepends=True)
        if not line.startswith(("[WARN] --debug-raw", "[DEBUG]"))
    )
    assert kept == plain_err


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param("status", marks=cases(case_spelling("status", "--debug-raw"))),
        pytest.param(
            "build-customscript",
            marks=cases(case_spelling("build-customscript", "--debug-raw")),
        ),
    ],
)
def test_debug_raw_reveals_the_token_only_when_asked(verb, tmp_path, capsys):
    path, _ = write_config(
        tmp_path, telegram_bot_token=TOKEN, telegram_chat_id="42", credential_mode="systemd"
    )
    base = [verb, "--config", path]
    code, out, err = run(capsys, base)
    assert TOKEN not in out + err
    if verb == "build-customscript":
        assert code == 2
        assert "must not be redacted" in err
    else:
        assert code == 0
        assert "notification failed for " in out
    code, out, err = run(capsys, [*base, "--debug-raw"])
    assert code == 0, err
    assert TOKEN in out


# --- build-customscript options --------------------------------------------

CUSTOM_REPO = "https://git.example.test/ops/vbpub"
CUSTOM_BOOTSTRAP = "https://git.example.test/ops/vbpub/raw/main/bootstrap-remote.py"


def script_parts(result):
    """Split the cloud-init command into its environment and Python launcher."""
    tokens = shlex.split(result["customScript"])
    assert tokens[-3:-1] == ["python3", "-c"]
    environment = dict(token.split("=", 1) for token in tokens[:-3])
    return environment, tokens[-1]


def bundle(capsys, tmp_path, *extra):
    path, _ = write_config(tmp_path)
    code, out, err = run(capsys, ["build-customscript", "--config", path, *extra])
    assert code == 0, err
    return json.loads(out)


@pytest.mark.cli_case(case_spelling("build-customscript", "--repo-url"))
@pytest.mark.cli_case(case_spelling("build-customscript", "--bootstrap-url"))
def test_custom_repository_with_its_bootstrap_url_is_rendered(tmp_path, capsys):
    result = bundle(
        capsys, tmp_path, "--repo-url", CUSTOM_REPO, "--bootstrap-url", CUSTOM_BOOTSTRAP
    )
    environment, launcher = script_parts(result)
    assert environment["REPO_URL"] == CUSTOM_REPO
    assert f"url = {CUSTOM_BOOTSTRAP!r}\n" in launcher


@pytest.mark.cli_case(case_spelling("build-customscript", "--bootstrap-url"))
def test_bootstrap_url_alone_overrides_only_the_fetched_script(tmp_path, capsys):
    result = bundle(capsys, tmp_path, "--bootstrap-url", CUSTOM_BOOTSTRAP)
    environment, launcher = script_parts(result)
    assert environment["REPO_URL"] == "https://github.com/volkb79-2/vbpub"
    assert f"url = {CUSTOM_BOOTSTRAP!r}\n" in launcher


def test_custom_repository_without_a_bootstrap_url_is_refused_with_help(tmp_path, capsys):
    path, _ = write_config(tmp_path)
    code, out, err = run(
        capsys, ["build-customscript", "--config", path, "--repo-url", CUSTOM_REPO]
    )
    assert code == 2
    assert out == ""
    assert err.startswith(
        "[ERROR] bootstrap_url is required when repo_url is not the canonical vbpub repository\n"
    )
    assert "usage:" in err


def test_explicit_canonical_repo_url_needs_no_bootstrap_url(tmp_path, capsys):
    result = bundle(capsys, tmp_path, "--repo-url", "https://github.com/volkb79-2/vbpub")
    environment, launcher = script_parts(result)
    assert environment["REPO_URL"] == "https://github.com/volkb79-2/vbpub"
    assert (
        "url = 'https://raw.githubusercontent.com/volkb79-2/vbpub/main/"
        "scripts/debian-install-v2/bootstrap-remote.py'\n"
    ) in launcher


@pytest.mark.cli_case(case_spelling("build-customscript", "--repo-branch"))
def test_repo_branch_selects_the_archive_and_the_default_bootstrap_url(tmp_path, capsys):
    result = bundle(capsys, tmp_path, "--repo-branch", "feature/install-v2")
    environment, launcher = script_parts(result)
    assert environment["REPO_BRANCH"] == "feature/install-v2"
    assert (
        "url = 'https://raw.githubusercontent.com/volkb79-2/vbpub/feature/install-v2/"
        "scripts/debian-install-v2/bootstrap-remote.py'\n"
    ) in launcher


@pytest.mark.cli_case(case_spelling("build-customscript", "--controller-ssh-placeholder"))
def test_controller_ssh_placeholder_emits_the_provider_marker(tmp_path, capsys):
    result = bundle(capsys, tmp_path, "--controller-ssh-placeholder")
    assert result["config"]["controller_ssh_pubkey"] == "{{CONTROLLER_SSH_PUBKEY}}"
    plain = bundle(capsys, tmp_path)
    assert plain["config"]["controller_ssh_pubkey"] == ""


# --- wizard ---------------------------------------------------------------


@pytest.mark.cli_case(case_min("wizard"))
def test_wizard_without_a_terminal_refuses_before_prompting(tmp_path, capsys):
    target = tmp_path / "install.json"
    code, out, err = run(capsys, ["wizard", "--output", str(target)])
    assert code == 2
    assert out == ""
    assert "wizard requires an interactive terminal" in err
    assert not target.exists()


@pytest.mark.cli_case(case_spelling("wizard", "--output"))
@pytest.mark.cli_case(case_spelling("wizard", "--from-config"))
def test_wizard_passes_output_and_starting_configuration_through(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        wizard_module, "run_configuration_wizard", lambda **kwargs: seen.append(kwargs) or 0
    )
    start, _ = write_config(tmp_path)
    target = str(tmp_path / "out.json")
    assert main(["wizard", "--output", target, "--from-config", start]) == 0
    assert main(["wizard", "--output", target]) == 0
    assert [(item["output_path"], item["from_config"]) for item in seen] == [
        (target, start),
        (target, None),
    ]
    assert capsys.readouterr().err == ""


@pytest.mark.cli_case(case_spelling("wizard", "--yes"))
def test_wizard_yes_writes_the_validated_file_and_without_it_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(CliOutput, "is_interactive", property(lambda self: True))
    questionary = ModuleType("questionary")
    questionary.checkbox = lambda *args, **kwargs: SimpleNamespace(ask=lambda **kw: [])
    monkeypatch.setitem(sys.modules, "questionary", questionary)
    target = tmp_path / "install.json"

    code, out, err = run(capsys, ["wizard", "--output", str(target)])
    assert code == 2
    assert "confirmation is required, but stdin is not interactive" in err
    assert not target.exists()

    code, out, err = run(capsys, ["wizard", "--output", str(target), "--yes"])
    assert code == 0, err
    assert "Confirmation accepted via --yes." in err
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert load_config(path=str(target)).schema_version == 1
