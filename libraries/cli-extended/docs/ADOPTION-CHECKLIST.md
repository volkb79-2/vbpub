# cli-extended adoption checklist

Every library feature a tool should use, with what correct use looks like and
the hand-rolled pattern it removes. `cli-extended audit` verifies the rows
marked `audit:<check>`; rows marked `manual` need judgement and are handled by
the packaged `cli-extended-adoption` skill (see
[Adopting cli-extended end to end](CONSUMERS.md#adopting-cli-extended-end-to-end)).

Row ids (`AC-NN`) are stable: never renumber, never reuse a retired id. An
`audit:` check name appears in exactly one row, and a test keeps this file and
the audit code in step. Checks labelled `heuristic:` in the audit output are
text scans of the project sources and can be wrong; the audit names the files it
matched.

## Identity and version

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-01 | The version comes from `CliIdentity.resolve(...)`: installed metadata and/or an absolute VERSION file, which must agree, with no literal fallback. | Regex or `importlib.metadata` readers feeding `CliIdentity(...)`; a hard-coded fallback version. | audit:version-source | [Install and choose a version source](CONSUMERS.md#install-and-choose-a-version-source) |
| AC-02 | `CliIdentity` carries the product name, a long name and the executable `command` that equals the configured CLI id. | Hand-written `--version` strings and banner text. | manual | [README](../README.md#adopt-it) |

## Registration and grammar

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-03 | Every verb, argument and option is declared with `CliRegistry`, `VerbSpec`, `ArgumentSpec` and `OptionSpec`; handlers receive a `CliRuntime`. | Hand-built `argparse` parsers and dispatch tables. | manual | [Turn an interface inventory into registrations](CONSUMERS.md#turn-an-interface-inventory-into-registrations) |
| AC-04 | A verb's synopsis is derived from its declared arguments and options; a `synopsis=` override exists only when the derived line misleads. | Copy-pasted usage strings that drift from the grammar. | audit:synopsis-overrides | [Turn an interface inventory into registrations](CONSUMERS.md#turn-an-interface-inventory-into-registrations) |
| AC-05 | No consumer option reuses a library-owned control (`--dry-run`, `--yes`, `--json`, `--traceback`, `--no-color`, `--color`, `--quiet`, `--debug`, `--verbose`, `--log-level`, `--progress`, `--debug-raw`, `--help`, `--version`). | Hand-rolled copies of the shared controls. | audit:shadowed-controls | [Replacing hand-rolled version lookup, exception wrappers, and --dry-run](CONSUMERS.md#replacing-hand-rolled-version-lookup-exception-wrappers-and---dry-run) |
| AC-06 | Option conflicts, requirements and one-of rules are declared with `Requires`, `Conflicts` and `RequiresChoice`. | Handler-side `if a and b: error` checks. | manual | [Replacing handler-side option checks and name-list parsing](CONSUMERS.md#replacing-handler-side-option-checks-and-name-list-parsing) |
| AC-07 | A `configure` callback is used only for syntax the declarative specs cannot express. | Callbacks that add plain positionals and options. | audit:configure-callbacks | [Replacing handler-side option checks and name-list parsing](CONSUMERS.md#replacing-handler-side-option-checks-and-name-list-parsing) |
| AC-08 | A hidden option is internal or deprecated, and the reason is recorded. | Undocumented public behaviour behind hidden flags. | audit:hidden-options | [README](../README.md#generated-documentation-and-contract-tests) |
| AC-09 | Comma- or space-separated name lists use `SelectorList`. | Ad hoc `split(",")` parsing and its error messages. | manual | [Replacing handler-side option checks and name-list parsing](CONSUMERS.md#replacing-handler-side-option-checks-and-name-list-parsing) |

## Runtime policy

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-10 | `CliRegistry(unexpected_exceptions="report")`: an unexpected exception becomes one `[ERROR]` line, exit 1, with `--traceback` for the stack. | `try/except Exception` wrappers around `main`. | audit:exception-policy | [Replacing hand-rolled version lookup, exception wrappers, and --dry-run](CONSUMERS.md#replacing-hand-rolled-version-lookup-exception-wrappers-and---dry-run) |
| AC-11 | Domain failures raise `CliFailure` (or are listed in `expected_exceptions`) with the right exit code. | `print(...)` to stderr followed by `return 1` or `sys.exit`. | manual | [Consumer responsibilities](CONSUMERS.md#consumer-responsibilities) |

## Confirmation and dry-run

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-12 | Every mutating verb has confirmation (`--yes`) or `dry_run=True`, or a recorded reason it needs neither. | Mutating verbs that act immediately and silently. | audit:mutation-safety | [Replacing hand-rolled version lookup, exception wrappers, and --dry-run](CONSUMERS.md#replacing-hand-rolled-version-lookup-exception-wrappers-and---dry-run) |
| AC-13 | Questions go through `runtime.confirm` and the shared prompt driver, after preflight and respecting `--dry-run`. | `input("Continue? [y/N]")` and custom prompt loops. | manual | [Interactive data collection](CONSUMERS.md#interactive-data-collection) |

## Output

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-14 | Results go through `runtime.output` (`primary`, levelled messages); read verbs offer `--json`; logging uses the tool's logger namespace. | `print` calls and hand-rolled `--json` and verbosity flags. | manual | [JSON and progress](CONSUMERS.md#json-and-progress) |
| AC-15 | Long operations use the shared progress renderer and the shared colour policy. | Custom spinners and ANSI escapes. | manual | [Color and terminal presentation](CONSUMERS.md#color-and-terminal-presentation) |

## Surface and semantic review

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-16 | `review`, `manifest` and `spec` are configured for the CLI in `cli-extended.toml` or `[tool.cli-extended]`. | A bespoke surface-export script and a hand-maintained CLI spec. | audit:surface-configured | [Adopt the generator](CONSUMERS.md#adopt-the-generator) |
| AC-17 | `cli-extended surface check` passes: manifest and spec region are current, the catalog is judged, no open blocker or major finding remains. An agent reviews the surface with `surface pack` and the review skill. | Unreviewed grammar changes; hand-edited manifests. | audit:surface-check | [The review loop](CONSUMERS.md#the-review-loop) |
| AC-18 | The exported surface is syntax-complete: every route's arguments, options and constraints are declared. | Grammar hidden inside `configure` callbacks or handlers. | audit:surface-complete | [Review caller surfaces and command semantics](CONSUMERS.md#review-caller-surfaces-and-command-semantics) |

## Skills

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-19 | Agent skills ship as package data through `register_skills_verbs`, every packaged `SKILL.md` validates, and no `.claude/skills` source tree is the source of truth. | Copied skill folders and ad hoc install scripts. | audit:skills-packaged | [Ship your agent skills](CONSUMERS.md#ship-your-agent-skills) |

## Doctor

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-20 | A tool with an environment to verify registers a `doctor` verb with named checks. | Setup docs and one-off diagnostic scripts. | audit:doctor | [Add a doctor verb](CONSUMERS.md#add-a-doctor-verb) |

## Tests

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-21 | Tests call `assert_cli_contract` at the rigor level the tool needs. | Hand-written help and exit-code assertions. | manual | [Tests required for an adoption](CONSUMERS.md#tests-required-for-an-adoption) |
| AC-22 | When a review catalog exists, pytest runs with the `cli_extended.pytest_plugin` plugin so review cases link to real tests. | Hand-maintained lists of test ids. | audit:pytest-plugin | [Generated documentation and contract tests](../README.md#generated-documentation-and-contract-tests) |
| AC-23 | Tests run scripts and modules through the library invoker helpers (`invoke_script`). | Per-project `subprocess` wrappers. | manual | [Generated documentation and contract tests](../README.md#generated-documentation-and-contract-tests) |

## Packaging and dependency

| ID | Requirement | Replaces | Verification | Docs |
| --- | --- | --- | --- | --- |
| AC-24 | A packaged tool declares `cli-extended>=X.Y.Z` in `[project].dependencies` and does not map `cli_extended` through `package-dir`; standalone scripts use the installed library. | Vendored copies and source-tree dependencies. | audit:dependency-declared | [Package the same library revision that was tested](CONSUMERS.md#package-the-same-library-revision-that-was-tested) |
| AC-25 | No project source puts the library checkout on `sys.path` or `PYTHONPATH` (gate lanes that test the library itself are exempt). | `sys.path.insert(0, ".../libraries/cli-extended/src")`. | audit:no-path-hacks | [Package the same library revision that was tested](CONSUMERS.md#package-the-same-library-revision-that-was-tested) |
