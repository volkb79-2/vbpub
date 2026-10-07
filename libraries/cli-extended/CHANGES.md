# Changelog

All notable changes to cli-extended are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before the
release gate runs. The hand-written section below carries what a consumer must
know when upgrading; it is not generated.

## Contract and upgrade notes

### 0.4.0 — fallback verb and doctor exit code/detail lines (LCR-1, LCR-2)

Library contract version is unchanged (1) and the surface manifest schema is
unchanged (7). Both additions are opt-in: a tool that uses neither behaves, and
exports its manifest and doctor JSON, byte-for-byte as before.

- **Fallback ("default") verb (LCR-1).** `VerbSpec(fallback=True)` marks one verb
  that runs when the first token is not a verb: `tool LANE ...` is `tool run LANE ...`.
  A registry may have at most one; it must declare at least one `ArgumentSpec`,
  cannot use `delegate=`, and is refused in a `single_command` registry (`ValueError`).
  `run_cli` and the new `RegisteredCli.parse_args(argv)` (which runs no handler)
  share one normalisation: leading options are scanned using the root parser's
  options **plus** the fallback verb's own, and the fallback name is put
  **first** (`tool --worktree X LANE` becomes `tool run --worktree X LANE`). A
  `--` or an unknown leading option, a leading `--help`/`--version`, and an
  empty argv are never rewritten (empty argv still prints help, exit 0).
  Registered verbs, delegates, `help` and `version` always win, so a lane named
  like a verb needs the explicit `tool run NAME` spelling. Errors are never
  swallowed: `tool docter` becomes `tool run docter` and fails in the handler.
  `RegisteredCli.fallback_verb` names the verb (`None` when absent).
- **Help.** The catalog marks the verb `[default verb]`, adds the usage line
  `tool [options] ARG ...`, and the verb's help shows `Behavior: default verb.`;
  `help <verb>` works as for any verb.
- **Surface export.** The route record carries `"fallback": true` only for that
  verb (the key is absent for every other route), the review text says "a first
  token that is not a verb selects this verb", and the key is part of the route's
  review-signature context. A tool without a fallback verb exports an identical
  manifest, so no `surface sync` is needed on upgrade. `surface check`, `audit`
  and `assert_cli_contract` work unchanged with a fallback verb present.
- **`register_doctor(..., fail_exit_code=1)` (LCR-2).** The exit status when any
  check fails; an integer 1..255 (`ValueError` otherwise, including bools).
  Clean and warn-only runs still exit 0. The default keeps today's exit 1.
- **`CheckResult(lines=())` (LCR-2).** Single-line strings printed in human output,
  indented four spaces under the check line and before `remedy:`. JSON adds a
  `"lines"` array to a check only when it is non-empty, so the doctor JSON of
  every existing tool is unchanged. A non-tuple or a multi-line entry is a `ValueError`.
- **Notes.** `VerbSpec.summary` and the route record's `summary` for the fallback
  verb now include `default verb` in the bracketed behavior labels; the route's
  `behavior` list is unchanged (the fallback marker is its own key).

### 0.3.0 — library backlog fixes (CX-BACKLOG, 2026-10-06)

Library contract version is unchanged (1). Behaviour changes a consumer can see:

- **Release policy (operator ruling 2026-10-06).** The CLI-EXT-27 testing-helper
  behaviour change below ships in a 0.x minor without a contract-version bump
  (the contract concerns the CLI controls, not the test helpers); 0.x minors may
  carry such documented behaviour changes.
- **Notes.** The CLI-EXT-28 marker is honoured in non-test files too, by design.
  `CliIdentity.resolve(version_probe=...)` (CLI-EXT-24) has no timeout: a caller
  wrapping a slow command (for example `git describe`) must bound it itself.
  An `exit_code` (an expected exception's, or `CliFailure`'s) outside 1..255,
  including 0, negatives, values above 255 and bools, becomes 1.

