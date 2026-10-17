"""KI-81: the bare-host release-gate controller runs in the installed CMRU environment.

``run-gate gate`` / ``gate-provisional`` are ``bare-host`` lanes that start
``python3 -I -B run_release_gate.py``. ``python3`` there is whatever the host PATH
names, which need not hold the declared ``cli-extended`` wheel. These tests run the
REAL script as a subprocess. ``-I -S`` starts it from an interpreter that genuinely
cannot import ``cli_extended`` (``-S`` drops site-packages), and a logging wrapper
stands in for the interpreter of the installed ``cmru`` launcher, so the log proves
which interpreter ran which command.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "run_release_gate.py"
REEXEC_ENV = "_CMRU_RELEASE_GATE_REEXEC"

_SPEC = importlib.util.spec_from_file_location("run_release_gate_interp", SCRIPT)
run_release_gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_release_gate
_SPEC.loader.exec_module(run_release_gate)


def _can_import(*flags: str) -> bool:
    return subprocess.run(
        [sys.executable, "-I", *flags, "-c", "import cli_extended"],
        capture_output=True, check=False,
    ).returncode == 0


#: The probe the controller runs in the authoritative interpreter: it imports the
#: dependency and reports the INSTALLED distribution version, not mere importability.
PROBE = (
    "-I -B -c import importlib.metadata as _metadata; import cli_extended; "
    "print('cli-extended' + '=' + _metadata.version('cli-extended'))"
)


@pytest.fixture(autouse=True)
def _premise():
    """Without these facts the subprocess tests below would prove nothing."""
    assert _can_import(), "the test environment must have the declared cli-extended wheel"
    assert not _can_import("-S"), "`-S` must hide site-packages so the start interpreter lacks it"
    declared = dict(run_release_gate._declared_dependencies())
    installed = run_release_gate._installed_version("cli-extended")
    assert installed is not None and run_release_gate._requirement_satisfied(
        next(iter(declared)), installed,
    ), "the test environment's cli-extended must satisfy CMRU's declared floor"


def _fake_interpreter(directory: Path, log: Path, *, flags: str = "") -> Path:
    """An interpreter wrapper that logs its argv, then runs the real one (optionally crippled)."""
    wrapper = directory / "pyenv-python"
    wrapper.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log}"\nexec "{sys.executable}" {flags} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _launcher(directory: Path, interpreter: Path) -> Path:
    launcher = directory / "cmru"
    launcher.write_text(f"#!{interpreter}\nraise SystemExit(0)\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


#: The protected values CMRU's release runtime hands a step. Tests that are not about them
#: remove any inherited copy so the outcome never depends on how the suite was launched.
PROTECTED_ENV = ("CMRU_INTERNAL_PYTHON", "CMRU_RELEASE_GATE_COMMIT")
GATE_COMMIT = "a" * 40


def _run_gate_script(
    path_dir: Path, *args: str, flags=("-I", "-S", "-B"), extra_env=None, python=None,
):
    env = {
        key: value for key, value in os.environ.items()
        if key != REEXEC_ENV and key not in PROTECTED_ENV
    }
    env["PATH"] = str(path_dir)
    env.update(extra_env or {})
    return subprocess.run(
        [str(python or sys.executable), *flags, str(SCRIPT), *args],
        capture_output=True, text=True, check=False, env=env, timeout=120,
    )


@pytest.fixture
def installed_env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "interpreter.log"
    return bindir, log


def test_a_start_interpreter_without_the_dependency_runs_under_the_installed_cmru_environment(
    installed_env,
):
    bindir, log = installed_env
    interpreter = _fake_interpreter(bindir, log)
    launcher = _launcher(bindir, interpreter)

    result = _run_gate_script(bindir, "--help")

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout and "--postpone-mutation" in result.stdout
    # Loud, naming the failing interpreter, the dependency and the chosen environment.
    assert "cannot import cli_extended" in result.stderr
    assert str(interpreter) in result.stderr and str(launcher.resolve()) in result.stderr
    # The installed environment's interpreter was PROBED for the dependency, then the SAME
    # script was started under it with the isolation flags the lane declares.
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines == [PROBE, f"-I -B {SCRIPT.resolve()} --help"]


def test_the_switch_forwards_the_lane_arguments_unchanged(installed_env):
    bindir, log = installed_env
    _launcher(bindir, _fake_interpreter(bindir, log))
    worktree = bindir.parent / "wt"

    # A blank tracking id is refused by the controller's own parser: only a controller
    # that received the original arguments can say so.
    result = _run_gate_script(bindir, "--worktree", str(worktree), "--postpone-mutation", "  ")

    assert result.returncode == 2
    assert "the postponement tracking id must be non-empty" in result.stderr
    assert f"-I -B {SCRIPT.resolve()} --worktree {worktree} --postpone-mutation " in (
        log.read_text(encoding="utf-8")
    )


def test_no_switch_happens_when_the_start_interpreter_has_the_dependency_at_the_declared_floor(
    installed_env,
):
    bindir, log = installed_env
    _launcher(bindir, _fake_interpreter(bindir, log))

    result = _run_gate_script(bindir, "--help", flags=("-I", "-B"))

    assert result.returncode == 0, result.stderr
    assert "cannot import" not in result.stderr and "does not satisfy" not in result.stderr
    assert not log.exists()  # the cmru launcher's interpreter was never consulted


def _host_with_cli_extended(directory: Path, version: str) -> Path:
    """A real interpreter whose ONLY third-party package is a ``cli_extended`` <version>.

    ``-I`` ignores PYTHONPATH, so a genuine environment is the honest way to make the
    start interpreter import a below-floor wheel: a bare venv with one fake dist.
    """
    venv = directory / f"host-{version}"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        check=True, capture_output=True,
    )
    (site,) = (venv / "lib").glob("python*/site-packages")
    (site / "cli_extended").mkdir()
    (site / "cli_extended" / "__init__.py").write_text("", encoding="utf-8")
    info = site / f"cli_extended-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: cli-extended\nVersion: {version}\n", encoding="utf-8",
    )
    return venv / "bin" / "python"


def test_a_host_importing_an_old_cli_extended_switches_to_the_authoritative_environment(
    installed_env, tmp_path,
):
    """The host imports cli_extended 0.2.0 fine, but CMRU declares >=0.3.0."""
    bindir, log = installed_env
    host = _host_with_cli_extended(tmp_path, "0.2.0")
    interpreter = _fake_interpreter(bindir, log)  # the installed CMRU environment (>= floor)
    launcher = _launcher(bindir, interpreter)

    old = subprocess.run(
        [str(host), "-I", "-c", "import cli_extended, importlib.metadata as m; print(m.version('cli-extended'))"],
        capture_output=True, text=True, check=True,
    )
    assert old.stdout.strip() == "0.2.0"  # control: importable, and below the floor

    result = _run_gate_script(bindir, "--help", flags=("-I", "-B"), python=host)

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "has cli-extended 0.2.0 installed, which does not satisfy cli-extended>=0.3.0" in result.stderr
    assert str(host) in result.stderr and str(launcher.resolve()) in result.stderr
    # Probe first (it reports the installed version), then the same script, with -I -B.
    assert log.read_text(encoding="utf-8").splitlines() == [PROBE, f"-I -B {SCRIPT.resolve()} --help"]


def test_a_below_floor_authoritative_environment_is_refused_before_the_controller_starts(
    installed_env, tmp_path,
):
    bindir, log = installed_env
    host = _host_with_cli_extended(tmp_path, "0.2.0")
    _launcher(bindir, host)  # the "installed CMRU environment" is itself below the floor

    result = _run_gate_script(bindir, "--help", flags=("-I", "-B"), python=host)

    assert result.returncode == 1
    assert result.stdout == ""  # the controller never started
    assert f"the interpreter {host} named by the installed cmru launcher" in result.stderr
    assert "has cli-extended 0.2.0 installed, which does not satisfy cli-extended>=0.3.0" in result.stderr
    assert "pip install --no-index --find-links <wheelhouse> cmru" in result.stderr
    assert "cannot import" not in result.stderr  # it imports fine; the VERSION is the refusal
    assert not log.exists()


def test_a_below_floor_runtime_reported_interpreter_is_refused_not_replaced(installed_env, tmp_path):
    bindir, log = installed_env
    host = _host_with_cli_extended(tmp_path, "0.2.0")
    _launcher(bindir, _fake_interpreter(bindir, log))  # a good PATH fallback exists

    result = _run_gate_script(
        bindir, "--help", flags=("-I", "-B"), python=host,
        extra_env={"CMRU_INTERNAL_PYTHON": str(host), "CMRU_RELEASE_GATE_COMMIT": GATE_COMMIT},
    )

    assert result.returncode == 1
    assert "provided by the CMRU release runtime" in result.stderr
    assert "does not satisfy cli-extended>=0.3.0" in result.stderr
    assert not log.exists()  # no fallback to the PATH launcher


def test_the_floor_itself_is_accepted_by_the_start_interpreter(installed_env, tmp_path):
    """The negative of the 0.2.0 oracle: a host at exactly the floor does not switch."""
    bindir, log = installed_env
    host = _host_with_cli_extended(tmp_path, "0.3.0")
    _launcher(bindir, _fake_interpreter(bindir, log))

    result = _run_gate_script(bindir, "--help", flags=("-I", "-B"), python=host)

    assert result.returncode == 0, result.stderr
    assert "does not satisfy" not in result.stderr
    assert not log.exists()


def test_an_installed_environment_that_lacks_the_dependency_fails_with_the_remedy(installed_env):
    bindir, log = installed_env
    interpreter = _fake_interpreter(bindir, log, flags="-S")  # a venv without cli-extended
    launcher = _launcher(bindir, interpreter)

    result = _run_gate_script(bindir, "--help")

    assert result.returncode == 1
    assert result.stdout == ""  # no controller output: it never started
    assert "cmru-release-gate:" in result.stderr
    assert f"the interpreter {interpreter} named by the installed cmru launcher" in result.stderr
    assert str(launcher.resolve()) in result.stderr
    assert "cannot import cli_extended either" in result.stderr
    assert "No module named" in result.stderr
    assert "pip install --no-index --find-links <wheelhouse> cmru" in result.stderr
    assert "cli-extended" in result.stderr and "PATH" in result.stderr
    # Only the probe ran; the script was never started under the broken environment.
    assert log.read_text(encoding="utf-8").splitlines() == [PROBE]


def test_a_missing_cmru_launcher_fails_with_the_remedy_and_no_guess(tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()

    result = _run_gate_script(empty, "--help")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot import cli_extended" in result.stderr
    assert "requires the installed cmru CLI" in result.stderr
    assert "pip install --no-index --find-links <wheelhouse> cmru" in result.stderr


def test_a_second_failure_after_the_switch_is_refused_not_looped(installed_env):
    bindir, log = installed_env
    _launcher(bindir, _fake_interpreter(bindir, log))

    result = _run_gate_script(bindir, "--help", extra_env={REEXEC_ENV: "1"})

    assert result.returncode == 1
    assert "still fails the declared CMRU dependency set" in result.stderr
    assert not log.exists()  # no further switch was attempted


# --- inside `cmru release`: the REAL bound launcher is a shell wrapper --------------------


def _real_bound_launcher(directory: Path) -> Path:
    """The launcher CMRU really puts first on a step's PATH (identity-verified by cmru)."""
    from cmru import cli

    directory.mkdir()
    launcher = cli._create_bound_cmru_launcher(directory)
    assert launcher.read_text(encoding="utf-8").startswith("#!/bin/sh\nexec "), (
        "the premise of KI-81: CMRU's launcher is a shell wrapper, not a console script"
    )
    return launcher


