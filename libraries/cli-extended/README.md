# cli-extended

`cli-extended` is the shared, stdlib-first CLI contract layer for user-facing
Python tools in `vbpub`. Its Python import name is `cli_extended`.

It keeps the conventions already used by CIU and CMRU while making the
repository-wide contract reusable:

- `argparse` remains the parser, with no undocumented `-h` alias;
- `logging` remains available through a compatible stderr handler;
- help and diagnostics begin with one authoritative identity line;
- `help`, `version`, command-help-on-error, Ctrl-C, and exit statuses are
  handled consistently;
- human diagnostics use `[INFO]`, `[WARN]`, `[ERROR]`, and `[DEBUG]` tags;
- colour is TTY-aware and respects `NO_COLOR`/`--no-color`;
- `--log-level`, `--debug`, `--debug-raw`, `--quiet`, `--yes`, and progress
  modes share one interpretation; and
- progress supports TTY, plain, quiet, and JSONL output without contaminating
  a command's primary stdout result.

The package has no runtime dependencies. Rich or another renderer may be used
by an adopting CLI around this contract, but is not required to get stable
plain output or a usable non-TTY fallback.

## Minimal adoption

The application owns its version, verbs, handlers, API clients, and mutation
policy. `cli_extended` owns the common shell around them:

```python
import argparse

from cli_extended import (
    CliIdentity,
    CliRuntime,
    ExtendedArgumentParser,
    HelpCatalog,
    VerbSpec,
    add_common_options,
    run_cli,
)


identity = CliIdentity(
    name="EXAMPLE",
    command="example",
    version="1.2.3",
    long_name="Example Operator Tool",
)
catalog = HelpCatalog(
    identity,
    prog="example",
    getting_started=("example status",),
    verbs=(VerbSpec("status", "", "show current state"),),
    global_options=(
        ("--help", "show this help and exit"),
        ("--version", "print the short version and exit"),
        ("--log-level LEVEL", "set diagnostic verbosity"),
    ),
)
parser = ExtendedArgumentParser(
    prog="example",
    identity=identity,
    catalog=catalog,
    top_level=True,
)
add_common_options(parser, identity)
subparsers = parser.add_subparsers(dest="verb", required=True)
status = subparsers.add_parser("status", help="show current state")
add_common_options(status, identity, suppress_defaults=True)


def handle_status(args: argparse.Namespace, runtime: CliRuntime) -> int:
    runtime.output.primary({"state": "ready"} if runtime.json_mode else "ready")
    return 0


raise SystemExit(
    run_cli(
        parser,
        {"status": handle_status},
        identity=identity,
        command_parsers={"status": status},
    )
)
```

When called for a `top_level=True` parser, `add_common_options` also adds its
standard option metadata to the `HelpCatalog`; the grouped top-level help does
not require a second hand-maintained copy of those options.

Callers that already use a project-local parser can adopt the smaller pieces
individually: `CliIdentity`, `CliOutput`, `ProgressRenderer`,
`install_logging`, and `redact_value` do not require the dispatcher.

The observable contract is documented in
[`docs/CLI-STANDARD.md`](../../docs/CLI-STANDARD.md). The design rationale and
consumer adoption notes are in [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md)
and [`docs/CONSUMERS.md`](docs/CONSUMERS.md).
