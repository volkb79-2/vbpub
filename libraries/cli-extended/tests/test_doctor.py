"""W5: shared doctor verb (CLI-EXT-13).  HOME is always a tmp_path."""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from cli_extended import doctor as doctor_module
from cli_extended import (
    CheckResult,
    CliIdentity,
    CliRegistry,
    DoctorCheck,
    OptionSpec,
    VerbSpec,
    register_doctor,
    register_skills_verbs,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    before = set(sys.modules)
    yield home
    for name in set(sys.modules) - before:
        if name.startswith("docpkg_"):
            del sys.modules[name]


_counter = {"n": 0}


def make_pkg(tmp_path, monkeypatch, skills=None):
    _counter["n"] += 1
    package = f"docpkg_{_counter['n']}_{os.getpid()}"
    root = tmp_path / "pkgs"
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    for name in skills or {"alpha": "Does things."}:
        target = root / package / "skills" / name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Does things.\n---\n# Body\n"
        )
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    return package


def ok(summary="fine", **kwargs):
    return CheckResult("ok", summary, **kwargs)


def check(name, result=None, *, run=None):
    return DoctorCheck(name, f"checks {name}", run or (lambda runtime, args: result))


def registry():
    return CliRegistry(
        CliIdentity("MYTOOL", "1.2.3", "My tool", "mytool"),
        prog="mytool",
        description="My tool.",
    )