- **`cli_extended.testing` no longer edits `PYTHONPATH` implicitly (CLI-EXT-27).**
  `invoke_script`/`invoke_module` used to prepend the imported library's
  directory (a whole `site-packages` when installed), which shadowed a
  consumer's own pinned copy and hid a missing dependency. The child now gets
  `pythonpath` followed by the caller's inherited `PYTHONPATH`, nothing else.
  A test that relied on the implicit entry must either run under a
  `PYTHONPATH`/venv that already resolves `cli_extended`, or pass the new
  `library_path=True`. Check the netcup tests (`test_cli_contract.py`,
  `test_monitor_task.py`) and any `PYTHONPATH=…/src` gate lane.
- **`register_skills_verbs` forwards the parent's `global_options`
  (CLI-EXT-26)** (plus `identity_banner` and `error_help`) to its child
  registry. A consumer with a global option now gets it on every `skills`
  route, and `surface check` no longer reports `delegated parser does not
  register inherited global option(s)`. Re-run `surface sync` if the skills
  routes' review rows change.
- **`expected_exceptions` honours `exit_code` and `hint` (CLI-EXT-29).** A
  matched exception exits with its integer `exit_code` attribute (default 1)
  and prints its non-empty string `hint` after `[ERROR] <msg>`. Plain tuples
  and exceptions without those attributes behave as before.
- **New opt-ins, defaults unchanged (CLI-EXT-23).**
  `CliRegistry(identity_banner="never")` suppresses the identity headline
  before an error; `CliRegistry(error_help="usage")` prints the usage line and
  `Run '<prog> --help' for full help.` instead of the full help block on
  parser errors, a missing verb, an unknown help topic and
  `CliFailure(show_help=True)`. Exit codes are unchanged.
- **`CliIdentity.resolve(version_probe=…)` (CLI-EXT-24).** A zero-argument
  callable returning a version or `None`; it must agree with installed
  metadata and a version file (`VersionLookupError` on disagreement). The
  "no source" `ValueError` text now also names `version_probe`.
- **`invoke_*(python_args=…, isolated=True)` (CLI-EXT-19);
  `VerbSpec(dry_run_help=…)` (CLI-EXT-21).**
- **Audit (CLI-EXT-28, CLI-EXT-22).** AC-25 `no-path-hacks` accepts the
  `# cli-extended: allow-path-assertion` marker on (or immediately above) a
  line that asserts the library path is absent, and ignores comments; AC-01
  `version-source` ignores test files (`tests/`, `test_*.py`, `*_test.py`,
  `conftest.py`) that build a pinned `CliIdentity(...)`.

### 0.2.0 — first released wheel (unified adoption)

cli-extended is now a released wheel (`cli_extended-<version>-py3-none-any.whl`)
published on GitHub Releases under `cli-extended-v<version>`, with a moving
`cli-extended-latest/latest.json` pointer (`project`, `version`, `tag`, `asset`,
`sha256`, `url`). It is never published to PyPI: install it with
`pip install --no-index --find-links <dir-with-the-wheel> cli-extended`. Releases
stay on `0.x` until every vbpub CLI has adopted the library; `1.0.0` follows.

**Library contract version 1.** `contract.CONTRACT_VERSION = 1` names the set of
library-owned common controls (`--help`, `--version`, `--json`, `--yes`,
`--dry-run`, `--traceback`, `--color`/`--no-color`, `--quiet`, `--debug`/
`--verbose`, `--log-level`, `--progress`, `--debug-raw`). The surface manifest
(schema 7) records `library_contract` and per-route `common_controls`;
consumer review signatures cover only consumer grammar (the four reviewed
controls `--json`, `--yes`, `--debug-raw`, `--dry-run` keep their candidates
with a flag-and-route-only signature). Upgrading the library without a
contract bump leaves every consumer signature unchanged; a bump produces
exactly one `surface check` finding pointing here, not a re-review of every
route. Upgrading from a pre-0.2.0 manifest: run `cli-extended surface sync`
once. See SPEC "Library contract and contract version".

**Verb options now default to `None`/`False` instead of being absent.** A
`VerbSpec` option without an explicit `default` used to leave `args.<dest>`
unset when omitted (handlers crashed with `AttributeError`); it now has
argparse's own default. A destination shared with a global option or library
control still suppresses its default so a value parsed before the verb
survives. The surface manifest records these defaults, so manifests and the
review signatures of routes with such options change: run
`cli-extended surface sync` and re-review the cases `check` reports as
changed. `CONTRACT_VERSION` is not bumped: no library control changed, and
this lands before contract version 1 is first released, so it is part of the
version-1 baseline (SPEC "Library contract and contract version", rule 4).

