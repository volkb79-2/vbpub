# ciu-P46 — `persist:"secret"` hook channel, Vault-bootstrap migration, F7 static
# stage, `[state]` secret-shape guard, and the `ciu migration-check` framework

**Input revision:** ciu `main` @ current HEAD (post 7.8.0, `CHANGES.md`'s
`[7.8.0]` is the latest released section).

**Status:** design fully settled by the operator across a multi-turn interview
(this handoff is the distilled result — do not re-litigate the decisions
below; they are final). Read this document in full before touching code.

## Why this package exists

`docs/CIU-V8-TESTING-GATE-PROPOSAL.md` (rev 2.1) documents a full v8 redesign
of secrets and identity (§10, `SPEC-V8.md` §S10/§S4.1). The full v8 schema
cutover is a large, separate program (V8-1..V8-20) not started yet. Two
pieces of that redesign are self-contained, already-decided in the design
docs, and don't depend on any v8 schema/registry work — they are backported
onto **today's v7 model** now, ahead of the cutover:

- **F4** (`docs/CIU-V8-TESTING-GATE-PROPOSAL.md` line 880, "Vault bootstrap
  out of `[state]`") — today `post_compose_vault.py`-style hooks persist
  Vault's root token/unseal key into the stack's plaintext `[state]` table
  (`docs/SPEC.md` S3.4/S9.4), which sits **outside every S4 leak-prevention
  mechanism** (no masking, no post-render scan, no 0440 mode — it's an
  ordinarily-rendered, ordinarily-readable file).
- **F7** (line 883, "Vault-presence static rule") — today, a stack declaring
  `ASK_VAULT`/`GEN_TO_VAULT` with no Vault configured only fails at runtime
  (S4.16), not at `ciu check` time.

A third, operator-specified requirement ties them together: **no
fallback-reads or legacy-compat shims anywhere in ciu's normal code paths.**
Every rename/removal in this and the following package (ciu-P47, not yet
dispatched) is a **hard cutover** — old behavior/paths are simply gone, no
dual-reading. Instead, a new standalone diagnostic, `ciu migration-check`,
is the ONE place that knows about ciu's own version history and warns an
operator about stale artifacts. This keeps the normal render/read code paths
exactly as clean as if the legacy behavior had never existed, while still
giving operators a real signal instead of a silent break.

**Important — read this correction, do not re-derive it:** an earlier
framing of this work (mid-interview) suggested `persist:"secret"` would also
unblock CIU-38 (per-service Vault AppRole provisioning,
`KNOWN_ISSUES_TODO_BACKLOG.md` line 442, currently OPEN). That was wrong and
was corrected before this handoff was written: AppRole credentials route
through **Vault itself** (a hook mints them directly into Vault via its own
HTTP/`hvac` calls; the consumer reads them back with an ordinary `ASK_VAULT`
directive) — this already works today with zero new ciu mechanism, per
`docs/V8-REALIZATION-GRAPH.md`'s traced example. **Do not touch CIU-38's
backlog disposition; it stays OPEN/deferred exactly as filed, unrelated to
this package.** `persist:"secret"` exists solely for values that have **no
directive that could express them** — Vault's own root token/unseal key
being the only current example (nothing can `ASK_VAULT` a token before
Vault can be authenticated to at all — the same pre-existence problem
`S14.3a`/CIU-35 solved for host-scoped secrets).

## Scope of THIS package (ciu-P46)

Everything below. Do **not** implement the overlay-file split/rename
(`ciu.global.instance.toml.j2` / `ciu.instance.generated.toml`) — that is a
separate follow-up package (ciu-P47) dispatched after this one merges and
releases. This package's `ciu migration-check` registry must be a real,
extensible framework so ciu-P47 can add one more rule to it without
restructuring anything.

### A1 — `persist:"secret"` hook-return kind

Extend `S9.4`'s structured hook-return contract
(`docs/SPEC.md` line 1653, `src/ciu/` hook-return handling — grep for where
`persist` is currently read/dispatched, likely in `engine.py` near S9.4's
implementation) to accept `"persist": "secret"` alongside the existing
`"persist": "state"`.

- **Storage**: write through the **existing** per-stack secret store
  machinery in `src/ciu/secrets/materialize.py` — same file shape
  (`<stack>/.ciu/secrets/<name>`), same mode 0440, same atomic
  temp+`os.replace` write, same store-dir permissioning (`_STORE_DIR_MODE`,
  `_write_store_file`, etc. already exist — reuse them, do not reinvent a
  parallel writer). Do not introduce a project-store variant for this;
  hook-persisted secrets are stack-scoped like `GEN_LOCAL`'s per-stack path.
- **Naming/collision**: a hook-persisted name colliding with a name already
  declared in that stack's `[<root>.secrets]` table (any table, S4.1) is a
  contract-violation error at hook-return time, mirroring S4.6's uniqueness
  rule ("secret name already declared via directive X").
- **`apply_to_config` + `persist:"secret"` together is REJECTED** as a
  contract violation. `apply_to_config` was designed for non-secret facts
  (`state`); allowing a hook to also inject a raw secret value into the
  in-memory config for later templates/hooks to see would bypass S4.21's
  guard-object leak prevention entirely. If a later hook/configfile
  genuinely needs the value, it reads it the same way any other secret
  consumer does (`ctx.secret_file(name)`, or `secret('<name>')` in a
  configfile template once the corresponding delivery exists) — never via
  `apply_to_config`.
- **Never logged**: the value must be masked in every log/error path the
  same way S4.21-23 already guard GEN_*/ASK_* values. Grep for where hook
  return values are currently logged (if at all) and confirm no raw value
  can reach stdout/stderr on this path, including on a contract-violation
  error (e.g. the collision-name error above must name the KEY, never the
  VALUE).
- **`ciu secrets list`**: hook-persisted secrets should appear alongside
  directive-materialized ones, distinguishable as hook-originated (e.g. a
  `source: hook:<script>` annotation in the listing, or an equivalent
  minimal marker — your call on the exact shape, but it must be
  discoverable from `ciu secrets list` output, not silently invisible).
  `ciu secrets reset` must be able to remove them too (same store, same
  mechanism — this likely falls out for free once storage reuses
  `materialize.py`'s existing primitives).
- **Document**: new normative clause in `docs/SPEC.md` (S9.4 itself, or a
  new S9.4a immediately after it — pick whichever reads better given the
  surrounding numbering) covering all of the above as MUST-shaped rules,
  matching this doc's existing normative style (see S9.4/S9.5 for tone/
  format).

### A2 — Vault-bootstrap migration off `[state]`

`docs/SPEC.md` S4.16 (Vault token source order, line 688) and
`src/ciu/secrets/providers.py::resolve_vault_token` (line 81) currently
resolve source #3 by reading `[state].root_token` from the local vault
stack's rendered `ciu.toml` (`vault.stack_path`, default `"infra/vault"`).

- **Hard cutover — remove this read path entirely.** Source #3 becomes:
  read the local vault stack's **hook-persisted secret store** (the
  `persist:"secret"` file from A1) for a well-known name — keep the name
  `root_token` so the mental model doesn't change, just the storage
  location. No fallback to `[state]`; if the store file is absent, source #3
  yields nothing (same "falls through" semantics S4.16 already documents for
  an absent `token_file`).
