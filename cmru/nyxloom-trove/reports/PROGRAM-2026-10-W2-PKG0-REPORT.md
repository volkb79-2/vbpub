# W2-PKG0 REPORT: registry factory and target selector (cmru `cli_support.py`)

Branch `cmru-w2-pkg0` from `839e8841c`. Files: `cmru/src/cmru/cli_support.py`,
`cmru/tests/test_cli_support_registry.py` (new), `libraries/cli-extended/BACKLOG.md`.
No edit to `cli.py` or any delegate module, and no shim.

## Delivered
1. `cmru_registry(prog, description, **kw)`.
   - Identity: `CliIdentity.resolve(name="CMRU", distribution="cmru", ...)` (D2). There is no literal fallback; a missing distribution raises `VersionLookupError`.
   - Policy: ONE constant, `UNEXPECTED_EXCEPTIONS_POLICY = "raise"`. `unexpected_exceptions` and `identity` are refused as keywords (`TypeError`).
   - `--log-prefix-time-short`: `_ShortTimePrefixAction` and `cmru_presentation_options()` were already in this module. The factory is now the only place that passes them, and a caller's `global_options` extends rather than replaces them.
   - Logger: defaults to `"cmru"`, overridable. Other `CliRegistry` keywords pass through.
2. `target_argument()` returns the optional positional `[all|PROJECT[,PROJECT...]]` with `type=SelectorList()`, `nargs="?"`, `default=None`.
3. `select_target_names(...)` is the adapter. It kept its name and signature, so current importers are unaffected.
   - `raw` may be the legacy string (the old path, unchanged) or the library result (`None`, `SelectorList.ALL`, or a name tuple).
   - Semantics unchanged: `all` is exclusive and yields declared order, unknown names raise `TargetSelectionError`, results come back in declared order, and an omitted target resolves to the context project or the whole estate.
   - Programmatic tuples get the same empty, duplicate and `all`-mix refusals as the string path.
4. Backlog: CLI-EXT-24 (version probe, N1, with N2 in its "Related" block) and CLI-EXT-25 (selector cardinality, N3), both in the existing entry style.

## Decisions and deviations
- `cmru_version()` and `cmru_headline()` are untouched: they still call `cli._cmru_version` (git-describe). Until PKG-1 drops `_source_tree_version`, a factory-built registry's identity (installed metadata) can differ from the parser headline. This is expected and is PKG-1's to close.
- The factory has no callers yet (PKG-1/2 switch over). The `cmru_identity` helper stays for the same reason.
- Behaviour change visible at the library parse step, for PKG-1 to accept knowingly (CONSUMERS.md lists it): with `target_argument()`, structural errors (empty item, duplicate, `all` mix) become argparse usage errors (exit 2, library wording), not `TargetSelectionError` / CONFIG_ERROR. Unknown names still go through the adapter and the CONFIG_ERROR path. The adapter's own string path is unchanged.
- The full suite ran with `-x` by mistake (the brief says no maxfail). It was green, so nothing was cut off.
- All repo edits went through Edit/Write. The only scratch script was `scratchpad/pk0.sh` (a pytest wrapper), outside the repo.

## Tests (`tests/test_cli_support_registry.py`, 31 tests)
- Factory: the identity source (monkeypatched `installed_version`) and the absence of a fallback; the policy constant is used (monkeypatched to `"report"` and a parent/delegate `build()` succeeds); `TypeError` on owned keywords; logger and passthrough; the option is declared once and `global_options` extends it; a delegate built with the factory parses `--log-prefix-time-short`, with the env effect.
- Selector: parse results for omitted, `all` and a list. The adapter is compared against `cli._select_projects`, the live oracle, over `all`, whitespace, one name, several names and given-order permutations. Also covered: ALL beats the context project, omitted resolves to context or estate, the ALL path skips orchestration names that are not loaded, unknown names, malformed tuples, an empty tuple, and library refusals matching the legacy refusals.

| Plant (by hand, restored after) | Result |
|---|---|
| Factory hardcodes `unexpected_exceptions="raise"` instead of the constant | killed by `test_registry_uses_the_single_policy_constant` |
| Adapter drops the ALL sentinel (`return None`) | first attempt SURVIVED (omitted and ALL coincide without a context); added `test_adapter_explicit_all_beats_the_context_project`; now killed |

Full suite via `pt.py` (serial, flock, nice/ionice, PSI full avg60 about 1): 3249 passed, 2 skipped, 20 subtests.
Gate lanes: the first run of `coverage` and `canary` at commit `a7272ace5` FAILED at 99.99%. The only miss was `cli_support.py:168-169`: `parse_target_names(None)` became unreachable once the adapter normalises `None` first. Fixed with `test_legacy_string_parser_keeps_its_contract`; the lanes were then re-run on the follow-up commit (verdicts in the hand-back message).
