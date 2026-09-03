# nyxloom-P99 — pre-dispatch adversarial specification review

Handoff under review: `nyxloom-trove/handoffs/nyxloom-P99-l10-per-project-thresholds.md`
Frozen at: `9e59704d` (input_revision `f84953a7`, its own first commit — two-step
freeze confirmed via `git log -1 9e59704d` and reading the frontmatter).
Backlog basis: `nyxloom-trove/backlog/NL-3-l10-handoff-size-thresholds-are-hardcoded-constants-need-a-per.md`.
Reviewer: independent adversarial pass per `nyxloom/reference/AUTHORING.md`'s
"Pre-dispatch adversarial handoff review" prompt. No code proposed below.

**Verdict: NOT READY.**

The mechanical sweep claims in the handoff all verify as true (see §0). But
the oracle set has one structural hole that a plausible, convenient wrong
implementation walks straight through: **no oracle exercises the real
`nyxloom.toml -> ProjectConfig.load() -> _check_l10` pipeline with a
successfully-parsed, non-default `[lint.l10]` override.** O1 (the oracle
whose whole job is "proves the override is actually READ and applied, not
merely parsed and ignored") bypasses `ProjectConfig.load()` entirely via
`dataclasses.replace`. This is exactly the class of defect AUTHORING.md
asks a pre-dispatch review to catch: a test that shares the
implementation's assumption, producing a false PASS on the one behavior
the backlog entry exists to deliver.

---

## 0. Independently verified sweep claims

All re-checked against the actual files at `9e59704d`, not the handoff's prose:

| Claim | Verified | Evidence |
|---|---|---|
| `_check_l10`'s call site has `cfg` in scope | **True** | `lint.py:156` `lint_file(path, cfg)`; `_check_l7`/`_check_l9` at lines 197/203 already receive `cfg`; `_check_l10` call at line 206 is `_check_l10(findings, path, full_text)` (cfg not yet threaded — correct, that's Work item 3's job) |
| No other `ProjectConfig(...)` construction site breaks under a `default_factory` field | **True** | `grep -rn "ProjectConfig(" src/ tests/` finds 0 sites in `src/` and 13 in `tests/`, all keyword-argument construction (e.g. `tests/test_gap_audit.py:36`) — none positional, none would collide regardless of where the new field is inserted in the dataclass |
| `test_large_handoff_warning`/`test_huge_handoff_error` use plain `sample_project`, no `[lint.l10]` | **True** | `tests/test_lint.py:592-658`; `sample_project` fixture (`tests/conftest.py:96-115`) builds `ProjectConfig.load(root)` from `SAMPLE_PROJECT_TOML`, which has no `[lint]` table |
| `docs/SPEC.md`'s L10 row needs no edit | **True** | `docs/SPEC.md:114`: "Handoff token size within project budget (warn, then block at 2×)" — generic, no literal number |
| No stray hardcoded `10000`/`18000` outside the two comparisons | **True** | `_check_l10`'s `message = f"handoff size {tokens} tokens"` (lint.py:1082) carries no literal; only lines 1084/1091 compare against the two constants |
| No other rule/fixture depends on the literal values 10000/18000/6000/12000 | **True, with one adjacent staleness note** | `grep -rn` across `src/`, `tests/`, `docs/` for those four numbers turns up nothing load-bearing outside `lint.py` itself and the P99/NL-3 docs. One **pre-existing, P99-unrelated** staleness: `tests/test_lint.py:971`'s golden-corpus parametrize comment reads `# L10 warning becomes error over 12k`, a leftover from the pre-bump 6k/12k thresholds (the sibling docstrings at lines 593-596/628-629 already document that bump under the "NL-3" name). Functionally inert (the test only asserts `is_error=False`, and `demo-P21-huge.md` is untouched per `scope.forbid`), but it is a second, unnamed consumer of L10 behavior (`TestGoldenCorpus`, not `TestL10Size`) that the handoff's "Context to read first" never mentions. Worth a one-line comment fix while the implementer is already in this file, though not a blocker. |
| `nyxloom lint` passes on the handoff itself | **True** | `python3 -m nyxloom.cli lint 2>&1 \| grep P99` — zero findings |
| Top-level schema `additionalProperties` is `true`, so Work item 6 is genuinely optional for function | **True** | `additionalProperties: true` at schema root; `notify`/`policy`/`backlog_entries` are individually closed (`additionalProperties: false`) while `stage` is open (dynamic keys) |

## 1. Blocking ambiguities

**B1 — The `[lint.l10]` -> `ProjectConfig.load()` -> returned instance wiring is never named as an edit target, and nothing catches its omission.**
Work item 2 says to "read `data.get("lint", {}).get("l10", {})`, construct
`L10Config(**that_dict)`, and validate immediately" — but the handoff never
names the actual `return cls(...)` block (`config.py:450-474`, the same
block that explicitly threads `pipeline=pipeline` at line 470 to close the
analogous `validate_pipeline` precedent) as a location requiring an edit.
An implementer can satisfy every literal sentence in Work item 2 — parse
the dict, construct `L10Config(**that_dict)`, raise `ValueError` on bad
values — entirely inside a local scope that never reaches the `cls(...)`
call, and the returned `ProjectConfig.l10` stays the default
`L10Config()` forever regardless of what the project's `nyxloom.toml` says.
This is invented, not specified: whether the parsed value must be passed
to the constructor is left to the implementer's general competence, not to
a checkable instruction or a test.

**B2 — The `>`/`>=` boundary semantics of the parameterized comparison are never stated.**
Today's code is `if tokens > 18000: error elif tokens > 10000: warning`
(strict `>`, so a handoff at exactly 10000 or exactly 18000 tokens is
*not* flagged/escalated at that tier). Work item 3 says only to put
`cfg.l10.warn_tokens`/`error_tokens` "in place of" the literals — it never
states that the strict-`>` boundary behavior is part of the frozen
contract that must survive parameterization. No oracle names a
fixture at the exact boundary value.

**B3 — The `[lint.l10]` validation's own boundary (`>=` vs `>`) is stated in prose but not tested at its boundary.**
Work item 2 and O3's negative both say "if `warn_tokens >= error_tokens` ...
raise `ValueError`," but O3's own worked example (`warn_tokens=20000,
error_tokens=10000`) is a strict-inequality case, not the equality case
(`warn_tokens == error_tokens`). An implementation that checks
`warn_tokens > error_tokens` (permitting equality) passes the literal
proposed O3 test while violating the written contract, and downstream
silently collapses the two-tier system into one dead branch whenever a
project sets `warn_tokens == error_tokens`.

**B4 — Schema shape for `[lint.l10]` (Work item 6) has no owned-interface example.**
AUTHORING.md's Implementation-packet item 1 requires "one valid example and
at least two invalid examples" for any schema/protocol. Work item 6 states
the leaf type (`{"type": "integer", "exclusiveMinimum": 0}`) but not
whether `lint`/`l10` should be closed (`additionalProperties: false`, the
majority precedent: `notify`, `policy`, `backlog_entries`) or open (the
`stage` precedent, which is open only because its keys are dynamic stage
names — inapplicable here since `lint`/`l10` are both static). It also
never states whether `warn_tokens`/`error_tokens` should be schema-`required`
— which matters concretely, because O1's own fixture is a **partial**
override (`error_tokens` only). If an implementer mirrors
`backlog_entries`' one `required` field without noticing why it is
required there (a semantic dependency, not a style default) and marks
either L10 key required, `nyxloom lint`'s own CFG1 would reject the exact
`nyxloom.toml` shape O1 depends on for any project that actually adopts a
partial override in the real world — invisible to every stated oracle,
since none of them run `lint_config`/CFG1 against a `[lint.l10]` table.

**B5 — No Implementation Packet section, no tracer-bullet evidence.**
`tier: implement-2` maps to contract class `2d` on AUTHORING's ladder,
which normatively requires an `## Implementation packet (normative)`
section (owned interfaces, construction flow, decision table, bounds,
prepared proof, traceability, degrees of freedom) and a carver-run tracer
bullet witnessing the acceptance negatives fail today. This handoff has
none of that scaffolding — the Work section is well-written but is not a
substitute, and there is no carve log/LOG entry anywhere under
`nyxloom-trove/` for P99 recording a probe run. (`git log --all --grep
P99` and `find nyxloom-trove -iname '*P99*'` show only the handoff file
itself and its freeze commit.)

## 2. False-PASS attacks (per oracle)

- **O1** — *Wrong implementation that passes:* `ProjectConfig.load()`
  correctly parses and validates `[lint.l10]`, raising `ValueError` on bad
  input exactly as O3 wants, but never assigns the resulting `L10Config`
  onto the object it returns (`cls(...)` keeps the default). O1's test
  constructs its `cfg` via `dataclasses.replace(sample_project, l10=...)`,
  never calling `.load()`, so it cannot observe this. Every real project's
  `nyxloom.toml` override is silently inert — the exact "parsed but never
  reached the check" failure mode O1's own negative describes, just shifted
  one call-frame earlier than the negative anticipates.
- **O2** — *Wrong implementation that passes:* change `_check_l10`'s
  comparisons from strict `>` to `>=` on both branches. `test_large_handoff_warning`
  (11250 tokens) and `test_huge_handoff_error` (20000 tokens) sit nowhere
  near 10000/18000, so both keep passing unchanged while exact-boundary
  behavior silently shifts.
- **O3** — *Wrong implementation that passes:* validate with
  `warn_tokens > error_tokens` (not `>=`). The proposed test's only
  malformed-ordering fixture is `(20000, 10000)`, a strict violation either
  way, so the test can't distinguish `>` from `>=`; a project setting
  `warn_tokens == error_tokens` sails through construction and then
  silently disables the warning tier downstream (dead branch in
  `_check_l10`).

## 3. Missing implementation-packet content

- No `## Implementation packet (normative)` section (see B5).
- No carver-witnessed tracer bullet / probe log for a `2d`-classified package.
- Work item 6 (schema) lacks an owned-interface example (valid + ≥2 invalid)
  and doesn't pin `additionalProperties`/`required` (see B4).
- No decision table naming the four `[lint.l10]` states (absent / valid-full /
  valid-partial / malformed) against outcome + side effect — O1-O3 cover
  three of these ad hoc but nothing enumerates them together.
- "Context to read first" never names `config.py`'s `return cls(...)` block
  (config.py:450-474) despite it being the exact site where B1's gap lives,
  nor `tests/test_lint.py:953-990`'s `TestGoldenCorpus`/`demo-P21-huge.md`
  consumer of L10 behavior (only the fixture, not the test, is named, and
  only in `forbid`).

## 4. Scope/dependency defects

- **Narrowed from NL-3's own proposed contract.** NL-3's "Proposed contract"
  explicitly covers both directions: "[a project] may raise its own ceiling
  ... or lower it (a program that wants tighter handoffs)." P99's sole
  numeric oracle (O1) only exercises *raising* the ceiling
  (`error_tokens = 25000`, up from 18000). No oracle exercises a project
  *lowering* either threshold below the tool-wide default — a real,
  concrete narrowing an implementer defending only the "louder ceiling"
  direction (e.g. guarding with `max(cfg.l10.error_tokens, 18000)` out of
  a mistaken belief the override can only raise, never lower) would pass
  every stated oracle while breaking half of NL-3's stated use case.
- **NL-3's own SPEC-ownership instruction not addressed.** NL-3 says: "check
  for one [existing per-project lint config precedent] before assuming none
  exists." The handoff's context-to-read list never asks the implementer to
  check this, though independent verification here confirms no such
  precedent exists yet (`lint`/`l10` are new top-level schema keys) — so the
  omission happens to be harmless, but it's still an unperformed check the
  backlog entry asked for and the handoff dropped silently rather than
  recording as "checked, none found."
- No scope/gate conflicts found otherwise: `tester-unified` gate id exists
  in `nyxloom-trove/nyxloom.toml`; gate argv matches the declared pattern;
  `depends_on: []` is correct (nothing blocks this).

## 5. Corrected oracle/fixture matrix

Axes: **config source** (direct dataclass vs. real `.load()` from an
on-disk toml) × **`[lint.l10]` state** (absent / valid-full / valid-partial
/ malformed) × **token count vs. threshold** (below-warn / at-warn-boundary
/ between / at-error-boundary / above-error) × **override direction**
(raise vs. lower vs. unchanged).

Combined-axis fixtures that a convenient implementation is likely to fail
(all four are currently unrepresented by O1-O3 or the two existing tests):

1. **Real `.load()` + valid-partial override (`error_tokens` only) + tokens
   exactly at the *overridden* error boundary.** Catches B1 (load-time
   wiring omission) and B2 (boundary-operator drift) simultaneously — the
   single highest-value fixture missing from this handoff.
2. **Real `.load()` + malformed (`error_tokens <= 0`) + run through
   `lint_project()` (not `lint_file()` directly) on a project whose
   discoverable handoffs already trip the *default* thresholds.** Confirms
   the `ValueError` propagates uncaught to the actual caller path used in
   production (`lint_project`/CLI), not just to a direct `pytest.raises`
   around `ProjectConfig.load()` in isolation — checks the "fail loudly"
   terminal state is real, not just reachable in a unit test.
3. **Real `.load()` + valid-full override that *lowers* both thresholds
   below the tool-wide defaults (e.g. `warn_tokens=500, error_tokens=1000`)
   + a body sized between the new, tighter numbers but far under the old
   defaults (e.g. ~700 tokens).** Defeats an implementation that only
   defends the "raise the ceiling" direction; directly closes the §4
   narrowing gap.
4. **Direct dataclass construction + tokens exactly equal to
   `warn_tokens` and, separately, exactly equal to `error_tokens`.**
   Isolates B2's `_check_l10` boundary-operator ambiguity from the
   `.load()`-wiring question in fixture 1, pinning down whether `>` or
   `>=` is the frozen contract at each threshold independently.

Per-oracle table:

| Req (NL-3) | Oracle | Fixture | Gap |
|---|---|---|---|
| Override raises ceiling and is read | O1 | `dataclasses.replace` direct | Never exercises `.load()`'s TOML parse — see B1 |
| No-override fallback unchanged | O2 | existing `sample_project` tests | Sound, but doesn't pin boundary values (B2) |
| Malformed config fails loudly | O3 | `.load()` + 2 bad-value cases | Doesn't test `warn==error` equality boundary (B3) |
| Override *lowers* ceiling | **(none)** | — | Missing entirely (§4 narrowing) |
| Schema shape parity | Work item 6 only, no oracle | — | No CFG1 oracle at all against a real `[lint.l10]` toml (B4) |

## 6. Verdict

**NOT READY.** B1 alone is disqualifying under AUTHORING.md's standard
("mark NOT READY if any externally visible decision ... remains for the
implementer to invent"): whether the parsed `[lint.l10]` override actually
reaches the `ProjectConfig` instance returned by `.load()` is exactly such
a decision, and no oracle would catch getting it wrong. B2/B3 (boundary
semantics) and the §4 narrowing (lowering direction) compound the problem.
Recommend a repair pass that (a) names `config.py`'s `return cls(...)`
block explicitly and adds a `.load()`-based oracle for a valid, non-default
`[lint.l10]` override feeding an actual lint run: (b) states the `>`/`>=`
boundary contract explicitly for both the size check and the validation
check, with a boundary-value fixture for each; (c) adds a
ceiling-lowering oracle to restore NL-3's full proposed contract; (d) pins
the schema's `additionalProperties`/`required` shape with a concrete
example. The package is otherwise well-scoped, small, and its factual
sweep claims all check out — this is a fixable oracle-design gap, not a
wrong architecture.
