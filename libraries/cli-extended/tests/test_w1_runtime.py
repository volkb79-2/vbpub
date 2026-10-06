"""W1: version resolver, exception boundary and dry-run."""

from __future__ import annotations

import io
from importlib.metadata import PackageNotFoundError

import pytest

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbSpec,
    VersionLookupError,
)
from cli_extended import identity as identity_module
from cli_extended import parser as parser_module

IDENT = CliIdentity("TOOL", "1.0.0", "Tool CLI", "tool")


# ---------------------------------------------------------------- O1 resolve


def _installed(monkeypatch, mapping):
    def fake(name):
        if name not in mapping:
            raise PackageNotFoundError(name)
        return mapping[name]

    monkeypatch.setattr(identity_module, "installed_version", fake)


def _resolve(**kw):
    return CliIdentity.resolve(name="T", long_name="Tool", **kw)


def test_resolve_distribution_only(monkeypatch):
    _installed(monkeypatch, {"dist": "2.3.4"})
    ident = _resolve(distribution="dist", command="tt")
    assert (ident.version, ident.command, ident.name, ident.long_name) == (
        "2.3.4", "tt", "T", "Tool",
    )


def test_resolve_file_only_strips_and_accepts_suffixes(monkeypatch, tmp_path):
    _installed(monkeypatch, {})
    f = tmp_path / "VERSION"
    f.write_text("  1.2.3-rc.1+b5\n", encoding="utf-8")
    assert _resolve(version_file=f).version == "1.2.3-rc.1+b5"
    assert _resolve(version_file=str(f), distribution="absent").version == "1.2.3-rc.1+b5"


def test_resolve_both_agree(monkeypatch, tmp_path):
    _installed(monkeypatch, {"dist": "1.2.3"})
    f = tmp_path / "VERSION"
    f.write_text("1.2.3\n", encoding="utf-8")
    assert _resolve(distribution="dist", version_file=f).version == "1.2.3"


def test_resolve_both_disagree_names_both_values(monkeypatch, tmp_path):
    _installed(monkeypatch, {"dist": "1.2.3"})
    f = tmp_path / "VERSION"
    f.write_text("1.2.4\n", encoding="utf-8")
    with pytest.raises(VersionLookupError) as info:
        _resolve(distribution="dist", version_file=f)
    text = str(info.value)
    assert "'1.2.3'" in text and "'1.2.4'" in text
    assert "distribution 'dist'" in text and str(f) in text


def test_resolve_neither_lists_every_source(monkeypatch, tmp_path):
    _installed(monkeypatch, {})
    f = tmp_path / "VERSION"
    with pytest.raises(VersionLookupError) as info:
        _resolve(distribution="x", version_file=f)
    assert str(info.value) == (
        f"distribution 'x' is not installed; version file '{f}' does not exist"
    )


def test_resolve_requires_a_source():
    with pytest.raises(ValueError) as info:
        _resolve()
    assert str(info.value) == (
        "CliIdentity.resolve needs a distribution, a version_file, "
        "a version_probe, or a combination"
    )


# ------------------------------------------------ CLI-EXT-24 version_probe


def test_cx24_probe_none_is_unresolved_and_listed(monkeypatch):
    _installed(monkeypatch, {})
    with pytest.raises(VersionLookupError) as info:
        _resolve(distribution="x", version_probe=lambda: None)
    assert str(info.value) == (
        "distribution 'x' is not installed; version probe returned None"
    )


def test_cx24_probe_resolves_without_installed_distribution(monkeypatch):
    _installed(monkeypatch, {})
    ident = _resolve(distribution="x", version_probe=lambda: " 3.4.5-12-gabc \n")
    assert ident.version == "3.4.5-12-gabc"
    assert _resolve(version_probe=lambda: "1.0.0").version == "1.0.0"


def test_cx24_probe_agreeing_with_metadata_resolves(monkeypatch):
    _installed(monkeypatch, {"dist": "1.2.3"})
    assert _resolve(distribution="dist", version_probe=lambda: "1.2.3").version == "1.2.3"


def test_cx24_probe_disagreeing_with_metadata_names_both_values(monkeypatch):
    _installed(monkeypatch, {"dist": "7.20.0"})
    with pytest.raises(VersionLookupError) as info:
        _resolve(distribution="dist", version_probe=lambda: "7.21.0")
    text = str(info.value)
    assert "'7.20.0'" in text and "'7.21.0'" in text
    assert "distribution 'dist'" in text and "version probe" in text