When a future release bumps `CONTRACT_VERSION`, its entry below states what
changed in the controls and what a consumer must re-review.

New in this release (each documented in README, SPEC and docs/CONSUMERS.md):

- `CliIdentity.resolve(...)` — one version source (installed metadata and/or a
  VERSION file, which must agree), no literal fallback.
- `CliRegistry(expected_exceptions=..., unexpected_exceptions="report")` and the
  library `--traceback` control.
- `VerbSpec(dry_run=True)` — library `--dry-run`; `runtime.confirm()` returns
  False under it and prints `Dry run: no changes made.`.
- Declarative option constraints `Requires`, `Conflicts`, `RequiresChoice`, and
  the `SelectorList` value type.
- `register_skills_verbs(...)` — packaged agent skills with install, uninstall,
  check and list, frontmatter stamping, a banner and `.cli-extended-stamp.json`.
  `RegisteredCli.skills_package` carries the registration.
- `register_doctor(...)` — a shared `doctor` verb over named checks.
- Project configuration in `cli-extended.toml` or `[tool.cli-extended]`, and the
  `cli-extended` console script: `surface sync|check|template|pack|report`,
  `audit`, `skills`.
- The surface review workflow: `surface pack` plus the packaged
  `cli-extended-review` skill and review rubric, a findings file, and
  `surface check` refusing open blocker/major findings.
- `docs/ADOPTION-CHECKLIST.md` (AC-01..AC-25), `cli-extended audit`, and the
  packaged `cli-extended-adoption` skill.
- Test helpers `invoke_script`, `invoke_module`, `make_invoker` (mandatory
  absolute `home`; `HOME`/`XDG_*_HOME` cannot be overridden) and the opt-in
  `cli_extended.pytest_plugin` (`cli_case` marker; strict by default).

Behaviour changes a consumer can see:

- **Colour.** On Pythons whose `argparse` colours its own help (3.14+), that
  native palette is disabled; every colour comes from the cli-extended policy.
  On a TTY the help palette changes.
- **Help width.** `surface pack` renders help at a fixed 100 columns
  (`fixed_help_width`), so packs are byte-stable across terminals.
- **Deprecation.** `python -m cli_extended.surface_cli` still works, keeps its
  flags, and first writes `[WARN] ... is deprecated` to stderr. Use
  `cli-extended surface ...`; the shim is removed in a later release.

<!-- cmru: release history -->

## [0.3.0] - 2026-10-06
<!-- cmru: generated -->
<!-- cmru: source-end=0321ffc3c7009b31f17b3320611ad74a19567345 -->

### Added
- feat(cli-extended): name --cli-case-partial in the strict CLI-case coverage error (a63838063)
- feat(cli-extended): CLI-EXT-19/21/22/24 version_probe, python_args/isolated, dry_run_help, audit precision (code and tests) (7cfe227f3)

### Fixed
- fix(cli-extended): CX-BACKLOG review round 1 (audit row alignment, exit-code clamp, docs, backlog) (16fdee1f7)
- fix(cli-extended): CLI-EXT-23/26/27/28/29 library backlog fixes (code and tests) (f6ad8ce0c)

### Documentation
- docs(cli-extended): CX-BACKLOG 2026-10 report (933402fad)
- docs(cli-extended): CX-BACKLOG triage, backlog entries 23-29, CHANGES unreleased contract notes, consumer docs (919bafcbe)

### Testing
- test(cli-extended): sharpen AC-25 separator test; round 1 report (6b56d15b4)
- test(cli-extended): cover CliOutput policy validation (r0-r1 100% coverage) (318d6735b)

## [0.2.0] - 2026-10-05
<!-- cmru: generated -->
<!-- cmru: source-end=391c06cae51dff7eff93342f579277d7e652ef61 -->