- `[state].initialized` is **unchanged** — it is not secret-shaped, stays in
  `[state]`, still via `persist:"state"`.
- Update `docs/SPEC.md` S4.16's own text to describe the new source #3.

**The real reference fixture — read this analysis, it's already done, do
not re-derive it:** `test-repo/infra/vault/post_compose_vault.py` +
`test-repo/infra/vault/ciu.toml` are ciu's own canonical dev-mode example
(also the basis for `docs/SPEC.md`'s §B.2 worked example — check whether
B.2's prose needs a matching update). This fixture's `root_token` is
**already** declared as `GEN_LOCAL:demo/vault_root_token` in
`[vault_core.secrets]` — i.e. already safely materialized (0440, masked,
leak-scanned) from the moment it's generated. The hook merely re-reads it
(`ctx.secret_file("root_token")`) and re-persists it into `[state]` **purely
so S4.16's old source #3 could find it** — a redundant second copy of an
already-safe value. Once source #3 reads the hook-persisted secret store
directly:

1. **Delete the `root_token` return from `post_compose_vault.py` entirely**
   (keep only `initialized` → `persist:"state"`) — this fixture needs **no
   new mechanism at all**, it just stops writing a redundant, unsafe copy.
2. Remove `root_token = ""` from `test-repo/infra/vault/ciu.toml`'s
   `[state]` table.
3. Update the hook's own docstring (currently explains the old
   `[state]`-round-trip rationale) to describe the corrected flow.

