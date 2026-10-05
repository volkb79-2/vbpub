# cli-extended

`cli-extended` is the shared, stdlib-first CLI contract layer for user-facing
Python tools in `vbpub`. Its Python import name is `cli_extended`.

The library keeps `argparse` and the standard `logging` package familiar while
owning repetitive shell behavior: identity/version output, grouped help with
the overall CLI description and aligned verb summaries, parser registration,
common options, clean cancellation/errors, confirmation
prompts, output streams, redaction, and progress presentation. Its base install
has no runtime dependencies; an optional `interactive` extra provides
Questionary-backed prompts for multi-step workflows.
Registered CLIs require exact option spellings by default; long-option prefix
abbreviations are disabled for both the root command and its verbs.

## Adopt it

Install the package into the environment that runs the CLI, then use the
consumer's package metadata as the version source. `pyproject.toml` is the
authoritative version for this library; `setuptools_scm` or a fabricated
runtime fallback is deliberately not involved.

```bash
python3 -m pip install --editable ./libraries/cli-extended
```

Define each public command once. The registration below drives parser
construction, grouped top-level help, command help, option groups, handler
dispatch, `--yes` availability, and Markdown reference generation:

```python
import argparse
from pathlib import Path

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)

# Installed distribution and/or a checked-in VERSION file (absolute path);
# when both exist they must agree, and there is never a fallback version.
identity = CliIdentity.resolve(
    name="EXAMPLE",
    command="example",
    distribution="example-tool",  # replace with this CLI's installed distribution
    version_file=Path(__file__).resolve().parent / "VERSION",
    long_name="Example Operator Tool",
)


def status(args: argparse.Namespace, runtime) -> None:
    result = {"server_id": args.server_id, "filter": args.filter, "state": "ready"}
    runtime.output.primary(result if runtime.json_mode else f"state: {result['state']}")


def apply(args: argparse.Namespace, runtime) -> None:
    # A real handler loads and validates its complete change before consent.
    # With --dry-run, confirm() prints "Dry run: no changes made." and returns
    # False without prompting, so this gate makes the handler dry-run-safe.
    if not runtime.confirm(f"Apply {args.path} to production?"):
        return
    # A real handler performs exactly the confirmed domain mutation here.
    runtime.output.info(f"Applied {args.path}.")


cli = CliRegistry(
    identity,
    prog="example",
    description="Inspect state and apply reviewed changes.",
    getting_started=("example status",),
    logging_logger="example",
    unexpected_exceptions="report",  # bugs become "[ERROR] unexpected ..."; --traceback re-raises
)
cli.register(
    VerbSpec(
        name="status",
        description="show current state",
        group=VerbGroup.EXPLORATION.value,
        examples=("example status node-1",),
        arguments=(
            ArgumentSpec(
                "server_id",
                "optional server selector",
                metavar="server_id",
                parser_kwargs={"nargs": "?"},
            ),
        ),
        options=(
            OptionSpec(
                ("--filter",),
                "restrict results to matching values",
                group="FILTERS",
                metavar="TEXT",
            ),
        ),
        handler=status,
    )
)
cli.register(
    VerbSpec(
        name="apply",
        description="apply a validated change",
        group=VerbGroup.MODIFICATION.value,
        mutating=True,
        dry_run=True,
        examples=("example apply reviewed-change.json",),
        arguments=(ArgumentSpec("path", "change file", metavar="FILE"),),
        handler=apply,
    )
)

app = cli.build()
raise SystemExit(app.run())
```

