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

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)

identity = CliIdentity.from_distribution(
    name="EXAMPLE",
    command="example",
    distribution="example-tool",  # replace with this CLI's installed distribution
    long_name="Example Operator Tool",
)


def status(args: argparse.Namespace, runtime) -> None:
    result = {"server_id": args.server_id, "filter": args.filter, "state": "ready"}
    runtime.output.primary(result if runtime.json_mode else f"state: {result['state']}")


def apply(args: argparse.Namespace, runtime) -> None:
    # A real handler loads and validates its complete change before consent.
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
for positionals. Set `OptionSpec.hidden=True` for accepted internal plumbing
that must stay out of operator help and generated reference tables. A verb may
set `confirmation_required=False` when it mutates state without taking a
generic `--yes` acknowledgement; it still appears as mutating in the catalog.
The default `None` keeps the existing rule that mutating verbs require
confirmation. `True` explicitly requires confirmation and is valid only on a
mutating verb. Existing consumers may continue using `include_confirmation`;
when both fields are set, they must agree.
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

`RegisteredCli` can export its built parser tree as stable JSON and generate a
bounded semantic-review checklist. `cli-extended` also loads a product-owned
TOML decision catalog, updates a marked Markdown section in the product's
canonical CLI spec, reports signature/stale-case changes, and supplies a pytest
collection assertion for exact node IDs and `cli_case` markers. Consumers keep
one runtime grammar in `CliRegistry` and their semantic decisions in the
catalog; the library owns the repeatable export, diff, and marker plumbing.
The checker sanity-checks supplied argv against route placement and argument
shape. It tracks recognized option arity, parser depth, `--` terminators, and
required parent positionals; minimum cases must supply required option values
and exactly one alternative from each required exclusive group. It does not
invoke parser converters or command handlers. The linked test must still run
the real invocation and assert its behavior and effects; the marker proves
test collection and linkage only.

Expose an import-safe function such as `example.cli:build_cli` that returns the
consumer's `RegisteredCli`, then run:

```bash
python -m cli_extended.surface_cli \
  --factory example.cli:build_cli \
  --review docs/cli-review.toml \
  --manifest docs/cli-surface.json \
  --spec docs/SPEC.md sync

python -m cli_extended.surface_cli \
  --factory example.cli:build_cli \
  --review docs/cli-review.toml \
  --manifest docs/cli-surface.json \
  --spec docs/SPEC.md check
```

Use the `template` action to print missing case rows and instructions for
changed decisions. Sync never edits the TOML or content outside the marked
spec region. Candidates are review prompts, not guessed executable commands or
predicted outcomes. The shared [consumer workflow](docs/CONSUMERS.md#adopt-the-generator)
shows the catalog, pytest marker, and adoption lifecycle; the [design guide](docs/DESIGN-GUIDE.md#keep-a-generated-surface-and-a-human-semantic-record)
explains why the Python registry remains the grammar source.
Each generated file is replaced atomically; a stop between the manifest and
spec updates leaves a detectable mismatch for `check`, not a half-written file.

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
