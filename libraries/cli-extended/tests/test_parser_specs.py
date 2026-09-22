from __future__ import annotations

import argparse
from dataclasses import FrozenInstanceError

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    HelpCatalog,
    HelpFormat,
    OptionSpec,
    VerbSpec,
)
from cli_extended.parser import _nargs_display

IDENTITY = CliIdentity("TEST", "1", "Test Tool")


@pytest.mark.parametrize(
    ("nargs", "expected"),
    (
        (None, "ITEM"),
        ("?", "[ITEM]"),
        ("*", "[ITEM ...]"),
        ("+", "ITEM [ITEM ...]"),
        (2, "ITEM ITEM"),
        (3, "ITEM ITEM ITEM"),
        (argparse.REMAINDER, "ITEM ..."),
        (argparse.PARSER, "ITEM ..."),
        (1, "ITEM"),
    ),
)
def test_nargs_have_consistent_derived_synopsis(nargs, expected):
    assert _nargs_display("ITEM", nargs) == expected


@pytest.mark.parametrize(
    ("flags", "description", "kwargs", "message"),
    (
        ((), "desc", {}, "at least one"),
        (("",), "desc", {}, "at least one"),
        (("positional",), "desc", {}, "begin with"),
        (("--option",), "", {}, "description"),
        (("--option",), "   ", {}, "description"),
        (("--option",), "desc", {"group": ""}, "group"),
        (
            ("--option",),
            "desc",
            {"mutually_exclusive_group": ""},
            "group name",
        ),
        (
            ("--option",),
            "desc",
            {"mutually_exclusive_required": True},
            "needs a mutually_exclusive_group",
        ),
        (
            ("--option",),
            "desc",
            {
                "mutually_exclusive_group": "source",
                "parser_kwargs": {"required": True},
            },
            "required belongs",
        ),
    ),
)
def test_option_spec_rejects_invalid_metadata(flags, description, kwargs, message):
    with pytest.raises(ValueError, match=message):
        OptionSpec(flags, description, **kwargs)


def test_option_argument_and_verb_metadata_are_immutable():
    specs = (
        (OptionSpec(("--kind",), "select a kind"), "description"),
        (ArgumentSpec("path", "path to a file"), "description"),
        (VerbSpec("inspect", description="inspect the target"), "description"),
    )
    for spec, field_name in specs:
        with pytest.raises(FrozenInstanceError):
            setattr(spec, field_name, "changed")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        ({"metavar": "FILE"}, "--input FILE"),
        ({"parser_kwargs": {"metavar": "DEST"}}, "--input DEST"),
        ({"parser_kwargs": {"metavar": ("HOST", "PORT")}}, "--input HOST PORT"),
        ({"parser_kwargs": {"choices": ("one", "two")}}, "--input {one,two}"),
        ({"parser_kwargs": {"action": "store_true"}}, "--input"),
        ({"parser_kwargs": {"action": argparse.BooleanOptionalAction}}, "--input"),
        ({"parser_kwargs": {"dest": "target_name"}}, "--input TARGET_NAME"),
        ({"flags": ("-i", "--input-value")}, "-i/--input-value INPUT_VALUE"),
        ({"parser_kwargs": {"nargs": "+"}}, "--input INPUT [INPUT ...]"),
    ),
)
def test_option_display_derives_metavar_and_action_syntax(kwargs, expected):
    flags = kwargs.pop("flags", ("--input",))
    option = OptionSpec(flags, "input option", **kwargs)
    assert option.display == expected


def test_option_markdown_documents_choices_required_exclusivity_and_defaults():
    assert (
        OptionSpec(
            ("--kind",), "choose kind", parser_kwargs={"choices": ("a", "b")}
        ).markdown_description
        == "choose kind (choices: `a`, `b`)"
    )
    assert (
        OptionSpec(
            ("--required",), "required option", parser_kwargs={"required": True}
        ).markdown_description
        == "required option (required)"
    )
    assert (
        OptionSpec(
            ("--default",), "default option", parser_kwargs={"default": "safe"}
        ).markdown_description
        == "default option (default: `safe`)"
    )
    assert (
        OptionSpec(
            ("--none",), "none default", parser_kwargs={"default": None}
        ).markdown_description
        == "none default"
    )
    assert (
        OptionSpec(
            ("--suppressed",),
            "suppressed default",
            parser_kwargs={"default": argparse.SUPPRESS},
        ).markdown_description
        == "suppressed default"
    )
    required = OptionSpec(
        ("--file",),
        "read a file",
        mutually_exclusive_group="source",
        mutually_exclusive_required=True,
    )
    optional = OptionSpec(
        ("--inline",), "inline value", mutually_exclusive_group="source"
    )
    assert "one option in this group is required" in required.markdown_description
    assert (
        "mutually exclusive with other group members" in optional.markdown_description
    )


