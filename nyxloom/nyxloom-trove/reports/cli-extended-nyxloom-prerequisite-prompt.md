# Standalone prerequisite prompt: prepare cli-extended for Nyxloom adoption

This is a separate library task, not part of the Nyxloom-scoped P112 handoff.
`libraries/cli-extended` has no Nyxloom trove or dispatch metadata, so run this
as a normal repository work item in its own feature worktree. Complete and
merge it before dispatching P112, then repin P112's `input_revision` to the
post-merge Nyxloom base.

## Worktree and contract

Use `/workspaces/vbpub/.worktrees/cli-extended-nyxloom-api` on branch
`cli-extended-nyxloom-api`, created from current `main`. Commit only from that
worktree. Do not edit Nyxloom source or change any consumer in this task.

Contract class: **2a, design-bearing public-library change**. The required
consumer behavior and compatibility are fixed below. Private decomposition
may vary; changes to the documented Python API require updating the examples,
tests, and all four library documents in the same work.

## Context to read first

- `libraries/cli-extended/SPEC.md` §§1–12.
- `libraries/cli-extended/README.md`, sections “Adopt it” and “Generated
  documentation and contract tests”.
- `libraries/cli-extended/docs/CONSUMERS.md` and
  `libraries/cli-extended/docs/DESIGN-GUIDE.md`.
- `libraries/cli-extended/src/cli_extended/parser.py`: `VerbSpec`,
  `CliRuntime`, `RegisteredCli`, `CliRegistry`, common-option registration,
  runtime construction, and `run_cli()`.
- `libraries/cli-extended/src/cli_extended/output.py` and
  `libraries/cli-extended/src/cli_extended/__init__.py`.
- `libraries/cli-extended/tests/test_cli_extended.py`,
  `test_parser_specs.py`, `test_parser_edges.py`, and
  `test_output.py`.
- `libraries/cli-extended/pyproject.toml`, `run-gate.toml`, and
  `libraries/cli-extended/BACKLOG.md`.
- `scripts/debian-install-v2/debian_install_v2/wizard.py`,
  `scripts/debian-install-v2/wizard-requirements.txt`, and
  `scripts/netcup/install-host.py` prompt helpers. Reuse the repository's
  existing Questionary version policy; do not select a newer version by
  guesswork.

## Implementation packet (normative)

### Owned interfaces

1. Extend `VerbSpec` with
   `confirmation_required: bool | None = None`.
   - `None` preserves today's behavior: confirmation is required exactly when
     `mutating=True`.
   - `False` keeps the `mutating` catalog label but does not register `--yes`
     or claim that a confirmation is required.
   - `True` registers `--yes` and confirmation wording; reject it for a
     non-mutating verb.
   - This is a compatibility extension: every existing declaration behaves
     exactly as it does before this change unless it sets the new field.
2. Add the optional prompt API under `cli_extended.prompts` and expose it on
   each `CliRuntime` as `runtime.prompts`:

   ```python
   runtime.prompts.text(message, *, default=None, required=True) -> str
   runtime.prompts.password(message, *, required=True) -> str
   runtime.prompts.confirm(message, *, default=False) -> bool
   runtime.prompts.select(message, choices, *, default=None) -> str
   runtime.prompts.checkbox(message, choices, *, default=()) -> list[str]
   ```

   `choices` are a non-empty sequence of unique strings. A selected value must
   be one of those strings. Required text rejects whitespace-only values;
   optional text returns the supplied default or the empty string. Consumer
   schema/domain validation remains outside the library.
3. Define and export a `PromptDriver` protocol for those five interactions and
   accept an injected driver through `RegisteredCli.run(prompt_driver=...)`.
   The default driver is Questionary-backed. Import Questionary lazily only
   when the first prompt is used. Prompt collection must use the runtime's
   injected stdin/stdout policy; tests must not need a real terminal.
4. Export a stable `PromptCancelled` exception for Escape/question cancellation.
   It carries no field value. Preserve Ctrl-C as `KeyboardInterrupt` so
   `run_cli()` continues to own exit status 130. If either prompt stream is
   not a TTY, refuse before invoking the driver with a concise `CliFailure`
   that says an interactive terminal is required. If the optional dependency
   is absent, fail only when a real interactive prompt is requested and give
   an actionable install hint. Permit the caller to provide its distribution
   extra name so Nyxloom can say `nyxloom[interactive]` when it vendors the
   module.

