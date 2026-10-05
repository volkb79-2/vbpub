"""W6: hermetic subprocess helpers and the opt-in pytest plugin."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli_extended
from cli_extended import (
    CliIdentity,
    assert_cli_contract,
    invoke_module,
    invoke_script,
    make_invoker,
)
from cli_extended import testing as testing_module

LIBRARY_ROOT = str(Path(cli_extended.__file__).resolve().parents[1])
ENV_DUMP = "import json, os, sys\nprint(json.dumps({'env': dict(os.environ), 'cwd': os.getcwd(), 'argv': sys.argv[1:]}))\n"


def _dump(tmp_path: Path, **kwargs):
    script = tmp_path / "dump.py"
    script.write_text(ENV_DUMP, encoding="utf-8")
    home = tmp_path / "home"
    result = invoke_script(script, ["a", "--b"], home=home, **kwargs)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), home


@pytest.fixture()
def dirty_environment(monkeypatch):
    for key, value in {
        "FORCE_COLOR": "1",
        "CLICOLOR_FORCE": "1",
        "CLAUDE_CONFIG_DIR": "/real/claude",
        "SCRUBME_TOKEN": "secret",
        "SCRUBME_OTHER": "secret",
        "OTHER_PREFIX_KEY": "gone",
        "KEEP_ME": "kept",
        "NO_COLOR": "0",
        "XDG_CONFIG_HOME": "/real/config",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")


def test_invoke_script_builds_the_hermetic_environment(tmp_path, dirty_environment):
    dump, home = _dump(
        tmp_path,
        scrub_prefixes=("SCRUBME_", "OTHER_PREFIX"),
        pythonpath=("/extra/one", tmp_path),
    )
    env = dump["env"]

    assert dump["argv"] == ["a", "--b"]
    for removed in (
        "FORCE_COLOR",
        "CLICOLOR_FORCE",
        "CLAUDE_CONFIG_DIR",
        "SCRUBME_TOKEN",
        "SCRUBME_OTHER",
        "OTHER_PREFIX_KEY",
    ):
        assert removed not in env
    assert env["KEEP_ME"] == "kept"
    assert env["HOME"] == str(home)
    assert env["XDG_CONFIG_HOME"] == str(home / ".config")
    assert env["XDG_DATA_HOME"] == str(home / ".local" / "share")
    assert env["XDG_CACHE_HOME"] == str(home / ".cache")
    assert env["XDG_STATE_HOME"] == str(home / ".local" / "state")
    assert env["NO_COLOR"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep) == [
        LIBRARY_ROOT,
        "/extra/one",
        str(tmp_path),
        "/inherited/path",
    ]


def test_invoke_script_without_inherited_pythonpath_and_default_scrub(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("SCRUBME_TOKEN", "survives-without-prefixes")
    dump, _home = _dump(tmp_path)

    assert dump["env"]["PYTHONPATH"] == LIBRARY_ROOT
    assert dump["env"]["SCRUBME_TOKEN"] == "survives-without-prefixes"
    assert dump["cwd"] == str(Path.cwd())


def test_invoke_script_env_overrides_apply_last_and_none_deletes(
    tmp_path, dirty_environment
):
    dump, home = _dump(
        tmp_path,
        env={
            "KEEP_ME": None,
            "NEW_KEY": "new",
            "NOT_PRESENT": None,
            "PYTHONPATH": "/only",
        },
    )
    env = dump["env"]

    assert env["HOME"] == str(home)
    assert "KEEP_ME" not in env
    assert env["NEW_KEY"] == "new"
    assert env["PYTHONPATH"] == "/only"
    assert env["XDG_CONFIG_HOME"] == str(home / ".config")


@pytest.mark.parametrize(
    "key",
    ["HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"],
)
@pytest.mark.parametrize("value", ["/elsewhere", None])
def test_env_may_not_override_home_or_xdg(tmp_path, key, value):
    with pytest.raises(ValueError, match=key):
        invoke_script("x.py", [], home=tmp_path, env={key: value})
    with pytest.raises(ValueError, match=key):
        invoke_module("x", [], home=tmp_path, env={key: value})


def test_home_must_be_absolute(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        invoke_script("x.py", [], home="relative/home")
    with pytest.raises(ValueError, match="absolute"):
        invoke_module("x", [], home=Path("rel"))


def test_empty_scrub_prefix_is_refused(tmp_path):
    with pytest.raises(ValueError, match="empty string"):
        invoke_script("x.py", [], home=tmp_path, scrub_prefixes=("OK_", ""))


def test_library_root_is_prepended_only_for_the_same_interpreter(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    seen = []
    monkeypatch.setattr(
        testing_module.subprocess,
        "run",
        lambda command, **kw: seen.append(kw["env"]["PYTHONPATH"]),
    )
    invoke_script("x.py", [], home=tmp_path, pythonpath=["/mine"])
    invoke_script("x.py", [], home=tmp_path, python="/other/py", pythonpath=["/mine"])
    invoke_module("m", [], home=tmp_path, python="/other/py")
    invoke_module("m", [], home=tmp_path)

    assert seen == [LIBRARY_ROOT + os.pathsep + "/mine", "/mine", "", LIBRARY_ROOT]


def test_invoke_script_runs_in_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    dump, _home = _dump(tmp_path, cwd=work)

    assert dump["cwd"] == str(work.resolve())


def test_home_is_required_and_must_not_be_empty(tmp_path):
    script = tmp_path / "dump.py"
    script.write_text(ENV_DUMP, encoding="utf-8")

    with pytest.raises(TypeError):
        invoke_script(script, [])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        invoke_module("json.tool", [])  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="home"):
        invoke_script(script, [], home="")
    with pytest.raises(ValueError, match="home"):
        invoke_module("json.tool", [], home="")


def test_invocation_shape_python_override_and_failsafe_timeout(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(testing_module.subprocess, "run", fake_run)
    home = tmp_path / "h"
    invoke_script(Path("tool.py"), ["x"], home=home)
    invoke_script("tool.py", ["x"], home=home, python="/py", timeout=7, cwd=tmp_path)
    invoke_module("pkg.mod", ["y"], home=home)
    invoke_module("pkg.mod", ["y"], home=home, python="/py", timeout=9)

    assert calls[0][0] == [sys.executable, "tool.py", "x"]
    assert calls[1][0] == ["/py", "tool.py", "x"]
    assert calls[2][0] == [sys.executable, "-m", "pkg.mod", "y"]
    assert calls[3][0] == ["/py", "-m", "pkg.mod", "y"]
    for _command, kwargs in calls:
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["env"]["HOME"] == str(home)
    assert [kwargs["timeout"] for _c, kwargs in calls] == [60, 7, 60, 9]
    assert calls[0][1]["cwd"] is None
    assert calls[1][1]["cwd"] == tmp_path


TINY_CLI = """\
import sys
from cli_extended import CliIdentity, CliRegistry, VerbSpec