### Added
- feat(netcup): scp-api review catalog (151 rows), tests still pending (bc9930b68)
- feat(cli-extended): netcup monitor-task surface re-signed to schema 7; W9a checkpoint 2 (4b5178882)
- feat(cli-extended): netcup adopts identity, policy, dry-run and test helpers (W9a checkpoint, BLOCKED on plugin multi-CLI bug) (15ba2804b)
- feat(cli-extended): adoption checklist and cli-extended audit verb (CLI-EXT-15) (eb5bf4418)
- feat(cli-extended): W6 consumer test helpers and opt-in pytest plugin (1d642e625)
- feat(cli-extended): W3b project config, cli-extended CLI, review workflow and findings (CLI-EXT-10, CLI-EXT-11) (ee21b4fb9)
- feat(cli-extended): W5 doctor options and (runtime, args) check signature (8888e458c)
- feat(cli-extended): W5 shared doctor verb (CLI-EXT-13) (3ca4f3d47)
- feat(cli-extended): declarative option constraints and SelectorList (CLI-EXT-02, CLI-EXT-12) (5a580bfa0)
- feat(cli-extended): W4 packaged agent skills (CLI-EXT-05) (bbc79f021)
- feat(cli-extended): library contract version; manifest names common controls, schema 7 (96e4839e2)
- feat(cli-extended): W1 version resolver, exception boundary and dry-run (41e475662)
- feat(cli-extended): add semantic CLI surface helpers (8c8dd2af2)
- feat(cli-extended): harden semantic surface validation (9fa071095)
- feat(cli-extended): validate semantic interaction shapes (e2c3604b1)
- feat(cli-extended): generate semantic interaction surfaces (77bfd5848)
- feat(cli-extended): enrich semantic surface review (0630cfee7)
- feat(cli-extended): generate semantic CLI review surfaces (525a73c76)
- feat: adopt cli-extended and resolve CMRU CLI decisions (f6b577f41)
- feat: adopt cli-extended for Debian installer (e3cd117c1)
- feat: migrate Netcup CLIs to cli-extended (cb84789a5)
- feat: adopt cli-extended in Netcup task monitor (666d9651e)
- feat: generate CLI surfaces from command registry (593da6fa0)
- feat: add shared cli-extended contract library (5e093c801)

### Fixed
- fix(cli-extended): W8c batch 7 dry-run notice under quiet, drop dead common-control record keys (65580a103)
- fix(netcup): install ignores NETCUP_SCP_API_SERVER_ID; hidden-flag and no-leak tests (b7d4a8fd6)
- fix(netcup): findings rulings, effects reworded, docs corrected (ffb302843)
- fix(cli-extended): W8c batch 4 required common-option flags, HelpCatalog traceback default test (cc6b8677b)
- fix(netcup): watch exits 1 for non-FINISHED terminal states; document --filter semantics (aaf453b74)
- fix(cli-extended): verb options keep argparse defaults unless dest collides with a root dest (f74e2a1d7)
- fix(cli-extended): W8b batch 2, non-ASCII pack tests, no falsy-swap exit constants (365d0c572)
- fix(cli-extended): W8b release blockers and mutation survivors (4b8351b72)
- fix(cli-extended): multi-CLI pytest plugin, audit synopsis evidence, Netcup-adoption docs (422c1b66a)
- fix(cli-extended): W7 review round 1 (unreadable files, RegisteredCli.skills_package, probes, exclusions) (a417e0216)
- fix(cli-extended): W6 review decisions for invoke helpers and plugin docs (449287b1f)
- fix(cli-extended): W3b review round 2 (validate pinned help width, pin-value and nesting tests, pack width test) (a381b7b55)
- fix(cli-extended): W3b review round 1 (fixed-width colour-free pack, argparse colour policy, finding ids, survivors, docs) (a0b62d719)
- fix(cli-extended): W5 review round 1 (single-line crash/remedy, NaN details, skills-name refusal, mutant tests) (4f7201171)
- fix(cli-extended): W2 review round 1 (shared dest, trigger check, presence refusals) (380337921)
- fix(cli-extended): W4 review round 2, tool-scoped interrupted-install leftovers (e2386291e)
- fix(cli-extended): W4 review round 1 (umask modes, symlinks, hidden entries, source errors, leftovers) (7225ef773)
- fix(cli-extended): W3a review round 1 (pin library-control shape, strip interaction syntax, marking tests) (fa2658237)
- fix(cli-extended): reconcile W3a contract with W1 controls (3a0a79c8a)
- fix(cli-extended): W1 review round 1 hardening (b6c86c0ad)
- fix(cli-extended): bound greedy option scan (bf8a0e432)
- fix(cli-extended): close mutation survivors (0b0dbecde)
- fix(cli-extended): model argparse value semantics (d7d92622f)
- fix(cli-extended): validate optional choice values (60abef4bd)
- fix(cli-extended): reject opaque choice semantics (a9a41d22f)
- fix(cli-extended): honor argparse typed choice semantics (409222553)
- fix(cli-extended): normalize required grammar checks (f49fdc138)
- fix(cli-extended): validate complete positional shapes (5af36d798)
- fix(cli-extended): clarify invocation and delegate surfaces (0d7348365)
- fix(cli-extended): harden generated surface review contracts (4ef01533a)
- fix(cli-extended): capture parser-scoped token rules (860c28eb4)
- fix(cli-extended): validate positional review assignments (b2a5c5a5d)
- fix(cli-extended): lex reviewed invocations by parser scope (683a03f58)
- fix(cli-extended): replace generated surfaces atomically (8ccf924a5)
- fix(cli-extended): skip option values when checking CLI routes (2d5cb3d9e)
- fix: enforce shared CLI option semantics across verbs (eecd1438f)
- fix: harden cli-extended adoption contract (0a43478fa)