### Required flow and ownership

1. Construct normal CLI identity, help, and output without importing
   Questionary.
2. On a prompt call, check both injected stdin and stdout are TTYs before
   importing or calling the driver.
3. Load the default Questionary driver lazily, or use the injected test driver.
4. Translate an Escape/cancel result to `PromptCancelled`; do not confuse it
   with a valid empty/false answer. Let Ctrl-C reach the outer CLI boundary.
5. Return collected scalar/list values only. Do not write files, validate
   domain schemas, decide confirmation policy, log secret answers, or persist
   defaults in the library.

### Bounds, errors, and tests

- Keep the base `cli-extended` install dependency-free. Add an `interactive`
  optional extra with the repository-approved Questionary pin, and a test
  extra/test-only fake driver as appropriate.
- Password answers are never echoed or included in normal/debug diagnostics.
- A canceled prompt must not yield `None`, `False`, `""`, or a partial
  checkbox result as if it were an answer; it raises `PromptCancelled`.
- Test injected TTY and non-TTY streams, missing extra, all five prompt kinds,
  invalid choices, required/optional empty text, defaults, Escape, Ctrl-C,
  password redaction, and driver injection. Assert observable output/results
  and that a refusing TTY path never called the driver.
- Preserve current `mutating=True` confirmation behavior for existing
  consumers. Prove the new explicit opt-out suppresses `--yes` and the
  confirmation requirement label while leaving the mutating label intact.
- All library tests must run under its declared gate, not in the devcontainer
  as ship evidence.

## Work

1. Implement the `VerbSpec.confirmation_required` compatibility extension.
2. Implement and export the prompt API, Questionary driver, injection seam,
   cancellation type, and lazy optional-dependency handling.
3. Add behavioral and adversarial tests for every contract above, including a
   legacy consumer declaration with no new field.
4. Update `SPEC.md`, `README.md`, `docs/DESIGN-GUIDE.md`, and
   `docs/CONSUMERS.md`. README states what the API provides, DESIGN-GUIDE why
   mechanics are shared while schema/persistence stay in consumers, and
   CONSUMERS contains a pasteable wizard example using a fake driver in tests.
5. Do not change the Nyxloom wheel, Nyxloom dependencies, or any Nyxloom CLI
   command in this library task.
6. Commit from the assigned worktree. Do not merge to `main` until the exact
   gate below is complete and the library work has received review.

## Oracles

- Old `VerbSpec(mutating=True)` still displays as mutating, exposes `--yes`,
  and confirms through the same default-no runtime path.
- `mutating=True, confirmation_required=False` is still accurately labeled
  mutating, does not accept `--yes`, and does not prompt.
- The five prompt methods return documented types under the injected driver;
  validation, cancellation, TTY refusal, and missing-extra failures match the
  contract and never leak secret input.
- Importing the base package, invoking help/version, and running noninteractive
  commands succeeds without Questionary installed. A call that actually asks
  for a prompt reports the extra install command instead of a traceback.
- API examples and cross-document anchors agree with the shipped signatures;
  every behavior above is asserted in tests that fail if the relevant contract
  is removed.
- Run the complete project gate and record every lane's actual status:

  ```bash
  cd libraries/cli-extended && ./run-gate.py gate
  ```

## Scope

Allowed: `libraries/cli-extended/src/`, `tests/`, `pyproject.toml`,
`SPEC.md`, `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, and
`BACKLOG.md` only when a relevant entry needs a disposition.

Forbidden: Nyxloom runtime/docs/configuration, any other consumer, a base
runtime dependency on Questionary, or a local workaround in a consumer.

## BLOCKED rule

Write `BLOCKED: <specific reason>` in the library task log and stop if the
library gate cannot test the real Questionary boundary without a network call,
if preserving existing `mutating=True` behavior requires changing consumers,
if an optional install cannot remain lazy for help/version/noninteractive
paths, or if the work requires editing a path outside the allowed scope. Do not
silently change the API contract above or route around the library boundary.
