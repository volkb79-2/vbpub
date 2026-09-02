---
schema_version: 1
id: ciu-P33-generated-identity-facts-env-print-clean-vanilla
project: ciu
component: workspace_env+worktree+deploy+cli+docs
title: "Close the S3.12-vs-S9.3 gap the operator identified: Jinja TEMPLATE rendering's `env` context is still raw ambient os.environ (hooks already got the safe S9.3 treatment; templates never did). `ciu env generate` derives the identity tuple (repo_name, instance_id, network, physical_repo_root, repo_root) exactly as today, and ADDITIONALLY write-once-per-run upserts them into a new `[ciu.instance.generated]` table in `ciu.global.worktree.toml.j2` (gitignored, clean-surviving, already-merged-into-every-render source layer -- CIU-52's exact precedent), so templates/hooks read these facts from the merged TOML chain like any other config value instead of from ambient env. Plus two small, independently-motivated QOL additions from the same operator conversation: `ciu env print` (emits `export KEY=VALUE` lines for `eval \"$(ciu env print)\"` -- a subprocess cannot mutate its parent shell, so this is NOT named apply/source) and `ciu clean --vanilla` (additionally wipes the rendered global config, ciu.env, and the worktree overlay -- a full reset to freshly-cloned state; ordinary `clean` today does none of this and must keep not doing it by default)"
tier: implement-4
input_revision: "95908afb"
source: {kind: operator-report, ref: "operator architecture discussion 2026-08-25, same devcontainer live-bug thread as ciu-P32: 'should the env / ciu.global.defaults.toml.j2 usage be reconsidered for every ciu verb', 'we cannot write generated vars to ciu.global.toml.j2 because this gets committed', 'we could write to ciu.global.toml instead of using ciu.env', 'ciu env apply or ciu env source', 'does ciu clean also remove ciu.global.toml? ... maybe have a ciu clean --vanilla'. Controller grounded the design against the actual code (grepped render/clean paths) before carving -- see nyxloom-trove memory ciu-repo-root-precedence-p32.md 'P33 design -- REFINED' section for the full reasoning trail if more context is needed than this handoff carries."}
stack: none
depends_on: [P32]
session: fresh
scope:
  touch:
    - "src/ciu/workspace_env.py"
    - "src/ciu/worktree.py"
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "src/ciu/config_model.py"
    - "tests/tests/test_ciu_workspace_env.py"
    - "tests/tests/test_ciu_worktree*.py"
    - "tests/tests/test_ciu_deploy*.py"
    - "tests/tests/test_ciu_dev.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/CIU.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P33-generated-identity-facts-env-print-clean-vanilla-LOG.md"
  forbid:
    - "src/ciu/dev.py"
    - "src/ciu/engine.py"
    - "src/ciu/composefile.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-generated-table-written-and-idempotent
    observable: "`ciu env generate` writes a `[ciu.instance.generated]` table into `<repo_root>/ciu.global.worktree.toml.j2` (create the file fresh if absent) containing exactly six keys derived from the SAME in-memory values already computed for ciu.env in this same invocation, no re-derivation and no re-reading ciu.env: `repo_name`, `instance_id`, `network` (= DOCKER_NETWORK_INTERNAL), `physical_repo_root`, `repo_root`, `public_fqdn`. Running `ciu env generate` a second time on an unchanged workspace produces byte-identical `[ciu.instance.generated]` content (same six values) -- upsert, not append; no duplicate table."
    negative: "a second `env generate` run appending a second `[ciu.instance.generated]` block instead of replacing the first; deriving these six values independently from what ciu.env computed rather than reusing the same in-memory tuple (a second derivation could disagree with the first, e.g. if a detector is non-deterministic)"
    gate: "tester-unified"
  - id: O2-existing-overlay-content-untouched-byte-for-byte
    observable: "Given a `ciu.global.worktree.toml.j2` that ALREADY contains hand-authored content the operator is documented as being allowed to add (S3.1b) -- e.g. a `[ciu.instance]` header with `service_profiles = [...]`, a `[ciu.instance.shared_infra]` block (CIU-52 shape), a trailing hand-written comment, AND a sparse override table unrelated to `[ciu.instance]` entirely (e.g. `[deploy]\\nsome_key = \"operator override\"`) placed BEFORE, BETWEEN, or AFTER where `[ciu.instance.generated]` will be inserted -- running `ciu env generate` inserts/updates ONLY the `[ciu.instance.generated]` block. Every other line in the file, including comment text, blank-line spacing, and key ordering outside that one block, is preserved BYTE FOR BYTE (assert on the literal surrounding text, not just that the values round-trip through a TOML parse). This is a text-level surgical replace, not a parse-whole-file-then-tomli_w-dump-whole-file operation -- the latter would silently discard every comment and reformat every table in the file, which fails this oracle."
    negative: "any implementation that parses the whole file with tomllib and re-serializes the whole file with tomli_w (destroys comments/formatting outside the owned block -- write a test with a comment elsewhere in the file and assert it survives verbatim, this is the mutant that kills a naive full-rewrite)"
    gate: "tester-unified"
  - id: O3-generated-facts-reach-templates-and-hooks-via-the-merged-config
    observable: "After `ciu env generate`, `render_global_chain` (or the render entrypoint an ordinary `ciu up`/`ciu render`/`ciu dev` verb already calls -- no NEW context-injection code needed, this table flows through the EXISTING worktree-overrides merge step at config_model.py's current `worktree_overrides_path` read) exposes `ciu.instance.generated.physical_repo_root` (and the other five keys) in the merged config dict, reachable by a Jinja template as `{{ ciu.instance.generated.physical_repo_root }}` exactly like any other config value -- no special-cased Jinja global, no separate context-building code path from what every other `[ciu.instance.*]` value already uses."
    negative: "adding a bespoke Jinja context variable (e.g. a new `ciu_context` field) instead of relying on the existing worktree-overlay merge that ALREADY flows into the Jinja context for every other value in this file -- that would reintroduce the exact 'invisible variable with no file backing it' hazard the operator explicitly rejected for this package's predecessor design"
    gate: "tester-unified"
  - id: O4-main-checkout-also-covered-not-just-worktree-instances
    observable: "The write-once-upsert in O1 fires for BOTH a worktree instance (S16 lifecycle record present) AND the primary/main checkout (no S16 record, `ciu.global.worktree.toml.j2` did not previously exist there at all) -- `render_global_chain` already reads this file unconditionally by exact path with no S16-record gating (confirmed at config_model.py's `worktree_overrides_path = repo_root / GLOBAL_CONFIG_WORKTREE_OVERRIDES`); `env generate`'s write side must not gate on an S16 record either, or the main checkout keeps leaking to ambient env exactly as before while worktrees are fixed -- that would be an incomplete fix."
    negative: "gating the new write behind `find_instance_record(...) is not None` or equivalent, silently leaving the primary/main checkout on the old ambient-env-dependent path"
    gate: "tester-unified"
  - id: O5-env-print-verb
    observable: "New verb `ciu env print [--define-root PATH]` reads the ALREADY-WRITTEN `ciu.env` at the resolved workspace root and emits one `export KEY='value'` line per entry to stdout (shell-safely single-quoted, embedded single quotes escaped correctly -- reuse this codebase's existing shell-quoting helper if one exists, grep for it before writing a new one), nothing else on stdout, no side effects, no re-generation. If `ciu.env` does not exist yet, refuse loudly naming the exact remedy (`ciu env generate`) instead of printing nothing or a traceback. `eval \"$(ciu env print)\"` in a real shell (or the test's subprocess-based equivalent) results in every REQUIRED_KEYS_CORE key being present and correctly valued in that shell's environment afterward."
    negative: "naming this verb (or documenting it) as something that 'applies' or 'sources' the environment INTO the calling shell directly -- a subprocess structurally cannot do that; the docs/help text and CHANGES.md entry must describe it as printing lines for eval, not as an in-place apply"
    gate: "tester-unified"
  - id: O6-clean-vanilla-flag
    observable: "New flag `--vanilla` on `ciu clean` (wired through `cli.py`'s clean dispatch and `deploy.py`'s `--clean` argparse + `action_clean`). Plain `ciu clean` (no `--vanilla`) continues to leave `ciu.global.toml` (rendered), `ciu.env`, and `ciu.global.worktree.toml.j2` completely untouched -- exactly today's behavior, a regression guard proving this package does not change the default. `ciu clean --vanilla` additionally removes all three of those files (after doing everything plain clean already does), if present -- a full reset to freshly-cloned-repo state. Missing files are a silent no-op for that specific removal (not an error) -- `--vanilla` on an already-clean workspace succeeds."
    negative: "plain `ciu clean` (no flag) starting to remove any of the three files -- that is the exact default-behavior regression this package must not introduce; `--vanilla` erroring out when one of the three files happens to already be absent"
    gate: "tester-unified"
  - id: O7-docs-corrected
    observable: "docs/SPEC.md S3.1b gains the `ciu.instance.generated` table alongside its existing mention of `ciu.instance.service_profiles`/`ciu.instance.shared_infra`, explicitly stating (mirroring CIU-52's own 'do not hand-edit' comment precedent in `_worktree_overlay_text`) that this table is CIU-owned and refreshed on every `env generate` -- an operator hand-edit to keys INSIDE `[ciu.instance.generated]` specifically will be silently overwritten on the next generate, unlike the rest of the file. docs/CONFIG.md's file-role table and worked example gain the same table. docs/DESIGN-GUIDE.md gains a short section (near the existing ambient-REPO_ROOT hazard sections this session already added for CIU-41/47/53) explaining WHY these facts moved out of ambient env into a real, inspectable, gitignored file instead of a bespoke Jinja global -- this is the direct answer to the operator's own architecture question, record it as such. `ciu --help`/`ciu env --help`/`ciu clean --help` name the two new capabilities (`env print`, `clean --vanilla`) so an operator discovers them without having to read this handoff or ask again."
    negative: "documenting env print/clean --vanilla only in CHANGES.md with no discoverable --help text"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the identity tuple's six keys diverge in meaning/availability between what ciu.env already computes and what needs to reach `[ciu.instance.generated]` (e.g. a value only knowable after ciu.env is fully written, not during its own computation) -- BLOCKED naming the exact value and where its computation actually happens, do not invent a second derivation path"
  - "the text-surgery block-replace (O2) cannot be implemented safely for a file containing a construct this codebase's TOML writer/parser genuinely cannot round-trip through text scanning alone (e.g. a `[ciu.instance.generated]`-prefixed key appearing inside a multi-line string value elsewhere in the file) -- BLOCKED naming the exact construct, do not ship a block-replace that can misfire on it silently"