def test_the_real_bound_launcher_names_no_environment_so_a_manual_run_refuses_it(tmp_path):
    launcher = _real_bound_launcher(tmp_path / "bound")

    result = _run_gate_script(launcher.parent, "--help")  # no protected values: a manual run

    assert result.returncode == 1
    assert result.stdout == ""
    assert "is not a Python console script" in result.stderr
    assert "its shebang runs /bin/sh" in result.stderr
    assert "CMRU_INTERNAL_PYTHON" in result.stderr  # says where the fact comes from instead
    assert "pip install --no-index --find-links <wheelhouse> cmru" in result.stderr


def test_inside_the_release_workflow_the_runtime_reported_interpreter_is_used(installed_env, tmp_path):
    bindir, log = installed_env
    interpreter = _fake_interpreter(bindir, log)
    launcher = _real_bound_launcher(tmp_path / "bound")  # first and only PATH entry, as in a release

    result = _run_gate_script(
        launcher.parent, "--help",
        extra_env={"CMRU_INTERNAL_PYTHON": str(interpreter), "CMRU_RELEASE_GATE_COMMIT": GATE_COMMIT},
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert f"{interpreter} (provided by the CMRU release runtime (CMRU_INTERNAL_PYTHON))" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [PROBE, f"-I -B {SCRIPT.resolve()} --help"]


def test_a_value_in_an_operators_shell_cannot_choose_the_interpreter_of_a_manual_run(installed_env):
    bindir, log = installed_env
    installed = _fake_interpreter(bindir, log)
    launcher = _launcher(bindir, installed)

    # CMRU_INTERNAL_PYTHON without the protected gate commit is not the release workflow.
    result = _run_gate_script(
        bindir, "--help", extra_env={"CMRU_INTERNAL_PYTHON": "/nonexistent/evil-python"},
    )

    assert result.returncode == 0, result.stderr
    assert "evil-python" not in result.stderr
    assert f"named by the installed cmru launcher {launcher.resolve()}" in result.stderr


@pytest.mark.parametrize("bad", ["python3", "/nonexistent/python", "/etc"])
def test_an_unusable_runtime_interpreter_is_refused_not_replaced_by_a_path_lookup(installed_env, bad):
    bindir, log = installed_env
    _launcher(bindir, _fake_interpreter(bindir, log))  # a perfectly good fallback exists

    result = _run_gate_script(
        bindir, "--help",
        extra_env={"CMRU_INTERNAL_PYTHON": bad, "CMRU_RELEASE_GATE_COMMIT": GATE_COMMIT},
    )

    assert result.returncode == 1
    assert "not an absolute path to an executable file" in result.stderr
    assert not log.exists()  # the PATH launcher was never consulted


def test_a_real_release_step_environment_lets_the_gate_controller_start(monkeypatch, tmp_path):
    """No fakes: `run_project_step` builds the real launcher and protected env; the
    controller is started from a no-dependency interpreter with exactly that environment
    and PATH, and must come up under the interpreter CMRU reports."""
    from cmru import cli

    observed = {}

    def execute(step, root, logs, **kwargs):
        protected = {k: v for k, v in kwargs["protected_env"].items() if v is not None}
        path = os.pathsep.join(str(item) for item in kwargs["path_prefixes"])
        assert Path(kwargs["path_prefixes"][0], "cmru").is_file()  # CMRU's own wrapper is first
        env = {k: v for k, v in os.environ.items() if k not in PROTECTED_ENV and k != REEXEC_ENV}
        env.update(protected, PATH=path)
        observed["protected"] = protected
        observed["result"] = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--help"],
            capture_output=True, text=True, check=False, env=env, timeout=120,
        )

    monkeypatch.setattr(cli, "execute_step", execute)
    monkeypatch.setattr(cli, "_project_working_directory", lambda _project: ".")
    project = cli.ProjectConfig(
        "demo", {}, {"run-tests": []}, runtime_kind="none", build_metadata={},
        runner_steps={"run-tests": object()},
    )

    cli.run_project_step(
        project, "run-tests", tmp_path, tmp_path / "logs",
        protected_env={"CMRU_RELEASE_GATE_COMMIT": GATE_COMMIT},
    )

    assert observed["protected"]["CMRU_INTERNAL_PYTHON"] == sys.executable
    result = observed["result"]
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert f"{sys.executable} (provided by the CMRU release runtime" in result.stderr


