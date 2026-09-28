from __future__ import annotations

import builtins
import io
from types import SimpleNamespace

import pytest

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    PromptCancelled,
    PromptDriver,
    VerbSpec,
)
from cli_extended import prompts as prompts_module


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Driver:
    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def _answer(self, kind, *args, **kwargs):
        self.calls.append((kind, args, kwargs))
        return self.answers.get(kind)

    def text(self, message, *, default=None, required=True):
        return self._answer("text", message, default=default, required=required)

    def password(self, message, *, required=True):
        return self._answer("password", message, required=required)

    def confirm(self, message, *, default=False):
        return self._answer("confirm", message, default=default)

    def select(self, message, choices, *, default=None):
        return self._answer("select", message, tuple(choices), default=default)

    def checkbox(self, message, choices, *, default=()):
        return self._answer("checkbox", message, tuple(choices), default=tuple(default))


def _api(driver=None, *, stdin=None, stdout=None, register_secret=None):
    return prompts_module.PromptAPI(
        stdin=stdin if stdin is not None else _TTY(),
        stdout=stdout if stdout is not None else _TTY(),
        driver=driver,
        register_secret=register_secret,
    )


def test_injected_driver_collects_all_five_values_and_forwards_defaults():
    driver = _Driver(
        text="value", password="hidden", confirm=False, select="blue", checkbox=["blue"]
    )
    assert isinstance(driver, PromptDriver)
    api = _api(driver)
    assert api.text("text?", default="draft") == "value"
    assert api.password("password?") == "hidden"
    assert api.confirm("confirm?", default=True) is False
    assert api.select("color?", ["blue", "red"], default="red") == "blue"
    assert api.checkbox("colors?", ["blue", "red"], default=("red",)) == ["blue"]
    assert [call[0] for call in driver.calls] == [
        "text", "password", "confirm", "select", "checkbox"
    ]
    assert driver.calls[0][2] == {"default": "draft", "required": True}
    assert driver.calls[1][2] == {"required": True}
    assert driver.calls[2][2] == {"default": True}
    assert driver.calls[3][1][1] == ("blue", "red")
    assert driver.calls[3][2] == {"default": "red"}
    assert driver.calls[4][1][1] == ("blue", "red")
    assert driver.calls[4][2] == {"default": ("red",)}


@pytest.mark.parametrize("kind", ("text", "password", "confirm", "select", "checkbox"))
def test_none_from_each_prompt_is_cancellation(kind):
    api = _api(_Driver(**{kind: None}))
    actions = {
        "text": lambda: api.text("text"),
        "password": lambda: api.password("password"),
        "confirm": lambda: api.confirm("confirm"),
        "select": lambda: api.select("select", ["one"]),
        "checkbox": lambda: api.checkbox("checkbox", ["one"]),
    }
    with pytest.raises(PromptCancelled):
        actions[kind]()


@pytest.mark.parametrize("stream_name", ("stdin", "stdout"))
def test_non_tty_refuses_before_loading_or_calling_driver(monkeypatch, stream_name):
    calls = []
    driver = _Driver(text="unused")
    streams = {"stdin": _TTY(), "stdout": _TTY()}
    streams[stream_name] = io.StringIO()
    monkeypatch.setattr(
        prompts_module,
        "_load_questionary_driver",
        lambda *_args: calls.append("load") or driver,
    )
    api = _api(stdin=streams["stdin"], stdout=streams["stdout"])
    with pytest.raises(CliFailure, match="both stdin and stdout to be terminals"):
        api.text("prompt")
    assert calls == []
    assert driver.calls == []


def test_tty_probe_handles_missing_and_raising_isatty_methods():
    class NoIsatty:
        pass

    class BrokenTTY:
        def isatty(self):
            raise OSError("closed")

    assert not prompts_module._is_tty(NoIsatty())
    assert not prompts_module._is_tty(BrokenTTY())
    assert prompts_module._is_tty(_TTY())


def test_text_required_optional_and_type_behavior():
    assert _api(_Driver(text="")).text("optional", required=False) == ""
    assert _api(_Driver(text="  kept  ")).text("required") == "  kept  "
    with pytest.raises(CliFailure, match="cannot be blank"):
        _api(_Driver(text=" \t\n")).text("required")
    with pytest.raises(TypeError, match="default must be a string"):
        _api(_Driver(text="ok")).text("text", default=2)
    with pytest.raises(TypeError, match="required must be a bool"):
        _api(_Driver(text="ok")).text("text", required="yes")
    with pytest.raises(CliFailure, match="non-text"):
        _api(_Driver(text=3)).text("text")


