# Plan: F007 (gap-engine) + D-R8 (bounded reviewer repair)

Status: active · 2026-07-26 · operator directive: "keep implementing until built"

Lossless handoff for a multi-package build spanning two previously-unbuilt
roadmap items. Distilled per CLAUDE.md's planning→implementation transition.

## Design decisions (locked this session, see docs/routing-model-redesign.md
D-R2/D-R3/D-R8 "refined 2026-07-26" notes for full rationale)

1. **No sandbox-level scope enforcement exists in nyxloom** for any dispatch
   role today — `scope.touch`/`scope.forbid` is prompt instruction + review
   judgment only (grep confirmed: no sandbox_permissions wiring for it).
   D-R8's "mechanical enforcement" must therefore be **post-hoc**: after a
   reviewer's repair commit, the daemon checks `git diff --name-only` on that
   commit against the allowed test/fixture path pattern and invalidates the
   repair if it touched anything else — never a preventive sandbox block.
2. **Gap-engine's core dispatch mechanism already exists** — the test-health
   carve trigger (`reconcile.py` module contract item 15, `daemon.py`
   `_carve_packet_body_lines`'s `kind == "test-health"` branch) is the exact
   shape gap-engine needs: a periodic, project-WIDE (not per-task) carver
   dispatch that emits carve candidates via the same envelope. Gap-engine
   adds a sibling `kind == "gap-audit"` branch, not a new dispatch role or
   mechanism (`types.py`'s frozen `Role` enum is untouched).
3. **Activity-counted cadence, not calendar** (operator correction,
   2026-07-26): both the core gap-check and the verdict-audit extension fire
   on accumulated changed-lines since the last run, mirroring
   `days_since_gate_verify`'s exact shape (stored checkpoint, daemon computes
   the delta, reconcile.py stays pure) but with the unit swapped from
   calendar days to the same "changed executable lines" figure
   `coverage_gate` already computes per merge — reused, not a new metric.
4. **Verdict-audit runs BLIND FIRST**: given only the handoff's oracles + the
   final merged diff, form an independent judgment BEFORE being shown the
   recorded verdict — never primed with the original reviewer's reasoning
   (anchoring risk, same principle as the escalated-review handoff in D-R3).
5. **Component-level audit granularity**, not per-handoff: catches
   cross-task drift no single-task review can see. Drill-down attribution
   uses the EXISTING durable event log (`SP04`'s greppability bridge + git
   history) — no new storage. `P42` (first-class component/file mapping) is
   a precision improvement, NOT a hard blocker — v1 gap-audit does its own
   per-feature code discovery.

## Sequencing (collision-aware)

Two packages are in flight as of this plan (worktrees `dry-standing-
instructions`, `incapable-reject-class`) touching `adapters.py`/
`reconcile.py`/`daemon.py`. **Every package below must be based on a main
that already includes both merges** — do not dispatch against a stale base.

```
Wave 1 (sequential, blocks on the in-flight packages merging):
  GE0 — activity-cadence infra (small, foundational)
Wave 2 (parallel OK — disjoint primary files):
  GE1 — gap-engine core (reconcile.py trigger + daemon.py kind="gap-audit")
  DR8 — bounded reviewer repair (adapters.py dispatch + daemon.py post-hoc check)
Wave 3 (depends on GE1 + DR8 both merged):
  GE2 — verdict-audit extension (reuses GE1's trigger, needs DR8 merged only
        insofar as an audited task might itself carry a repair to inspect —
        soft dependency, not a hard block)
```

## GE0 — activity-cadence infra

**Files:** `config.py` (new `Policy` field, e.g. `gap_audit_after_changed_
lines: int = 0`, 0 = disabled, same convention as `gate_verify_interval_
days`), `daemon.py` (a `_changed_lines_since(<last-checkpoint-event>)`
helper mirroring how `_days_since_gate_verify` is computed today — grep
`daemon.py` for that name to find the exact precedent to mirror), `reconcile.
py` (a new `ReconcileInput` field, e.g. `changed_lines_since_gap_audit: int
| None`, computed by the daemon and passed in pure, exactly like
`days_since_gate_verify`'s own field).

**Oracle:** a fake sequence of merges whose cumulative changed-lines crosses
the configured threshold flips the trigger condition true; a sequence that
stays under it never fires, even after real calendar time passes (proves
it's activity-counted, not time-counted — this is the actual behavioral
distinction from `gate_verify_interval_days` and must be asserted
explicitly, not just "the field exists").

## GE1 — gap-engine core

**Files:** `reconcile.py` (new trigger item, mirror item 15's guard shape —
`policy.gap_audit_after_changed_lines > 0` AND GE0's due-check AND the
shared not-paused/not-carve-in-flight guards items 9/15 already reuse),
`daemon.py` (`_carve_packet_body_lines`'s new `kind == "gap-audit"` branch:
read `nyxloom-trove/2-product-definition.md`'s `status: done` features +
their `acceptance` criteria list, instruct the carver-mode dispatch to check
each criterion against actual code reality via its own file discovery — NOT
a component-map lookup, that's GE3/P42's job — and report ABSENT/PARTIAL
criteria as carve candidates via the existing carve-proposal envelope; carve
NOTHING if every checked feature's criteria hold, same "you are explicitly
authorized to return an empty carved list" framing test-health already
uses).

**Oracle:** a fixture product-definition with one `status: done` feature
whose acceptance criteria have no corresponding code produces a carve
candidate naming that gap; a fixture where every criterion has matching code
produces an empty carve list (mirror test-health's own "carve nothing if
healthy" oracle pair).

## DR8 — bounded reviewer repair

**Files:** `config.py` (policy knob enabling repair mode — gate it to fire
ONLY under serial scheduling per D-R8's own "safest in serialized operation"
text; read whatever per-stage `concurrency`/serial signal B3/P71 already
exposes rather than inventing a second one), `adapters.py`
(`build_dispatch`'s `REVIEW_INDEPENDENT` branch: when repair mode is on for
this dispatch, the prompt permits a bounded fix — test/fixture/assertion
paths only, never production logic — and the verdict contract gains
`VERDICT: APPROVED (repaired)` + a one-line note of what was patched,
mirroring the `REJECT_CLASS:` idiom), `daemon.py` (after a review commit
lands with an `(repaired)` verdict, `git diff --name-only` the reviewer's
commit(s) and reject the repair — fall back to treating it as an ordinary
`REJECTED` review, human/mechanical retry path unchanged — if anything
outside the allowed test/fixture pattern was touched; still requires a
fresh full-gate rerun exactly like any other commit, no new re-verification
layer beyond that).

**Oracle:** a reviewer repair touching only test files, verdict `(repaired)`,
passes through to normal APPROVED handling. A reviewer repair that ALSO
touched a production `src/` file is invalidated by the post-hoc check even
though the reviewer wrote `VERDICT: APPROVED (repaired)` — this is the
actual enforcement mechanism and needs its own explicit negative test, not
an assumption that the prompt instruction alone is sufficient.

## GE2 — verdict-audit extension

**Files:** extends GE1's `kind="gap-audit"` dispatch (or a sibling
`kind="verdict-audit"` — implementer's call based on how much the two turns'
prompts actually share) with: sampling recently-`COMPLETED` tasks up to
GE0's activity-threshold, grouped by component (free-text `component:`
field already present in backlog/product-definition entries — use it as-is,
do not wait on P42), each sample given ONLY the handoff's oracles + final
diff (never the recorded verdict) to render an independent judgment, THEN
compared against the recorded verdict — `DISPUTED` samples become carve
candidates through the same envelope GE1 uses.

**Oracle:** a fixture where the auditor's blind judgment disagrees with a
pre-recorded `APPROVED` verdict produces a `DISPUTED` carve candidate; a
fixture where they agree produces nothing. A test proving the auditor's
dispatch is genuinely built WITHOUT the recorded verdict in its prompt (not
just instructed to ignore it) — assert on the prompt string not containing
the verdict text, mirroring how this session's own reviewer-escalation
package proves non-anchoring.

## Worktree directive (per package, follow the established convention)
```
git worktree add -b feat/<package-slug> .worktrees/<package-slug> main
Gate: docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
  'cd /workspaces/vbpub/.worktrees/<package-slug>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n 4 -q --cov=src/nyxloom --cov-report=json:/tmp/cov-<slug>.json; echo PYTEST_EXIT:$?; PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main --coverage-json /tmp/cov-<slug>.json --source src/nyxloom; echo GATE_EXIT:$?'
```
Every branch from `main` AFTER the current in-flight packages (`dry-standing-
instructions`, `incapable-reject-class`) have merged — never from a stale
base, given `reconcile.py`/`daemon.py`/`adapters.py` overlap.

## Explicitly deferred (do not fold into this plan)
- `P42` (first-class component/file mapping) — precision improvement for
  GE1/GE2, not a blocker; separate backlog item.
- Batch/parallel-scheduling variant of DR8 (bounded to reviewed-diff files
  only per D-R8's own text) — serial-only for v1.