def test_the_protected_names_are_spelled_identically_on_both_sides_and_reserved():
    from cmru import config, transaction

    assert transaction.INTERNAL_PYTHON_ENV == run_release_gate._INTERNAL_PYTHON_ENV
    assert run_release_gate._GATE_COMMIT_ENV == "CMRU_RELEASE_GATE_COMMIT"
    # A project [env] can declare neither, so neither can be forged from configuration.
    assert config.is_reserved_internal_env(transaction.INTERNAL_PYTHON_ENV)
    assert config.is_reserved_internal_env(run_release_gate._GATE_COMMIT_ENV)


# --- the in-process contract (exact exec argv, environment hygiene) ---------------


def test_the_switch_execs_the_same_script_with_isolation_flags_and_a_one_shot_marker(
    tmp_path, monkeypatch,
):
    launcher = tmp_path / "cmru"
    launcher.write_text("#!/opt/env/bin/python3 -s\n", encoding="utf-8")
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: str(launcher))
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: None)
    probes, execs = [], []

    def fake_run(argv, **_kwargs):
        probes.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout="cli-extended=0.3.0\n", stderr="",
        )

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(run_release_gate.os, "execv", lambda path, argv: execs.append((path, argv)))
    for name in (REEXEC_ENV, *PROTECTED_ENV):
        monkeypatch.delenv(name, raising=False)

    run_release_gate._ensure_gate_environment(["--worktree", "/w", "--postpone-mutation", "KI-79"])

    assert [probe[:-1] for probe in probes] == [["/opt/env/bin/python3", "-s", "-I", "-B", "-c"]]
    assert probes[0][-1].startswith("import importlib.metadata as _metadata; import cli_extended;")
    assert execs == [(
        "/opt/env/bin/python3",
        ["/opt/env/bin/python3", "-s", "-I", "-B", str(SCRIPT.resolve()),
         "--worktree", "/w", "--postpone-mutation", "KI-79"],
    )]
    assert os.environ[REEXEC_ENV] == "1"