def invoke(reg, *args):
    app = reg.build()
    stdout, stderr = io.StringIO(), io.StringIO()
    code = app.run(argv=["doctor", *args], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def doctor_registry(*checks):
    reg = registry()
    register_doctor(reg, list(checks))
    return reg


# ---------------------------------------------------------------- O1


def test_text_rendering_exact_and_warn_only_exits_zero():
    reg = doctor_registry(
        check("alpha", ok("all good")),
        check("beta", CheckResult("warn", "meh", remedy="do a thing")),
        check("gamma", CheckResult("skip", "not applicable")),
    )
    code, out, err = invoke(reg)
    assert code == 0
    assert out == (
        "[OK] alpha: all good\n"
        "[WARN] beta: meh\n"
        "    remedy: do a thing\n"
        "[SKIP] gamma: not applicable\n"
        "doctor: 1 ok, 1 warn, 0 fail, 1 skip\n"
    )
    assert err == ""


def test_fail_exits_one_with_remedy_line():
    reg = doctor_registry(
        check("alpha", CheckResult("fail", "broken", remedy="fix it")),
        check("beta", ok()),
    )
    code, out, _ = invoke(reg)
    assert code == 1
    assert out == (
        "[FAIL] alpha: broken\n"
        "    remedy: fix it\n"
        "[OK] beta: fine\n"
        "doctor: 1 ok, 0 warn, 1 fail, 0 skip\n"
    )


def test_json_shape_exact():
    reg = doctor_registry(
        check("alpha", ok("good", details={"n": 3})),
        check("beta", CheckResult("fail", "bad", remedy="fix")),
        check("gamma", CheckResult("warn", "hm")),
        check("delta", CheckResult("skip", "no")),
    )
    code, out, _ = invoke(reg, "--json")
    assert code == 1
    assert json.loads(out) == {
        "tool": "mytool",
        "version": "1.2.3",
        "checks": [
            {"name": "alpha", "status": "ok", "summary": "good", "remedy": None,
             "details": {"n": 3}},
            {"name": "beta", "status": "fail", "summary": "bad", "remedy": "fix",
             "details": {}},
            {"name": "gamma", "status": "warn", "summary": "hm", "remedy": None,
             "details": {}},
            {"name": "delta", "status": "skip", "summary": "no", "remedy": None,
             "details": {}},
        ],
        "summary": {"ok": 1, "warn": 1, "fail": 1, "skip": 1},
    }


def test_json_warn_only_exit_zero():
    code, out, _ = invoke(doctor_registry(check("a", CheckResult("warn", "w"))), "--json")
    assert code == 0
    assert json.loads(out)["summary"] == {"ok": 0, "warn": 1, "fail": 0, "skip": 0}


def test_verb_is_maintenance_read_only_with_json_and_check_options():
    reg = doctor_registry(check("a", ok()))
    code, out, _ = invoke(reg, "--help")
    assert code == 0
    assert "--check NAME" in out
    assert "--json" in out
    assert "--progress" not in out
    assert "--yes" not in out
    assert "check this tool's environment and report problems" in out


def test_custom_description():
    reg = registry()
    register_doctor(reg, [check("a", ok())], description="probe the host")
    _, out, _ = invoke(reg, "--help")
    assert "probe the host" in out


# ---------------------------------------------------------------- O2


def test_crashing_check_becomes_fail_and_others_run():
    def boom(runtime, args):
        raise RuntimeError("kaput")

    reg = doctor_registry(
        check("first", ok()),
        check("boom", run=boom),
        check("last", ok("still ran")),
    )
    code, out, _ = invoke(reg)
    assert code == 1
    assert out == (
        "[OK] first: fine\n"
        "[FAIL] boom: check crashed: RuntimeError: kaput\n"
        "[OK] last: still ran\n"
        "doctor: 2 ok, 0 warn, 1 fail, 0 skip\n"
    )


def test_keyboard_interrupt_propagates():
    def stop(runtime, args):
        raise KeyboardInterrupt

    reg = doctor_registry(check("stop", run=stop))
    app = reg.build()
    code = app.run(argv=["doctor"], stdout=io.StringIO(), stderr=io.StringIO())
    assert code == 130


def test_wrong_return_type_is_crash_fail():
    reg = doctor_registry(check("odd", run=lambda runtime, args: "ok"))
    code, out, _ = invoke(reg)
    assert code == 1
    assert "[FAIL] odd: check crashed: TypeError: returned str, not CheckResult\n" in out


def test_non_json_details_become_fail():
    reg = doctor_registry(check("obj", ok(details={"x": object()})))
    code, out, _ = invoke(reg)
    assert code == 1
    assert out.startswith("[FAIL] obj: check returned non-JSON details\n")


def test_circular_details_become_fail():
    loop: dict = {}
    loop["self"] = loop
    reg = doctor_registry(check("loop", ok(details=loop)))
    code, out, _ = invoke(reg, "--json")
    assert code == 1
    data = json.loads(out)
    assert data["checks"][0]["summary"] == "check returned non-JSON details"
    assert data["checks"][0]["status"] == "fail"


# ---------------------------------------------------------------- O3


def test_check_filter_runs_only_selected_in_declared_order():
    ran = []

    def make(name):
        def run(runtime, args):
            ran.append(name)
            return ok(name)

        return check(name, run=run)

    reg = doctor_registry(make("a"), make("b"), make("c"))
    code, out, _ = invoke(reg, "--check", "c", "--check", "a")
    assert code == 0
    assert ran == ["a", "c"]
    assert out == (
        "[OK] a: a\n[OK] c: c\ndoctor: 2 ok, 0 warn, 0 fail, 0 skip\n"
    )


def test_unknown_check_is_usage_error_with_help():
    reg = doctor_registry(check("a", ok()), check("b", ok()))
    code, out, err = invoke(reg, "--check", "nope")
    assert code == 2
    assert "unknown doctor check 'nope'; available: a, b" in err
    assert "usage" in (out + err).lower()


def test_unknown_check_stops_before_running_anything():
    ran = []
    reg = doctor_registry(check("a", run=lambda runtime, args: ran.append(1) or ok()))
    code, _, _ = invoke(reg, "--check", "a", "--check", "zzz")
    assert code == 2
    assert ran == []


# ---------------------------------------------------------------- O4


def run_install(reg, *args):
    app = reg.build()
    code = app.run(argv=["skills", "install", *args], stdout=io.StringIO(),
                   stderr=io.StringIO())
    assert code == 0


def build_with_skills(tmp_path, monkeypatch, *, skills_first, checks=()):
    package = make_pkg(tmp_path, monkeypatch)
    reg = registry()
    if skills_first:
        register_skills_verbs(reg, package=package)
        register_doctor(reg, list(checks))
    else:
        register_doctor(reg, list(checks))
        register_skills_verbs(reg, package=package)
    return reg


@pytest.mark.parametrize("skills_first", [True, False])
def test_skills_check_present_in_either_registration_order(
    tmp_path, monkeypatch, isolated_home, skills_first
):
    reg = build_with_skills(
        tmp_path, monkeypatch, skills_first=skills_first, checks=[check("a", ok())]
    )
    code, out, _ = invoke(reg)
    assert code == 1
    skills_dir = isolated_home / ".claude" / "skills"
    lines = out.splitlines()
    assert lines[0] == "[OK] a: fine"
    assert lines[1] == "[FAIL] skills: 2 skill(s) not current"
    assert lines[2] == "    remedy: run 'mytool skills install'"
    assert lines[3] == "doctor: 1 ok, 0 warn, 1 fail, 0 skip"
    assert not skills_dir.exists()


def test_skills_check_ok_when_all_current(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    run_install(reg)
    code, out, _ = invoke(reg)
    assert code == 0
    assert out == (
        "[OK] skills: all skills current\n"
        "doctor: 1 ok, 0 warn, 0 fail, 0 skip\n"
    )


def test_skills_json_details_and_selection(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=False)
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    assert code == 1
    data = json.loads(out)
    (entry,) = data["checks"]
    assert entry["name"] == "skills"
    assert entry["remedy"] == "run 'mytool skills install'"
    claude = str(isolated_home / ".claude" / "skills")
    agents = str(isolated_home / ".agents" / "skills")
    assert entry["details"] == {
        "skills": [
            {"name": "alpha", "destination": agents, "state": "absent"},
            {"name": "alpha", "destination": claude, "state": "absent"},
        ],
        "leftovers": [],
    }


def test_skills_stale_fails(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    run_install(reg)
    skill_md = tmp_path / "pkgs"
    source = next(skill_md.rglob("SKILL.md"))
    source.write_text(source.read_text() + "more\n")
    code, out, _ = invoke(reg, "--check", "skills")
    assert code == 1
    assert out == (
        "[FAIL] skills: 2 skill(s) not current\n"
        "    remedy: run 'mytool skills install'\n"
        "doctor: 0 ok, 0 warn, 1 fail, 0 skip\n"
    )


def test_skills_orphan_fails(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    run_install(reg)
    package_dir = next((tmp_path / "pkgs").iterdir())
    source = package_dir / "skills" / "alpha"
    # Rename the packaged skill so the installed copy becomes an orphan.
    source.rename(package_dir / "skills" / "beta")
    (package_dir / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Does things.\n---\n# Body\n"
    )
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    assert code == 1
    states = {
        (row["name"], row["state"])
        for row in json.loads(out)["checks"][0]["details"]["skills"]
    }
    assert states == {("alpha", "orphaned"), ("beta", "absent")}


def test_skills_leftover_alone_fails(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    run_install(reg)
    leftover = isolated_home / ".claude" / "skills" / (
        ".alpha.cli-extended-mytool-tmp-0123456789abcdef"
    )
    leftover.mkdir()
    other = isolated_home / ".claude" / "skills" / (
        ".alpha.cli-extended-other-tmp-0123456789abcdef"
    )
    other.mkdir()
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    assert code == 1
    entry = json.loads(out)["checks"][0]
    assert entry["summary"] == "1 leftover path(s)"
    assert entry["remedy"] == "run 'mytool skills install'"
    assert entry["details"]["leftovers"] == [str(leftover)]


def test_skills_not_current_and_leftover_combined(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    skills_dir = isolated_home / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / ".alpha.cli-extended-mytool-old-0123456789abcdef").mkdir()
    code, out, _ = invoke(reg, "--check", "skills")
    assert code == 1
    assert out.splitlines()[0] == "[FAIL] skills: 2 skill(s) not current; 1 leftover path(s)"


def test_skills_source_error_is_crash_fail(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    package_dir = next((tmp_path / "pkgs").iterdir())
    (package_dir / "skills" / "alpha" / "SKILL.md").write_text("no frontmatter\n")
    code, out, _ = invoke(reg, "--check", "skills")
    assert code == 1
    assert out.startswith("[FAIL] skills: check crashed: SkillError: ")


def test_skills_verbs_refuse_existing_doctor_check_named_skills(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch)
    reg = registry()
    register_doctor(reg, [check("a", ok()), check("skills", ok())])
    with pytest.raises(ValueError, match="'skills' is reserved"):
        register_skills_verbs(reg, package=package)
    assert not hasattr(reg, "_cli_extended_skills")


def test_skills_verbs_accept_doctor_with_other_check_names(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch)
    reg = registry()
    register_doctor(reg, [check("a", ok())])
    register_skills_verbs(reg, package=package)
    assert hasattr(reg, "_cli_extended_skills")


def test_one_stale_one_current_counts_one(tmp_path, monkeypatch, isolated_home):
    package = make_pkg(
        tmp_path, monkeypatch, {"alpha": "Does things.", "beta": "Does things."}
    )
    reg = registry()
    register_skills_verbs(reg, package=package)
    register_doctor(reg, [])
    only = isolated_home / "only"
    monkeypatch.setattr(doctor_module, "default_skill_destinations", lambda: [only])
    assert reg.build().run(
        argv=["skills", "install", "--dest", str(only)],
        stdout=io.StringIO(), stderr=io.StringIO(),
    ) == 0
    source = tmp_path / "pkgs" / package / "skills" / "alpha" / "SKILL.md"
    source.write_text(source.read_text() + "changed\n")
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    assert code == 1
    entry = json.loads(out)["checks"][0]
    assert entry["summary"] == "1 skill(s) not current"
    assert [(r["name"], r["state"]) for r in entry["details"]["skills"]] == [
        ("alpha", "stale"), ("beta", "current"),
    ]


def test_skills_details_order_is_destination_then_name(
    tmp_path, monkeypatch, isolated_home
):
    package = make_pkg(
        tmp_path, monkeypatch, {"zeta": "Does things.", "alpha": "Does things."}
    )
    reg = registry()
    register_skills_verbs(reg, package=package)
    register_doctor(reg, [])
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    assert code == 1
    entry = json.loads(out)["checks"][0]
    assert entry["summary"] == "4 skill(s) not current"
    agents = str(isolated_home / ".agents" / "skills")
    claude = str(isolated_home / ".claude" / "skills")
    assert [(r["destination"], r["name"]) for r in entry["details"]["skills"]] == [
        (agents, "alpha"), (agents, "zeta"), (claude, "alpha"), (claude, "zeta"),
    ]


def test_doctor_listed_under_maintenance_in_top_level_help():
    reg = doctor_registry(check("a", ok()))
    reg.register(VerbSpec("zzz", description="other", handler=lambda a, rt: 0))
    app = reg.build()
    out = io.StringIO()
    assert app.run(argv=["--help"], stdout=out, stderr=io.StringIO()) == 0
    assert "MAINTENANCE\n  doctor  check this tool's environment" in out.getvalue()
    assert "EXPLORATION\n  zzz " in out.getvalue()


def test_multiline_exception_message_collapses_and_others_run():
    def boom(runtime, args):
        raise ValueError("line one\n  line two\r\n\tline three  ")

    reg = doctor_registry(check("a", ok()), check("boom", run=boom), check("z", ok()))
    code, out, _ = invoke(reg)
    assert code == 1
    assert out == (
        "[OK] a: fine\n"
        "[FAIL] boom: check crashed: ValueError: line one line two line three\n"
        "[OK] z: fine\n"
        "doctor: 2 ok, 0 warn, 1 fail, 0 skip\n"
    )


@pytest.mark.parametrize("message", ["", "  \n "])
def test_empty_exception_message_omits_separator(message):
    def boom(runtime, args):
        raise RuntimeError(message)

    reg = doctor_registry(check("boom", run=boom), check("z", ok()))
    code, out, _ = invoke(reg)
    assert code == 1
    assert out == (
        "[FAIL] boom: check crashed: RuntimeError\n"
        "[OK] z: fine\n"
        "doctor: 1 ok, 0 warn, 1 fail, 0 skip\n"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_details_become_fail(value):
    reg = doctor_registry(check("n", ok(details={"x": value})), check("z", ok()))
    code, out, _ = invoke(reg, "--json")
    assert code == 1
    data = json.loads(out)
    assert data["checks"][0]["status"] == "fail"
    assert data["checks"][0]["summary"] == "check returned non-JSON details"
    assert data["checks"][0]["details"] == {}
    assert data["checks"][1]["status"] == "ok"


def test_multiline_remedy_is_collapsed_not_rejected():
    reg = doctor_registry(
        check("a", CheckResult("warn", "meh", remedy="first\n second\r\nthird")),
        check("b", CheckResult("warn", "hm", remedy=" \n ")),
        check("z", ok()),
    )
    code, out, _ = invoke(reg)
    assert code == 0
    assert out == (
        "[WARN] a: meh\n"
        "    remedy: first second third\n"
        "[WARN] b: hm\n"
        "[OK] z: fine\n"
        "doctor: 1 ok, 2 warn, 0 fail, 0 skip\n"
    )
    _, out, _ = invoke(reg, "--json")
    remedies = [c["remedy"] for c in json.loads(out)["checks"]]
    assert remedies == ["first second third", None, None]


def test_no_skills_check_without_skills_verbs():
    code, out, err = invoke(doctor_registry(check("a", ok())), "--check", "skills")
    assert code == 2
    assert "unknown doctor check 'skills'; available: a" in err


def test_claude_config_dir_is_respected(tmp_path, monkeypatch, isolated_home):
    reg = build_with_skills(tmp_path, monkeypatch, skills_first=True)
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    code, out, _ = invoke(reg, "--check", "skills", "--json")
    destinations = {
        row["destination"] for row in json.loads(out)["checks"][0]["details"]["skills"]
    }
    assert destinations == {
        str(config / "skills"),
        str(isolated_home / ".agents" / "skills"),
    }


# ---------------------------------------------------------------- O5


def test_result_validation():
    with pytest.raises(ValueError, match="status must be one of ok, warn, fail, skip"):
        CheckResult("good", "x")
    for summary in ("", "two\nlines", "cr\rline", 5):
        with pytest.raises(ValueError, match="non-empty single line"):
            CheckResult("ok", summary)
    result = CheckResult("ok", "x")
    assert result.remedy is None
    assert result.details == {}
    with pytest.raises(Exception):
        result.status = "fail"  # type: ignore[misc]


def test_check_validation():
    for name in ("Bad", "", "a b", "a--b", "-a", "a-", "a_b"):
        with pytest.raises(ValueError, match="must be lowercase tokens"):
            DoctorCheck(name, "d", lambda runtime, args: ok())
    with pytest.raises(ValueError, match="must define a description"):
        DoctorCheck("a", "", lambda runtime, args: ok())
    assert DoctorCheck("a1-b2", "d", lambda runtime, args: ok()).name == "a1-b2"


def test_check_reads_consumer_option_from_args():
    seen = []

    def run(runtime, args):
        seen.append(args.helper_image)
        return ok(f"image {args.helper_image}")

    reg = registry()
    register_doctor(
        reg,
        [check("helper", run=run)],
        options=[
            OptionSpec(
                ("--helper-image",), "helper image", metavar="IMAGE",
                parser_kwargs={"default": "none"},
            )
        ],
    )
    code, out, _ = invoke(reg, "--helper-image", "img:1")
    assert code == 0
    assert seen == ["img:1"]
    assert out.startswith("[OK] helper: image img:1\n")
    _, out, _ = invoke(reg)
    assert seen == ["img:1", "none"]
    _, out, _ = invoke(reg, "--help")
    assert "--helper-image IMAGE" in out


@pytest.mark.parametrize("flag", ["--check", "-h", "--help", "--json", "--yes", "--log-level"])
def test_option_shadowing_check_or_library_control_rejected(flag):
    reg = registry()
    with pytest.raises(ValueError, match="shadows --check or a library control"):
        register_doctor(
            reg, [check("a", ok())], options=[OptionSpec((flag,), "dup")]
        )
    assert not hasattr(reg, "_cli_extended_doctor")


def test_shadowing_alias_among_several_flags_rejected():
    with pytest.raises(ValueError, match="'--check'"):
        register_doctor(
            registry(), [], options=[OptionSpec(("--mine", "--check"), "dup")]
        )


def test_duplicate_names_rejected():
    with pytest.raises(ValueError, match="duplicate doctor check 'a'"):
        register_doctor(registry(), [check("a", ok()), check("a", ok())])


def test_double_registration_rejected():
    reg = doctor_registry(check("a", ok()))
    with pytest.raises(ValueError, match="doctor is already registered"):
        register_doctor(reg, [check("b", ok())])


def test_skills_named_check_rejected_when_skills_registered(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch)
    reg = registry()
    register_skills_verbs(reg, package=package)
    with pytest.raises(ValueError, match="'skills' is reserved"):
        register_doctor(reg, [check("skills", ok())])


def test_skills_named_check_allowed_without_skills_registered():
    reg = doctor_registry(check("skills", ok("mine")))
    code, out, _ = invoke(reg)
    assert code == 0
    assert out.startswith("[OK] skills: mine\n")


def test_empty_checks_list_runs_clean():
    code, out, _ = invoke(doctor_registry())
    assert code == 0
    assert out == "doctor: 0 ok, 0 warn, 0 fail, 0 skip\n"


def test_doctor_inside_nested_registry_stdout_only():
    reg = doctor_registry(check("a", ok()))
    _, out, err = invoke(reg)
    assert out != ""
    assert err == ""


def test_home_is_tmp(isolated_home, tmp_path):
    assert Path.home() == isolated_home
    assert tmp_path in isolated_home.parents