mutexes: [merge-lane]
review_focus:
  - "prove O2 with an actual comment + actual unrelated table BOTH before and after the generated block in the same test fixture file, and assert the exact surrounding text survives verbatim -- this is the oracle most likely to be under-tested by a rushed 'parse and re-dump' shortcut"
  - "confirm O4 by testing against the PRIMARY checkout specifically (no S16 instance record), not just a worktree fixture -- the whole point of this package is closing the gap for the workspace the operator was ACTUALLY standing in when they hit the original bug"
  - "confirm O3 without adding any new Jinja context-building code -- if the implementer added a new context field instead of relying on the existing worktree-overlay merge, that is a design deviation from what this handoff mandates and should be flagged even if it 'works'"
  - "confirm O6's default-unchanged half as carefully as its new-flag half -- a regression here would silently start deleting an operator's ciu.env/rendered config on every ordinary clean, which is exactly the kind of destructive-default hazard this whole session has been about"
---

# ciu-P33 — generated identity facts into the worktree overlay, `ciu env print`, `ciu clean --vanilla`

## Context to read first

1. `src/ciu/workspace_env.py:40-62` — `GENERATED_IDENTITY_KEYS` (REPO_NAME,
   INSTANCE_ID, DOCKER_NETWORK_INTERNAL, PUBLIC_FQDN) and `REQUIRED_KEYS_CORE`
   (REPO_ROOT, PHYSICAL_REPO_ROOT, DOCKER_NETWORK_INTERNAL, CONTAINER_UID,
   DOCKER_GID) — the existing derived-identity machinery this package extends,
   not replaces. `generate_ciu_env` (around line 892) is where all of these
   values are ALREADY computed in memory before being written to `ciu.env`;
   this package's new write happens in the SAME function, from the SAME
   already-computed values — do not re-derive anything.