### Changed
- Merge main into cli-extended-unified before release (ce1696562)
- Merge W9a: Netcup adopts cli-extended (three CLIs, reviewed surfaces, MT-001 fix) (0f2e644f3)
- Merge integration (W8c option defaults) into W9a (fd35de63e)
- Merge W8c: R2 survivors batch 1, verb options keep real defaults (bf996d5cb)
- Merge integration (W6b plugin fix, W8b) into W9a (5450fd1c7)
- build(cli-extended): size the final R2 run from measured throughput (1e6cc4895)
- Merge W8b: release blockers and first R2 survivors (225015534)
- chore(cli-extended): regenerate cmru dependency graph; program doc matches built API (7a85e3e32)
- Merge W10: planned cli-extended adoptions filed in each tool's backlog (20b7d601a)
- build(cli-extended): mutate every shipped module in R2 (8e083b307)
- Merge W8 groundwork: cmru release contract and wheel packaging (CLI-EXT-16) (87095f9c5)
- Merge integration (W6) into W7 (3e7a29cad)
- Merge W6: consumer test helpers and pytest plugin (CLI-EXT-14) (371d3f540)
- build(cli-extended): size the R2 mutation campaign for the wave (641c9710e)
- build(cli-extended): release as a wheel through cmru (CLI-EXT-16) (9f3c08377)
- Merge integration (W2, W5) into W3b (aed20895c)
- Merge W5: shared doctor verb (CLI-EXT-13) (a7710d770)
- Merge W2: declarative constraints and SelectorList (CLI-EXT-02, CLI-EXT-12) (c3a0f4410)
- Merge W4: packaged agent skills (CLI-EXT-05) (3f8811149)
- Merge integration (W1) into W3a (0cd3a7a52)
- Merge branch 'cli-extended-review' (583694b88)
- backlog(cli-extended): CLI-EXT-02 shared skills install/list/check verb group (ab52242e8)
- Record completed CLI surface gates (e203a609a)
- Record EOF boundary mutation triage (75c007e3a)
- Bound CLI review option values at EOF (827f67d70)
- Record CLI review gate triage (e0ec2c9e9)
- Clarify CLI review EOF and factory path errors (c88b7fd36)
- Pilot generated semantic CLI review on Netcup (77d139123)
- merge(cli-extended): argparse semantic review fixes (f017293ba)
- Close cli-extended prompt contract mutation gaps (6e5c6ef49)
- Add shared interactive prompt API to cli-extended (573194352)
- merge: sync CMRU CLI work with current main (b14643995)
- Adopt cli-extended across CMRU command surfaces (c54e90581)