That is the registration boundary. The library generates the parser and
handler map, exposes `--yes` only for the mutating command, scopes standard
logging to the selected logger, and validates that command/help registration
cannot drift. `OptionSpec` carries argparse's own `action`, `choices`, `type`,
`nargs`, and related options in `parser_kwargs`; `ArgumentSpec` does the same
for positionals. Set `OptionSpec.hidden=True` to omit internal plumbing from
operator help. The generated semantic surface still inventories accepted
hidden options and labels them as hidden, so their behavior remains reviewable.
A verb may
set `confirmation_required=False` when it mutates state without taking a
generic `--yes` acknowledgement; it still appears as mutating in the catalog.
The default `None` keeps the existing rule that mutating verbs require
confirmation. `True` explicitly requires confirmation and is valid only on a
mutating verb. Existing consumers may continue using `include_confirmation`;
when both fields are set, they must agree.
`VerbSpec(mutating=True, dry_run=True)` adds a library-owned `--dry-run`: it
sets `runtime.dry_run`, and `runtime.confirm()` then declines without prompting
(see the `apply` handler above). `CliIdentity.resolve()` checks an installed
distribution and a version file against each other, and
`unexpected_exceptions="report"` turns unexpected exceptions into a one-line
error with a `--traceback` escape hatch. The reasons are in the
[design guide](docs/DESIGN-GUIDE.md#version-sources-must-agree-and-failures-must-be-explicit).
Handlers may return `None` for success; `RegisteredCli.run()` converts that to
process status `0`. Prefer this shared behavior over repeating `return 0` in
each successful handler. Return a status explicitly only when the command has
a meaningful non-default outcome.
`VerbSpec.synopsis` is optional: by default the library derives
the command synopsis from required positionals, required options, and required
or optional mutually-exclusive option groups. Set it only when a public syntax
shape cannot be represented by those declarations. For example, options that
accept either a file or inline JSON can declare the same
`mutually_exclusive_group="configuration-source"` and
`mutually_exclusive_required=True`; the parser then both enforces the choice
and displays `(--config FILE | --config-json JSON)` without a second, drifting
usage string. Use `VerbSpec.configure` only for genuinely custom parser
structures such as nested sub-actions.

Put options that apply to the whole CLI (for example, a config-file path) in
`CliRegistry.global_options`; the registry accepts them both before and after
command selection and lists them in root and command help. A command-local
option with the same complete flag set handles the after-command spelling.
Put options that
belong to one command in that verb's `options`; put standard output/debug controls at the appropriate
level only when they genuinely apply there. Supported common output/debug
controls work both before and after the selected verb; selectors such as
`--json` and `--progress` may be unavailable on particular verbs. Root help
lists their union, while command help lists the selected verb's supported set;
an unsupported selector is refused with command-specific help, independent of
placement. Conflicting verbosity or colour selectors are rejected across
parser levels. Each `OptionSpec` names its help group, so the registry places
and renders options from metadata rather than maintaining a second
hand-written usage block. The top-level catalog lists only verb names, aligns
their concise descriptions across groups, and includes the `CliRegistry`
description of the overall tool. Detailed invocation syntax remains in
per-verb help. `VerbSpec.description` is the full command
description: it appears after the generated usage syntax, separated by blank
lines, and is passed to argparse as that verb's help description. A verb may
set `summary_description` when its full command description is too long for the
one-line top-level catalog; command-specific help keeps the full `description`.
The command parser uses a
terminal-width-aware formatter too, with a 120-column fallback when no usable
terminal width is reported.

`runtime.confirm()` is default-no. It refuses to prompt on non-TTY stdin,
handles EOF/decline cleanly, and honors `--yes`; call it only after domain
validation and immediately before the exact mutation. The library cannot
decide whether a firewall, deployment, or account change is safe.
For multi-prompt flows, `runtime.output.is_interactive` reports whether both
injected stdin and stdout are TTYs; use it to refuse wizard mode cleanly when
the invocation is redirected or piped.

### Optional interactive prompts

See the [design guide's prompt boundary](docs/DESIGN-GUIDE.md#optional-interactive-prompts)
for why terminal mechanics are shared while schema and persistence remain
consumer-owned.

Use `runtime.prompts` when a command needs to collect several values in a
terminal. The API returns values and leaves schema validation, domain policy,
and persistence in the handler:

```python
def configure(_args, runtime):
    name = runtime.prompts.text("Project name")
    mode = runtime.prompts.select("Release mode", ("safe", "full"))
    approved = runtime.prompts.confirm("Use these settings?", default=False)
    if not approved:
        return 0
    # Validate and persist name/mode in the owning application here.
    return 0


cli = CliRegistry(identity, prog="example", description="Configure a project.")
cli.register(VerbSpec("configure", description="configure project", handler=configure))
app = cli.build()
raise SystemExit(app.run(interactive_extra="example-tool[interactive]"))
```

Normally `RegisteredCli.run()` handles an uncaught `PromptCancelled` and exits
with status `130`; catch it only when the command needs cancellation cleanup.
The five methods are `text`, `password`, `confirm`, `select`, and `checkbox`.
`select` and `checkbox` require non-empty, unique string choices. A cancel is
never returned as a valid empty or false answer. Both injected stdin and stdout
must be TTYs before a driver is loaded or called.

Install Questionary only for the wizard-enabled command surface:

```bash
python3 -m pip install 'cli-extended[interactive]'
```

Help, version, and non-interactive commands do not import Questionary. A
vendored consumer passes its own extra name, such as
`interactive_extra="nyxloom[interactive]"`, so missing-dependency guidance
names the package the operator installs. Tests inject a `PromptDriver` through
`app.run(prompt_driver=...)`; fake drivers still use TTY-marked streams so the
production terminal policy remains covered.

For a CLI with one command and no verb token, register one `VerbSpec` with
`CliRegistry(single_command=True)`. That supports transitional tools; a tool
with several operator actions should expose explicit verbs instead (for
example, a task client may distinguish `show TASK_UUID` for one fetch and
`watch TASK_UUID` for polling until a terminal state). Add `wait` only if it
has a real automation contract distinct from human-readable `watch`; do not
turn a presentation flag such as `--json` into a second action. Keep task
cancellation in the API-owning CLI when that CLI already owns task mutations.
Bare invocation still prints help by default. Only a truly intentional
no-argument action may opt in with `single_command=True, no_args_action=True`;
`--help` remains side-effect free.

## Declared option constraints and selector lists

Structural relationships between a verb's options are declared, not hand-coded
in the handler. `Requires`, `Conflicts` and `RequiresChoice` go in
`VerbSpec.constraints`; the registry refuses a violating invocation with exit
`2` and the verb's help, lists the rules under a `CONSTRAINTS` help section and
in the Markdown reference, and exports them to the surface manifest with one
review candidate per rule. See the
[design guide](docs/DESIGN-GUIDE.md#declare-option-constraints-structurally)
for why presence means "differs from the default" and why only structural
rules belong here.

```python
VerbSpec(
    "sync", description="Sync projects.", mutating=True, dry_run=True,
    handler=sync,
    options=(
        OptionSpec(("--update",), "update", parser_kwargs={"action": "store_true"}),
        OptionSpec(("--refresh",), "refresh", parser_kwargs={"action": "store_true"}),
    ),
    constraints=(
        Requires("--dry-run", ("--update", "--refresh"), "a dry run needs work to preview"),
        Conflicts(("--refresh", "--json"), "refresh has no JSON form"),
    ),
)
# tool sync --dry-run   ->  [ERROR] --dry-run requires --update or --refresh: a dry run needs work to preview
```

`SelectorList` is an argparse `type` for "`all`, or these names":
`SelectorList(("alpha", "beta"))("beta,alpha")` returns `("beta", "alpha")`,
`"all"` returns every choice (or `SelectorList.ALL` when no choices are
declared), and an empty, duplicate, unknown or `all`-mixed item is a normal
argparse usage error.

```python
OptionSpec(("--only",), "projects to process",
           parser_kwargs={"type": SelectorList(("alpha", "beta"))})
```

## Generated documentation and contract tests

The generated catalog is available as `app.catalog`. Full Markdown reference
output includes verb descriptions, behavior labels, examples, positional
arguments, option groups, global options, and the choices/defaults carried in
structured argparse attributes:

```python
markdown = app.catalog.render(output_format="markdown")
```

For an executable, generated terminal help uses the same color policy as
diagnostics. Color is automatic only when the destination stream is a TTY and
`NO_COLOR` is unset; `--color` forces it, while `--no-color` disables it.
Explicit `--color` may override `NO_COLOR`, but supplying both switches is an
invocation error. This applies to bare-invocation help, `--help`, `help VERB`,
and help accompanying parser/runtime refusals. Markdown help, version output,
and primary results remain plain; JSON is never decorated with ANSI. The CLI
boundary applies the policy, so use `app.run()` rather than printing parser
help directly. Consumers should use `runtime.output.info/warn/error/hint()` for
diagnostics and should not add ANSI sequences or a second color library for
severity tags. A custom primary renderer can query
`runtime.output.color_enabled(runtime.output.stdout)` or pass its stderr stream
for diagnostics; consumers must still keep JSON plain by disabling color when
`runtime.json_mode` is true.

For a real executable, the package provides a black-box contract assertion:

```python
import subprocess
import sys

from cli_extended import assert_cli_contract


def invoke(args):
    return subprocess.run(
        [sys.executable, "path/to/example.py", *args],
        text=True,
        capture_output=True,
        check=False,
    )


assert_cli_contract(
    invoke,
    identity,
    ("status", "apply"),
    invalid_invocations={"missing file": ("apply",)},
    known_verb_errors={"missing file": "apply"},
)
```

That checks observable help/version/error conventions, rejects `-h` by
default, and verifies that selected known-verb errors include the complete
verb help rather than only a usage line. Set `allow_short_help=True` only for
a documented compatibility exception. The consumer must also assert that
help/version cause no API calls, credential reads, or filesystem changes using
its own fakes or state probes. For service entrypoints, keep startup behind
argument parsing and command dispatch; see the
[consumer guide](docs/CONSUMERS.md#service-entrypoints-and-side-effect-free-discovery)
for the single-command pattern and its installed-executable oracle.

See [`SPEC.md`](SPEC.md) for the complete behavioral contract;
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for install, migration, and
responsibility guidance; [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md)
for the design rationale; and [`BACKLOG.md`](BACKLOG.md) for open library
follow-ups.

## Keep the canonical CLI spec in sync

The generated surface names the library's own common controls per route
(`Common controls: ...`) and records one integer
contract version, stated once in the region header, instead of signing their syntax, so a library upgrade leaves
your manifest and review signatures untouched unless the contract version
changes; then `check` reports a single finding telling you to read the contract
notes and re-run `sync`. See
[Version the library's own controls](docs/DESIGN-GUIDE.md#version-the-librarys-own-controls-dont-sign-them)
and
[What a library upgrade does to your surface](docs/CONSUMERS.md#what-a-library-upgrade-does-to-your-surface).

`RegisteredCli` can export its built parser tree as stable JSON and generate a
bounded semantic-review checklist. Its generated Markdown shows each route's
invocation mode, help summary and description, the entrypoint's empty-argv
behavior, and how each route handles its remaining tokens (including delegated
groups and single-command routes), behavior and confirmation policy,
parser settings (including how negative-number tokens are parsed) and callbacks,
delegated metadata, and opaque fields. Its argument and option rows include descriptions,
grammar shape, argparse action and converter, choices, defaults, const values,
exclusive-group requirements, scope, placement, visibility, and help group.
Each route's `single_command` and `no_args_action` flags are required manifest
facts; rendering refuses a route record that omits either instead of assuming
`false`.
Single-command entrypoints list only the built-in help forms they actually
accept. `cli-extended` also loads a product-owned
TOML decision catalog, updates a marked Markdown section in the product's
canonical CLI spec, reports signature/stale-case changes, and supplies a pytest
collection assertion for exact node IDs and `cli_case` markers. Consumers keep
one runtime grammar in `CliRegistry` and their semantic decisions in the
catalog; the library owns the repeatable export, diff, and marker plumbing.
The checker sanity-checks supplied argv against route placement and argument
shape. It tracks recognized option arity, scope, parser depth, `--` terminators,
parser-scoped abbreviation policy, and required parent positionals. The
manifest records each parser's `allow_abbrev` setting. Custom `prefix_chars`
and argparse argument-file expansion are marked incomplete until the checker
can model them. Positional values must reach the registered
argument being reviewed; a matching token in a sibling position is not enough.
Bare tokens must map to declared positionals; leftover tokens are findings.
Every generated case includes the route's required positional, option, and
exclusive-group baseline in its signature and must supply that baseline. The
baseline requires one selected member per required exclusive group; repeating
that same member remains valid when the built parser accepts it. Consumers use
explicit interaction cases to review any meaning attached to repetition. The
checker verifies each option occurrence's declared arity and modeled value
conversion, so a valid first occurrence cannot hide a malformed repeat. It
reports failures from exact built-in `str`, `int`, `float`, and `bool`
conversions even when no choices are declared, and checks membership when
enumerable choices exist. Consumer-defined converters remain
opaque and are never invoked by the checker. Custom argparse actions also
remain opaque; only exact stock store, append, and extend action classes are
checked statically. A non-callable type reference on a value-taking action makes
the surface incomplete. Any non-default parser type-registry registration
also makes it incomplete: argparse resolves registry keys by dictionary
equality, so a key equal to an action's type can change its converter even
when the key is not the same object. Built-in converter behavior is modeled
only when the converter is the exact built-in object; a matching label is not
proof. Choice containers must be exact built-in lists,
tuples, sets, or frozensets, and their members must be plain strings, integers,
finite floats, or booleans. Custom containers or other choice objects make
the surface incomplete rather than being flattened into different membership
or equality behavior. A flag-only option cannot use `--flag=value`. Delegated
global options must also keep their mutually
exclusive group membership and requiredness in sync with the parent parser.
Argparse does not check `choices` on a flag-only action, so the exporter marks
that registration incomplete instead of presenting its choices as enforced.
For `nargs=argparse.PARSER`, argparse checks the first converted value against
choices. For `nargs=argparse.REMAINDER`, it converts values but skips the
choices check; a remainder action that declares choices is marked incomplete
so the declaration is not mistaken for an enforced restriction.
For `nargs="?"`, omitting the value selects `const`. Argparse applies the
action's converter only when the constant is a string. Whether the stock
runtime checks an omitted constant against `choices` can depend on the constant
type, so the exporter probes each supported built-in constant type on a
disposable parser, records the result in the surface and generated spec, and
signs it into candidates. The review checker follows the recorded behavior
without invoking consumer code. This rule applies to optional options; an
omitted optional positional uses its default rather than `const`. A non-scalar
option constant makes the surface incomplete rather than flattening its
runtime value.
The linked test must still run the real invocation and assert its behavior and
effects; the marker proves test collection and linkage only. Catalog interactions may
refer to an option owned by another route, so a consumer can record a misuse
such as `show --poll` and link the real refusal test. The structural check
requires every named local option and the target route's required arguments,
options, and exclusive selections. It confirms the foreign spelling appears as
an unrecognized option at the target route's parser depth. It consumes a
value-taking foreign option using its owner action's arity and target option
boundaries, so those tokens cannot satisfy required target positionals in the
checklist. It
checks minimum/fixed arity and enumerable choices using the same built-in
conversion rules; a custom converter's choice behavior belongs to its linked
real-CLI test. Flag-only options cannot carry an inline value. The consumer
catalog owns the expected decision and status; check mode does not infer
semantics from the option's route. A command
that intentionally forwards such tokens can record acceptance when its
behavior test proves that contract. Any other unrecognized option must be
declared in the interaction or check mode rejects the invocation. The check
does not call consumer-defined converters or handlers.
Parser callbacks that add ordinary argparse actions are inventoried; callbacks
that replace argparse parsing methods, set uncaptured parser-level defaults,
or leave the option lookup table inconsistent make the surface incomplete. For
optional-arity, variadic, or repeatable options with distinct behavior, declare
separate named interactions for each reviewed invocation shape; the generator
does not guess every repetition or value count. See the
[consumer workflow](docs/CONSUMERS.md#review-cross-route-and-arity-interactions)
for examples and limits. If an interaction's route or option ID becomes stale,
sync still regenerates the current grammar and keeps its semantic case visible
as stale; it reports the broken catalog reference without editing the TOML.

Expose an import-safe function that returns the consumer's `RegisteredCli`.
Use `python.module:build_cli` for an importable module, or
`path/to/hyphenated-script.py:build_cli` when the registry lives in a
single-file script. The path loader imports the file for the workflow and adds
its parent directory for sibling imports, so consumers do not need an adapter
module just to expose the registry. Declare the CLI once in
`[tool.cli-extended]` of `pyproject.toml` (or a standalone `cli-extended.toml`)
and run the `cli-extended` console script:

```bash
cli-extended surface sync
cli-extended surface check
```

The review workflow, in six steps (the `cli-extended-review` skill drives it):

1. Locate the project config (`--config PATH`, or found by walking up).
2. `cli-extended surface sync` refreshes the generated manifest and spec region.
3. `cli-extended surface pack --output FILE` writes the rubric, every route's
   help and every case awaiting review as one Markdown bundle.
4. Judge the bundle and edit the review catalog and the findings file by hand.
5. `cli-extended surface sync`, then `surface check`: an open `blocker` or
   `major` finding fails it.
6. `cli-extended surface report` lists what is still open.

`python -m cli_extended.surface_cli` keeps its old flags but is deprecated. The
[design guide](docs/DESIGN-GUIDE.md#review-the-surface-with-the-agent-harness-and-keep-findings-separate)
explains why the harness runs the review and why the library never edits your
catalog.

Use the `surface template` command to print missing case rows and instructions for
changed decisions. Sync never edits the TOML or content outside the marked
spec region. Candidates are review prompts, not guessed executable commands or
predicted outcomes. The shared [consumer workflow](docs/CONSUMERS.md#adopt-the-generator)
shows the catalog, pytest marker, and adoption lifecycle; the [design guide](docs/DESIGN-GUIDE.md#keep-a-generated-surface-and-a-human-semantic-record)
explains why the Python registry remains the grammar source.
Keep the review catalog, manifest, and spec at distinct file paths; sync and
check reject equal paths and symlink or hard-link aliases.
Each generated file is replaced atomically; a stop between the manifest and
spec updates leaves a detectable mismatch for `check`, not a half-written file.

## Ship agent skills with your tool

A tool that carries agent skills (`SKILL.md` trees for Claude Code and the
`~/.agents` harnesses) packages them as package data and registers one shared
`skills` verb group: `<tool> skills install|uninstall|check|list`. Installed
copies are stamped, so a version bump is reported `stale`, a local edit
`modified`, and a directory of another tool or without a stamp is never
overwritten. The [design guide](docs/DESIGN-GUIDE.md#ship-agent-skills-per-tool-and-stamp-them)
explains the choices; the contract is [SPEC §14](SPEC.md#14-packaged-agent-skills).

```python
from cli_extended import CliIdentity, CliRegistry, register_skills_verbs

identity = CliIdentity("EXAMPLE", "1.2.3", "Example Operator Tool", "example")
registry = CliRegistry(identity, prog="example", description="Example tool.")
# ... register your own verbs ...
register_skills_verbs(registry, package="example_tool")  # <pkg>/skills/<name>/SKILL.md
app = registry.build()
```

## Report environment problems with `doctor`

`register_doctor(registry, [DoctorCheck(...)])` adds one shared read-only
`<tool> doctor` verb. Each check returns `ok`, `warn`, `fail` or `skip` with a
summary and optional remedy and JSON details; a crashing check is reported as
`fail`, never swallowed. It supports `--check NAME` and `--json`, and exits 1
only when a check failed. A registry that also ships agent skills gets a
`skills` check automatically. The
[design guide](docs/DESIGN-GUIDE.md#one-doctor-verb-crashes-are-failures)
explains the choices; the contract is [SPEC §15](SPEC.md#15-doctor).

```python
from cli_extended import CheckResult, CliIdentity, CliRegistry, DoctorCheck, register_doctor

identity = CliIdentity("EXAMPLE", "1.2.3", "Example Operator Tool", "example")
registry = CliRegistry(identity, prog="example", description="Example tool.")
register_doctor(registry, [
    DoctorCheck("config", "configuration file is readable",
                lambda runtime, args: CheckResult("ok", "config found")),
])
app = registry.build()
```

## Consumer test helpers

`invoke_script`, `invoke_module` and `make_invoker` run a real executable with
a hermetic environment (required `home`, `XDG_*`, `NO_COLOR`, the tested
library first on `PYTHONPATH`), and `cli_extended.pytest_plugin` (one
`pytest_plugins` line) registers the `cli_case` marker and checks reviewed
cases at collection; `--cli-case-partial` relaxes it for focused runs. See the
[consumer guide](docs/CONSUMERS.md#test-helpers-invoke_script) and the
[design guide](docs/DESIGN-GUIDE.md#keep-consumer-tests-hermetic-and-the-plugin-opt-in).

## Test and gate

The package gate covers R0/R1/R2 plus an independent R3 canary. R1 requires
100% statement and branch coverage for every shipped `cli_extended` module.
The tests and canary run in `tester-unified`, not in the devcontainer cockpit:

```bash
cd libraries/cli-extended
./run-gate.py gate
```

CMRU, the Debian installer, and the Netcup entrypoints are current first-party
adopters. The consumer guide explains how to inventory shipped entrypoints and
review each product's command semantics before registering its grammar.