def test_cx24_probe_disagreeing_with_version_file_is_refused(monkeypatch, tmp_path):
    _installed(monkeypatch, {})
    f = tmp_path / "VERSION"
    f.write_text("1.0.0\n", encoding="utf-8")
    with pytest.raises(VersionLookupError, match="disagree"):
        _resolve(version_file=f, version_probe=lambda: "1.0.1")


@pytest.mark.parametrize("value", ["", "v1.2.3", "1.2", 7])
def test_cx24_malformed_probe_value_is_an_error(monkeypatch, value):
    _installed(monkeypatch, {})
    with pytest.raises(VersionLookupError, match="probe returned malformed value"):
        _resolve(version_probe=lambda: value)


def test_cx24_raising_probe_is_a_lookup_error_with_cause(monkeypatch):
    _installed(monkeypatch, {})

    def boom():
        raise OSError("git missing")

    with pytest.raises(VersionLookupError, match="version probe failed: git missing") as info:
        _resolve(version_probe=boom)
    assert isinstance(info.value.__cause__, OSError)


def test_resolve_relative_path_is_refused():
    with pytest.raises(ValueError, match="absolute path.*'rel/VERSION'"):
        _resolve(version_file="rel/VERSION")


@pytest.mark.parametrize("content", ["", "1.2", "v1.2.3", "1.2.3 junk", "1.2.3\n4"])
def test_resolve_malformed_file_is_an_error(monkeypatch, tmp_path, content):
    _installed(monkeypatch, {"dist": "1.2.3"})
    f = tmp_path / "VERSION"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(VersionLookupError, match="malformed content") as info:
        _resolve(distribution="dist", version_file=f)
    assert str(f) in str(info.value)


def test_resolve_unreadable_file_is_a_lookup_error(monkeypatch, tmp_path):
    _installed(monkeypatch, {})
    with pytest.raises(VersionLookupError, match="cannot read version file") as info:
        _resolve(version_file=tmp_path)  # a directory
    assert isinstance(info.value.__cause__, OSError)


# ------------------------------------------------------------ helpers


def _cli(handler, *, verbs=None, **registry_kw):
    registry = CliRegistry(IDENT, prog="tool", description="Tool.", **registry_kw)
    for verb in verbs or [VerbSpec("go", description="Go.", handler=handler)]:
        registry.register(verb)
    return registry.build()


def _run(cli, argv, stdin=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv=argv, stdout=out, stderr=err, stdin=stdin or io.StringIO(), **kw)
    return code, out.getvalue(), err.getvalue()


def _boom(args, runtime):
    raise RuntimeError("boom")


# ------------------------------------------------------------ O2 report


def test_report_mode_reports_unexpected_exception():
    code, out, err = _run(_cli(_boom, unexpected_exceptions="report"), ["go"])
    assert code == 1
    assert "unexpected RuntimeError: boom" in err
    assert "rerun with --traceback to see the stack" in err
    assert "Traceback" not in err and "Traceback" not in out


@pytest.mark.parametrize("argv", (["go", "--traceback"], ["--traceback", "go"]))
def test_report_mode_traceback_flag_propagates(argv):
    with pytest.raises(RuntimeError, match="boom"):
        _run(_cli(_boom, unexpected_exceptions="report"), argv)


def test_raise_mode_propagates_and_has_no_traceback_option():
    cli = _cli(_boom)
    with pytest.raises(RuntimeError, match="boom"):
        _run(cli, ["go"])
    for argv in (["go", "--traceback"], ["--traceback", "go"]):
        code, _, err = _run(cli, argv)
        assert code == 2
        assert "--traceback" in err


def test_report_mode_without_runtime_uses_help_output(monkeypatch):
    # Runtime creation fails with an unexpected error before the handler.
    cli = _cli(lambda a, r: 0, unexpected_exceptions="report")

    def explode(*a, **k):
        raise RuntimeError("early")

    monkeypatch.setattr(parser_module, "_runtime_from_args", explode)
    code, _, err = _run(cli, ["go"])
    assert code == 1
    assert "unexpected RuntimeError: early" in err
    assert "rerun with --traceback" in err