**This fixture does NOT exercise a hook genuinely MINTING a value with no
directive backing it** — the real-world case this primitive exists for
(Vault's own `operator init` producing a fresh root token/unseal key nobody
could `GEN_LOCAL` in advance — see `docs/V8-REALIZATION-GRAPH.md`'s Wave-0
trace, "root_token+unseal_key → ... [state] AND ... vault-init.json"). Add a
**second, minimal illustrative worked example** for that genuine case —
either a small new test fixture or an addition to `docs/SPEC.md` §B
(alongside B.2) showing a hook returning
`{"root_token": {"value": <freshly generated>, "persist": "secret"}}` with
no corresponding directive anywhere. This is the pattern dstdns's own real
(non-dev-mode) vault hook will need to adopt — **note it explicitly in
`docs/CONSUMERS.md`** as a required consumer-side follow-up (mirror the
CIU-54/CIU-75 migration-note convention: named, dated, "action needed if
your hook currently persists a secret-shaped value into `[state]`").

### A3 — New `ciu check` static stage: Vault-presence (F7)

Any `ASK_VAULT`/`GEN_TO_VAULT` directive discovered anywhere in the deploy
set (reuse `src/ciu/secrets/directives.py::discover`, called per-stack the
same way existing S4/S13.4a validation already does) requires
`topology.services.vault.internal_host`/`internal_port` (S4.16's own address
source, read the same way `providers.py::vault_addr_from_config` does) to be
present in the merged config. Missing → **ERROR**, message shape
`[S13.x] vault directive(s) present ('<directive>' in stack '<stack>') but
topology.services.vault is not declared`.

- Pure static/config-shape check — no I/O, no live probe, matches every
  other `S13.4a` stage's side-effect-free contract.
- Add to `ciu check`'s stage table (`docs/SPEC.md` S13.4a, ~line 2102) as a
  new stage appended after the existing ones — mirror how `ciu-P22`'s
  `service-registry` stage was appended last "not part of the V8 proposal's
  own stage numbering" (see the existing stage table row for the exact
  wording style to match).
