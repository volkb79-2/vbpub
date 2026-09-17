# ciu-P47 — overlay file split (`ciu.instance.generated.toml`) + rename
# (`ciu.global.worktree.toml.j2` → `ciu.global.instance.toml.j2`)

**Input revision:** ciu `main` @ current HEAD (post ciu-v7.9.0 / ciu-P46,
shipped and deployed 2026-09-02). Read `nyxloom-trove/handoffs/ciu-P46-
persist-secret-hooks-migration-check.md` and `nyxloom-trove/reports/
ciu-P46-{LOG,REPORT,REVIEW}.md` first for context on the sibling package and
the process this repo actually follows — you are the second checkpoint in
the same program, extending the `ciu migration-check` framework P46 built.

**Status:** design fully settled by the operator across a multi-turn
interview; this handoff is the distilled, final result. Do not re-litigate
anything below.

**Learn from P46's review, don't repeat its gap:** P46's mechanism was
accepted on the first pass with zero defects; ALL 8 of its review blockers
were documentation/consumer-facing text that still described the OLD
behavior after the code changed (a stale example, a stale error message, 5
docs). Before you report done, **do your own exhaustive grep-based sweep**
for every mention of `ciu.global.worktree.toml.j2`, `ciu.instance.
generated` (as a concept, not just the literal string), and the "surgical
text-region replace" write mechanism, across `src/`, `docs/`, `test-repo/`,
`README.md`, and test names/docstrings — not just the files you already
know you're editing. A fresh reviewer will grep just as hard as P46's did.

## Why this exists

Today, CIU-owned identity facts (`repo_name`, `instance_id`, `network`,
`physical_repo_root`, `repo_root`, `public_fqdn` — the `[ciu.instance.
generated]` table, CIU-60/CIU-75, `docs/SPEC.md` S3.1b/S3.1c) live INSIDE
the same file operators hand-author sparse global overrides into
(`ciu.global.worktree.toml.j2`). Because the file is shared, `ciu env
generate` can't just rewrite it — it does a byte-level "surgical text-region
replace" (find the `[ciu.instance.generated]` header line, find the next
line that starts a table at column 0, replace only that span) to avoid
clobbering hand-authored content elsewhere in the same file. This is
fragile by construction. Splitting the CIU-owned facts into their own
dedicated file removes the entire text-surgery mechanism: a file only CIU
ever writes can just be rewritten wholesale, every time.

The V8 design (`docs/CIU-V8-TESTING-GATE-PROPOSAL.md`, V8-2; `SPEC-V8.md`
S4.1/S14.2) already does this split and additionally renames the hand-
edited overlay itself (`ciu.global.worktree.toml.j2` → `ciu.global.
instance.toml.j2`, reflecting "every checkout is an instance," not just
literal git worktrees). This package backports BOTH pieces onto v7 now.

**Operator-mandated constraint (same as P46): no fallback-reads, no
legacy-compat shims in normal code paths.** The rename is a hard cutover —
`ciu env generate` and every reader look ONLY for the new filename, full
stop. `ciu migration-check` (P46, `src/ciu/migration_check.py`) already
ships a rule for exactly this — see "Activate the dormant rule" below.