def test_unexpected_policy_validation():
    with pytest.raises(ValueError, match="'raise' or 'report'"):
        CliRegistry(IDENT, prog="t", description="d", unexpected_exceptions="swallow")
    with pytest.raises(TypeError, match="tuple"):
        CliRegistry(IDENT, prog="t", description="d", expected_exceptions=[KeyError])
    with pytest.raises(ValueError, match="'raise' or 'report'"):
        CliRegistry(IDENT, prog="t", description="d", unexpected_exceptions="")
    cli = _cli(lambda a, r: 0)
    for bad in ("nope", "Report", "RAISE"):
        with pytest.raises(ValueError, match="'raise' or 'report'"):
            parser_module.run_cli(
                cli.parser, cli.handlers, identity=IDENT, argv=["go"],
                unexpected_exceptions=bad,
            )
    for good in ("raise", "report"):
        assert parser_module.run_cli(
            cli.parser, cli.handlers, identity=IDENT, argv=["go"],
            stdout=io.StringIO(), stderr=io.StringIO(), unexpected_exceptions=good,
        ) == 0


def test_run_policy_is_set_on_the_registry_not_on_run():
    cli = _cli(lambda a, r: 0, unexpected_exceptions="report")
    with pytest.raises(TypeError) as info:
        cli.run(argv=["go"], unexpected_exceptions="raise")
    assert str(info.value) == "unexpected_exceptions is set on CliRegistry, not run()"


def test_run_cli_policy_values_decide_reporting():
    def boom_cli():
        registry = CliRegistry(IDENT, prog="tool", description="Tool.")
        registry.register(VerbSpec("go", description="Go.", handler=_boom))
        return registry.build()

    cli = boom_cli()
    with pytest.raises(RuntimeError, match="boom"):
        parser_module.run_cli(
            cli.parser, cli.handlers, identity=IDENT, argv=["go"],
            stdout=io.StringIO(), stderr=io.StringIO(),
            unexpected_exceptions="raise",
        )
    err = io.StringIO()
    code = parser_module.run_cli(
        cli.parser, cli.handlers, identity=IDENT, argv=["go"],
        stdout=io.StringIO(), stderr=err, unexpected_exceptions="report",
    )
    assert code == 1 and "unexpected RuntimeError: boom" in err.getvalue()


def test_registered_cli_stores_policies():
    cli = _cli(
        lambda a, r: 0, expected_exceptions=(KeyError,), unexpected_exceptions="report"
    )
    assert cli.expected_exceptions == (KeyError,)
    assert cli.unexpected_exceptions == "report"
    plain = _cli(lambda a, r: 0)
    assert plain.expected_exceptions == () and plain.unexpected_exceptions == "raise"


# ------------------------------------------------------------ O3 union


def test_expected_exceptions_union_of_registry_and_run_kwarg():
    def handler(args, runtime):
        raise {"k": KeyError("k"), "v": ValueError("v"), "r": RuntimeError("r")}[args.kind]

    verb = VerbSpec(
        "go",
        description="Go.",
        handler=handler,
        configure=lambda p: p.add_argument("kind"),
    )
    cli = _cli(None, verbs=[verb], expected_exceptions=(KeyError,))
    for kind in ("k", "v"):
        code, _, err = _run(cli, ["go", kind], expected_exceptions=(ValueError,))
        assert code == 1 and "[ERROR]" in err
    with pytest.raises(RuntimeError):
        _run(cli, ["go", "r"], expected_exceptions=(ValueError,))


# ------------------------------------------------------------ O4 dry-run


class _TtyIn(io.StringIO):
    def isatty(self):
        return True


def _dry_cli(seen):
    def handler(args, runtime):
        seen.append(runtime.dry_run)
        seen.append(runtime.confirm("Apply?"))
        return 0

    return _cli(
        None,
        verbs=[
            VerbSpec("apply", description="Apply.", mutating=True, dry_run=True,
                     handler=handler),
            VerbSpec("show", description="Show.", handler=lambda a, r: 0),
        ],
    )


@pytest.mark.parametrize(
    "argv",
    (["apply", "--dry-run"], ["--dry-run", "apply"],
     ["apply", "--dry-run", "--yes"], ["--dry-run", "apply", "--yes"]),
)
def test_dry_run_sets_runtime_and_confirm_declines(argv):
    seen: list = []
    stdin = _TtyIn("y\n")
    code, _, err = _run(_dry_cli(seen), argv, stdin=stdin)
    assert code == 0
    assert seen == [True, False]
    assert "Dry run: no changes made." in err
    assert "[y/N]" not in err and "--yes" not in err
    assert stdin.read() == "y\n"