- Runs automatically pre-`ciu up` via the existing `S13.4c` mechanism
  (`ciu up`'s automatic static preflight, CIU-64) — this falls out for free
  once it's a normal `ciu check` stage; no separate wiring needed.

### A4 — New `ciu check` static stage: secret-shaped key in `[state]` is an
error

An **ordinary, always-on** validation rule — NOT migration-specific, fires
on any `[state]` table regardless of how it got that way. Reuse S2.4.1's
sensitive-key-name heuristic (last `_`-separated component is one of
`password`, `token`, `secret`, `api_key`, `credential`, `passphrase`,
`private_key`, `key`, paired with a literal string value ≥ 8 characters —
match the exact heuristic already implemented for S2.4.1, do not
reimplement a slightly different one) against every key in every stack's
rendered `[state]` table. A match → **ERROR**, naming the stack and key
(never the value), pointing at `persist:"secret"` as the sanctioned channel.

- Document as a new normative clause near S3.4/S9.4's `[state]` discussion
  (pick the least disruptive insertion point — e.g. S3.4a — given
  `docs/SPEC.md`'s existing numbering; do not renumber existing clauses).
- This stage is what makes A2's migration meaningful going forward: without
  it, nothing stops a future hook from regressing back to `[state]`.

### A5 — `ciu migration-check`: new verb + rule-registry framework

A new top-level CLI verb, `ciu migration-check`, plus a small, dedicated,
**version-history-aware** module (e.g. `src/ciu/migration_check.py`) that
today's normal code paths know nothing about — no legacy-reading branches
leak into `render_global_chain`/`S4.16`/anywhere else. All detection logic
lives here, in one place.

**Design**

- A plain list/registry of independent detector functions. Each takes
  `(repo_root, ...)` (whatever inputs each detector actually needs — e.g. a
  detector checking the overlay file needs `repo_root` and the ciu-root
  path resolution `S1`/`S2` already provides; a detector checking rendered
  config needs the merged config the same way a `ciu check` stage already
  gets it) and returns zero or more findings: `(severity, message,
  remediation)` — reuse the SAME `WARN`/`ERROR` vocabulary and shape S9.5's
  hook-preflight findings already use, for consistency.
- **No detector compares an "installed ciu version" against anything.**
  Every rule is purely pattern-based (does this file exist / does this key
  exist / is this table shaped a certain way) so it works regardless of
  which historical ciu version last touched the checkout — this avoids an
  entire class of version-comparison bugs and keeps each rule trivially
  testable in isolation.
- **Two entry points, one registry — do not duplicate detector logic:**
  1. `ciu migration-check` — directly invokable, `--json` supported (same
     convention as every other verb), root-resolution via `--define-root`
     per CIU-54's established convention (`deploy.resolve_repo_root`).
     Exit code: **0 when no findings, non-zero when any finding exists**
     (regardless of WARN/ERROR — this is a diagnostic tool, not `ciu
     check`'s own severity-gated exit).
  2. **Also registered as a new stage inside `ciu check`'s existing stage
     pipeline** — this is what gives it automatic coverage on every `ciu
     up` for free, via the pre-existing `S13.4c` mechanism (CIU-64), with
     zero new invocation machinery. As a `ciu check` stage, its findings
     feed into `ciu check`'s **existing** severity/exit aggregation exactly
     like any other stage (WARN = note only; ERROR = fails the stage) — do
     **not** invent a parallel exit-code scheme for the stage form; only
     the standalone verb (1) gets the "any finding → non-zero" rule.

**v1 rule set — exactly 3 rules** (a 4th was considered during design and
dropped as redundant — do not add it back): a rule detecting a
secret-shaped key in `[state]` would be **100% redundant with A4 above**,
which already fires unconditionally on that exact condition regardless of
its age. Do not duplicate it here.

1. **Legacy overlay filename present**: `ciu.global.worktree.toml.j2` exists
   at the ciu-root. **This package (P46) only needs to implement the
   detector function and register it in the framework — it will find
   nothing today, since P46 does not rename anything.** It becomes live the
   moment ciu-P47 (not yet dispatched) ships the rename. Implement it now
   so the framework has ≥1 real rule to prove the mechanism, and so P47
   doesn't have to touch the registry's plumbing, only add this one
   detector body. Severity: **WARN** (existence alone doesn't prove real
   content was lost — don't hard-block `ciu up` over a possibly-empty
   leftover file). Message: "migrate any hand-authored overrides in this
   file into `ciu.global.instance.toml.j2` by hand, then delete it — its
   content is no longer merged into any render." (Note: as of P46, the
   NEW filename doesn't exist yet either — word the detector/message so it
   reads sensibly whether or not P47 has shipped; simplest correct framing:
   "found `ciu.global.worktree.toml.j2` — this file is being retired; see
   `ciu migration-check --json` / release notes for the current
   destination filename" is acceptable if you'd rather not hard-code a
   filename that doesn't exist until P47. Use your judgment on wording,
   but do not block on it.)
2. **Backfill — stale reliance on `ciu.env` for identity** (CIU-60/CIU-75,
   `docs/SPEC.md` S3.1c): a checkout where `ciu.env` exists but the overlay
   has **no** `[ciu.instance.generated]` table at all (or the table exists
   but is missing one of the six keys) is carrying pre-CIU-75-shaped state.
   WARN, pointing at `ciu env generate`.
3. **Backfill — pre-CIU-61 gitignore gaps**: read CIU-61's own entry
   (`KNOWN_ISSUES_TODO_BACKLOG.md`, search "CIU-61") for the exact shipped
   mechanism and its test
   (`tests/tests/test_init_scaffolding.py::test_gitignore_entries_match_gitignored_ciu_sample`)
   — reuse that SAME comparison (the checkout's actual `.gitignore` against
   `_GITIGNORE_ENTRIES`/`.gitignored.ciu`'s canonical set) as a detector
   here, run against the CURRENT checkout's own `.gitignore` rather than a
   scaffold fixture. WARN, naming the missing entries.

**Tests**: full coverage for the registry framework itself plus each
detector (positive/negative fixtures), consistent with this repo's existing
test conventions and naming (`tests/tests/test_ciu_*.py`).

## Process requirements

- **Fresh implementer, zero prior context** — everything you need is in this
  document plus the live repo. Read `docs/SPEC.md`'s cited sections in full
  before editing (S3.4, S4.16, S9.1-S9.5, S13.4a/c) — do not skim.
- **Real gate required.** Run `./run-gate.py ciu` (the actual registered
  gate for this project — see `AGENTS.md`/`run-gate.toml`). A green
  `pytest tests/` alone is **not** proof of a green gate — this estate has
  an established, named gap between the two (`assay-gate-vs-pytest-gap`);
  run the real gate script and read its verdict in a **separate step**,
  never off a piped tail.
- **Docs to update**: `docs/SPEC.md` (every new normative clause named
  above — S9.4a, S4.16, the two new `ciu check` stages, S3.4a), `docs/
  CONFIG.md` / `docs/CONSUMERS.md` (consumer-facing migration notes — this
  ships **BREAKING** since S4.16's token source #3 changes and a new `ciu
  check` stage can newly refuse existing checkouts; per this project's
  established convention (see the `[7.7.0]`/`[7.8.0]` `CHANGES.md`
  sections), a deliberate breaking change may still ship as a MINOR when
  self-contained — use your judgment on MAJOR vs. MINOR but state the
  reasoning explicitly in `CHANGES.md`'s own text the way those two
  sections did), `CHANGES.md` (new version section with an
  Adoption/Migration Notes subsection — name exactly which existing
  checkouts are affected: anyone with a `[state].root_token`/`unseal_key`
  or Vault directives with no `[vault]`/`topology.services.vault`
  configured will now hit a new `ciu check` refusal; `ciu migration-check`
  is how they find out what to do about it).
- **`KNOWN_ISSUES_TODO_BACKLOG.md`**: add a note that F4/F7 (from
  `docs/CIU-V8-TESTING-GATE-PROPOSAL.md`'s Secrets audit table, lines
  880/883) are backported early via this package, ahead of the full v8
  cutover. **Do not touch CIU-38** — it stays OPEN/deferred, unrelated (see
  the correction at the top of this document).
- **LOG/REPORT**: `nyxloom-trove/reports/ciu-P46-LOG.md` (one entry per
  commit, self-hash rule — hash the commit that adds the LOG entry
  describing itself, matching prior packages' convention, e.g. `ciu-P45-
  LOG.md`) and `nyxloom-trove/reports/ciu-P46-REPORT.md` (per-oracle
  evidence: the real gate's verdict output, test counts, a summary of the
  A1-A5 design decisions as actually implemented, anywhere your
  implementation had to make a judgment call this handoff left open).
- **Checkpoint clause**: if you're still working past roughly 120k tokens of
  context or ~60 tool calls, cut at the next coherent boundary (a green
  gate > a commit > a LOG/REPORT write > the end of an edit cluster — never
  on a red gate), write a continuation brief to a durable file under
  `nyxloom-trove/reports/` naming exactly what's done/left/any open judgment
  calls, commit, and stop rather than pushing through ungrounded.
- **Commits**: conventional style matching this repo's history
  (`feat(ciu): ...`, `docs(ciu): ...`), trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015vMn5oN1w6KpvjGsStwVbW
  ```
- **Do not merge to `main`.** Commit your work in your worktree/branch and
  stop — a separate fresh adversarial reviewer verifies before any merge,
  per this repo's established pipeline (fresh implementer → real gate →
  fresh reviewer → merge on ACCEPT).
- Closing discipline: **claim only what you ran.** State the real gate's
  actual verdict, not an inference from `pytest`. A fresh reviewer will
  independently re-run everything.