2. `src/ciu/worktree.py:518-594` (`_worktree_overlay_text` +
   `_write_worktree_overlay`) — CIU-52's precedent for writing INTO
   `ciu.global.worktree.toml.j2`. Read this closely, then note the ONE
   important way this package's write must differ: `_write_worktree_overlay`
   REFUSES if the file already exists (correct for its own call site — worktree
   creation, day zero, nothing to preserve yet). This package's write runs on
   EVERY `env generate`, potentially the tenth time on a workspace that already
   has operator-authored content in this file — it must UPSERT (replace just
   its own `[ciu.instance.generated]` block; preserve every other byte) rather
   than refuse-if-exists or append-blindly. Write a NEW function for this; do
   not relax `_write_worktree_overlay`'s existing refusal (other callers still
   need it).
3. `src/ciu/config_model.py:492-501` (`render_global_chain`) — confirms this
   file is read UNCONDITIONALLY by exact path, no S16-instance-record gating,
   for every `repo_root`. This is why O4 (main checkout coverage) is
   achievable with zero changes to the read side — only the write side
   (`env generate`) needs to stop gating on an instance record, if it does
   today (verify; the reading suggests it currently doesn't gate on one at
   all for the write, since `env generate` predates worktree instances).
4. `docs/SPEC.md` — search `S3.1b` — the normative home for
   `ciu.global.worktree.toml.j2`'s contents; extend it, do not contradict it.
   Also search `S16.1a` (CIU-52's own SPEC section) for the exact prose/table
   style to mirror for the new `[ciu.instance.generated]` table's own
   documentation.
5. `docs/CONFIG.md` around line 70-77 (the file-role table) and around line
   728-740 (the `[ciu.instance]`/`[ciu.instance.shared_infra]` worked
   example) — extend both with `[ciu.instance.generated]`.
6. `src/ciu/deploy.py` — find `action_clean` (search the function) and its
   argparse wiring for `--clean` (search where `--clean` is registered as a
   CLI flag, likely near other deploy-level action flags). `--vanilla` is a
   new flag on the SAME parser, forwarded to `action_clean` as a new keyword
   parameter defaulting to `False`.
7. `src/ciu/cli.py:1631-1633` — `clean`'s dispatch just forwards `rest` to
   `deploy.main(["--clean"] + rest)`; `--vanilla` needs no NEW cli.py
   dispatch logic, only argparse registration in `deploy.py` itself (verify
   this by checking how `--clean`'s sibling flags, e.g. any existing
   `--force`/`--yes`-style modifier if one exists on the same parser, are
   wired, and match that pattern exactly).
8. `KNOWN_ISSUES_TODO_BACKLOG.md` — search `CIU-53`/`CIU-54` (the entries this
   session's ciu-P32 filed) for this backlog's current numbering; this
   package's own filing (if the operator's proposal is filed as a formal
   backlog entry at all — controller's call: file it as CIU-60 (or whatever the next free number actually is at implementation time -- re-verify) alongside the
   fix, following this backlog's own established style, so a future reader
   finds the "why" without re-deriving this handoff's reasoning) should slot
   in immediately after CIU-54.

## Design mandate (not open to re-litigation — the controller already made
these calls; implement them, do not re-derive from scratch)

- **Table name and location**: `[ciu.instance.generated]` inside
  `ciu.global.worktree.toml.j2` — a sibling of the existing
  `[ciu.instance.shared_infra]`, under the SAME `[ciu.instance]` parent that
  already carries per-workspace/per-instance facts. NOT a new top-level
  `[ciu.generated]` table, NOT the rendered `ciu.global.toml` (that file has
  no state-preservation — `render_global_chain` fully regenerates it from
  source layers on nearly every verb invocation, so anything written directly
  there that isn't re-derived identically every time would be silently lost —
  confirmed by grep: only `render_stack`'s per-STACK `ciu.toml` has `[state]`
  preservation, S3.4; the global rendered file has no equivalent).
- **Six keys, snake_case, matching this file's existing key-naming
  convention** (`ref_path`, `network`, `ref_projects` — not SCREAMING_CASE
  like the shell-env names): `repo_name`, `instance_id`, `network`,
  `physical_repo_root`, `repo_root`, `public_fqdn`.
- **Write mechanism: text-level surgical block replace, not a
  parse-whole-file-then-dump-whole-file TOML round-trip.** Algorithm: read
  the file's raw text if it exists (else start from `""`); find a line
  matching exactly `[ciu.instance.generated]`; if found, delete from that
  line up to (not including) the next line that starts with `[` at column 0,
  or EOF if none; insert the freshly rendered block (same header line plus
  `key = value` lines, `json.dumps`-formatted values exactly like
  `_worktree_overlay_text` already does for its own tables) at that same
  position; if not found, append the block at EOF (with the file's existing
  header comment untouched, or `_worktree_overlay_text`'s own header-comment
  style if creating the file fresh). Write via the SAME atomic
  temp-file-then-`os.replace` pattern `_write_worktree_overlay` already uses
  (S16 durability). This bounds every write to ONLY the bytes CIU owns, so an
  operator's hand-authored comments/tables anywhere else in the file survive
  untouched forever — the whole reason this design beats a `tomllib`+`tomli_w`
  full-file round-trip (which would work correctly for VALUES but silently
  destroy every comment and reformat every table, an unacceptable regression
  against this file's documented "operator-editable" status, S3.1b).
- **`ciu env print`, not `apply`/`source`.** A subprocess cannot mutate its
  parent shell's environment — this is an OS-level fact, not a CIU
  limitation. Naming the verb `apply` or `source` would document a
  capability the implementation structurally cannot provide. `print` is
  read-only, honest, and composes with `eval "$(ciu env print)"` for anyone
  wanting the old `source ciu.env` ergonomics without hand-writing the
  `source` call themselves.
- **`ciu clean --vanilla` changes nothing about plain `ciu clean`'s
  default.** The three newly-removable files (`ciu.global.toml`, `ciu.env`,
  `ciu.global.worktree.toml.j2`) are removed ONLY when `--vanilla` is passed.
  This is additive, not a behavior change to the existing verb.

## Why this package exists (for the LOG, and for docs/DESIGN-GUIDE.md's new section)

Hooks already read `ctx.instance_id`/`ctx.network` from this workspace's own
`ciu.env` by exact path (S9.3) — never ambient `os.environ`. Jinja TEMPLATE
rendering's `env` context variable, by contrast, is still raw `os.environ`
(S3.2) — the exact ambient-trust gap `ciu-P32` closed one level up for repo
ROOT DISCOVERY (`dev.resolve_repo_root`, CIU-53). This package closes it for
FACTS ABOUT the already-discovered workspace. The operator explicitly
rejected an earlier controller proposal to fix this via a bespoke
fresh-every-render Jinja context injection (`ciu.physical_repo_root`
appearing from nowhere, backed by no file) as reintroducing exactly the
"magically available var" hazard this whole session has been fighting —
correctly. The design in this handoff instead reuses the ALREADY-SHIPPED,
ALREADY-gitignored, ALREADY-clean-surviving `ciu.global.worktree.toml.j2`
merge mechanism CIU-52 just proved out for a different field
(`ref_services`) — an operator can `cat` this file and see exactly what CIU
derived, with a real file backing every value.

## Definition of done

- All 7 oracles pass under `tester-unified` (100% line+branch coverage,
  R0+R1; R2 deferred per this wave's established protocol).
- `docs/SPEC.md`, `docs/CONFIG.md`, `docs/CIU.md`, `docs/DESIGN-GUIDE.md`,
  `docs/CONSUMERS.md` (if it documents `ciu env`/`ciu clean` usage patterns —
  check) all reflect the new contract; `--help` text names both new
  capabilities.
- `CHANGES.md` gets a real entry (not a stub) describing all three pieces
  (generated-facts overlay, `env print`, `clean --vanilla`); mark the
  generated-facts piece as additive/non-breaking (nothing existing changes
  behavior) and the other two as new, additive capabilities — no `!` marker
  needed unless the implementer finds a genuine breaking edge case, in which
  case escalate rather than silently deciding either way.
- `KNOWN_ISSUES_TODO_BACKLOG.md` gets a new entry (controller suggests
  CIU-60 at time of writing, but this wave hit a real ID collision during the P26/P27 merge -- ALWAYS re-verify against KNOWN_ISSUES_TODO_BACKLOG.md live rather than trusting this number) recording the operator's
  original architecture question and this package's resolution, following
  this file's own established style (see CIU-52/CIU-53's entries for the
  shape).