@pytest.mark.parametrize(
    "extra", (["--quiet"], ["--log-level", "error"]), ids=("quiet", "log-level-error")
)
def test_dry_run_message_is_forced_past_verbosity(extra):
    seen: list = []
    stdin = _TtyIn("y\n")
    code, _, err = _run(_dry_cli(seen), ["apply", "--dry-run", *extra], stdin=stdin)
    assert code == 0 and seen == [True, False]
    assert "Dry run: no changes made." in err
    assert stdin.read() == "y\n"


def test_without_dry_run_confirm_still_prompts():
    seen: list = []
    code, _, err = _run(_dry_cli(seen), ["apply"], stdin=_TtyIn("y\n"))
    assert code == 0 and seen == [False, True]
    assert "Dry run" not in err


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        # Mirrors --json: after the verb argparse itself refuses, before it the
        # library's CliFailure does. Both exit 2 with that verb's help.
        (["show", "--dry-run"], "tool show: unrecognized arguments: --dry-run"),
        (["--dry-run", "show"], "--dry-run is not supported for verb 'show'"),
    ),
)
def test_unsupported_verb_refuses_dry_run_with_verb_help(argv, message):
    code, _, err = _run(_dry_cli([]), argv)
    assert code == 2
    assert message in err
    assert "usage: tool show" in err


def test_single_command_dry_run():
    seen: list = []

    def handler(args, runtime):
        seen.append(runtime.dry_run)
        return 0

    verb = VerbSpec("only", description="Only.", mutating=True, dry_run=True,
                    handler=handler)
    cli = _cli(None, verbs=[verb], single_command=True, no_args_action=True)
    assert _run(cli, ["--dry-run"])[0] == 0
    assert seen == [True]
    assert _run(cli, [])[0] == 0
    assert seen == [True, False]
    plain = _cli(lambda a, r: 0, single_command=True)
    code, _, err = _run(plain, ["--dry-run"])
    assert code == 2 and "--dry-run" in err


def test_cx21_per_verb_dry_run_help_sentence():
    generic = "show what would change without changing anything"
    cli = _cli(
        None,
        verbs=[
            VerbSpec("apply", description="Apply.", mutating=True, dry_run=True,
                     dry_run_help="print the repo URL that would be written",
                     handler=lambda a, r: 0),
            VerbSpec("wipe", description="Wipe.", mutating=True, dry_run=True,
                     handler=lambda a, r: 0),
        ],
    )
    _, apply_out, apply_err = _run(cli, ["apply", "--help"])
    apply_text = apply_out + apply_err
    assert "print the repo URL that would be written" in apply_text
    assert generic not in apply_text
    _, wipe_out, wipe_err = _run(cli, ["wipe", "--help"])
    assert generic in wipe_out + wipe_err
    assert "print the repo URL" not in wipe_out + wipe_err


def test_cx21_dry_run_help_validation_and_single_command():
    with pytest.raises(ValueError, match="dry_run_help but not dry_run=True"):
        VerbSpec("x", description="d", mutating=True, dry_run_help="why")
    for bad in ("", "  ", "a\nb"):
        with pytest.raises(ValueError, match="non-empty single-line"):
            VerbSpec("x", description="d", mutating=True, dry_run=True,
                     dry_run_help=bad)
    verb = VerbSpec("only", description="Only.", mutating=True, dry_run=True,
                    dry_run_help="preview the single change",
                    handler=lambda a, r: 0)
    cli = _cli(None, verbs=[verb], single_command=True)
    _, out, err = _run(cli, ["--help"])
    assert "preview the single change" in out + err


def test_dry_run_requires_mutating_and_bool():
    with pytest.raises(ValueError) as info:
        VerbSpec("x", description="d", dry_run=True)
    assert str(info.value) == "verb 'x' declares dry_run but is not marked mutating"
    with pytest.raises(TypeError, match="dry_run must be a bool"):
        VerbSpec("x", description="d", mutating=True, dry_run=1)
    assert VerbSpec("x", description="d", mutating=True).dry_run is False


