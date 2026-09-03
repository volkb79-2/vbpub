---
schema_version: 1
id: nyxloom-P100-tier-routes-toml-validation
project: nyxloom
title: "Fix AUTHORING.md's stale tier example + add L14 (tier must resolve against live routes.toml)"
tier: luna-high
input_revision: "1183d702"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-2-authoring-md-s-tier-worked-example-implement-2-and-prose-are-un.md
scope:
  touch:
    - "reference/AUTHORING.md"          # two edits: (1) lines ~88-93, remove the false "implement-1 and implement-2 are deployed today" claim and clarify contract_class (body) vs tier (frontmatter, must be a live routes.toml key) are two different vocabularies; (2) line ~390's worked example, `tier: implement-2` -> a placeholder matching the template's OWN existing convention (every other value in that block, e.g. `<project>`, `<base commit short sha>`, `<gate id from nyxloom.toml [gates.*]>`, is already an angle-bracket placeholder -- tier is the one exception, and the one that's also factually wrong)
    - "src/nyxloom/lint.py"             # new L14 check: fm.tier must be a key in Routes.load().tiers; ERROR naming the bad value + up to 3 nearest valid keys (difflib.get_close_matches) when routes.toml loads but the tier doesn't resolve; WARNING (not ERROR -- see escalate_if) when routes.toml itself is missing/malformed, so lint doesn't become unusable in an environment that hasn't run onboarding yet. Call site: add to lint_file's existing L1-L13 sequence as the next step after L13 (~line 215)
    - "tests/test_lint.py"              # new TestL14TierRoutesToml class: positive fixture (a real routes.toml key), negative fixtures (the three real historical bad values from NL-2's own reproduction: implement-2, sonnet-xhigh, opus-xhigh), a nearest-match wording check, a missing-routes.toml warning case, and one test going through the ACTUAL `nyxloom lint <file>` CLI entry point (not lint_file() called directly) to prove L14 is really wired into the real command, not just reachable in isolation
    - "src/nyxloom/schemas/handoff-frontmatter.schema.json"  # no schema-shape change needed -- tier's existing "type":"string","pattern" stays exactly as is (see Scope/forbid); confirmed present in scope.touch only because the carve review must independently verify this file needs NO edit, not because it does
    - "tests/conftest.py"               # verify-only, no edit expected: read (not modified) for its sample_project fixture and paths.routes_path().write_text(...) pattern, the template every new L14 test must follow; listed because O2 cites it directly
    - "nyxloom-trove/reports/nyxloom-P100-LOG.md"     # NEW: per-commit LOG per the estate's standard contract
    - "nyxloom-trove/reports/nyxloom-P100-REPORT.md"  # NEW: per-oracle evidence, including O6's real-repo lint output verbatim (O6 cannot run inside the tester-unified container, so its proof lives here, not in a new automated test)
    - "tests/test_core_characterization.py"  # verify-only, no edit expected: it reads the inventory doc below and asserts against the real tree -- fixing the doc (Work item 5) is what makes it pass again; listed because O7 requires confirming it collects and passes
    - "nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md"  # a LIVE, mechanically-checked inventory (unlike tests/legacy_planner.py -- this one is meant to track current reality; tests/test_core_characterization.py enforces it), missed by all four carve-review rounds because it's a nyxloom-trove/reports/ doc, outside the reference/src/tests sweep scope -- surfaced only when the implementer actually ran the gate (same class of miss as nyxloom-P98's own Work item 10). Only the src/nyxloom/lint.py row needs updating (confirmed: neither tests/test_lint.py nor reference/AUTHORING.md has its own row in this file) -- re-measure with `wc -l` on the tree after Work items 1-4 land, do not hardcode a number, add a "Re-measured <today> (nyxloom-P100)" note per the doc's own convention (see Work item 5)
    - "nyxloom-trove/handoffs"           # directory sweep, verify-only: O6 lints every real file here at implementation time; already swept clean by the carver (P98/P99 archived) at freeze time, re-verified as part of this package's own Work item 4/O6, no edit expected unless escalate_if fires
    - "nyxloom-trove/archive"            # directory sweep, verify-only: the destination the carver already moved nyxloom-P98's and nyxloom-P99's handoff+LOG+REPORT+review files to (before this freeze) -- named here only so O6's premise ("both packages' handoffs are no longer in the live directory") is auditable against the real tree, not touched further by this package
  forbid:
    - "src/nyxloom/config.py"            # Routes.load()/Routes.for_tier() (this file's Routes class) are existing, already-used, working infrastructure (reconcile.py:1072, rules_dispatch.py:109 already call inp.routes.for_tier(fm.tier) in the daemon's real routing path) -- this package CONSUMES Routes, it does not change it. Do not edit this file at all.