def test_password_result_and_secret_registration():
    registered = []
    assert _api(_Driver(password=""), register_secret=registered.append).password(
        "password", required=False
    ) == ""
    assert registered == []
    assert _api(_Driver(password="new-secret"), register_secret=registered.append).password(
        "password"
    ) == "new-secret"
    assert registered == ["new-secret"]
    with pytest.raises(CliFailure, match="cannot be empty"):
        _api(_Driver(password="")).password("required")
    with pytest.raises(CliFailure, match="non-text"):
        _api(_Driver(password=3)).password("password")
    with pytest.raises(TypeError, match="required must be a bool"):
        _api(_Driver(password="ok")).password("password", required=1)


def test_confirm_result_and_type_behavior():
    assert _api(_Driver(confirm=True)).confirm("continue?") is True
    assert _api(_Driver(confirm=False)).confirm("continue?") is False
    with pytest.raises(CliFailure, match="non-boolean"):
        _api(_Driver(confirm=1)).confirm("continue?")
    with pytest.raises(TypeError, match="default must be a bool"):
        _api(_Driver(confirm=True)).confirm("continue?", default=1)


def test_select_validates_choices_defaults_and_answers():
    assert _api(_Driver(select="one")).select("select", ["one", "two"]) == "one"
    bad_calls = (
        ("select", [], None, "at least one"),
        ("select", ["same", "same"], None, "unique"),
        ("select", ["one", 2], None, "only strings"),
        ("select", "one", None, "not a string"),
        ("select", ["one"], "other", "one of the choices"),
    )
    for _name, choices, default, message in bad_calls:
        with pytest.raises((TypeError, ValueError), match=message):
            _api(_Driver(select="one")).select("select", choices, default=default)
    with pytest.raises(CliFailure, match="outside its choices"):
        _api(_Driver(select="outside")).select("select", ["one"])
    with pytest.raises(CliFailure, match="outside its choices"):
        _api(_Driver(select=1)).select("select", ["one"])


def test_checkbox_validates_defaults_and_answers():
    assert _api(_Driver(checkbox=[])).checkbox("many", ["one"]) == []
    assert _api(_Driver(checkbox=["one"])).checkbox("many", ["one"]) == ["one"]
    bad_defaults = (
        ("one", "sequence of choice strings"),
        (("one", "one"), "unique"),
        ((1,), "only choice strings"),
        (("outside",), "among the choices"),
    )
    for default, message in bad_defaults:
        with pytest.raises((TypeError, ValueError), match=message):
            _api(_Driver(checkbox=[])).checkbox("many", ["one"], default=default)
    with pytest.raises(CliFailure, match="outside its choices"):
        _api(_Driver(checkbox="one")).checkbox("many", ["one"])
    with pytest.raises(CliFailure, match="outside its choices"):
        _api(_Driver(checkbox=["outside"])).checkbox("many", ["one"])
    with pytest.raises(CliFailure, match="duplicate choices"):
        _api(_Driver(checkbox=["one", "one"])).checkbox("many", ["one"])


def test_choice_type_errors_include_nonsequence_inputs():
    with pytest.raises(TypeError, match="sequence of strings"):
        _api(_Driver(select="one")).select("select", None)
    with pytest.raises(TypeError, match="sequence of choice strings"):
        _api(_Driver(checkbox=[])).checkbox("many", ["one"], default=None)


def test_default_driver_is_loaded_only_after_tty_check_and_cached(monkeypatch):
    driver = _Driver(text="ready")
    loaded = []

    def load(stdin, stdout, extra):
        loaded.append((stdin, stdout, extra))
        return driver

    monkeypatch.setattr(prompts_module, "_load_questionary_driver", load)
    stdin, stdout = _TTY(), _TTY()
    api = _api(stdin=stdin, stdout=stdout)
    assert api.text("first") == "ready"
    assert api.text("second") == "ready"
    assert loaded == [(stdin, stdout, "cli-extended[interactive]")]


class _FakeQuestion:
    def __init__(self, answer):
        self.answer = answer
        self.unsafe_calls = 0

    def unsafe_ask(self):
        self.unsafe_calls += 1
        return self.answer

    def ask(self, *_args, **_kwargs):
        raise AssertionError("the adapter must let KeyboardInterrupt propagate")