IDENTITY = CliIdentity(
    name="TINY", command="tiny", version="1.2.3", long_name="Tiny Tool"
)
registry = CliRegistry(IDENTITY, prog="tiny", description="tiny", single_command=True)
registry.register(VerbSpec("run", "", "run once", handler=lambda args, runtime: 0))
BROKEN = %s
if BROKEN and sys.argv[1:] in (["version"], ["--version"]):
    print(IDENTITY.version_line, file=sys.stderr)
    raise SystemExit(0)
if __name__ == "__main__":
    raise SystemExit(registry.build().run())
"""
TINY_IDENTITY = CliIdentity(
    name="TINY", command="tiny", version="1.2.3", long_name="Tiny Tool"
)


def _tiny(tmp_path: Path, broken: bool) -> Path:
    path = tmp_path / "tiny.py"
    path.write_text(TINY_CLI % broken, encoding="utf-8")
    return path


def test_make_invoker_script_drives_assert_cli_contract(tmp_path):
    invoke = make_invoker(_tiny(tmp_path, False), home=tmp_path / "home")

    assert_cli_contract(invoke, TINY_IDENTITY, ())


def test_make_invoker_module_drives_assert_cli_contract(tmp_path):
    _tiny(tmp_path, False)
    invoke = make_invoker(
        "tiny", module=True, home=tmp_path / "home", pythonpath=[tmp_path]
    )

    assert_cli_contract(invoke, TINY_IDENTITY, ())
    assert invoke(["--version"]).stdout == TINY_IDENTITY.version_line + "\n"


def test_make_invoker_rejects_a_cli_whose_version_goes_to_stderr(tmp_path):
    invoke = make_invoker(_tiny(tmp_path, True), home=tmp_path / "home")

    with pytest.raises(AssertionError, match="version"):
        assert_cli_contract(invoke, TINY_IDENTITY, ())


def test_make_invoker_defaults_to_script_mode(tmp_path):
    invoker = make_invoker("t.py", home=tmp_path)

    assert invoker.func is invoke_script
    assert invoker.args == ("t.py",)
    assert make_invoker("m", module=True, home=tmp_path).func is invoke_module


# --- the opt-in pytest plugin -------------------------------------------------

CASE_ID = "case:tool/one"
REVIEW = f"""\
schema_version = 1
cli_id = "audit-tool"