def test_the_runtime_reported_interpreter_is_exec_ed_verbatim_and_beats_the_path_launcher(
    tmp_path, monkeypatch,
):
    runtime = tmp_path / "estate-python"
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime.chmod(0o755)
    launcher = tmp_path / "cmru"
    launcher.write_text("#!/bin/sh\nexec /somewhere/else\n", encoding="utf-8")
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: str(launcher))
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: None)
    probes, execs = [], []
    monkeypatch.setattr(
        run_release_gate.subprocess, "run",
        lambda argv, **_kw: probes.append(argv) or subprocess.CompletedProcess(
            argv, 0, "cli-extended=0.3.0\n", "",
        ),
    )
    monkeypatch.setattr(run_release_gate.os, "execv", lambda path, argv: execs.append((path, argv)))
    monkeypatch.delenv(REEXEC_ENV, raising=False)
    monkeypatch.setenv("CMRU_INTERNAL_PYTHON", f"  {runtime}  ")
    monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", GATE_COMMIT)

    run_release_gate._ensure_gate_environment(["--worktree", "/w"])

    assert [probe[:-1] for probe in probes] == [[str(runtime), "-I", "-B", "-c"]]
    assert execs == [(str(runtime), [str(runtime), "-I", "-B", str(SCRIPT.resolve()), "--worktree", "/w"])]