def test_option_add_to_preserves_explicit_default_and_suppresses_implicit_default():
    parser = argparse.ArgumentParser(add_help=False)
    implicit = OptionSpec(("--implicit",), "implicit value")
    explicit = OptionSpec(
        ("--explicit",), "explicit value", parser_kwargs={"default": "kept"}
    )
    implicit.add_to(parser, suppress_default=True)
    explicit.add_to(parser, suppress_default=True)
    parsed = parser.parse_args([])
    assert not hasattr(parsed, "implicit")
    assert parsed.explicit == "kept"


def test_option_add_to_keeps_argparse_defaults_by_default():
    parser = argparse.ArgumentParser(add_help=False)
    OptionSpec(
        ("--switch",), "enable the switch", parser_kwargs={"action": "store_true"}
    ).add_to(parser)

    assert parser.parse_args([]).switch is False


@pytest.mark.parametrize("destination", ("", 17))
def test_option_display_falls_back_from_invalid_destinations(destination):
    option = OptionSpec(("--input",), "read input", parser_kwargs={"dest": destination})
    assert option.display == "--input INPUT"


@pytest.mark.parametrize(
    ("name", "description", "message"),
    (
        ("", "desc", "needs a name"),
        ("-bad", "desc", "without a leading dash"),
        ("arg", "", "must define a description"),
    ),
)
def test_argument_spec_rejects_invalid_metadata(name, description, message):
    with pytest.raises(ValueError, match=message):
        ArgumentSpec(name, description)


def test_argument_display_markdown_and_registration_use_metadata():
    argument = ArgumentSpec(
        "targets",
        "one or more targets",
        parser_kwargs={"nargs": "+", "choices": ("a", "b"), "default": "a"},
    )
    assert argument.display == "targets"
    assert "choices: `a`, `b`" in argument.markdown_description
    assert "default: `a`" in argument.markdown_description
    assert (
        ArgumentSpec(
            "plain", "plain argument", parser_kwargs={"default": None}
        ).markdown_description
        == "plain argument"
    )
    assert (
        ArgumentSpec(
            "suppressed",
            "suppressed argument",
            parser_kwargs={"default": argparse.SUPPRESS},
        ).markdown_description
        == "suppressed argument"
    )

    fallback_metavar = ArgumentSpec(
        "path", "path to a file", parser_kwargs={"metavar": ("FROM", "TO")}
    )
    assert fallback_metavar.display == "path"

    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "copy",
            description="copy files",
            arguments=(argument,),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    assert app.command_parsers["copy"].parse_args(["a", "b"]).targets == ["a", "b"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"name": "", "description": "desc"}, "non-empty name"),
        ({"name": "-verb", "description": "desc"}, "without a leading dash"),
        ({"name": "verb", "description": ""}, "must define a description"),
        ({"name": "verb", "description": "desc", "group": ""}, "semantic group"),
        (
            {"name": "verb", "description": "desc", "summary_description": ""},
            "empty summary description",
        ),
    ),
)
def test_verb_spec_rejects_invalid_metadata(kwargs, message):
    defaults = {"name": "verb", "description": "desc"}
    defaults.update(kwargs)
    with pytest.raises(ValueError, match=message):
        VerbSpec(**defaults)


