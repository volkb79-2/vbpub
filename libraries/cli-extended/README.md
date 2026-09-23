# cli-extended

`cli-extended` is the shared, stdlib-first CLI contract layer for user-facing
Python tools in `vbpub`. Its Python import name is `cli_extended`.

The library keeps `argparse` and the standard `logging` package familiar while
owning repetitive shell behavior: identity/version output, grouped help with
the overall CLI description and aligned verb summaries, parser registration,
common options, clean cancellation/errors, confirmation
prompts, output streams, redaction, and progress presentation. It has no
runtime dependencies.

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


def status(args: argparse.Namespace, runtime) -> int:
    result = {"server_id": args.server_id, "filter": args.filter, "state": "ready"}
    runtime.output.primary(result if runtime.json_mode else f"state: {result['state']}")
    return 0


def apply(args: argparse.Namespace, runtime) -> int:
    # A real handler loads and validates its complete change before consent.
    if not runtime.confirm(f"Apply {args.path} to production?"):
        return 0
    # A real handler performs exactly the confirmed domain mutation here.
    runtime.output.info(f"Applied {args.path}.")
    return 0


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
for positionals. `VerbSpec.synopsis` is optional: by default the library derives
the command synopsis from required positionals, required options, and required
or optional mutually-exclusive option groups. Set it only when a public syntax
shape cannot be represented by those declarations. For example, options that
accept either a file or inline JSON can declare the same
`mutually_exclusive_group="configuration-source"` and
`mutually_exclusive_required=True`; the parser then both enforces the choice
and displays `(--config FILE | --config-json JSON)` without a second, drifting
usage string. Use `VerbSpec.configure` only for genuinely custom parser
structures such as nested sub-actions.

Put options that apply before command selection (for example, a config-file
path) in `CliRegistry.global_options`. Put options that belong to one command
in that verb's `options`; put standard output/debug controls at the appropriate
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
severity tags.

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
its own fakes or state probes.

See [`SPEC.md`](SPEC.md) for the complete behavioral contract;
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for install, migration, and
responsibility guidance; and [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md)
for the design rationale.

## Test and gate

The package gate covers R0/R1/R2 plus an independent R3 canary. R1 requires
100% statement and branch coverage for every shipped `cli_extended` module.
The tests and canary run in `tester-unified`, not in the devcontainer cockpit:

```bash
cd libraries/cli-extended
./run-gate.py gate
```

The Debian installer and the Netcup entrypoints are the current first-party
adopters in this scoped migration.