def test_the_marker_is_consumed_so_it_never_reaches_the_lane_subprocesses(monkeypatch):
    monkeypatch.setenv(REEXEC_ENV, "1")
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(run_release_gate, "_installed_version", lambda _name: "0.3.0")

    run_release_gate._ensure_gate_environment([])

    assert REEXEC_ENV not in os.environ


def test_an_env_shebang_launcher_is_resolved_through_path(tmp_path, monkeypatch):
    launcher = tmp_path / "cmru"
    launcher.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    resolved = {"cmru": str(launcher), "python3": "/opt/env/bin/python3"}
    monkeypatch.setattr(run_release_gate.shutil, "which", resolved.get)

    _executable, command, interpreter = run_release_gate._installed_launcher("cmru")

    assert command == launcher.resolve() and interpreter == ["/opt/env/bin/python3"]


@pytest.mark.parametrize("shebang, fragment", [
    ("python3 -s", "has no Python shebang"),
    ("#!", "has an empty Python shebang"),
    ("#!python3", "non-absolute interpreter"),
    ("#!/usr/bin/env -S python3", "unsupported env shebang"),
])
def test_an_unusable_launcher_is_named_in_the_refusal(tmp_path, monkeypatch, shebang, fragment):
    launcher = tmp_path / "cmru"
    launcher.write_text(f"{shebang}\n", encoding="utf-8")
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: str(launcher))

    with pytest.raises(RuntimeError, match=fragment) as refusal:
        run_release_gate._installed_launcher("cmru")

    assert "installed cmru launcher" in str(refusal.value) and str(launcher.resolve()) in str(refusal.value)


