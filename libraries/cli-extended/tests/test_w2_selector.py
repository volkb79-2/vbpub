"""W2: the SelectorList value type (CLI-EXT-12)."""

from __future__ import annotations

import argparse
import copy
import io

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    ReviewCase,
    ReviewCatalog,
    SelectorList,
    VerbSpec,
    export_cli_surface,
)
from cli_extended import values as values_module
from cli_extended.review import _review_findings
from cli_extended.surface import SELECTOR_LIST_LABEL

IDENT = CliIdentity("TOOL", "1.0.0", "Tool CLI", "tool")
ROUTE = "route:entrypoint:tool/pick"
NAMES = ("alpha", "beta", "gamma")


def _reject(selector, text):
    with pytest.raises(argparse.ArgumentTypeError) as info:
        selector(text)
    return str(info.value)


# ------------------------------------------------------------------- unit


def test_all_alone_returns_choices_or_the_sentinel():
    assert SelectorList(NAMES)("all") == NAMES
    assert SelectorList(NAMES)("  all  ") == NAMES
    sentinel = SelectorList()("all")
    assert sentinel is SelectorList.ALL
    assert sentinel is values_module._ALL
    assert repr(sentinel) == "SelectorList.ALL"
    assert SelectorList(all_token="*")("*") is SelectorList.ALL
    assert SelectorList(NAMES, all_token="*")("*") == NAMES


def test_names_come_back_in_the_order_given_and_are_stripped():
    selector = SelectorList(NAMES)
    assert selector("gamma,alpha") == ("gamma", "alpha")
    assert selector(" beta , alpha ") == ("beta", "alpha")
    assert selector("beta") == ("beta",)
    assert SelectorList()("x,y") == ("x", "y")
    assert SelectorList(separator=";")("x;y,z") == ("x", "y,z")
    assert SelectorList(all_token="*")("all,b") == ("all", "b")


def test_rejections_use_exact_messages():
    selector = SelectorList(NAMES)
    assert _reject(selector, "") == "empty selector item in ''"
    assert _reject(selector, "alpha,") == "empty selector item in 'alpha,'"
    assert _reject(selector, ",alpha") == "empty selector item in ',alpha'"
    assert _reject(selector, "alpha, ,beta") == "empty selector item in 'alpha, ,beta'"
    assert _reject(selector, "all,alpha") == "'all' cannot be combined with other names"
    assert _reject(selector, "alpha,all") == "'all' cannot be combined with other names"
    assert _reject(selector, "all,all") == "'all' cannot be combined with other names"
    assert _reject(selector, "alpha,alpha") == "duplicate selector 'alpha'"
    assert _reject(selector, "alpha,beta,alpha") == "duplicate selector 'alpha'"
    assert _reject(selector, "delta") == (
        "unknown selector 'delta'; choose from alpha, beta, gamma or all"
    )
    assert _reject(selector, "alpha,delta") == (
        "unknown selector 'delta'; choose from alpha, beta, gamma or all"
    )
    # Without choices only structure is checked.
    assert _reject(SelectorList(), "a,a") == "duplicate selector 'a'"
    assert _reject(SelectorList(all_token="*"), "*,a") == (
        "'*' cannot be combined with other names"
    )
    assert _reject(SelectorList(NAMES, all_token="*"), "zzz") == (
        "unknown selector 'zzz'; choose from alpha, beta, gamma or *"
    )
    # Empty items win over the other structural errors.
    assert _reject(selector, "all,,all") == "empty selector item in 'all,,all'"


def test_constructor_exposes_normalized_fields():
    selector = SelectorList(["a", "b"], all_token="*", separator=";")
    assert (selector.choices, selector.all_token, selector.separator) == (
        ("a", "b"), "*", ";",
    )
    default = SelectorList()
    assert (default.choices, default.all_token, default.separator) == (None, "all", ",")
    assert repr(default) == "SelectorList(choices=None, all_token='all', separator=',')"


@pytest.mark.parametrize(
    ("args", "kwargs", "fragment"),
    (
        ((), {"all_token": ""}, "all_token must be a non-empty string"),
        ((), {"all_token": None}, "all_token must be a non-empty string"),
        ((), {"all_token": " all"}, "all_token must not have surrounding whitespace"),
        ((), {"separator": ""}, "separator must be a single non-space character"),
        ((), {"separator": ",,"}, "separator must be a single non-space character"),
        ((), {"separator": " "}, "separator must be a single non-space character"),
        ((), {"separator": None}, "separator must be a single non-space character"),
        ((), {"all_token": "a,b"}, "all_token must not contain the separator"),
        (("abc",), {}, "not one string"),
        (((),), {}, "choices must be None or non-empty"),
        ((("a", ""),), {}, "choices must be non-empty strings"),
        ((("a", 1),), {}, "choices must be non-empty strings"),
        ((("a", " b"),), {}, "choice ' b' must not have surrounding whitespace"),
        ((("a", "all"),), {}, "choice 'all' collides with the all token"),
        ((("a", "b,c"),), {}, "choice 'b,c' contains the separator"),
        ((("a", "b", "a"),), {}, "choices must be unique"),
    ),
)
def test_constructor_validation(args, kwargs, fragment):
    with pytest.raises(ValueError) as info:
        SelectorList(*args, **kwargs)
    assert fragment in str(info.value)