**Operator decision, do not re-derive it:** the template-facing binding
name does **not** change. Templates keep reading `{{ ciu.instance.
generated.* }}` exactly as today. Only the FILE that backs it moves and is
renamed — this was explicitly weighed against adopting v8's `instance.*`
binding shape now, and declined, to keep this package's consumer-visible
blast radius to the file rename alone (v8's richer `instance.*` binding,
`[ciu.host.generated]`, `[ciu.instance.build]`, and realness records are
NOT part of this package — those are tied to v8's host/topology/realness
model, which doesn't exist in v7 yet, and are explicitly out of scope).

## Scope

### C1 — `ciu.instance.generated.toml`: new dedicated file

- A plain TOML file (NOT a `.j2` template — no Jinja rendering, no render
  context needed to read it, matching S3.1c clause 5's existing "the block
  is plain TOML by construction... a reader needs no render context"
  principle, now made literal instead of a targeted parse of a larger
  file).
- Lives at the same root the current overlay lives at (ciu-root /
  `repo_root` — check exactly where `ciu.global.worktree.toml.j2` is
  resolved from today and put the new file there).
- Contains exactly the same `[ciu.instance.generated]` table, same six
  keys, same values, same semantics as today (S3.1b) — this package moves
  WHERE it lives, not WHAT it contains.
- `ciu env generate` writes it **wholesale** (full-file rewrite, no
  text-surgery, no preservation logic needed — nothing else is ever in this
  file). Idempotent: an unchanged workspace produces a byte-identical file
  on a second run (same requirement S3.1b already states, now trivially
  satisfied by construction).
- **Internal ciu reads** (S3.1c: every internal read of `repo_name`,
  `instance_id`, `network`, `physical_repo_root`, `repo_root`,
  `public_fqdn`) now read THIS file directly, by exact path, for the
  checkout the fact is about — same semantics as today (three-outcome
  reader per S3.1c clause 4: absent file = "no facts", present-but-
  unreadable = INDETERMINATE/refuse-or-announce, never collapsed into
  "no facts"). Find every current call site that reads the embedded table
  out of `ciu.global.worktree.toml.j2` (grep for the constant/function
  that does the targeted parse) and repoint it at the new file.
- **Template-visible reads**: `{{ ciu.instance.generated.* }}` must resolve
  **identically** to today from every Jinja render context that currently
  sees it (S3.5.2/S3.5.3's merged global configuration). Concretely: this
  file's content must be merged into `render_global_chain`'s merged view at
  the exact point the embedded table used to occupy in the old file's
  position in the S3.3 chain (global defaults → global overrides → nested
  → the worktree/instance override, merged last before stack defaults).
  Whether you implement this by parsing the new file and injecting its
  table into the merge dict at that point, or some other mechanism, the
  OBSERVABLE contract is: no template anywhere changes a single character
  to keep reading `ciu.instance.generated.*`, before or after this package.
  Write a test that renders a real stack template referencing
  `ciu.instance.generated.physical_repo_root` (or reuse an existing one)
  and pins that it's unaffected by this change.

### C2 — rename: `ciu.global.worktree.toml.j2` → `ciu.global.instance.toml.j2`

- **Hard cutover, no fallback.** Update every read/write site (`config_
  constants.py` or wherever the filename is constant-defined, `workspace_
  env.py`'s writer, `render_global_chain`'s S3.3 merge chain, `ciu clean`'s
  preservation logic (S3.1b: "`ciu clean` and `ciu env generate` MUST
  preserve it"; `--vanilla`'s removal path), `ciu init`'s scaffold/`_
  GITIGNORE_ENTRIES` (CIU-61's precedent — `.gitignored.ciu` and `_
  GITIGNORE_ENTRIES` both need the new name added; **and the OLD name
  removed from both**, since it's no longer a CIU-generated artifact this
  repo should be telling every scaffolded consumer to gitignore — but see
  the migration-check interaction note below before doing that) to use ONLY
  the new filename.
- The file's ROLE is otherwise **unchanged**: still the sparse, non-secret,
  gitignored, hand-editable per-checkout override (S3.1b), still merged at
  the same point in the S3.3 chain, still where managed-lifecycle commands
  create `ciu.instance.service_profiles`/`ciu.instance.shared_infra` on
  first use. **Only its filename changes and the `[ciu.instance.generated]`
  content leaves it** (moved to the new file per C1) — this means the
  surgical-text-region-replace writer logic that existed SOLELY to protect
  hand-authored content from the generated-table upsert can be **deleted
  entirely**: after C1+C2, nothing CIU-owned is ever written into this file
  again, so a hand-edit inside it is never at risk of being silently
  overwritten. Confirm this by grepping for the surgical-replace code (S3.1b
  clause 2's implementation) and removing it if it has no remaining caller.

### C3 — activate `ciu migration-check`'s dormant rule

P46 shipped a rule detecting a legacy overlay filename, deliberately
**dormant** because at P46-ship-time the "legacy" filename was still the
live one. It filters a history list of retired names against a live-
filename constant (`GLOBAL_CONFIG_WORKTREE_OVERRIDES` or equivalent — check
`src/ciu/migration_check.py` for the exact name P46 used) so it fires on
anything in the history list EXCEPT whatever the constant currently points
to.

- **Flip that one constant** to `"ciu.global.instance.toml.j2"` (the new
  name). The moment you do, the rule should start firing a WARN on any
  checkout that still has `ciu.global.worktree.toml.j2` present — verify
  this happens (P46's own tests already prove the mechanism flips
  correctly by monkeypatching this exact constant; re-run/extend those with
  the REAL flip, not a monkeypatch, to prove the real cutover works end to
  end).
- Message content: point the operator at migrating any hand-authored
  content in the old file into the new one, then deleting the old file —
  its content is no longer merged into any render (P46's placeholder
  wording said as much; tighten it now that the real destination filename
  exists).
- **Do NOT remove the old filename from `_GITIGNORE_ENTRIES`/`.gitignored.
  ciu` in a way that makes `ciu migration-check`'s own detector unable to
  find it** — the detector needs to be able to check for the OLD file's
  presence regardless of what the current gitignore list says; these are
  independent concerns (gitignore hygiene vs. migration detection) — don't
  let fixing one accidentally blind the other.

### C4 — the real fixture(s) and every doc reference

`test-repo/` almost certainly has a `ciu.global.worktree.toml.j2` (or
relies on the generated-facts mechanism some other way) — find it and
update it to the new shape. Then sweep (this is the step P46's review
found gaps in — do it exhaustively, don't rely on your own memory of what
you touched):

- `docs/SPEC.md` — S3.1, S3.1a, S3.1b, S3.1c all reference the old filename
  and the surgical-replace mechanism by name; update all of them (S3.1b's
  clause 2 describing the text-surgery algorithm should be replaced with a
  much shorter "wholesale rewrite" description once C1 lands, or removed
  entirely and folded into a plain statement that the file is CIU-owned and
  fully regenerated).
- `docs/CONFIG.md`, `docs/CONSUMERS.md` (new dated migration-note entry,
  same convention as CIU-54/75/P46's #20 — name exactly what changes for an
  existing checkout: the old overlay filename stops being read, hand-
  authored content in it needs manual migration, `ciu migration-check` is
  how an operator finds out), `docs/CIU.md`, `docs/CIU-DEPLOY.md`,
  `docs/ARCHITECTURE.md`, `docs/FEATURES.md`, `README.md`, `docs/DESIGN-
  GUIDE.md` — grep every one of these for the old filename AND for prose
  describing the text-surgery mechanism (not just the literal filename
  string — P46's reviewer found prose that described a mechanism without
  naming the file).
- `CHANGES.md` — new version section (this ships BREAKING-as-MINOR, same
  established override as 7.7.0/7.8.0/7.9.0 — state the reasoning inline
  the way those did), Adoption/Migration Notes subsection.
- `KNOWN_ISSUES_TODO_BACKLOG.md` — note this closes the remaining half of
  the identity-file backport program (pointer to CIU-75's entry and this
  program's memory record); do not touch CIU-38 or CIU-50 (CIU-50, the
  `instance_id` *key* rename inside config, is a separate, still-
  independently-deferred item — don't fold it in here even though it's
  naming-adjacent).

## Process requirements (identical to P46, repeated so you don't need to
cross-reference)

- Fresh implementer, zero prior context beyond this document + the live
  repo + P46's LOG/REPORT/REVIEW (read those for process/tone, not for
  design decisions — this package's design is fully specified above).
- **Real gate required**: `./run-gate.py ciu` (or `--worktree <path>` if
  you're in an isolated worktree without a clean top-level tree — check
  which applies; P46's implementer needed `--worktree` for its own isolated
  checkout). A green `pytest tests/` alone is not proof of a green gate;
  read the verdict in a separate step, never off a piped tail.
- Update all docs named above, `CHANGES.md`, `KNOWN_ISSUES_TODO_BACKLOG.md`.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P47-{LOG,REPORT}.md`, same
  per-commit/per-oracle convention as `ciu-P46-{LOG,REPORT}.md` — read that
  pair first for the expected shape.
- Checkpoint clause: ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary (green gate > commit > LOG/REPORT write > edit-
  cluster end — never on a red gate), write a continuation brief to a
  durable file under `nyxloom-trove/reports/` if you need to stop, commit,
  and stop rather than pushing through ungrounded.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015vMn5oN1w6KpvjGsStwVbW
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge (same pipeline as
  P46: fresh implementer → real gate → fresh reviewer → merge on ACCEPT).
- Closing discipline: claim only what you ran. State the real gate's actual
  verdict. A fresh reviewer will independently re-run everything, and will
  specifically re-grep every doc/consumer surface the way P46's reviewer
  did — do that sweep yourself first so there's nothing left to find.
