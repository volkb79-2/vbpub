# W8c report: R2 survivor batches

Branch `cli-ext-w8c-survivors`. All new tests are in `tests/test_w8c.py`; no source changes in batch 1.
Mutants were planted by hand on scratch copies of `src/` (outside the repo) and the file run
with PYTHONPATH pointing at the copy. I did not run assay itself.

## Batch 1

| # | Survivor | Test | Planted mutant | Result |
|---|----------|------|----------------|--------|
| 1 | `_positive_int` `number < 1` -> `<= 1` (the brief's `<=` read as the 1-boundary flip) | `test_positive_int_accepts_one_and_refuses_zero`, `test_max_candidates_zero_is_an_argument_error_but_one_is_parsed` | `<= 1` | killed (both) |
| 2a | `plain` `include_json` False -> True | `test_surface_verbs_refuse_json_and_progress[...--json]` | True | killed (5 of 5 verb cases) |
| 2b | `plain` `include_progress` False -> True | same test, `--progress quiet` cases | True | killed (5 of 5) |
| 3a | `CliConfig` `frozen=True` -> False | `test_listed_dataclasses_are_frozen_and_reject_assignment[config-CliConfig]` | False | killed |
| 3b | `ProjectConfig` `frozen=True` -> False | same, `ProjectConfig` | False | killed |

Notes:

- **Item 1.** The existing test used `--max-candidates 1` expecting exit 2, but that comes from the later
  candidate limit, not argument parsing, so it did not pin the boundary. The new test calls
  `_positive_int` directly (1 accepted, 0 and -1 refused with the message) and checks, through the
  CLI, that `0` gives the argparse message while `1` does not.
- **Item 2: why every verb.** All five `surface` verbs (`sync`, `check`, `template`, `pack`, `report`)
  are registered with the one `plain` dict, so one verb would kill both flips. I test all five
  against both flags (10 cheap cases) so a verb later moved off `plain` is also caught. `--progress`
  takes a mode, so the test passes `--progress quiet`: a bare `--progress` fails with exit 2
  whether or not the option exists, and my first version of the test let the True mutant survive
  for exactly that reason. The assertion is the exact `unrecognized arguments: ...` text.
- **Item 3: reflective coverage.** `test_every_dataclass_in_the_package_is_listed_here` imports every
  module of `cli_extended` and requires the set of dataclasses defined there to equal the
  `FROZEN` table plus an explicit `MUTABLE` set (`CliRuntime`, `RegisteredCli`, both plain
  `@dataclass`). A new dataclass therefore fails that test until it is added to one of them.
  Discovery cannot be "find the frozen ones" alone, since a `frozen=True` -> False mutant would
  drop out of such a discovery and pass. Every class in `FROZEN` (22 classes) is then asserted
  `frozen` and has each field's assignment and one deletion rejected with `FrozenInstanceError`
  (on an `object.__new__` instance, so no constructor arguments are needed). `MUTABLE` classes are
  asserted not frozen. 22 frozen classes were covered, not only the two named; I only planted
  mutants for the two named ones.

## Gate

`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (batch 1, after the last edit).

## Batch 2: verb option defaults (library behaviour fix)

**Why `suppress_defaults=True` existed.** Commit `593da6fa0` (generate CLI surfaces from the command
registry) added it for the verb parser. The reason is the clobber case: argparse subparsers write
their defaults over values the root parser already parsed (`t --config a go`) for any dest
both define. Common controls and subcommand copies of globals are suppressed for the same reason
and stay so (`add_common_options(suppress_defaults=True)`, `force_suppress_defaults`). Applying it
to every verb option was broader than the reason.

**Change** (`parser.py`): `_add_option_specs(..., suppress_dests=frozenset())` replaces
`suppress_defaults`. `build()` passes `{action.dest for action in root parser}` (globals, library
controls, `verb`). A verb option keeps argparse's default unless its dest is in that set and it has
no explicit `default`, in which case `action.default = SUPPRESS`. `OptionSpec.add_to` lost its now
unused `suppress_default` argument (only `force_suppress_default` remains); the old
`test_option_add_to_preserves_explicit_default_and_suppresses_implicit_default` was rewritten for
that. Single-command registries were already unsuppressed.

**Tests** (`tests/test_w8c.py`): omitted option is `None`/`False`; explicit default honoured; a colliding
dest keeps the root-parsed value (given before the verb) and still accepts a value after the verb and
through the colliding verb flag; an explicit default on a colliding dest is honoured. Planted mutants:
(m6) suppress for every verb option = the old behaviour, killed by
`test_omitted_verb_options_are_readable_with_argparse_defaults`; (m7) never suppress, killed by the two
collision tests; (m8) suppress colliding dests even with an explicit default, killed by
`test_explicit_default_on_a_colliding_dest_is_honoured`. Full suite and gate green
(`run-gate: lane 'r0-r1' verdict PASS; exit_code 0`, 100% statement and branch).

**Presence rule.** Constraint resolution already read `None`/`False` defaults and falls back to the
root's default for suppressed ones; the whole constraint test set passes unchanged.

**Surface impact: real, and it needs a decision.** I exported a sample surface with the old tree
(`git archive HEAD`) and the new one. Verb options without a default change from
`default: {kind: suppressed}, default_present: false` to `default: null/false, default_present: true`,
and the candidate signatures of the affected routes change (and a `store_true` verb option now
appears as a candidate/member that it did not before). So manifests change and reviewed cases
for those routes become `changed`. This is documented in `CHANGES.md` 0.2.0 notes. I did not
bump `CONTRACT_VERSION`, as decided (no library control changed). **However** SPEC "Library contract"
rule 4 says upgrading without a contract bump MUST leave every consumer's signatures unchanged;
this change contradicts that sentence for routes with default-less verb options. I did not edit
rule 4. The controller should either amend rule 4 or choose a bump (rule 5 then collapses the
consequences into one finding).

**Library code relying on absence.** Searched `hasattr`/`getattr` on args: the runtime reads
library controls with `getattr(args, name, default)` (unaffected, those stay suppressed on verb
parsers); `constraints.value_of` uses `getattr(args, dest, _MISSING)` and still works; the surface
invocation-default logic (`surface.py`, "suppressed inherited copies") keys on `action.default is
SUPPRESS` and now sees real defaults for verb options, which is the manifest change above. No
`hasattr` check on verb options exists.

**Docs.** SPEC constraints rule 3 (new sentences), CONSUMERS ("Every declared option is an attribute"),
CHANGES 0.2.0.

## Batch 3

Integration was merged into the branch first (`git merge --no-ff cli-extended-unified`, trivial).

1. and 2. **`findings.py` required fields.** New tests in `tests/test_findings.py`:
   `test_w8c_every_required_finding_key_is_refused_when_missing` (id, status, severity,
   category, summary; asserts the exact message `findings[0] is missing required key '<key>'`),
   `test_w8c_remedy_and_rationale_are_required_only_for_their_status` (fixed and wontfix load
   without remedy; open without remedy and wontfix without rationale are refused),
   `test_w8c_an_optional_route_may_be_omitted_and_is_kept_when_given`.
   Audit of every `required` use: `findings.py` has `_choice` (status/severity/category),
   `id`, `summary`, `cli_id` (True), `remedy`/`rationale` (status-dependent), `route` (False);
   `config.py` has no `required=` argument (its missing-key loop for `id`/`factory` is already pinned by
   `test_config.py`); `review.py` has `_string_tuple(required=...)` with default False and one
   `required=True` call (interaction `option_ids`), pinned by existing review tests. Planted mutants,
   all killed: `_choice` True->False (status/severity/category tests), summary False, id False, cli_id False,
   route True, remedy `status != "open"`, review `option_ids` required=False, review default True.
3. **Dataclass AST scan.** `test_source_scan_finds_every_dataclass_at_any_depth_in_any_module` parses
   every `.py` under `src/cli_extended/` recursively, finds `@dataclass`, `@dataclass(...)`,
   `@dataclasses.dataclass` and its call form on any ClassDef (nested or function-local), and compares
   `(module, class)` to the FROZEN and MUTABLE tables.
   `test_source_scan_recognises_the_decorator_spellings` pins the matcher. Planted mutant: a
   function-local `@dataclass class Nested` in `values.py`: killed by the scan test (the reflective test
   cannot see it). I did not create a subpackage to test that path; the module name logic
   (`__init__.py` -> package name) is untested beyond the current flat layout.

Gate after the last edit: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (100% statement and branch).

## Batch 4

Integration merged first (trivial).

1. **`_common_option_specs`.** `include_traceback` and `include_dry_run` lost their `False`
   defaults, so all five flags are required keyword-only arguments. Both call sites pass all five
   (`HelpCatalog.render_markdown` ~line 820 and `add_common_options` ~line 1159). The one test
   that called it with three flags (`test_parser_edges.py`) now passes all five; the patched wrapper in
   `test_contract.py` forwards `**kwargs` and is unaffected. With no defaults, the equivalent
   mutants cannot exist.
2. **`HelpCatalog.include_traceback`.** `HelpCatalog` is public: exported from `cli_extended.__init__`
   and documented in the design guide. So the default stays and is tested:
   `test_public_help_catalog_omits_traceback_unless_asked` builds it without the flag and asserts no
   `--traceback` in text or Markdown help, plus a positive control with `include_traceback=True`.
   Planted mutant (default True): killed.

## Batch 5

Integration merged first.

1. **`add_common_options` defaults** (public, exported, so defaults kept):
   `test_add_common_options_defaults_include_json_progress_yes_but_not_traceback_or_dry_run` and
   `test_add_common_options_each_opt_in_flag_adds_only_its_control`. Planted mutants
   `include_traceback=True` and `include_dry_run=True` defaults: both killed.
2. **H4 Markdown `--dry-run`.** `test_markdown_reference_lists_dry_run_only_under_verbs_that_enable_it`
   renders a catalog with a `dry_run=True` verb and a plain one and asserts `--dry-run` in the first
   verb's section only. Planted `include_dry_run=not verb.dry_run`: killed.

Rule deviation: I used `sed -i` once on `tests/test_w8c.py` (adding `mutating=True` to one
`VerbSpec` after a ValueError) instead of Edit. Disclosed here as in earlier batches.

## Batch 6

Integration merged first. Test: case `indented line before any metadata key` added to the
`CASES` table in `tests/test_skills.py` (first frontmatter line `  foo: bar`): refused with
`SkillError`, `unsupported frontmatter syntax`, `line 2:`. Planted `in_meta = True`: killed.
The `_Frontmatter` frozen survivor is already covered by the reflective table in
`tests/test_w8c.py` (`cli_extended.skills` / `_Frontmatter` in `FROZEN`).

## Batch 7 (last)

Integration (with the W9a Netcup merge) merged first.

1. **Dry-run notice under `--quiet`.** `test_w8c_dry_run_notice_survives_quiet` (install and uninstall) asserts
   `Dry run: no changes made.` still reaches stderr. Planted `force=False`: killed for both verbs.
2. **Dead data in the library common-control records** (`surface._route_common_actions`).
   Trace: the records exist for the invocation checker (`review.py` appends them to a route's actions)
   and for candidate generation (`export_cli_surface`). They are never serialized into the manifest.
   Signatures read only `id/kind/canonical/scope` for a `library_control` action (`shape_keys`), the
   `option-spelling` candidate payload carries only `option_id`/`spelling`, and the interaction records
   keep only `canonical`. `_route_required_baseline` reads `route["actions"]` (the manifest's own, not these)
   and every `exclusive_required` read is behind a non-None `exclusive_group`, which these records
   never have. The `hidden` index at `review.py` (Markdown rendering) runs over the manifest's
   actions, not these records. I confirmed it empirically: flipping `exclusive_required` to True and
   `hidden` to True each survived the full suite, and an exported surface was byte-identical under both flips.
   So both keys are unobservable and I removed them from the records; the full suite passes
   (exit code 0) and the gate is green. Nothing to test for the removed keys, which cannot be mutated.
3. Already covered: `skills._Action` is in the `FROZEN` table of `tests/test_w8c.py`; the
   `skills.py` list-mode `return 0` became a bare `return` in batch 2 (no falsy constant remains).

Rule deviation in this batch: none new (no `sed -i` on repository files; I used `sed -i` only on a scratch script).