def test_a_choice_may_equal_the_default_token_when_another_token_is_used():
    assert SelectorList(("all", "b"), all_token="*")("all,b") == ("all", "b")


# ------------------------------------------------------- real registry


def _cli(handler=None, choices=NAMES):
    def default(args, runtime):
        seen.append(args)
        return 0

    seen: list = []
    verb = VerbSpec(
        "pick",
        description="Pick.",
        handler=handler or default,
        arguments=(
            ArgumentSpec(
                "targets", "target names",
                parser_kwargs={"type": SelectorList(choices), "nargs": "?"},
            ),
        ),
        options=(
            OptionSpec(
                ("--only",), "only these",
                parser_kwargs={"type": SelectorList(choices)},
            ),
            OptionSpec(
                ("--any",), "any names",
                parser_kwargs={"type": SelectorList()},
            ),
        ),
    )
    registry = CliRegistry(IDENT, prog="tool", description="Tool.")
    registry.register(verb)
    cli = registry.build()
    cli.seen = seen  # type: ignore[attr-defined]
    return cli


def _run(cli, argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv=argv, stdout=out, stderr=err, stdin=io.StringIO())
    return code, out.getvalue(), err.getvalue()


def test_option_and_positional_values_reach_the_handler_converted():
    cli = _cli()
    assert _run(cli, ["pick", "--only", "gamma,alpha", "beta"])[0] == 0
    args = cli.seen[-1]
    assert args.only == ("gamma", "alpha")
    assert args.targets == ("beta",)
    assert _run(cli, ["pick", "--only", "all", "all"])[0] == 0
    assert cli.seen[-1].only == NAMES and cli.seen[-1].targets == NAMES
    assert _run(cli, ["pick", "--any", "all"])[0] == 0
    assert cli.seen[-1].any is SelectorList.ALL
    assert _run(cli, ["pick"])[0] == 0
    assert getattr(cli.seen[-1], "only", None) is None
    assert cli.seen[-1].targets is None


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        (
            ["pick", "--only", "zzz"],
            "argument --only: unknown selector 'zzz'; choose from alpha, beta, gamma or all",
        ),
        (["pick", "--only", "alpha,,beta"], "argument --only: empty selector item in 'alpha,,beta'"),
        (["pick", "--only", "all,alpha"], "argument --only: 'all' cannot be combined with other names"),
        (["pick", "--any", "a,a"], "argument --any: duplicate selector 'a'"),
        (
            ["pick", "zzz"],
            "argument targets: unknown selector 'zzz'; choose from alpha, beta, gamma or all",
        ),
    ),
)
def test_bad_input_is_an_argparse_usage_error_with_exit_2(argv, message):
    cli = _cli()
    code, out, err = _run(cli, argv)
    assert code == 2
    assert out == ""
    assert f"[ERROR] tool pick: {message}\n" in err
    assert cli.seen == []


# ------------------------------------------------------------- surface


def _routes():
    return {route["id"]: route for route in export_cli_surface(_cli())["routes"]}


def test_surface_records_the_label_and_fields_and_is_not_opaque():
    surface = export_cli_surface(_cli())
    route = next(item for item in surface["routes"] if item["id"] == ROUTE)
    actions = {action["id"]: action for action in route["actions"]}
    bounded = {
        "callable": "cli_extended.SelectorList",
        "choices": list(NAMES),
        "all_token": "all",
        "separator": ",",
    }
    free = {
        "callable": "cli_extended.SelectorList",
        "choices": None,
        "all_token": "all",
        "separator": ",",
    }
    assert SELECTOR_LIST_LABEL == "cli_extended.SelectorList"
    assert actions[f"option:{ROUTE}/--only"]["type"] == bounded
    assert actions[f"option:{ROUTE}/--only"]["parser_kwargs"]["type"] == bounded
    assert actions[f"option:{ROUTE}/--any"]["type"] == free
    assert actions[f"argument:{ROUTE}/targets"]["type"] == bounded
    assert route["opaque_fields"] == []
    assert route["syntax_complete"] is True
    assert surface["syntax_complete"] is True