# --- the dependency set is derived from the declaration, never hardcoded ----------------


def test_the_shipped_declaration_yields_the_cli_extended_import():
    declared = dict(run_release_gate._declared_dependencies())
    assert declared == {"cli-extended>=0.3.0": "cli_extended"}  # pyproject.toml's own list


def test_every_declared_requirement_is_mapped_to_its_import_name(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["cli-extended>=0.3.0", "Some.Pkg[extra]>=1"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(run_release_gate, "PROJECT_ROOT", tmp_path)

    assert run_release_gate._declared_dependencies() == [
        ("cli-extended>=0.3.0", "cli_extended"), ("Some.Pkg[extra]>=1", "some_pkg"),
    ]


@pytest.mark.parametrize("content", [None, "not toml [", "[tool.x]\n", '[project]\ndependencies = [3]\n'])
def test_an_unreadable_declaration_is_a_loud_refusal(tmp_path, monkeypatch, content):
    if content is not None:
        (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    monkeypatch.setattr(run_release_gate, "PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="cannot read CMRU's declared dependencies"):
        run_release_gate._declared_dependencies()


# --- the registered lanes carry no host-specific interpreter -------------------------


def test_the_registered_gate_lanes_name_no_host_specific_interpreter():
    lanes = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]
    for name in ("gate", "gate-provisional"):
        argv = lanes[name]["argv"]
        assert lanes[name]["environment"] == "bare-host"
        assert argv[:4] == [
            "python3", "-I", "-B", "{worktree}/cmru/tools/run_release_gate.py",
        ], name
        # The environment is DERIVED from the installed cmru launcher at run time; a
        # venv path written here would silently go stale on every other host.
        assert not any(item.startswith("/") for item in argv), name
        assert not any("venv" in item for item in argv), name


# --- the declared FLOOR, decided from installed distribution metadata (in-process) -----------


@pytest.mark.parametrize("installed, expected", [
    ("0.2.0", False), ("0.2.99", False), ("0.3.0.dev1", False), ("0.3.0rc1", False), ("0.3.0a2", False),
    ("0.3.0", True), ("0.3", True), ("0.3.0+bootstrap.source", True), ("0.3.1", True),
    ("0.10.0", True), ("1", True), ("0.3.0.post1", True),
    ("not-a-version", False), ("", False),
])
def test_the_declared_floor_is_decided_by_version_not_by_importability(installed, expected):
    assert run_release_gate._requirement_satisfied("cli-extended>=0.3.0", installed) is expected


@pytest.mark.parametrize("requirement, installed, expected", [
    ("pkg>=1,<2", "1.5", True), ("pkg>=1,<2", "2.0", False), ("pkg>=1,<2", "0.9", False),
    ("pkg==1.2", "1.2.0", True), ("pkg==1.2", "1.3", False),
    ("pkg!=1.2", "1.2", False), ("pkg!=1.2", "1.3", True),
    ("pkg>1", "1", False), ("pkg>1", "1.0.1", True), ("pkg<=1", "1.0", True),
    ("pkg[extra]>=2", "2", True), ("pkg", "0.0.1", True),
])
def test_every_supported_specifier_clause_must_hold(requirement, installed, expected):
    assert run_release_gate._requirement_satisfied(requirement, installed) is expected


@pytest.mark.parametrize("requirement", ["pkg~=1.2", "pkg>=1; python_version<'3.9'", "pkg===1", "pkg>=1 <2"])
def test_a_specifier_the_controller_cannot_evaluate_is_a_loud_refusal_not_a_pass(requirement):
    with pytest.raises(RuntimeError, match="cannot evaluate"):
        run_release_gate._requirement_satisfied(requirement, "1.5")


def _switching_fakes(monkeypatch, tmp_path, *, host_version, authoritative_stdout, authoritative_rc=0):
    """Deterministic fakes for the in-process switch: no real interpreter is started."""
    launcher = tmp_path / "cmru"
    launcher.write_text("#!/opt/env/bin/python3\n", encoding="utf-8")
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: str(launcher))
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(run_release_gate, "_installed_version", lambda _name: host_version)
    probes, execs = [], []

    def fake_run(argv, **_kwargs):
        probes.append(argv)
        return subprocess.CompletedProcess(argv, authoritative_rc, stdout=authoritative_stdout, stderr="boom\n")

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(run_release_gate.os, "execv", lambda path, argv: execs.append((path, argv)))
    for name in (REEXEC_ENV, *PROTECTED_ENV):
        monkeypatch.delenv(name, raising=False)
    return probes, execs


def test_an_importable_but_below_floor_host_switches_to_an_authoritative_interpreter_at_the_floor(
    tmp_path, monkeypatch, capsys,
):
    probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.2.0", authoritative_stdout="cli-extended=0.3.0\n",
    )

    run_release_gate._ensure_gate_environment(["--worktree", "/w"])

    assert len(probes) == 1 and len(execs) == 1  # probed once, then switched once
    assert execs[0][1][:4] == ["/opt/env/bin/python3", "-I", "-B", str(SCRIPT.resolve())]
    assert "has cli-extended 0.2.0 installed, which does not satisfy cli-extended>=0.3.0" in (
        capsys.readouterr().err
    )
    assert os.environ[REEXEC_ENV] == "1"


def test_a_below_floor_authoritative_interpreter_is_refused_and_nothing_is_exec_ed(
    tmp_path, monkeypatch,
):
    _probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.2.0", authoritative_stdout="cli-extended=0.2.5\n",
    )

    with pytest.raises(RuntimeError, match="has cli-extended 0.2.5 installed, which does not satisfy"):
        run_release_gate._ensure_gate_environment(["--worktree", "/w"])

    assert execs == [] and REEXEC_ENV not in os.environ