class _FakeQuestionary:
    def __init__(self):
        self.calls = []
        self.answers = {
            "text": "typed",
            "password": "secret",
            "confirm": False,
            "select": "blue",
            "checkbox": [],
        }
        self.questions = []

    def __getattr__(self, name):
        def create(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            question = _FakeQuestion(self.answers[name])
            self.questions.append(question)
            return question

        return create


def test_questionary_adapter_calls_unsafe_api_and_passes_streams():
    questionary = _FakeQuestionary()
    input_handle, output_handle = object(), object()
    driver = prompts_module._QuestionaryDriver(questionary, input_handle, output_handle)

    assert driver.text("text?", default="draft", required=True) == "typed"
    assert driver.text("optional?", required=False) == "typed"
    assert driver.password("password?", required=True) == "secret"
    assert driver.password("optional password?", required=False) == "secret"
    assert driver.confirm("confirm?", default=False) is False
    assert driver.select("select?", ["blue", "red"], default="red") == "blue"
    assert driver.checkbox("checkbox?", ["blue", "red"], default=("red",)) == []
    assert all(question.unsafe_calls == 1 for question in questionary.questions)

    calls = questionary.calls
    assert calls[0][2]["default"] == "draft"
    assert calls[0][2]["validate"]("  ") == "A non-whitespace value is required."
    assert calls[0][2]["validate"]("ok") is True
    assert calls[1][2]["validate"] is None
    assert calls[1][2]["default"] == ""
    assert calls[2][2]["validate"]("") == "A password is required."
    assert calls[2][2]["validate"]("secret") is True
    assert calls[3][2]["validate"] is None
    assert calls[4][2]["default"] is False
    assert calls[5][2]["default"] == "red"
    assert calls[6][2]["choices"] == [
        {"name": "blue", "value": "blue", "checked": False},
        {"name": "red", "value": "red", "checked": True},
    ]
    assert all(call[2]["input"] is input_handle for call in calls)
    assert all(call[2]["output"] is output_handle for call in calls)


def test_questionary_escape_result_becomes_prompt_cancelled():
    questionary = _FakeQuestionary()
    questionary.answers["text"] = None
    driver = prompts_module._QuestionaryDriver(questionary, object(), object())
    with pytest.raises(PromptCancelled):
        _api(driver).text("text")


def test_lazy_loader_uses_questionary_and_prompt_toolkit_stream_factories(monkeypatch):
    questionary = _FakeQuestionary()
    stdin, stdout = _TTY(), _TTY()
    loaded = []
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "questionary":
            loaded.append(name)
            return questionary
        if name == "prompt_toolkit.input.defaults":
            loaded.append(name)
            return SimpleNamespace(create_input=lambda **kw: ("input", kw["stdin"]))
        if name == "prompt_toolkit.output.defaults":
            loaded.append(name)
            return SimpleNamespace(create_output=lambda **kw: ("output", kw["stdout"]))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    driver = prompts_module._load_questionary_driver(
        stdin, stdout, "nyxloom[interactive]"
    )
    assert loaded == [
        "questionary",
        "prompt_toolkit.input.defaults",
        "prompt_toolkit.output.defaults",
    ]
    assert driver._input == ("input", stdin)
    assert driver._output == ("output", stdout)


def test_lazy_loader_gives_actionable_missing_extra_hint(monkeypatch):
    original_import = builtins.__import__

    def missing_questionary(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "questionary":
            raise ImportError("missing optional dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_questionary)
    with pytest.raises(CliFailure, match="optional Questionary dependency") as exc:
        prompts_module._load_questionary_driver(
            _TTY(), _TTY(), "nyxloom[interactive]"
        )
    assert "pip install 'nyxloom[interactive]'" in exc.value.hint


def test_cli_runtime_injects_driver_redacts_password_and_handles_prompt_cancel():
    identity = CliIdentity("TEST", "1.0", "Test", command="test-tool")

    def password_handler(_args, runtime):
        value = runtime.prompts.password("password")
        runtime.output.debug(value)
        runtime.output.primary(value)
        return 0

    registry = CliRegistry(identity, prog="test-tool", description="prompt test")
    registry.register(VerbSpec("ask", description="ask", handler=password_handler))
    app = registry.build()
    stdout, stderr = _TTY(), io.StringIO()
    assert app.run(
        argv=["ask", "--debug"],
        prompt_driver=_Driver(password="new-secret"),
        stdout=stdout,
        stderr=stderr,
        stdin=_TTY(),
    ) == 0
    assert "new-secret" not in stdout.getvalue()
    assert "new-secret" not in stderr.getvalue()
    assert "<redacted>" in stdout.getvalue()
    assert "<redacted>" in stderr.getvalue()

    def cancel_handler(_args, runtime):
        runtime.prompts.confirm("continue?")
        return 0

    registry = CliRegistry(identity, prog="test-tool", description="prompt test")
    registry.register(VerbSpec("ask", description="ask", handler=cancel_handler))
    stderr = io.StringIO()
    assert registry.build().run(
        argv=["ask"],
        prompt_driver=_Driver(confirm=None),
        stdout=_TTY(),
        stderr=stderr,
        stdin=_TTY(),
    ) == 130
    assert "Cancelled." in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_prompt_ctrl_c_remains_keyboard_interrupt_exit_130():
    identity = CliIdentity("TEST", "1.0", "Test", command="test-tool")

    class InterruptingDriver(_Driver):
        def confirm(self, message, *, default=False):
            raise KeyboardInterrupt

    registry = CliRegistry(identity, prog="test-tool", description="prompt test")
    registry.register(
        VerbSpec(
            "ask",
            description="ask",
            handler=lambda _args, runtime: runtime.prompts.confirm("continue?"),
        )
    )
    stderr = io.StringIO()
    assert registry.build().run(
        argv=["ask"],
        prompt_driver=InterruptingDriver(),
        stdout=_TTY(),
        stderr=stderr,
        stdin=_TTY(),
    ) == 130
    assert "Cancelled." in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_tty_refusal_precedes_optional_import_and_missing_extra_is_actionable(monkeypatch):
    identity = CliIdentity("TEST", "1.0", "Test", command="test-tool")
    registry = CliRegistry(identity, prog="test-tool", description="prompt test")
    registry.register(
        VerbSpec(
            "ask",
            description="ask",
            handler=lambda _args, runtime: runtime.prompts.confirm("continue?"),
        )
    )
    app = registry.build()
    original_import = builtins.__import__
    attempted = []

    def forbid_questionary(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "questionary":
            attempted.append(name)
            raise AssertionError("TTY refusal must precede the optional import")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", forbid_questionary)
    stderr = io.StringIO()
    assert app.run(argv=["ask"], stdout=io.StringIO(), stderr=stderr, stdin=_TTY()) == 2
    assert attempted == []
    assert "both stdin and stdout" in stderr.getvalue()

    def missing_questionary(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "questionary":
            raise ImportError("missing optional dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_questionary)
    stderr = io.StringIO()
    assert app.run(
        argv=["ask"],
        stdout=_TTY(),
        stderr=stderr,
        stdin=_TTY(),
        interactive_extra="nyxloom[interactive]",
    ) == 2
    assert "pip install 'nyxloom[interactive]'" in stderr.getvalue()


def test_help_version_and_normal_command_do_not_import_questionary(monkeypatch):
    identity = CliIdentity("TEST", "1.0", "Test", command="test-tool")
    registry = CliRegistry(identity, prog="test-tool", description="prompt test")
    registry.register(VerbSpec("show", description="show", handler=lambda *_: 0))
    app = registry.build()
    original_import = builtins.__import__
    attempted = []

    def forbid_questionary(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "questionary":
            attempted.append(name)
            raise AssertionError("noninteractive paths must not import Questionary")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", forbid_questionary)
    assert app.run(argv=["--help"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert app.run(argv=["version"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert app.run(argv=["show"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert attempted == []


def test_prompt_driver_and_extra_name_are_forwarded_to_delegated_cli():
    identity = CliIdentity("TEST", "1.0", "Test", command="test-tool")

    def setup(_args, runtime):
        runtime.prompts.confirm("continue?")
        return 0

    child = CliRegistry(
        identity,
        prog="test-tool-admin",
        description="admin prompt",
        single_command=True,
        no_args_action=True,
    )
    child.register(
        VerbSpec(
            "setup",
            description="setup",
            handler=setup,
        )
    )
    parent = CliRegistry(identity, prog="test-tool", description="parent")
    parent.register(VerbSpec("admin", description="admin", delegate=child.build()))
    driver = _Driver(confirm=True)

    assert parent.build().run(
        argv=["admin"],
        prompt_driver=driver,
        interactive_extra="example[interactive]",
        stdin=_TTY(),
        stdout=_TTY(),
        stderr=io.StringIO(),
    ) == 0
    assert driver.calls[0][0] == "confirm"