def test_surface_signature_changes_with_the_selector_settings():
    before = {c["id"]: c["signature"] for c in export_cli_surface(_cli())["candidates"]}
    after = {
        c["id"]: c["signature"]
        for c in export_cli_surface(_cli(choices=("alpha", "beta"))) ["candidates"]
    }
    only = f"case:{ROUTE}/option-spelling/option:{ROUTE}/--only/--only"
    assert before[only] != after[only]


def test_subclass_and_colliding_converters_stay_opaque():
    class Sub(SelectorList):
        pass

    class Impostor:
        __module__ = "cli_extended"

        def __call__(self, text):
            return text

    Impostor.__qualname__ = "SelectorList"
    registry = CliRegistry(IDENT, prog="tool", description="Tool.")
    registry.register(
        VerbSpec(
            "pick",
            description="Pick.",
            handler=lambda a, r: 0,
            options=(
                OptionSpec(("--sub",), "sub", parser_kwargs={"type": Sub(NAMES)}),
                OptionSpec(("--fake",), "fake", parser_kwargs={"type": Impostor()}),
            ),
        )
    )
    route = next(
        item
        for item in export_cli_surface(registry.build())["routes"]
        if item["id"] == ROUTE
    )
    types = {a["canonical"]: a["type"] for a in route["actions"] if a["kind"] == "option"}
    assert types["--fake"] == {"opaque": "built-in-converter-label-collision"}
    assert types["--sub"]["callable"] != "cli_extended.SelectorList"
    assert "choices" not in types["--sub"]
    assert route["opaque_fields"] == [
        f"option:{ROUTE}/--fake.type",
        f"option:{ROUTE}/--sub.type",
    ]


# -------------------------------------------------------------- checker


def _case(candidate, invocation):
    return ReviewCase(
        case_id=candidate["id"],
        state="active",
        decision="accept",
        reviewed_signature=candidate["signature"],
        rationale="reviewed",
        invocation=tuple(invocation),
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_cli.py::test_it",),
        retirement_reason="",
    )


def _findings(surface, candidate, invocation):
    catalog = ReviewCatalog("tool", 512, (), (_case(candidate, invocation),))
    return [i for i in _review_findings(surface, catalog) if candidate["id"] in i]


def _spelling(surface, flag):
    case_id = f"case:{ROUTE}/option-spelling/option:{ROUTE}/{flag}/{flag}"
    return next(item for item in surface["candidates"] if item["id"] == case_id)


def test_checker_models_the_selector_exactly():
    surface = export_cli_surface(_cli())
    only = _spelling(surface, "--only")
    assert _findings(surface, only, ["pick", "--only", "alpha,beta"]) == []
    assert _findings(surface, only, ["pick", "--only", "all"]) == []
    assert _findings(surface, only, ["pick", "--only", " gamma "]) == []
    for bad in ("zzz", "alpha,zzz", "alpha,,beta", "all,alpha", "alpha,alpha", ""):
        findings = _findings(surface, only, ["pick", "--only", bad])
        assert any("supplies an invalid value for option" in i for i in findings), bad
    free = _spelling(surface, "--any")
    assert _findings(surface, free, ["pick", "--any", "x,y"]) == []
    assert _findings(surface, free, ["pick", "--any", "all"]) == []
    assert _findings(surface, free, ["pick", "--any", "x,x"]) != []
    positional = next(
        item
        for item in surface["candidates"]
        if item["kind"] == "argument-shape"
    )
    assert _findings(surface, positional, ["pick", "alpha,beta"]) == []
    assert _findings(surface, positional, ["pick", "nope"]) != []


def test_checker_treats_a_malformed_selector_record_as_opaque():
    surface = export_cli_surface(_cli())
    only = _spelling(surface, "--only")
    base = copy.deepcopy(surface)
    route = next(item for item in base["routes"] if item["id"] == ROUTE)
    action = next(a for a in route["actions"] if a["id"] == f"option:{ROUTE}/--only")
    for mutate in (
        lambda spec: spec.pop("separator"),
        lambda spec: spec.pop("choices"),
        lambda spec: spec.update(choices=["a", "a"]),
        lambda spec: spec.update(separator="ab"),
        lambda spec: spec.update(choices="abc"),
    ):
        broken = copy.deepcopy(base)
        broken_route = next(i for i in broken["routes"] if i["id"] == ROUTE)
        broken_action = next(
            a for a in broken_route["actions"] if a["id"] == action["id"]
        )
        mutate(broken_action["type"])
        assert _findings(broken, only, ["pick", "--only", "anything,at,all"]) == []
