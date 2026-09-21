# cli-extended consumers

`cli-extended` is a shared library, not a required repository bootstrap
dependency. An adopter can install it from this checkout while migrating a
CLI, or build its independently packageable wheel.

## Install from this checkout

From the repository root:

```bash
python3 -m pip install --editable ./libraries/cli-extended
python3 -c 'from cli_extended import CliIdentity; print(CliIdentity("X", "1.0.0", "Example").headline)'
```

The package has no runtime dependencies. The editable install is convenient
for a local migration; a release or isolated consumer should install the
wheel produced from `libraries/cli-extended/` instead.

## Add the shared shell to an argparse CLI

The smallest complete pattern is shown in the package
[`README.md`](../README.md). A consumer supplies its own authoritative version,
verb metadata, handlers, configuration, API client, and mutation policy:

```python
from cli_extended import (
    CliIdentity,
    ExtendedArgumentParser,
    HelpCatalog,
    VerbSpec,
    add_common_options,
    run_cli,
)

identity = CliIdentity(
    name="TOOL",
    command="tool",
    version="1.2.3",  # derive this from the consumer's package metadata
    long_name="Tool Long Name",
)
catalog = HelpCatalog(
    identity,
    prog="tool",
    verbs=(VerbSpec("status", "", "show current state"),),
)
parser = ExtendedArgumentParser(
    prog="tool", identity=identity, catalog=catalog, top_level=True
)
add_common_options(parser, identity)
subparsers = parser.add_subparsers(dest="verb", required=True)
status = subparsers.add_parser("status")
add_common_options(status, identity, suppress_defaults=True)
```

Use `run_cli` with a handler mapping and the command parser mapping. Handlers
receive `CliRuntime`; use `runtime.output` for diagnostics/results and
`runtime.progress()` for long operations.

## Verify an adoption

```bash
PYTHONPATH=libraries/cli-extended/src pytest -q libraries/cli-extended/tests
ruff check libraries/cli-extended
ruff format --check libraries/cli-extended
```

The consumer's own contract tests must additionally prove its real entrypoint
has side-effect-free help/version, complete help after local parse errors,
clean Ctrl-C handling, correct JSON/stdout separation, and redacted output.
