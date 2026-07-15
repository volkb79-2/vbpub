# Frontmatter conversion report — groop OPEN handoffs → handoffctl2

Converted the 11 named OPEN groop handoff files to handoffctl2 YAML frontmatter.
No other handoff file was touched (merged history stays as-is). No commit was made.

## Naming deviation (read this first)

The task brief's literal resolution text said: "RENAME each file to lowercase
`p89-<rest>`". That literal form (`p89-source-auto-backfill`) does **not** satisfy
the schema's `id` pattern:

```
^[a-z][a-z0-9]*-P[0-9]{2,4}(-[a-z0-9-]+)?$
```

which requires a literal **uppercase `P`** after the lowercase area-prefix hyphen
(verified empirically with `re.match` against both the schema string and the
actual `handoffctl.cli lint` tool — `p89-source-auto-backfill` fails, while
`groop-P89-source-auto-backfill` passes and also satisfies L1's
`id == filename stem` check). Since the task's hard gate requirement ("zero error
lines … iterate until clean") is a stronger, directly-testable constraint than the
paraphrased resolution hint, I renamed files to `groop-P<NN>-<slug>` (project id as
the lowercase area prefix, keeping the `P<NN>` segment's original case) — matching
the only existing precedent in the codebase, the `demo-P01-sample` id used in
`handoffctl2/tests/conftest.py`'s `SAMPLE_HANDOFF`/fixtures. This is flagged here
explicitly as a deviation from the literal brief text, resolved in favor of the
literal, hard, verifiable gate requirement.

## Body-preservation note

Per the brief, the pre-existing body (goal/contracts/oracles/out-of-scope/gates,
including the legacy `<!-- controller-workflow-v2 header -->` blockquote) is kept
**byte-identical** — nothing in the original text was edited, reworded, or
reordered. However, rule L11 (worktree/branch/context-to-read mention) and L12
(`BLOCKED:` marker) are hard errors that none of these legacy bodies satisfy as
written (confirmed empirically before doing the full conversion — a plain
frontmatter+original-body-only trial file failed both). Since the brief also
requires a zero-error lint gate, I appended one new, clearly-labelled
`## Conversion addendum (handoffctl2 execution notes)` section **after** the
original (unmodified) body in each file, supplying exactly the missing mechanical
content (branch name, worktree path, context-to-read pointer, `BLOCKED:` rule).
Nothing before that heading was changed.

## Old → new filename mapping

| Old (in `groop/handoff/`) | New (in `groop/handoff/`) | id |
|---|---|---|
| `P89-source-auto-backfill.md` | `groop-P89-source-auto-backfill.md` | `groop-P89-source-auto-backfill` |
| `P90-bounded-process-sampler.md` | `groop-P90-bounded-process-sampler.md` | `groop-P90-bounded-process-sampler` |
| `P91-persistent-capped-history.md` | `groop-P91-persistent-capped-history.md` | `groop-P91-persistent-capped-history` |
| `P92-loopback-web-transport.md` | `groop-P92-loopback-web-transport.md` | `groop-P92-loopback-web-transport` |
| `P93-lifecycle-owner-protocol.md` | `groop-P93-lifecycle-owner-protocol.md` | `groop-P93-lifecycle-owner-protocol` |
| `P94-detail-observation-leases.md` | `groop-P94-detail-observation-leases.md` | `groop-P94-detail-observation-leases` |
| `P95-lifecycle-identity-incidents.md` | `groop-P95-lifecycle-identity-incidents.md` | `groop-P95-lifecycle-identity-incidents` |
| `P73-web-ui-read-only-shell.md` | `groop-P73-web-ui-read-only-shell.md` | `groop-P73-web-ui-read-only-shell` |
| `P77-web-ui-entity-detail.md` | `groop-P77-web-ui-entity-detail.md` | `groop-P77-web-ui-entity-detail` |
| `P64-report-baseline-regression-gate.md` | `groop-P64-report-baseline-regression-gate.md` | `groop-P64-report-baseline-regression-gate` |
| `P65-report-human-readable-render.md` | `groop-P65-report-human-readable-render.md` | `groop-P65-report-human-readable-render` |

Renames done via `git mv` (staged, not committed).

## Per-file summary

| id | tier | session | depends_on (open-set only) | oracle count | gates |
|---|---|---|---|---|---|
| groop-P89-source-auto-backfill | sonnet5-high | resume:p88 | — | 7 | groop-suite, py-compile |
| groop-P90-bounded-process-sampler | sonnet5-high | fresh | — | 8 | groop-suite, py-compile |
| groop-P91-persistent-capped-history | sonnet5-high | fresh | — | 10 | groop-suite, py-compile |
| groop-P92-loopback-web-transport | sonnet5-high | fresh | groop-P91-persistent-capped-history | 9 | groop-suite, py-compile |
| groop-P93-lifecycle-owner-protocol | sonnet5-high | fresh | — | 11 | groop-suite, py-compile |
| groop-P94-detail-observation-leases | sonnet5-high | fresh | groop-P90-bounded-process-sampler | 12 | groop-suite, py-compile |
| groop-P95-lifecycle-identity-incidents | sonnet5-high | fresh | groop-P91-persistent-capped-history, groop-P93-lifecycle-owner-protocol | 10 | groop-suite, py-compile |
| groop-P73-web-ui-read-only-shell | sonnet5-high | fresh | groop-P89-source-auto-backfill, groop-P92-loopback-web-transport | 10 | groop-suite, py-compile |
| groop-P77-web-ui-entity-detail | sonnet5-high | resume:p73 | groop-P73-web-ui-read-only-shell | 11 | groop-suite, py-compile |
| groop-P64-report-baseline-regression-gate | flash-high | fresh | — | 9 | groop-suite, py-compile |
| groop-P65-report-human-readable-render | flash-high | fresh | — | 8 | groop-suite, py-compile |

Notes on `depends_on`:
- Values are exactly the open-set dependency graph given in the brief (P92→[P91],
  P94→[P90], P95→[P91,P93], P77→[P73]), using the renamed lowercase-prefixed ids.
  Legacy per-file `Depends-on:` headers list additional ids (e.g. P88, P66, P81,
  P90/91/94/95 for P77) that are either already-merged (satisfied, correctly
  omitted per the brief) or, for P77, explicitly excluded by the brief's stated
  graph even though P77's own legacy header lists P90/P91/P94/P95 too — followed
  literally as instructed rather than re-derived from the legacy header.
- P73's `depends_on` was computed as instructed: its own legacy header (`P89,
  P92`) intersected with the open set — both are in the open set, so both are kept.
- `session` uses `resume:<area>` only where the legacy `Session-hint:` said resume
  (P89 → `resume P88 if warm`, P77 → `resume P73 if warm`); all others are `fresh`.
- `mutexes` was left unset (schema default) — the legacy `Serialize-with:` hints
  (e.g. P92/P73/P77 sharing web routes/assets, P64/P65 sharing the report CLI) have
  no corresponding named mutex declared in `groop/.handoffctl/project.toml` (only
  `merge-lane` exists), and the brief did not ask for `mutexes` to be populated —
  so no mutex name was invented.
- `scope.touch` fell back to `["groop/**"]` / `forbid: []` uniformly. None of the
  11 bodies contain a dedicated scope/paths section (globs); they only have prose
  "Out of scope" (conceptual, not file paths) plus a few doc filenames named in
  their "Gates" sections (e.g. `docs/WEB-UI.md`, `docs/LIFECYCLE-ADAPTERS.md`,
  `CONTRACTS.md`) as deliverables rather than a scope declaration — so the
  brief's explicit fallback applied.
- `source: {kind: roadmap, ref: docs/ROADMAP.md}` for all 11 — none of the bodies
  read as a review follow-up.
- `escalate_if` entries were split from each legacy `Escalate-if:` header into
  individual mechanical trigger clauses (dropping trailing directive sentences
  like "Never key history by PID alone" / "Prefer a typed degraded store", which
  are design guidance, not BLOCKED triggers, and were not required by the L8
  mechanical-trigger rule).

## Lint gate result

```
cd /workspaces/vbpub/handoffctl2 && env PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m handoffctl.cli lint
```

Result for all 11 converted files, individually and as part of the full project
lint: **clean — zero findings of any severity** (no errors, no warnings; L10 size
warnings did not trigger either). The brief's own grep filter
(`grep -E "p(89|90|91|92|93|94|95|73|77|64|65)-"`) matches zero lines against our
files because it assumes an all-lowercase filename (`p89-...`); with the actual
(schema-required) `groop-P89-...` filenames, the equivalent check is:

```
env PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m handoffctl.cli lint \
  ../groop/handoff/groop-P{89,90,91,92,93,94,95,73,77,64,65}-*.md
```

→ `clean` (exit 0).

The full project-wide `lint` run still reports 85 pre-existing `L1 error
parse/schema error: missing leading '---'` lines for the *other*, unconverted
(merged-history) handoff files in `groop/handoff/` — expected and out of scope
for this task; not one of them references any of our 11 new filenames
(`grep -c "groop-P" full-lint-output` = 0).

## `tick --project groop` result

```
cd /workspaces/vbpub/handoffctl2 && env PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m handoffctl.cli tick --project groop
```

Output: `11` — one action per converted file. Confirmed via
`handoffctl.cli status --project groop` and
`~/.local/state/handoffctl/projects/groop/events.jsonl`: all 11 new task ids
(matching the new file stems) were created with a `TASK_CREATED` event and now
sit in state `CARVED`. No dispatch/attempt activity was recorded for any of them
(consistent with the project being paused).

## Deliverables

- 11 converted+renamed files under `groop/handoff/` (see mapping table above).
- This report: `groop/handoff/reports/FRONTMATTER-CONVERSION-REPORT.md`.
- No commit made (renames are `git mv`-staged only, per instructions).