[[cases]]
id = "{CASE_ID}"
state = "active"
decision = "accept"
reviewed_signature = "sha256:x"
rationale = "reviewed"
invocation = []
expected_exit_status = 0
effects = []
test_ids = [{{test_ids}}]
"""
CONFIG = """\
schema_version = 1

[[clis]]
id = "audit-tool"
factory = "tool.py:build"
{review_line}
"""
MARKED = f"""\
import pytest

@pytest.mark.cli_case("{CASE_ID}")
def test_one():
    pass
"""
SECOND = f"""\
import pytest

@pytest.mark.cli_case("{CASE_ID}")
def test_two():
    pass
"""


def _project(pytester, *, test_ids, files, config_review=True):
    pytester.makeconftest('pytest_plugins = ["cli_extended.pytest_plugin"]\n')
    pytester.path.joinpath("cli-extended.toml").write_text(
        CONFIG.format(review_line='review = "review.toml"' if config_review else ""),
        encoding="utf-8",
    )
    ids = ", ".join(json.dumps(item) for item in test_ids)
    pytester.path.joinpath("review.toml").write_text(
        REVIEW.replace("{test_ids}", ids), encoding="utf-8"
    )
    for name, source in files.items():
        pytester.makepyfile(**{name: source})


def test_strict_mode_fails_when_an_active_cases_test_is_not_collected(pytester):
    _project(
        pytester,
        test_ids=["test_a.py::test_one", "test_b.py::test_two"],
        files={"test_a": MARKED, "test_b": SECOND},
    )

    full = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    full.assert_outcomes(passed=2)
    assert full.ret == 0

    focused = pytester.runpytest_inprocess("test_a.py", "-p", "no:cacheprovider")

    assert focused.ret == pytest.ExitCode.USAGE_ERROR
    focused.stderr.fnmatch_lines(
        [f"*cli-extended: CLI case test coverage failed*", "*uncollected test 'test_b.py::test_two'*"]
    )


def test_partial_mode_passes_the_same_focused_run(pytester):
    _project(
        pytester,
        test_ids=["test_a.py::test_one", "test_b.py::test_two"],
        files={"test_a": MARKED, "test_b": SECOND},
    )

    focused = pytester.runpytest_inprocess(
        "test_a.py", "--cli-case-partial", "-p", "no:cacheprovider"
    )

    focused.assert_outcomes(passed=1)
    assert focused.ret == 0
    # Nothing collected for the case at all is also fine in partial mode.
    pytester.makepyfile(test_c="def test_other():\n    pass\n")
    unrelated = pytester.runpytest_inprocess(
        "test_c.py", "--cli-case-partial", "-p", "no:cacheprovider"
    )
    unrelated.assert_outcomes(passed=1)
    strict = pytester.runpytest_inprocess("test_c.py", "-p", "no:cacheprovider")
    assert strict.ret == pytest.ExitCode.USAGE_ERROR
    strict.stderr.fnmatch_lines(["*has no collected marked test*"])


@pytest.mark.parametrize("flags", [(), ("--cli-case-partial",)])
def test_unknown_marker_case_fails_in_both_modes(pytester, flags):
    _project(
        pytester,
        test_ids=["test_a.py::test_one"],
        files={
            "test_a": MARKED
            + '\n@pytest.mark.cli_case("case:tool/missing")\ndef test_three():\n    pass\n'
        },
    )

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", *flags)

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown CLI case 'case:tool/missing'*"])


def test_partial_mode_still_enforces_errors_about_collected_tests(pytester):
    _project(
        pytester,
        test_ids=["test_a.py::test_one"],
        files={
            "test_a": MARKED
            + f'\n@pytest.mark.cli_case("{CASE_ID}")\ndef test_unlisted():\n    pass\n',
            "test_b": "def test_two():\n    pass\n",
        },
    )

    unlisted = pytester.runpytest_inprocess(
        "test_a.py", "--cli-case-partial", "-p", "no:cacheprovider"
    )
    assert unlisted.ret == pytest.ExitCode.USAGE_ERROR
    unlisted.stderr.fnmatch_lines(["*is marked for CLI case*not listed in test_ids*"])

    pytester.makepyfile(test_a="def test_one():\n    pass\n")
    unmarked = pytester.runpytest_inprocess(
        "test_a.py", "--cli-case-partial", "-p", "no:cacheprovider"
    )
    assert unmarked.ret == pytest.ExitCode.USAGE_ERROR
    unmarked.stderr.fnmatch_lines(["*lacks cli_case*marker*"])


def test_marker_is_registered_for_strict_markers(pytester):
    _project(
        pytester,
        test_ids=["test_a.py::test_one"],
        files={"test_a": MARKED},
    )

    result = pytester.runpytest_inprocess(
        "--strict-markers", "-p", "no:cacheprovider", "-rw"
    )

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert "PytestUnknownMarkWarning" not in result.stdout.str()
    listing = pytester.runpytest_inprocess("--markers", "-p", "no:cacheprovider")
    listing.stdout.fnmatch_lines(
        ["*cli_case(case_id): links a behavior test to a reviewed CLI case*"]
    )


def test_no_review_configured_is_a_no_op(pytester):
    _project(
        pytester,
        test_ids=["test_a.py::test_one"],
        files={"test_a": "def test_x():\n    pass\n"},
        config_review=False,
    )

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    result.assert_outcomes(passed=1)
    assert result.ret == 0


def test_ini_option_points_at_the_config_file_relative_to_rootdir(pytester):
    pytester.makeconftest('pytest_plugins = ["cli_extended.pytest_plugin"]\n')
    pytester.makeini("[pytest]\ncli_extended_config = conf/custom.toml\n")
    conf = pytester.mkdir("conf")
    conf.joinpath("custom.toml").write_text(
        CONFIG.format(review_line='review = "review.toml"'), encoding="utf-8"
    )
    conf.joinpath("review.toml").write_text(
        REVIEW.replace("{test_ids}", '"test_a.py::test_one", "test_b.py::test_two"'),
        encoding="utf-8",
    )
    pytester.makepyfile(test_a=MARKED, test_b=SECOND)

    assert pytester.runpytest_inprocess("-p", "no:cacheprovider").ret == 0
    focused = pytester.runpytest_inprocess("test_a.py", "-p", "no:cacheprovider")
    assert focused.ret == pytest.ExitCode.USAGE_ERROR
    focused.stderr.fnmatch_lines(["*uncollected test 'test_b.py::test_two'*"])


def test_missing_config_and_bad_catalog_are_usage_errors(pytester):
    pytester.makeconftest('pytest_plugins = ["cli_extended.pytest_plugin"]\n')
    pytester.makeini("[pytest]\ncli_extended_config = nowhere.toml\n")
    pytester.makepyfile(test_a="def test_x():\n    pass\n")

    missing = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    assert missing.ret == pytest.ExitCode.USAGE_ERROR
    missing.stderr.fnmatch_lines(["*cli-extended: cannot read*nowhere.toml*"])

    pytester.makeini("[pytest]\ncli_extended_config = cli-extended.toml\n")
    pytester.path.joinpath("cli-extended.toml").write_text(
        CONFIG.format(review_line='review = "review.toml"'), encoding="utf-8"
    )
    pytester.path.joinpath("review.toml").write_text("not = [valid", encoding="utf-8")
    bad = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    assert bad.ret == pytest.ExitCode.USAGE_ERROR
    bad.stderr.fnmatch_lines(["*cli-extended: cannot read CLI review catalog*"])


def test_plugin_imports_without_pytest_installed(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "sys.modules['pytest'] = None\n"
        "import cli_extended.pytest_plugin as plugin\n"
        "print(plugin.PARTIAL_OPTION)\n",
        encoding="utf-8",
    )

    result = invoke_script(script, [], home=tmp_path / "home")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "--cli-case-partial\n"


def test_plugin_is_not_registered_as_an_entry_point():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert "pytest11" not in pyproject