### Documentation
- docs(cli-extended): W9a report, successor session 4 (962606c40)
- docs(cli-extended): contract v1 baseline is the 0.2.0 export (rule 4) (e48b4b66c)
- docs(cli-extended): W9a report checkpoint 3 (install-host catalog done, scp-api brief) (9fb893bbd)
- docs(cli-extended): W9b adoption friction (source run, plugin lanes, debug-raw; CLI-EXT-19..22) (18e8b3e02)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(cli-extended): W4 skills spec, guides and report (dd311c760)
- docs(cli-extended): fix program anchors in backlog (5318b211f)
- docs(cli-extended): unified-adoption program plan and backlog CLI-EXT-06..16 (7121613b0)
- docs(cli-extended): correct gate launch instructions (afb2727cc)
- docs(cli-extended): record committed gate results (4d303f7b9)
- docs(cli-extended): correct mutation timeout analysis (b68f9d3fe)
- docs(cli-extended): record R2 findings (4b99816d4)
- docs(cli-extended): record R2 survivors (0e00c9688)
- docs(cli-extended): record mutation checkpoint findings (d38cd5807)
- docs(cli-extended): reconcile R2 checkpoint history (841657291)
- docs(cli-extended): record typed-choice gate status (33e645aa4)
- docs(cli-extended): refresh mutation progress estimate (11900d07a)
- docs(cli-extended): update R2 runtime estimate (c9eaf5714)
- docs(cli-extended): reconcile gate status in plan (88fa16371)
- docs(cli-extended): correct CMRU interaction evidence (dd51af8cb)
- docs(cli-extended): report active mutation gate (b156808b3)
- docs(cli-extended): clean plan formatting (ccfe62bf8)
- docs(cli-extended): record coverage and canary gates (cd4af48e5)
- docs(cli-extended): record gate progress (9c14091c2)
- docs(cli-extended): clarify cross-route surface checks (63c9b37bb)
- docs(cli-extended): record parser token surface (cb96fec1e)
- docs(cli-extended): define generated semantic review workflow (ce7655bde)
- docs(cli-extended): explain handler success normalization (98a203d81)
- docs(cli-extended): capture Nyxloom adoption lessons (b29cafdfc)
- docs(cli-extended): record adoption review lessons (e4e6b5ae0)
- docs(cli-extended): track consumer color policy gap (8a4a97092)
- docs: move cli-extended contract to project spec (8137885dc)

### Testing
- test(cli-extended): drop the pending Netcup rename exception (W9a merged) (be6f301f6)
- test(cli-extended): W8c batch 6 frontmatter indented line before metadata (2f888b966)
- test(netcup): reviewer survivors killed, effects audited, report (82bdedc0a)
- test(cli-extended): W8c batch 5 add_common_options defaults, Markdown dry-run per verb (6f1a5faa5)
- test(cli-extended): W8c batch 3 findings required fields, AST dataclass scan (5f826a018)
- test(cli-extended): W8c batch 1 survivors (positive int, plain verbs, frozen dataclasses) (b97ca91ac)
- test(cli-extended): W8b pack tolerates invalid interaction groups (2be21a5e5)
- test(cli-extended): pin the CliIdentity.resolve( call in version-source (f74d33fe7)
- test(cli-extended): resolve generated argument IDs (9f7099ef9)
- test(cli-extended): avoid duplicate const probe test (982a4cbd9)
- test(cli-extended): cover review helper edge cases (eea75868d)
- test(cli-extended): align positional semantics with argparse (fb5b5c811)
- test(cli-extended): assert positional rejection finding (ff9f2c5aa)
- test(cli-extended): cover optional const semantics (f5bc85c23)
- test(cli-extended): clarify interaction repeat checks (164c584fa)
- test(cli-extended): close surface review edge coverage (2ea6325f1)
- test(cli-extended): cover confirmation signature changes (d76e67c1d)
- test(cli-extended): verify exported parser abbreviation policy (f0d50a52d)
- test(cli-extended): keep consumer docs verifiable (74a55db3d)
- test(cli-extended): cover positional route checks (187fa56a8)
- test(cli-extended): cover none handler success (7269bfadb)
- test: harden CLI semantic checks and transaction context (9b2273d58)
- test: strengthen shared CLI contract assertions (9a71e21b5)