def test_dry_run_shadowing_rules():
    own = OptionSpec(("--dry-run",), "mine", parser_kwargs={"action": "store_true"})
    dry = VerbSpec("a", description="A.", mutating=True, dry_run=True,
                   handler=lambda a, r: 0)
    with pytest.raises(ValueError, match="VerbSpec\\(dry_run=True\\)"):
        _cli(None, verbs=[dry], global_options=(own,))
    local = VerbSpec("b", description="B.", options=(own,), handler=lambda a, r: 0)
    with pytest.raises(ValueError, match="verb 'b'.*VerbSpec\\(dry_run=True\\)"):
        _cli(None, verbs=[dry, local])
    # Legal while no verb opts in.
    cli = _cli(None, verbs=[local])
    assert "--dry-run" in cli.command_parsers["b"]._option_string_actions


def test_traceback_shadowing_rules():
    own = OptionSpec(("--traceback",), "mine", parser_kwargs={"action": "store_true"})
    verb = VerbSpec("a", description="A.", options=(own,), handler=lambda a, r: 0)
    with pytest.raises(ValueError, match="verb 'a'.*library provides"):
        _cli(None, verbs=[verb], unexpected_exceptions="report")
    plain = VerbSpec("a", description="A.", handler=lambda a, r: 0)
    with pytest.raises(ValueError, match="library provides"):
        _cli(None, verbs=[plain], unexpected_exceptions="report", global_options=(own,))
    assert _cli(None, verbs=[verb]).command_parsers["a"]  # raise mode: legal


def test_delegate_policy_mismatch():
    child = _cli(lambda a, r: 0, unexpected_exceptions="report")
    verb = VerbSpec("sub", description="Sub.", delegate=child)
    with pytest.raises(ValueError, match="verb 'sub'"):
        _cli(None, verbs=[verb])
    parent = _cli(None, verbs=[verb], unexpected_exceptions="report")
    code, _, err = _run(parent, ["sub", "go"])
    assert code == 0


# ------------------------------------------------------------ O5 help


def test_traceback_is_listed_directly_after_debug_raw():
    registry = CliRegistry(IDENT, prog="tool", description="Tool.",
                           unexpected_exceptions="report")
    registry.register(VerbSpec("show", description="Show.", handler=lambda a, r: 0))
    cli = registry.build()
    _, text, _ = _run(cli, ["show", "--help"])
    flags = [
        line.split()[0] for line in text.splitlines()
        if line.strip().startswith("--")
    ]
    assert flags[flags.index("--debug-raw") + 1] == "--traceback"
    md = cli.catalog.render(output_format="markdown")
    rows = [line for line in md.splitlines() if line.startswith("| `--")]
    names = [line.split("`")[1] for line in rows]
    assert names[names.index("--debug-raw") + 1] == "--traceback"
    debugging = md.split("#### Debugging")[1].split("####")[0]
    assert "--traceback" in debugging


def test_help_and_markdown_show_new_controls():
    registry = CliRegistry(IDENT, prog="tool", description="Tool.",
                           unexpected_exceptions="report")
    registry.register(VerbSpec("apply", description="Apply.", mutating=True,
                               dry_run=True, handler=lambda a, r: 0))
    registry.register(VerbSpec("show", description="Show.", handler=lambda a, r: 0))
    cli = registry.build()
    code, out, _ = _run(cli, ["apply", "--help"])
    assert code == 0
    assert "--dry-run" in out and "show what would change" in out
    assert "--traceback" in out and "DEBUGGING" in out and "CONFIRMATION" in out
    assert "Behavior: mutating; dry-run." in out
    _, top, _ = _run(cli, ["--help"])
    assert "--traceback" in top and "--dry-run" in top
    assert "[mutating; dry-run]" in top
    md = cli.catalog.render(output_format="markdown")
    assert "| `--dry-run` | show what would change without changing anything |" in md
    assert "| `--traceback` | show the Python stack for an unexpected error |" in md
    assert "**Behavior:** mutating; dry-run." in md
    _, show_help, _ = _run(cli, ["show", "--help"])
    assert "--dry-run" not in show_help and "--traceback" in show_help


# ------------------------------------------- CLI-EXT-29: exit code and hint


class _Refused(Exception):
    exit_code = 3
    hint = "ask an owner"


class _BadAttrs(Exception):
    exit_code = True  # bool is not an exit code
    hint = ""