def test_an_authoritative_interpreter_that_reports_no_version_is_refused(tmp_path, monkeypatch):
    _probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.2.0", authoritative_stdout="",
    )

    with pytest.raises(RuntimeError, match="no reported version"):
        run_release_gate._ensure_gate_environment([])

    assert execs == []


def test_an_authoritative_interpreter_that_cannot_run_the_probe_is_refused(tmp_path, monkeypatch):
    _probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.2.0", authoritative_stdout="", authoritative_rc=1,
    )

    with pytest.raises(RuntimeError, match="cannot import cli_extended either"):
        run_release_gate._ensure_gate_environment([])

    assert execs == []


def test_a_host_that_meets_the_floor_neither_probes_nor_switches(tmp_path, monkeypatch):
    probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.3.0", authoritative_stdout="",
    )

    run_release_gate._ensure_gate_environment(["--worktree", "/w"])

    assert probes == [] and execs == []


def test_an_importable_module_without_distribution_metadata_does_not_count_as_installed(
    tmp_path, monkeypatch, capsys,
):
    _probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version=None, authoritative_stdout="cli-extended=0.3.0\n",
    )

    run_release_gate._ensure_gate_environment([])

    assert len(execs) == 1
    assert "imports cli_extended but has no installed cli-extended distribution metadata" in (
        capsys.readouterr().err
    )


def test_a_second_below_floor_failure_after_the_switch_is_refused_not_looped(tmp_path, monkeypatch):
    _probes, execs = _switching_fakes(
        monkeypatch, tmp_path, host_version="0.2.0", authoritative_stdout="cli-extended=0.3.0\n",
    )
    monkeypatch.setenv(REEXEC_ENV, "1")

    with pytest.raises(RuntimeError, match="still fails the declared dependencies: has cli-extended 0.2.0"):
        run_release_gate._ensure_gate_environment([])

    assert execs == [] and REEXEC_ENV not in os.environ


def test_the_installed_version_reader_returns_none_for_an_absent_distribution():
    assert run_release_gate._installed_version("no-such-distribution-cmru-test") is None