def test_verb_behavior_summary_and_custom_synopsis_derivation():
    verb = VerbSpec(
        "apply",
        description="apply the operation",
        summary_description="apply a change",
        mutating=True,
        interactive=True,
        expensive=True,
        examples=("tool apply FILE",),
        arguments=(
            ArgumentSpec("source", "source", metavar="SOURCE"),
            ArgumentSpec("target", "optional target", parser_kwargs={"nargs": "?"}),
        ),
        options=(
            OptionSpec(("--format",), "format", parser_kwargs={"required": True}),
            OptionSpec(
                ("--config",),
                "file config",
                metavar="FILE",
                mutually_exclusive_group="config",
                mutually_exclusive_required=True,
            ),
            OptionSpec(
                ("--config-json",),
                "inline config",
                metavar="JSON",
                mutually_exclusive_group="config",
                mutually_exclusive_required=True,
            ),
            OptionSpec(("--verbose-result",), "optional result"),
        ),
    )
    assert verb.behavior_labels == ("mutating", "interactive", "potentially expensive")
    assert (
        verb.summary == "apply a change [mutating; interactive; potentially expensive]"
    )
    assert verb.display_synopsis == (
        "SOURCE [target] --format FORMAT (--config FILE | --config-json JSON) [options]"
    )
    command_description = verb.command_description
    assert "Mutating actions require confirmation" in command_description
    assert "tool apply FILE" in command_description

    optional_group = VerbSpec(
        "read",
        description="read data",
        options=(
            OptionSpec(("--json-file",), "file", mutually_exclusive_group="input"),
            OptionSpec(("--json-inline",), "inline", mutually_exclusive_group="input"),
        ),
    )
    assert (
        optional_group.display_synopsis
        == "[--json-file JSON_FILE | --json-inline JSON_INLINE]"
    )
    custom = VerbSpec("custom", description="custom parser", configure=lambda _p: None)
    assert custom.display_synopsis == "[options]"
    assert (
        VerbSpec("simple", description="simple", synopsis="SPECIAL").display_synopsis
        == "SPECIAL"
    )


def test_help_format_and_catalog_duplicate_metadata_validation():
    assert HelpFormat.parse("MARKDOWN") is HelpFormat.MARKDOWN
    with pytest.raises(ValueError, match="invalid help format"):
        HelpFormat.parse("rst")

    duplicate_option = OptionSpec(("--same",), "same")
    with pytest.raises(ValueError, match="duplicate option"):
        HelpCatalog(
            IDENTITY,
            prog="tool",
            global_options=(duplicate_option, duplicate_option),
        )
    duplicate_verb = VerbSpec("same", description="same")
    with pytest.raises(ValueError, match="duplicate verb"):
        HelpCatalog(IDENTITY, prog="tool", verbs=(duplicate_verb, duplicate_verb))


def test_catalog_global_options_rendering_markdown_summary_and_group_parsing():
    option = OptionSpec(("--one",), "first option", group="INPUT")
    catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        description="First paragraph.\n\nSecond paragraph.",
        verbs=(
            VerbSpec("inspect", description="inspect data"),
            VerbSpec("review", description="review data"),
        ),
        global_options=(option,),
        width=65,
    )
    catalog.add_global_options((option, ("--two FILE", "second option")))
    assert len(catalog.global_options) == 2
    with pytest.raises(ValueError, match="conflicting top-level"):
        catalog.add_global_options((OptionSpec(("--one",), "other", group="INPUT"),))
    text = catalog.render(width=65)
    assert "First paragraph.\n\nSecond paragraph." in text
    assert "--one" in text and "--two FILE" in text
    summary = catalog.render_markdown(include_details=False)
    assert "- `inspect` — inspect data" in summary
    assert "### `inspect`" not in summary


def test_catalog_text_and_markdown_preserve_unbroken_and_hyphenated_tokens():
    prose_token = "descriptiontoken" * 6
    verb_token = "verb-part-" * 10
    option_token = "option-part-" * 10
    markdown_token = "markdown-part-" * 10
    catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        description=f"{prose_token} {verb_token}",
        verbs=(VerbSpec("inspect", description=f"{verb_token} {markdown_token}"),),
        global_options=(OptionSpec(("--token",), option_token),),
        width=90,
    )

    text = catalog.render(width=60)
    markdown = catalog.render_markdown(include_details=False, width=60)
    assert prose_token in text
    assert verb_token in text
    assert option_token in text
    assert markdown_token in markdown