class _Plain(Exception):
    pass


def _raiser(exc):
    def handler(args, runtime):
        raise exc

    return handler


def test_cx29_expected_exception_carries_exit_code_and_hint():
    cli = _cli(
        _raiser(_Refused("denied")),
        expected_exceptions=(_Refused,), unexpected_exceptions="report",
    )
    code, _out, err = _run(cli, ["go"])
    assert code == 3
    assert "[ERROR] denied" in err
    assert "Hint: ask an owner" in err
    assert "unexpected" not in err


def test_cx29_plain_tuples_keep_exit_one_and_no_hint():
    cli = _cli(_raiser(_Plain("nope")), expected_exceptions=(_Plain,))
    code, _out, err = _run(cli, ["go"])
    assert code == 1
    assert "[ERROR] nope" in err
    assert "Hint:" not in err


def test_cx29_invalid_attributes_fall_back_to_the_plain_rendering():
    cli = _cli(_raiser(_BadAttrs("bad")), expected_exceptions=(_BadAttrs,))
    code, _out, err = _run(cli, ["go"])
    assert code == 1
    assert "Hint:" not in err


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 1), (-2, 1), (300, 1), (True, 1), (False, 1), (1, 1), (255, 255), (7, 7)],
)
def test_cx29_exit_codes_outside_1_to_255_fall_back_to_one(value, expected):
    class _Coded(Exception):
        exit_code = value

    cli = _cli(_raiser(_Coded("x")), expected_exceptions=(_Coded,))
    assert _run(cli, ["go"])[0] == expected
    cli = _cli(_raiser(CliFailure("x", exit_code=value)))
    assert _run(cli, ["go"])[0] == expected


# --------------------------- CLI-EXT-23: identity banner and parser-error help


def _fail(args, runtime):
    runtime.output.error("first")
    runtime.output.error("second")
    return 1


def _opts_cli(**kw):
    verb = VerbSpec(
        "go", description="Go.", handler=_fail,
        options=(OptionSpec(("--n",), "a number", parser_kwargs={"type": int}),),
    )
    return _cli(None, verbs=[verb], **kw)


def test_cx23_defaults_are_unchanged_banner_once_and_full_help():
    code, _out, err = _run(_opts_cli(), ["go"])
    assert code == 1
    assert err.count(IDENT.headline) == 1
    assert err.index(IDENT.headline) > err.index("first")  # banner after 1st error
    assert err.rstrip().splitlines()[-1].endswith("second")
    code, _out, err = _run(_opts_cli(), ["go", "--n", "x"])
    assert code == 2
    assert "usage: tool go" in err
    assert "Run 'tool go --help'" not in err
    assert len(err.splitlines()) > 6  # the whole help block


def test_cx23_identity_banner_never_leaves_the_error_last():
    code, _out, err = _run(_opts_cli(identity_banner="never"), ["go"])
    assert code == 1
    assert IDENT.headline not in err
    assert err.rstrip().splitlines()[-1].endswith("second")


def test_cx23_error_help_usage_prints_usage_and_a_pointer_with_exit_two():
    code, _out, err = _run(_opts_cli(error_help="usage"), ["go", "--n", "x"])
    assert code == 2
    assert "tool go: argument --n: invalid int value: 'x'" in err
    lines = err.rstrip().splitlines()
    assert lines[-1] == "Run 'tool go --help' for full help."
    assert sum(line.startswith("usage: tool go") for line in lines) == 1
    assert "options:" not in err and "OPTIONS" not in err
    assert IDENT.headline not in err


def test_cx23_error_help_usage_covers_unknown_verb_and_missing_verb():
    for argv in (["help", "nope"], ["nope"], ["go", "--bogus"]):
        code, _out, err = _run(_opts_cli(error_help="usage"), argv)
        assert code == 2, argv
        assert "for full help." in err, argv
        assert IDENT.headline not in err, argv


@pytest.mark.parametrize("kw", [{"identity_banner": "sometimes"}, {"error_help": "short"}])
def test_cx23_invalid_policies_are_refused(kw):
    with pytest.raises(ValueError, match="must be one of"):
        CliRegistry(IDENT, prog="t", description="d", **kw)
    from cli_extended.output import CliOutput

    with pytest.raises(ValueError, match="must be one of"):
        CliOutput(IDENT, **kw)
