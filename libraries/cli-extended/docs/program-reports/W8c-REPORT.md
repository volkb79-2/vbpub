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
