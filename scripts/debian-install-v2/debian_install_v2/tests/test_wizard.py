from __future__ import annotations

import json
import shlex
import sys

import pytest
from cli_extended import CliFailure

from debian_install_v2.config import Config, ConfigError, load_config, save_config
from debian_install_v2.wizard import (
    _load_questionary,
    run_configuration_wizard,
    uncovered_config_fields,
)


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self, **kwargs):
        return self.value


class _TTY:
    def isatty(self):
        return True


class _Output:
    stdin = _TTY()
    stdout = _TTY()

    def __init__(self):
        self.messages = []

    @staticmethod
    def _is_tty(stream):
        try:
            return stream.isatty()
        except AttributeError:
            return False

    @property
    def is_interactive(self):
        return self._is_tty(self.stdin) and self._is_tty(self.stdout)

    def emit(self, level, message, *, force=False):
        self.messages.append(str(message))

    def info(self, message):
        self.messages.append(str(message))

    def warn(self, message):
        self.messages.append(str(message))


class _Runtime:
    yes = False

    def __init__(self, accept=True):
        self.output = _Output()
        self.accept = accept
        self.prompts = []

    def confirm(self, prompt):
        self.prompts.append(prompt)
        return self.accept


class _Questionary:
    def __init__(self, *, selected=(), select=(), text=()):
        self.selected = None if selected is None else list(selected)
        self.select_values = list(select)
        self.text_values = list(text)

    def checkbox(self, *args, **kwargs):
        return _Answer(self.selected)

    def select(self, *args, **kwargs):
        return _Answer(self.select_values.pop(0))

    def text(self, *args, **kwargs):
        return _Answer(self.text_values.pop(0))

    def password(self, *args, **kwargs):
        return _Answer("")

    def confirm(self, *args, **kwargs):
        return _Answer(False)


class _CrossSectionQuestionary:
    """Select a path section, then add credentials after cross-field validation."""

    def __init__(self):
        self.sections = iter((["install"], ["notifications"]))

    def checkbox(self, *args, **kwargs):
        return _Answer(next(self.sections))

    def text(self, prompt, *, default=""):
        if prompt == "Persistent installer state directory":
            return _Answer("/srv/vbpub/state")
        return _Answer(default)

    @staticmethod
    def confirm(_prompt, *, default=False):
        return _Answer(default)

    @staticmethod
    def select(prompt, **kwargs):
        if prompt == "Credential delivery to stage two":
            return _Answer("systemd")
        return _Answer(kwargs.get("default"))

    @staticmethod
    def password(_prompt, *, default=""):
        return _Answer(default)


def test_wizard_metadata_covers_every_config_field_except_fixed_contract_fields():
    assert uncovered_config_fields() == frozenset()


def test_save_config_writes_valid_mode_0600_json_and_refuses_clobber(tmp_path):
    path = tmp_path / "install.json"
    config = Config(telegram_bot_token="123:token", telegram_chat_id="chat")

    save_config(str(path), config)

    assert path.stat().st_mode & 0o777 == 0o600
    assert load_config(path=str(path)) == config
    with pytest.raises(ConfigError, match="already exists"):
        save_config(str(path), Config())
    save_config(str(path), Config(), overwrite=True)
    assert load_config(path=str(path)) == Config()


def test_wizard_default_flow_summarizes_without_printing_secrets(tmp_path):
    starting = tmp_path / "starting.json"
    starting.write_text(json.dumps({
        "schema_version": 1,
        "fresh_install": True,
        "telegram_bot_token": "123:private-token",
        "telegram_chat_id": "chat-42",
    }))
    output = tmp_path / "generated.json"
    runtime = _Runtime()

    assert run_configuration_wizard(
        output_path=str(output),
        from_config=str(starting),
        runtime=runtime,
        questionary_module=_Questionary(),
        save=save_config,
    ) == 0

    saved = load_config(path=str(output))
    assert saved.telegram_bot_token == "123:private-token"
    assert output.stat().st_mode & 0o777 == 0o600
    summary = "\n".join(runtime.output.messages)
    assert "<configured>" in summary
    assert "123:private-token" not in summary


def test_wizard_updates_selected_section_and_validates_before_save(tmp_path):
    output = tmp_path / "generated.json"
    runtime = _Runtime()
    prompts = _Questionary(
        selected=("updates",),
        select=("security-only",),
        text=("02:30",),
    )

    assert run_configuration_wizard(
        output_path=str(output),
        from_config=None,
        runtime=runtime,
        questionary_module=prompts,
        save=save_config,
    ) == 0

    result = load_config(path=str(output))
    assert result.apt_auto_upgrade_mode == "security-only"
    assert result.reboot_window_time == "02:30"


def test_wizard_can_recover_from_a_cross_section_validation_error(tmp_path):
    output = tmp_path / "generated.json"
    runtime = _Runtime()

    assert run_configuration_wizard(
        output_path=str(output),
        from_config=None,
        runtime=runtime,
        questionary_module=_CrossSectionQuestionary(),
        save=save_config,
    ) == 0

    result = load_config(path=str(output))
    assert result.state_dir == "/srv/vbpub/state"
    assert result.credential_mode == "systemd"
    assert any("choose the section(s) to review" in item for item in runtime.output.messages)


def test_wizard_declines_to_write_without_creating_output(tmp_path):
    output = tmp_path / "not-written.json"
    runtime = _Runtime(accept=False)

    assert run_configuration_wizard(
        output_path=str(output),
        from_config=None,
        runtime=runtime,
        questionary_module=_Questionary(),
        save=save_config,
    ) == 0
    assert not output.exists()


def test_wizard_refuses_noninteractive_execution_before_prompting(tmp_path):
    runtime = _Runtime()
    runtime.output.stdin = object()
    with pytest.raises(CliFailure, match="interactive terminal"):
        run_configuration_wizard(
            output_path=str(tmp_path / "install.json"),
            from_config=None,
            runtime=runtime,
            questionary_module=_Questionary(),
            save=save_config,
        )


def test_questionary_cancel_uses_the_shared_ctrl_c_boundary(tmp_path):
    runtime = _Runtime()
    with pytest.raises(KeyboardInterrupt):
        run_configuration_wizard(
            output_path=str(tmp_path / "install.json"),
            from_config=None,
            runtime=runtime,
            questionary_module=_Questionary(selected=None),
            save=save_config,
        )
    assert not (tmp_path / "install.json").exists()


def test_missing_optional_prompt_package_has_a_project_specific_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "questionary", None)
    with pytest.raises(CliFailure, match="optional Questionary") as exc:
        _load_questionary()
    assert "scripts/debian-install-v2/wizard-requirements.txt" in exc.value.hint
    assert shlex.quote(sys.executable) in exc.value.hint