def test_zero_width_uses_the_catalog_width_for_text_and_markdown():
    catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        description="A paragraph with enough ordinary words to make line wrapping observable. "
        * 3,
        width=90,
    )

    assert catalog.render(width=0) == catalog.render()
    assert catalog.render_markdown(width=0) == catalog.render_markdown()
    assert catalog.render(width=60) != catalog.render(width=90)
    markdown_catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        verbs=(
            VerbSpec(
                "inspect",
                description="ordinary word " * 30,
            ),
        ),
        width=90,
    )
    assert markdown_catalog.render_markdown(width=60, include_details=False) != (
        markdown_catalog.render_markdown(width=90, include_details=False)
    )


def test_markdown_keeps_command_and_global_options_in_their_groups():
    registry = CliRegistry(
        IDENTITY,
        prog="tool",
        description="test CLI",
        global_options=(
            OptionSpec(("--global-in",), "global input", group="GLOBAL INPUT"),
            OptionSpec(("--global-out",), "global output", group="GLOBAL OUTPUT"),
        ),
    )
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect data",
            options=(
                OptionSpec(("--source",), "source file", group="QUERY INPUT"),
                OptionSpec(("--format",), "output format", group="RENDER OUTPUT"),
            ),
            handler=lambda *_: 0,
        )
    )
    markdown = registry.build().catalog.render_markdown()

    assert markdown.index("#### Query Input") < markdown.index("`--source")
    assert markdown.index("`--source") < markdown.index("#### Render Output")
    assert markdown.index("#### Render Output") < markdown.index("`--format")
    assert markdown.index("## Global Input") < markdown.index("`--global-in")
    assert markdown.index("`--global-in") < markdown.index("## Global Output")
    assert markdown.index("## Global Output") < markdown.index("`--global-out")


def test_catalog_does_not_split_long_words_in_description():
    long_word = "unbroken-" * 12
    catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        description=f"Keep this token intact: {long_word}",
    )

    assert long_word in catalog.render(width=60)


def test_catalog_does_not_split_long_words_in_verb_or_option_help():
    long_word = "unbrokenword" * 12
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "inspect",
            description=f"verb token: {long_word}",
            options=(OptionSpec(("--input",), f"option token: {long_word}"),),
            handler=lambda *_: 0,
        )
    )

    rendered = registry.build().catalog.render(width=60)
    assert long_word in rendered


def test_catalog_does_not_split_hyphenated_verb_tokens():
    long_word = "segment-" * 18
    catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        description="test",
        verbs=(VerbSpec("inspect", description=long_word),),
    )

    assert long_word in catalog.render(width=60)


def test_catalog_markdown_custom_parser_and_behavior_branches():
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "apply",
            description="apply data",
            mutating=True,
            configure=lambda parser: parser.add_argument("--special", help="custom"),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    detailed = app.catalog.render(output_format=HelpFormat.MARKDOWN)
    assert "**Behavior:** mutating." in detailed
    assert "--special" in detailed
    assert "## Modification" not in detailed
    catalog = HelpCatalog(IDENTITY, prog="empty")
    assert "Usage: empty" in catalog.render()
    assert catalog.render_markdown(width=64).startswith("# TEST")

    class PlainParser:
        @staticmethod
        def format_help():
            return "usage: custom\n\ncustom parser body\n"

    custom_catalog = HelpCatalog(
        IDENTITY,
        prog="tool",
        verbs=(
            VerbSpec(
                "custom", description="custom command", configure=lambda _parser: None
            ),
        ),
    )
    custom_catalog.attach_command_parsers({"custom": PlainParser()})
    assert "custom parser body" in custom_catalog.render_markdown()


def test_catalog_parser_validation_reports_both_drift_directions():
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(VerbSpec("listed", description="listed", handler=lambda *_: 0))
    app = registry.build()
    parser = app.parser
    app.catalog.validate_parser(parser)
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    subparsers.add_parser("extra")
    with pytest.raises(ValueError, match="parser verbs missing from catalog: extra"):
        app.catalog.validate_parser(parser)

    parser = app.parser
    subparsers.choices.pop("extra")
    app.catalog.validate_parser(parser)
    parser.catalog.verbs = (
        VerbSpec("listed", description="listed"),
        VerbSpec("missing", description="missing"),
    )
    with pytest.raises(ValueError, match="catalog verbs missing from parser: missing"):
        parser.catalog.validate_parser(parser)