oracles:
  - id: O1
    observable: >-
      `reference/AUTHORING.md` contains, VERBATIM (a plain fixed-string `grep -F`, not a regex --
      Work item 1 pins the exact replacement text precisely so this check does not need to guess
      at paraphrase), the full replacement paragraph Work item 1 specifies (the one starting "The
      table above names the PLANNED tier..."). Separately, `grep -c` for the literal string "are
      deployed today" prints `0` (the old sentence, or anything reusing its exact closing clause,
      is gone). The Level 2 worked example's `tier:` line no longer prints a literal `implement-2`
      value (it uses an angle-bracket placeholder matching the block's own convention, e.g.
      `tier: <a live key from routes.toml>`).
    negative: >-
      Any deviation from Work item 1's pinned replacement text (missing sentences, reordered
      clauses, a paraphrase that conveys the same meaning in different words) fails the verbatim
      check -- that is the point of pinning exact text instead of trying to regex-detect every
      possible bad paraphrase, which the first carve-review round on this package found both
      evadable by real paraphrases and prone to false positives on legitimately correct prose,
      because a fixed-window keyword match cannot carry semantic polarity. Changing the worked example's
      literal value to a DIFFERENT hardcoded real key (e.g. `tier: sonnet5-high`) instead of a
      placeholder also fails this oracle: NL-3's own lesson applies here too -- a literal current
      value drifts the exact way `implement-2` did, one level later.
    gate: tester-unified
  - id: O2
    observable: >-
      A REAL on-disk routes.toml (written via `paths.routes_path().write_text(...)` inside a
      `tmp_state`-isolated test, mirroring `tests/conftest.py`'s `sample_project` fixture -- never
      a bare `Routes(...)` constructed in memory for this oracle) declares
      `[tiers.sonnet5-high]`. Linting a handoff whose frontmatter has `tier: sonnet5-high`
      produces NO L14 finding. Separately, `nyxloom lint <path>` (the actual CLI entry point, not
      `lint.lint_file()` called directly) run against that same handoff+project produces the same
      result -- proving L14 is wired into the real command path, not only reachable via a direct
      function call in a test.
    negative: >-
      A finding produced when calling `lint_file()` directly but NOT when going through the real
      `nyxloom lint` CLI (or vice versa) fails this oracle -- both paths must agree, because a
      check that only fires in one is exactly the kind of "reachable but never actually invoked"
      gap a real operator would never see.
    gate: tester-unified
  - id: O3
    observable: >-
      With the SAME on-disk routes.toml from O2 (whose only declared tier is `sonnet5-high`),
      linting handoffs with `tier: implement-2`, `tier: sonnet-xhigh`, `tier: opus-xhigh` (the
      three real historical values from NL-2's own reproduction), AND `tier: sonnet5-hgih` (a
      transposition typo, NOT one of the three historical values, REQUIRED -- see negative) each
      produce an L14 ERROR naming the bad value AND at least one nearest valid key from
      `Routes.load().tiers` (e.g. mentioning `sonnet5-high` for both the `sonnet-xhigh` and
      `sonnet5-hgih` cases). The message must name the ACTUAL live keys, not a hardcoded list --
      see O5.
    negative: >-
      A lint rule that only checks `tier` is a non-empty string, or that checks it against a
      hardcoded ALLOWLIST of "known good" names baked into `lint.py`'s own source, fails this
      oracle even if it happens to reject these values today -- that reintroduces NL-2's own root
      cause one level down (see O5, which asserts the check reads the file at lint-time).
      Symmetrically, a hardcoded BLOCKLIST of exactly these known-bad strings (checking `tier in
      {"implement-2", "sonnet-xhigh", "opus-xhigh"}` rather than `tier not in
      Routes.load().tiers`) also fails this oracle -- the REQUIRED `sonnet5-hgih` fixture is a
      value such a blocklist has never seen and would silently pass, while a real
      live-data-driven check correctly rejects it.
    gate: tester-unified
  - id: O4
    observable: >-
      TWO cases, both required: (a) linting a handoff in a project whose `paths.routes_path()`
      points at a file that does not exist produces an L14 WARNING (not ERROR) whose message says
      the tier could not be validated because routes.toml is unavailable; (b) linting a handoff in
      a project whose `routes_path()` file EXISTS but is malformed (either invalid TOML syntax, or
      a `[tiers.x]` entry missing its `routes` key -- reproduce the exact `Routes.load()` failure
      mode by constructing this on disk, do not guess) ALSO produces an L14 WARNING, not an
      uncaught exception. Both cases must be run through the REAL `nyxloom lint <path>` CLI
      subprocess, not `lint_file()` called directly -- confirming the process exits cleanly with
      L1-L13's other findings for the same file still reported, not a crashed subprocess with a
      non-zero exit code from an unhandled traceback.
    negative: >-
      A hard ERROR, or an uncaught exception propagating out of `lint_file` entirely (aborting the
      whole lint run, or crashing the `nyxloom lint` subprocess with a non-zero exit from a raw
      traceback instead of a reported finding), fails this oracle for EITHER case -- an environment
      that hasn't run onboarding yet, or one with a broken routes.toml, must still be able to use
      `nyxloom lint` for every other rule. Catching only `FileNotFoundError` and letting a
      malformed-but-present file's `KeyError`/`tomllib.TOMLDecodeError` propagate uncaught passes
      case (a) while failing case (b) -- both must be caught by the same broad handling Work item 3
      specifies.
    gate: tester-unified
  - id: O5
    observable: >-
      After O2's fixture project is linted once, a SECOND on-disk routes.toml write replaces
      `[tiers.sonnet5-high]` with `[tiers.new-tier-name]` (a different key), in the SAME test
      process, with no code change and no process restart. Re-linting the identical handoff whose
      `tier: sonnet5-high` previously passed now produces an L14 ERROR (the key no longer exists),
      and a handoff with `tier: new-tier-name` now passes. This is the oracle that actually proves
      L14 reads the file live rather than caching/hardcoding a snapshot of valid tiers at import
      time or at the first call.
    negative: >-
      The second lint run still treating `sonnet5-high` as valid (a module-level cache, an
      import-time snapshot, or a memoized `Routes.load()` result reused across the two calls)
      fails this oracle -- L14 must re-resolve routes.toml on every lint invocation, matching how
      every other route lookup in this codebase already works (`Routes.load()` has no caching
      today; do not add any as part of this package).
    gate: tester-unified
  - id: O6
    observable: >-
      Run DIRECTLY ON THE WORKTREE'S OWN FILESYSTEM (the implementer/reviewer's own shell,
      `python3 -m nyxloom.cli lint` or equivalent invoked from the worktree root -- NOT as a new
      pytest test inside the `tester-unified` gate container, and NOT asserted as part of that
      container's automated suite). This is necessary, not a style choice: `Routes.load()` reads
      `paths.routes_path()`, which resolves to a path under the OPERATOR's real `$XDG_STATE_HOME`
      on the host -- the `tester-unified` container does not mount it, so this check cannot run
      inside that container at all. Immediately before this package's own gate run is treated as
      final (i.e. as the last step of Work item 5, after L14 itself is implemented and before
      claiming done): `nyxloom lint` against every file currently in `nyxloom-trove/handoffs/*.md`
      on the worktree's tree at that moment (this handoff's own file included) produces no L14
      finding. Capture the exact command and its full output verbatim in
      `nyxloom-trove/reports/nyxloom-P100-REPORT.md` as evidence -- this oracle's proof lives in
      the REPORT, not in a new automated test, because the check's own precondition (a real,
      operator-scoped `routes.toml`) cannot be reproduced inside the containerized gate the other
      oracles run in. As of this carve (`input_revision`), the carver has already archived
      nyxloom-P98's and nyxloom-P99's handoffs (moved to `nyxloom-trove/archive/`, outside
      `handoff_globs`, per their own completed merges) specifically because both declared the same
      invalid `tier: implement-2` this package's own frontmatter also carried until this repair --
      this oracle both confirms that cleanup held and guards against a NEW handoff appearing in the
      live directory with an invalid tier between carve and dispatch (a real risk under concurrent
      package development, not hypothetical).
    negative: >-
      Skipping this check because "the sweep was already done at carve time" fails this oracle --
      the whole point is re-verifying at implementation time, since other handoffs can appear in
      the live directory after the carve freezes and before this package's own gate run. Attempting
      to run this check INSIDE the `tester-unified` container (e.g. as a new pytest test) also
      fails this oracle even if the implementer works around the missing mount somehow (a fake/
      injected routes.toml inside the container is not the real check) -- this must run against the
      real host-scoped `routes.toml` on the worktree's own filesystem. If any OTHER handoff in that
      directory fails L14 when this check runs, that is `escalate_if`-worthy (see below), not
      something to silently fix by editing a file outside `scope.touch`.
    gate: tester-unified
  - id: O7
    observable: >-
      `tests/test_core_characterization.py::test_inventory_sizes_are_within_the_declared_tolerance`
      and `::test_inventory_paths_all_exist` both pass on the tree after Work items 1-5 land (i.e.
      the full `tester-unified` gate, which runs the whole `pytest` suite, is green -- this is not
      a new isolated test, it is confirming a PRE-EXISTING test that Work items 1-4's real line-
      count growth would otherwise break). The `src/nyxloom/lint.py` row in
      `CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` reflects the REAL post-edit line count
      (`wc -l`), not a guessed or pre-computed number.
    negative: >-
      Hardcoding a predicted line count in the inventory row instead of re-measuring after the
      real edits land fails this oracle if the prediction is even one line off (exactly the trap
      nyxloom-P99's own carve review warned about for this same file). Leaving the row stale and
      instead loosening or deleting the characterization test fails this oracle even more directly
      -- the test is correct today, the row is what's wrong.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any touched non-test file outside this list needs an edit to keep the gate green (a
    reverse-dependency this carve's sweep missed)"
  - "O6's sweep finds ANY handoff in nyxloom-trove/handoffs/ (other than this package's own, before
    Work item 3 lands) with an invalid tier -- this means a new package was carved concurrently
    with an invalid tier after this carve froze; report BLOCKED naming the specific file rather
    than editing it (it is outside scope.touch and its disposition -- fix vs. archive vs. a
    coordinator decision -- is not this package's call to make unilaterally)"
  - "Routes.load() already has retry/caching/memoization logic at input_revision that would make
    O5 fail regardless of a correct L14 implementation -- verified clean (Routes.load() re-parses
    the file on every call, no module-level cache) at input_revision; re-verify this before
    relying on it, since a cache added elsewhere between carve and dispatch would make O5
    unsatisfiable through no fault of this package's own code"
  - "fm (the parsed frontmatter object) does not expose a `.tier` attribute the way
    reconcile.py:1072 and rules_dispatch.py:109 already consume it (`fm.tier`) -- verified true at
    input_revision; if this has changed, L14 needs a different accessor and that is a carve
    defect to report, not to route around silently"
  - "E-008 checkpoint clause: arm at ~120k context or ~60 tool calls (whichever first), cut at
    the next coherent boundary (green gate > commit > LOG/REPORT write), repeat every ~40-55
    calls, stop when <~40 calls remain. At the cut: continuation brief to
    nyxloom-trove/reports/nyxloom-P100-BRIEF.md + self-authored retention prompt to
    nyxloom-trove/reports/nyxloom-P100-COMPACT.md (both authorised touches), commit, return --
    do not resume/fork past the cut yourself. (Unlikely to be needed -- this is a small package.)"
---

# nyxloom-P100 — fix AUTHORING.md's stale tier example + add L14

## BLOCKED protocol

If any contract item below cannot be met exactly as specified, or an
`escalate_if` condition fires, stop and report **BLOCKED: <reason>** rather
than improvising a substitute. Do not silently narrow, widen, or reinterpret
a contract item.

## Context to read first

1. `nyxloom-trove/backlog/NL-2-authoring-md-s-tier-worked-example-implement-2-and-prose-are-un.md`
   — full entry. Its "Reproduction" section names the three real historical
   bad values O3 tests; its "Proposed contract" names both independent
   fixes this package delivers; its "Behavioral oracle" section is the
   direct basis for O2/O3.
2. `reference/AUTHORING.md` lines 65-93 (the 2a-2e ladder table and the
   "Only `implement-1` and `implement-2` are deployed today" paragraph
   immediately after it — the source of the false claim) and lines
   383-408 (the Level 2 worked example — note EVERY other value in that
   YAML block is already an angle-bracket placeholder; `tier: implement-2`
   is the one literal, and the one that's wrong).
3. `src/nyxloom/config.py`'s `Routes` class (~line 684) — `Routes.load()`
   reads `paths.routes_path()` fresh on every call (no caching), returns
   `.tiers: dict[str, list[str]]`. This is the SAME object
   `reconcile.py:1072` (`inp.routes.for_tier(fm.tier)`) and
   `rules_dispatch.py:109` already use in the daemon's real routing path
   — L14 is a NEW consumer of existing, proven infrastructure, not new
   plumbing.
4. `src/nyxloom/lint.py` lines 150-215 (`lint_file`'s call sequence,
   L1 through L13 — L14 is the next step) and lines 1163-1230-ish (L13's
   implementation, `_check_l13` — a style reference for how a lint check
   in this file reads its inputs and appends `LintFinding` objects).
5. `tests/conftest.py`'s `sample_project` fixture (~line 96) and its
   `paths.routes_path().write_text(SAMPLE_ROUTES_TOML)` call (~line 113)
   — the established pattern for giving a test an isolated, on-disk,
   `tmp_state`-scoped routes.toml. **O2/O3/O4/O5 all require this real
   on-disk pattern** — never construct a bare `Routes(...)` object in
   memory to drive an L14 test (see `tests/test_behavioral.py:718-722`'s
   own documented rationale for when each style is appropriate; L14's
   whole job is validating the REAL file-read path, so the in-memory
   shortcut is exactly wrong here, the same class of gap NL-3's own
   carve review caught for a different feature).
6. **The sibling-handoff landmine, already resolved once, must not
   regress (see O6).** The first carve draft of this package did not
   notice that landing L14 would mechanically invalidate nyxloom's OWN
   then-open handoffs — `nyxloom-P98-retire-toolkit-gate-verify.md`,
   `nyxloom-P99-l10-per-project-thresholds.md`, and THIS package's own
   frontmatter — all of which declared `tier: implement-2`
   (`nyxloom.toml`'s `handoff_globs` lints the whole live
   `nyxloom-trove/handoffs/` directory; `daemon.py:1452` and
   `effects_carver.py:592` both gate real state transitions on
   `lint.has_blocking()`). The carver has since archived P98's and P99's
   handoff+LOG+REPORT+review files to `nyxloom-trove/archive/` (both
   packages were already merged; this is normal post-merge housekeeping
   per `nyxloom-P48`'s own precedent, just overdue) and fixed this
   package's own frontmatter `tier` (now `luna-high`, a real live key).
   O6 exists to catch this class of problem recurring, not to redo work
   already done — do not re-archive anything or second-guess `luna-high`
   as this package's own tier value, just confirm O6 passes.
7. **`src/nyxloom/adapters.py`'s `_TIER_BAND` is a second, live instance
   of NL-2's root cause** (`{"implement-1": 1, "implement-2": 2,
   "implement-3": 3}`, silently dead since D-BATCHC 2026-07-26 — no real
   handoff has ever matched these keys). It is filed separately as NL-7
   (`nyxloom-trove/backlog/NL-7-*.md`) and is explicitly **NOT** in this
   package's `scope.touch` — do not fix it here, a different file with a
   different owner. Mentioned so the implementer doesn't independently
   rediscover and unilaterally "fix" it outside scope.

## Work

1. **Fix AUTHORING.md's false "deployed today" claim.** Replace the
   paragraph at lines ~88-93 (currently: "Only `implement-1` and
   `implement-2` are deployed today. Until the remaining bands exist,
   `contract_class` is an authoring/review classification recorded in the
   body, while frontmatter `tier` names a live route. Do not put a
   nonexistent tier in frontmatter...") with EXACTLY this text, verbatim,
   no rewording (pinned to make O1 a plain substring check — this
   package's earlier oracle draft tried to catch every possible
   paraphrase of the false claim with a regex and failed both ways, see
   the fix-verification report; fixing the WORDING itself, not just
   banning bad wording, is what actually closes it):

   > The table above names the PLANNED tier each contract class is
   > intended to route through once the remaining implementer bands
   > exist — it is not itself a claim about what `routes.toml` declares
   > today. `contract_class` (2a-2e) is an authoring/review
   > classification recorded in the body; frontmatter `tier` is a
   > different thing entirely: it must always be a literal key that
   > exists in the CURRENT live `routes.toml`, chosen for the capability
   > the assigned contract class needs, regardless of what name a future
   > band will eventually carry. Do not put a nonexistent tier in
   > frontmatter. Routing a `2a`-`2c` package through a live tier whose
   > capability is below what the class needs requires an explicit
   > human/controller override and a frontier-capable route; preferably
   > carve it down first.

   (Confirmed accurate at carve time: the live matrix's actual keys are
   `flash-high`, `flash-max`, `terra-med`, `luna-high`, `sonnet5-high`,
   `frontier-review`, `haiku-low`, `free-high`; `implement-N` is a
   PLANNED future renaming per `routes.toml`'s own comment — "Destined
   for the `implement-2` tier once B16 ... lands" — not a current
   routing key.)
2. **Fix the worked example's literal `tier: implement-2`** (~line 390)
   to an angle-bracket placeholder consistent with every other value in
   that same YAML block (e.g. `tier: <a live key from routes.toml>` —
   exact wording is free, per Implementation packet's degrees of freedom
   below, but it must be a placeholder, not a second hardcoded literal).
3. **Add L14 to `lint.py`.** A new `_check_l14(findings, path, fm)`
   function: call `Routes.load()` (fresh, no caching — mirror how the
   rest of the codebase calls it); if that raises (missing file, parse
   error — catch broadly, this is a "can't determine" case, not a "found
   a defect" case), append a WARNING finding saying tier could not be
   validated and why, then return (do not raise further, do not block
   L1-L13's findings). If `Routes.load()` succeeds and `fm.tier not in
   routes.tiers`, append an ERROR finding naming the actual bad value and
   up to 3 nearest keys via `difflib.get_close_matches(fm.tier,
   routes.tiers.keys(), n=3)` (empty list is fine — do not error if no
   close match exists, just name the bad value). Call `_check_l14` from
   `lint_file` immediately after L13 (~line 215).
4. **Tests.** New `TestL14TierRoutesToml` class in `test_lint.py`,
   building its own isolated on-disk routes.toml per Context item 5 (not
   a bare `Routes(...)`):
   - O2: a real key passes with no L14 finding, via BOTH `lint_file()`
     and the real `nyxloom lint <path>` CLI invocation (use
     `subprocess.run` or however this test file already invokes the CLI
     elsewhere — check for an existing precedent in `test_lint.py`
     before inventing a new invocation style).
   - O3: the three real historical bad values (`implement-2`,
     `sonnet-xhigh`, `opus-xhigh`) AND the required near-miss
     `sonnet5-hgih` (not one of the three historical values — this is
     the fixture that distinguishes a real live-data check from a
     hardcoded blocklist of just the three named strings) each produce
     an L14 ERROR naming the value and at least one nearest-match
     suggestion.
   - O4: TWO cases — a project whose `routes_path()` doesn't exist, AND a
     project whose `routes_path()` exists but is malformed (build an
     actual malformed file: invalid TOML syntax OR a `[tiers.x]` table
     missing its `routes` key) — both produce an L14 WARNING via the real
     `nyxloom lint <path>` CLI subprocess (not `lint_file()` directly),
     with L1-L13's other checks still running normally for the same file
     (pick any other rule's fixture to confirm this — e.g. reuse a
     `TestL10Size` fixture's handoff and confirm its L10 finding is still
     present alongside L14's WARNING).
   - O5: the two-write, same-process re-lint proving no caching.
   - O6: after all of the above pass, on the worktree's own filesystem
     directly (never inside the `tester-unified` container, which does
     not mount the operator's real `routes.toml` — see O6's own text),
     lint every file currently in `nyxloom-trove/handoffs/*.md` (the real
     directory, not a test fixture) and confirm none produces an L14
     finding — a one-time real-repo check, not a new automated test, run
     and recorded verbatim in the REPORT as evidence (see LOG/REPORT
     contract).
5. **Fix the ownership inventory `tests/test_core_characterization.py`
   checks against reality — a reverse dependency Work items 1-4's real
   line-count growth trips, exactly like nyxloom-P98 hit and fixed the
   same way.** Adding L14 to `lint.py` grows it past
   `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`'s
   recorded row for that file (recorded 1,112 lines at carve time;
   `test_inventory_sizes_are_within_the_declared_tolerance` fails once
   the real count drifts past its declared tolerance). Re-measure
   `src/nyxloom/lint.py` with `wc -l` on the tree AFTER Work items 1-4's
   edits land (do not hardcode a number from this handoff's own prose —
   re-measure for real, matching nyxloom-P99's own hard-won lesson about
   this exact file), update its recorded line count, and add a short
   "Re-measured <today> (nyxloom-P100)" note following the document's own
   existing convention (see its "Re-measured DATE (CR-NN review) for ..."
   paragraphs near the top). This file is a *live*, mechanically-checked
   inventory — unlike `tests/legacy_planner.py` (a DIFFERENT, frozen file
   this package never touches), it is meant to be kept current, not
   frozen; updating it is the correct fix, not a forbidden edit. Do not
   re-measure or touch any OTHER row in this file — only `lint.py`'s.

## Implementation packet (normative)

**Owned interfaces:**
- `_check_l14(findings: list[LintFinding], path: Path, fm) -> None` —
  no new parameters beyond what L9-L13 already take (`fm` is the parsed
  frontmatter object already available in `lint_file`); does not need
  `cfg` (routes.toml is a global resource, not per-project) or `body`/
  `full_text`.
- No new dataclass, no new config field — this package only ADDS a lint
  check that consumes existing `Routes`/`fm.tier`.

**Construction/decision table:**

| routes.toml state | `fm.tier` value | L14 outcome |
|---|---|---|
| Loads successfully | a key in `.tiers` | no finding |
| Loads successfully | NOT a key in `.tiers` | ERROR, names the bad value + up to 3 nearest keys |
| Missing / unparseable | (any) | WARNING, "could not validate," L1-L13 unaffected |

**Bounds:** `difflib.get_close_matches`'s `n=3` cap and default `cutoff=0.6`
are both free choices (not tested at an exact boundary) — the oracle only
requires "at least one" suggestion appear for the `sonnet-xhigh` case,
which is a strong match against `sonnet5-high` well within defaults.

**Prepared proof / traceability:** O1 → the doc fix is accurate, not just
absent. O2 → the check exists AND is wired into the real CLI path,
proven twice (direct call + subprocess). O3 → the check rejects real
historical bad values with a helpful message. O4 → fail-soft on missing
data, doesn't break the rest of lint. O5 → no caching, genuinely
live-reads the file.

**Degrees of freedom left to the implementer:** the AUTHORING.md
replacement paragraph's wording is NOT free — Work item 1 pins it
verbatim, and O1 checks for its exact presence (a lesson from this
package's own first carve-review round: an open-ended "wording is free,
just don't say the false thing" instruction is exactly what made the
first O1 draft unenforceable). The worked example's placeholder text (the
`tier:` line's replacement value) IS free, as long as it's an
angle-bracket placeholder, not a second literal. The exact
`LintFinding.message` text for L14 is free (must name the bad value and,
when applicable, nearest matches, per O3 — exact phrasing is free);
whether `_check_l14` is implemented as one function or delegates to a
small private helper for the `difflib` lookup is free (either is fine, no
test distinguishes them).

## Scope / forbid

- **`src/nyxloom/config.py`'s `Routes` class** — already-used, working
  infrastructure (`reconcile.py:1072`, `rules_dispatch.py:109` already
  call `inp.routes.for_tier(fm.tier)` in the daemon's real routing path).
  This package is a new CONSUMER of `Routes.load()`, not a change to it.
- **The operator's real, live routing matrix** (whatever
  `paths.routes_path()` resolves to outside a test — an XDG state-dir
  file, not part of this repository) — never read or write it directly
  from a test or from any new code this package adds outside the normal
  `Routes.load()`/`paths.routes_path()` indirection. Every test isolates
  via the `tmp_state` fixture, matching `tests/conftest.py`'s
  `sample_project` pattern (`paths.routes_path().write_text(...)`).
- **`src/nyxloom/schemas/handoff-frontmatter.schema.json`'s `tier`
  field** — its `"type": "string"` + `"pattern": "^[a-z][a-z0-9-]{1,62}$"`
  stay exactly as they are. The schema validates SHAPE (a lowercase
  kebab-ish string); L14 validates LIVE VALUE (is it an actual routing
  key right now). These are deliberately different layers — do not try
  to fold live-data validation into the schema, which is checked without
  reading any external file.

## Environment setup

Mode-B, this worktree only (`/workspaces/vbpub/.worktrees/nyxloom-nl2`,
branch `feat/nyxloom-P100-tier-routes-toml-validation`). No package image
tag needed. Gate runs via `./run-gate.py --worktree {worktree}
tester-unified`.

## Gate argv (verbatim)

```
cd /workspaces/vbpub/.worktrees/nyxloom-nl2/nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl2 tester-unified
```
