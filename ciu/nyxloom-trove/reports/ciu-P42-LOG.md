# ciu-P42 — LOG (CIU-75, the v8 F2 identity cutover)

Worktree: `/workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2`
Branch: `fix/ciu-P42-cutover-identity-f2` — originally cut from `main` @
`332af5a1`, **rebased onto `main` @ `815c50d6`** (the ciu-P43 merge:
CIU-77/79/80/81) on 2026-08-31. The rebase is Entry 4, and it rewrote every
hash below; the pre-rebase hash is named in each entry so an earlier reference
to it still resolves.

One entry per commit, each naming the commit hash it describes.

---

## Entry 1 — `ebcc7ad7` (pre-rebase `c1985542`) — `feat(ciu)!: CIU-75 -- the overlay becomes the sole instance-fact source (BREAKING)`

**What it did.** The whole code + test cutover.

*Site derivation (not trusted from the entry).* The backlog's line numbers had
moved, as it warned. Re-derived with
`grep -rn '"ciu\.env"\|WORKSPACE_ENV' src/ciu/`, which found exactly twelve
exact-path constructions in the three named modules — `worktree.py` 1137,
1232, 2652, 2901, 3267, 3876, 4293; `engine.py` 971, 1221, 1538; `deploy.py`
2220, 3285 — matching the entry's count with shifted numbers. Four further
`ciu.env` uses live in `cli.py` (`ciu env` / `ciu env print`), and those are
deliberately NOT internal identity reads: they are the legacy export's own
shell-facing surface, which contract item 2 keeps.

*Design decision, recorded because a reviewer will ask.* The reader is a
text-level scan of the CIU-owned block, mirroring `upsert_generated_facts`'s
own documented ownership boundary — NOT a `render_global_chain` call. Three
reasons, each of which the chain render fails: (a) the overlay is a Jinja
template whose render needs the merged config it is a layer of, and CIU-74
made an undefined name a hard error, while the CIU-owned block is plain TOML
by construction; (b) six of the twelve sites read a checkout that is NOT this
process's repo root (a shared-infra reference, a budget candidate, a reap
group), whose committed chain may legitimately be absent or broken — the
`ciu.env` read they replace had no such dependency and neither may the
replacement; (c) the block is merged LAST, so its own bytes ARE the merged
value; a chain render could only agree with it.

*Two behaviour deltas, both deliberate and both documented in code, CHANGES
and SPEC S3.1c.* (1) The three child/render-environment sites (`_clean_in`,
`_sanitized_target_env`, `_resolve_budget_candidates`) now build
ambient-minus-identity + the target's own facts. The overlay carries identity
facts only; the alternative — passing just six variables where the whole
`ciu.env` used to go — would have broken any candidate template referencing a
machine fact, which under CIU-74's StrictUndefined is now a hard error.
(2) A DIRECTORY where the record belongs is now indeterminate rather than
silently absent: `ciu.env`'s `is_file()` guard folded it into "never
provisioned", the estate's absence-for-emptiness anti-pattern.

*The one existence-check.* `worktree._reap_uses_clean` was the only pure
`.is_file()` readiness signal, and it did NOT stay as-is. Post-cutover
`ciu clean` derives both its identity network and its identity compose project
from the overlay table, so a checkout carrying only a legacy `ciu.env` can no
longer clean itself; keeping the old check would have made it certify a
readiness that no longer holds. It now asks for the generated table's
PRESENCE and deliberately not its readability — a corrupt table answers "yes"
so `_clean_in` refuses loudly instead of the predicate demoting indeterminacy
into a bare `docker rm`.

*Test work.* 32 existing test files were migrated off `ciu.env` fixtures;
`write_instance_facts` was added to `tests/conftest.py` so a fixture goes
through the SHIPPED writer rather than hand-rolled TOML. Three existing tests
had to change their claims rather than their fixtures, and each is called out
in its own docstring:

- `test_ciu_worktree_budget.py::test_candidate_env_isolation_ambient_leak_does_not_apply`
  → `…_ambient_identity_never_leaks`. The old test proved NO ambient variable
  reached a candidate render; that is no longer true and cannot be, given the
  delta above. It now proves the property that actually matters: an ambient
  `INSTANCE_ID` set to a third value never reaches either candidate.
- `test_ciu_worktree_shared_infra.py::test_reference_with_no_global_config_at_all_refuses`
  → split into `…_no_deploy_identity_refuses` and
  `…_whose_own_chain_cannot_render_refuses`. A reference with no committed
  config now RENDERS (its gitignored overlay alone is a legal chain layer —
  true since CIU-60, and the old fixture was simply unrealistic in not writing
  one), so the refusal arrives one step later, from `container_name`. The
  render-failure arm is still covered, by a genuinely unparseable template.
- `test_ciu_render_selection_context.py`'s two S3.12 degradation arcs now
  monkeypatch the READER rather than corrupting the file: the overlay is also
  a config-chain layer, so corrupting the file fails the render long before
  step 12 — a strictly louder outcome, and not the arc under test. The three
  file-level corruption forms are proven against the reader itself in the new
  cutover suite.

*Acceptance oracle.* `tests/tests/test_ciu_identity_cutover_ciu75.py` — a real
`git init` + real `git worktree add` + the real `generate_ciu_env` (only its
host detectors pinned), then every fact-reading site exercised TWICE: once
with `ciu.env` unlinked, once with it replaced by undecodable bytes. Plus the
converse (`…_leaves_the_overlay_as_the_only_load_bearing_record`), without
which "still works with `ciu.env` deleted" would also be satisfied by a site
that reads neither record.

**Gate at this commit:** not run (docs still owed). Full suite green,
`3393 passed`, coverage `100%` line+branch via `run-ciu-tests.py`. (Both
numbers are as-written; Entry 4's rebase merged ciu-P43's tests in and the
post-rebase count is `3399`.)

---

## Entry 2 — `f8f29778` (pre-rebase `788908e2`) — `docs(ciu): CIU-75 -- SPEC S3.1c, CONSUMERS migration, BREAKING changelog; CIU-75 FIXED, CIU-82 filed`

**What it did.** The docs half, which AGENTS.md makes part of the change, not
a follow-up.

- `docs/SPEC.md` **S3.1c — Identity-source precedence** (new; the entry
  correctly noted no existing section owned it). Seven normative clauses: the
  sole source, the write-only export, the one-release WARN, the reader's three
  outcomes (absent / table-absent / indeterminate — never two), the scoped
  read and why a chain render must not be required, readiness meaning the
  table rather than the file, and the child/candidate environment rule.
- `docs/CONSUMERS.md` **§11b — "Migrating off `ciu.env` as an identity
  source"**: what changed, what does NOT break (with the working commands), a
  five-row pattern→replacement table, a paste-able `tomllib` one-liner, the
  four dstdns shapes found by the real grep, the sweep commands another
  consumer can run, and a post-migration sanity check that deletes `ciu.env`.
- `CHANGES.md` `## [7.7.0] - UNRELEASED` with the deliberate-minor override
  stated up front, **Adoption / Migration Notes** (action-needed vs
  safe-to-ignore), and `feat(ciu)!:`-marked BREAKING entries. Not folded into
  an ordinary bullet.
- `README.md`: the file-inventory line now says `ciu.env` is a legacy
  write-only export and adds a short "Where instance identity lives" block
  linking CONSUMERS §11b and SPEC S3.1c; the quick-start gains
  `eval "$(ciu env print)"`; the comparison-table row is renamed off `ciu.env`.
- `KNOWN_ISSUES_TODO_BACKLOG.md`: CIU-75 marked **FIXED (ciu-P42)** with all
  four acceptance boxes checked and each annotated with what was actually
  done; the header's "Last updated" line rewritten. **CIU-82 filed** for the
  dstdns notification (numbered 82 because CIU-81 was already taken by
  ciu-P38's scaffold filing — an ID collision caught before it landed, the
  same hazard this backlog has hit before).

**Consumer grep, as required.** `/workspaces/dstdns` WAS reachable (HEAD
`a1098ad8`). The entry's "plausible but unconfirmed" risk is **confirmed**,
not ruled out — four shapes, listed in CONSUMERS §11b and in the REPORT. The
load-bearing one is `dstdns/scripts/ciu/workspace_env.py`, a vendored stub
that PARSES `ciu.env` for the test-runner container. dstdns was NOT edited:
that is outside this worktree's scope, and estate convention files a
consumer-side finding in the consumer's backlog. CIU-82 exists so the
notification is not lost between repos.

**Gate at this commit:** `./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2` → `ciu: PASS (exit 0)`,
`run-gate: lane 'ciu' exit 0`, `GATE_EXIT=0`, with
`.assay/verdict-ciu.json`'s `"commit"` equal to `788908e2c94956abd37dd0ed8b6ef4b89cc77e62`
== HEAD *at that run* (the pre-rebase hash — Entry 4 rewrote it and re-ran the
gate against the rebased tip). No baseline/comparison gate was run at any point
in this package, so nothing could have overwritten that artifact. Verbatim
verdict and full artifact in `ciu-P42-REPORT.md` §6.

---

## Entry 3 — `67a588f8` (pre-rebase, same content) — `report(ciu): ciu-P42 -- CIU-75 REPORT + LOG gate/hash record`

**What it did.** Added `ciu-P42-REPORT.md` (per-site classification table for
all 12 sites, the two behaviour deltas, the delete-and-corrupt evidence, the
verbatim gate verdict plus the full verdict artifact, the dstdns sweep with
`file:line`, and the four acceptance criteria answered one by one) and filled
Entry 2's own hash and gate result into this LOG.

Report-only: no source, test or user-facing doc changed.

**Gate at this commit:** re-run after the rebase (Entry 4) — see there.

---

## Entry 4 — `ebcc7ad7`/`f8f29778`/`67a588f8` — the rebase onto ciu-P43 (`815c50d6`)

**Why.** ciu-P43 (CIU-77/79/80/81) merged to `main` as `815c50d6` while this
package was in flight, and it touches the SAME identity code: CIU-80 added
`HookContext.identity_unreadable`, set at BOTH S3.12 readers. This package
moves where those readers read FROM. Neither change is correct without the
other, so the reconciliation is the work, not the merge mechanics.

**Conflicts, and how each was resolved.**

| file | conflict | resolution |
|---|---|---|
| `src/ciu/deploy.py` | P43 made `_workspace_identity` return `(facts, unreadable)`; CIU-75 changed what it reads | BOTH: `-> tuple[dict, bool]`, reading `read_generated_facts(repo_root)`, `({}, False)` for absent, `({}, True)` for present-but-unreadable |
| `src/ciu/engine.py` | same shape at the STEP-12 inline reader | BOTH, and P43's comment about the now-deleted `if _env_path.is_file()` guard was corrected rather than carried forward stale |
| `src/ciu/hooks_runner.py` | **none — auto-merged** | the field's docstring was re-pointed off `ciu.env` onto the overlay table by hand; see the hazard note below |
| `tests/.../test_ciu_render_selection_context.py` | P43's pairing test corrupts the identity record | re-pointed at the overlay with a **non-string fact** (`instance_id = 7`) — valid TOML the config chain renders, which the identity reader refuses, so the oracle stays end-to-end instead of failing earlier in the render |
| `tests/.../test_ciu_deploy_actions.py` | P43's directory-case early return | dropped: post-CIU-75 a DIRECTORY is present-but-unreadable, so all three parametrized cases now warn and set the flag |
| `tests/.../test_ciu_identity_cutover_ciu75.py` | mine, against P43's new tuple | destructured, and each site's assertion tightened to `(… , False)` |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | two "Last updated" headers | both kept, chained "Previously, …"; my stale "CIU-81 FILED" paragraph dropped because P43 FIXED CIU-81 |
| `CHANGES.md`, `docs/SPEC.md`, `docs/CONSUMERS.md`, `README.md` | header/section adjacency | both sets of entries kept; P43's S9.3 and CONSUMERS §10 `identity_unreadable` text re-pointed at the overlay, and a CIU-75 × CIU-80 bullet added to `[7.7.0]` since both ship in the same release |

**The hazard the reviewer named, checked explicitly.** `hooks_runner.py`
auto-merges cleanly, so a conflict resolution that dropped the
`identity_unreadable=` argument at the two `HookContext(...)` constructions
would leave the field permanently `False` with nothing failing. Both sites
carry it, both carry CIU-75's renamed snake_case keys, and both now have a
comment naming the two ways the pair can silently rot:

```
deploy.py:2554  instance_id=identity.get("instance_id"),
                network=identity.get("network"),
                identity_unreadable=identity_unreadable,
engine.py:1566  instance_id=_hook_identity.get("instance_id"),
                network=_hook_identity.get("network"),
                identity_unreadable=_hook_identity_unreadable,
```

`grep -n "HookContext(" src/ciu/*.py` returns exactly those two.
`test_identity_unreadable_agrees_between_check_preflight_and_real_run` (P43's,
now over the overlay) is the test that fails if either side is dropped.

**The re-derived contract.** CIU-75 does not just survive CIU-80 — it makes
CIU-80's field honest. P43 drew absent-vs-unreadable with `ciu.env.is_file()`,
which answers "absent" for a DIRECTORY named `ciu.env`; the overlay reader
answers `WorkspaceEnvError` for anything PRESENT it cannot read, directory
included. The estate's absence-for-emptiness anti-pattern was still live in
the field designed to defeat it.

**Also in this rebase (test hygiene the cutover itself made stale):** an
`import os` left unused when a chmod-based fixture became a reader injection;
the comment still describing that chmod; and
`test_engine_identity_read_survives_an_unparseable_ciu_env` →
`…_an_unparseable_identity_record`, since it no longer touches `ciu.env`.

**`main` moved again during this work** (`7c47a707`, run-gate-P03/RG-27). Not
rebased onto: `comm -12` over `git diff --name-only` from `815c50d6` shows
**zero** overlap with this branch's 39 files — it is entirely
`run-gate-project/` — so there is nothing to reconcile and a merge is trivial.

**Gate after the rebase:** `ciu: PASS (exit 0)`, `run-gate: lane 'ciu' exit 0`,
`GATE_EXIT=0`, verdict artifact `"commit": "67a588f8…"` == HEAD at that run,
now under assay **3.2.0** (P43 bumped the judge from 2.3.0) and verdict schema
8. Further runs followed each later commit so that the LAST gate invocation of
the package is the one against the branch tip; §6 of the REPORT quotes the
latest.

---

## Entry 5 — `8cc79745` — `docs+test(ciu): ciu-P42 -- record the ciu-P43 rebase, the merged identity contract, and the post-rebase gate`

**What it did.** Entries 3 and 4 above, REPORT §11, the re-quoted post-rebase
gate verdict in REPORT §6 — and three pieces of test hygiene that this
cutover's own earlier commits left stale in
`test_ciu_render_selection_context.py`: an `import os` orphaned when a
chmod-based fixture became a reader injection, the comment still describing
that chmod, and
`test_engine_identity_read_survives_an_unparseable_ciu_env` →
`…_an_unparseable_identity_record` (the test no longer touches `ciu.env`; a
name that says otherwise is the same defect class as a wrong error message).

Suite after the rename: `3399 passed`, coverage `100%` line+branch, exit 0.

**Gate at this commit:** `ciu: PASS (exit 0)`, `run-gate: lane 'ciu' exit 0`,
`GATE_EXIT=0`, artifact `"commit": "8cc79745…"` == HEAD at that run. Quoted
verbatim in REPORT §6, together with the two output changes it surfaced (the
gate is now `rev 30`, and RG-27's new lane-history book-keeping warns that
`.run-gate/` is not git-ignored on a branch based on `815c50d6`, which
predates main's `.gitignore` line for it — it wrote nothing, the worktree is
clean, the lane exits 0).

---

## Entry 6 — this commit — `backlog+docs(ciu): CIU-83 filed; REPORT/LOG carry the final gate verdict`

**What it did.** Filed **CIU-83** and recorded the final verdict.

CIU-83 is a finding the rebase surfaced and this package deliberately did NOT
fix: `git show 815c50d6 --stat` does not list `ciu/CHANGES.md`, so ciu-P43's
CIU-77/79/80/81 landed with SPEC/CONSUMERS/CONFIG updates but no changelog
entry — and `[7.7.0]`, the section all five of this checkpoint's items ship
in, therefore announces CIU-75's breaking change while **CIU-79 is breaking
too** by ciu-P43's own merge message. Writing another package's changelog from
its merge message is how release notes become fiction, so the entry names what
is owed and who owes it instead. The backlog header and REPORT §11 both carry
it.

This entry cannot name its own hash (no file can). `git log -1` names it, and
the gate artifact on disk names it too: the LAST gate run of this package is
the one against this commit, so `.assay/verdict-ciu.json`'s `"commit"` equals
`git rev-parse HEAD` — one command for a reviewer to check, no quoted hash to
trust.

---

## Entry 7 — `c979de02` — `fix(ciu)!: CIU-75 review round 1 -- complete the cutover at STEP 1, and stop the notice breaking ciu check --json`

**The review verdict was REJECT, on five blockers.** Blocker 3 (CIU-83) was
already filed and the controller took it. This entry covers the other four,
and the first of them is the one that matters.

**Blocker 1 — the cutover was incomplete, and I completed it rather than
narrowing the claim.** The twelve migrated call sites were real, but they were
never the whole surface: `bootstrap_workspace_env` — STEP 1 of `ciu up` /
`check` / `render` / `graph` — still seeded `os.environ` from `ciu.env`, and
seeded it **skip-if-present**, so an inherited value was never displaced. Some
26 internal sites read `REPO_ROOT` / `PHYSICAL_REPO_ROOT` /
`DOCKER_NETWORK_INTERNAL` / `PUBLIC_FQDN` straight from ambient, and so does
every `$VAR` in a rendered config — the shipped demo's
`network_name = "$DOCKER_NETWORK_INTERNAL"` included. A shell that had sourced
a SIBLING checkout's `ciu.env` therefore still won, with nothing corrupted and
nothing hand-edited: `deploy.network_name` rendered as the sibling's network,
and containers would have joined it. **S3.1c clause 1 was true of twelve
functions and false of the product**, and my own REPORT line "never read back
by any CIU internal" was false as shipped.

What landed (SPEC S3.1c clause 2a, new):

- `seed_identity_env` writes the six facts from the table into `os.environ`
  **unconditionally**. Override, never skip-if-present — the latter helps only
  in the case that does not bite. `adopt_file_identity` is deleted: it applied
  the CIU-41 rule only after a generate in the same run, i.e. never on the
  common path, which is precisely why the hazard survived.
- `_load_legacy_machine_env` reads `ciu.env` for MACHINE facts only, **by
  exact path** (`load_workspace_env` walks via `find_workspace_env`, which
  honors an ambient `REPO_ROOT` and can therefore hand back another checkout's
  file entirely — the same leak, one level down), and it can no longer abort a
  verb: the three CIU-62 exception types become a WARN naming
  `ciu env generate`. Those four bootstrap reads had never been given that
  treatment, so a corrupt export crashed `ciu up` with a raw
  `UnicodeDecodeError` at the first statement of the run.
- A checkout whose overlay carries no generated table is **repaired**, not
  refused. The overlay is gitignored, so "no table" is an ordinary state (a
  fresh clone, CI, a pre-CIU-60 record), and the record CIU reads is the
  record CIU regenerates — the same self-healing `ciu.env` has always had when
  absent. A PRESENT but unreadable table is not repaired; clause 4 wants that
  loud.

**Blocker 2 — the migration advice broke `ciu check --json`.** The deprecation
notice went to stdout, and `deploy._run` (that verb's own entry point) calls
the bootstrap as its FIRST statement, regenerating `ciu.env` when absent. A
consumer who followed this release's own advice got a `[WARN]` line ahead of
the JSON document. `_log_warn` gained a stream and `generate_ciu_env` a
`notice_stream=`; bootstrap-triggered regeneration announces on stderr, the
typed `ciu env generate` still on stdout. S3.1c clause 3 now states which and
why, rather than leaving it to whoever reads the code.

**Blocker 4's behaviour half — I had described my own change wrongly.**
`_reap_uses_clean` answering False is **not** a refusal: the caller falls
through to bare `docker rm` + volume/network removal, which leaves every
`vol-*` hostdir on disk. The docstring said the opposite. Corrected — and the
silence fixed: when the checkout still EXISTS, `_reap_one_group` now notes
that it took the blunt path and names the `ciu env generate` + `ciu clean`
repair. A teardown that quietly leaves data behind is worse than one that
refuses.

**The test gap, and why more of the same would not have closed it.** O3 drives
the twelve helpers directly — which is exactly why it could not see a seeding
hole — and `tests/conftest.py`'s autouse ambient-identity scrubber meant no
other test could observe an ambient value surviving either. The new **O4**
section therefore changes KIND: it runs a real user-facing verb
(`ciu secrets list` — full STEP 1 plus `render_global_chain`, no daemon)
against a really-generated workspace carrying the SHIPPED `test-repo` global
config, and spies on the render rather than replacing it. Seven tests: the
hostile-ambient loss (the reviewer's Repro A), machine facts staying
ambient-first (the boundary, or the fix is a hammer), the corrupt export not
crashing STEP 1 (Repro B), the regenerated export being unable to move
identity (Repro C, answered rather than defended), stdout purity for `--json`,
the no-table repair, and the reap note firing only when a checkout survives.

**Ten existing tests migrated, three claims made STRONGER.** Every migration
was a pre-cutover FIXTURE (writing only `ciu.env`), not a weakened claim.
`deeper3`'s CIU-41 test became a three-way oracle — ambient, legacy file and
overlay all disagree and only the overlay may win — and `deeper9`'s
`--define-root` test likewise. `test_spec_contracts.build_repo` now pins
`_physical_root_from_mountinfo`, making its documented
"REPO_ROOT == PHYSICAL_REPO_ROOT == repo_root" true for the first time: inside
a devcontainer mountinfo overrode the pre-set value, and those three tests
passed only because the ambient leak this commit closes kept the stale value
in `os.environ`. That is the clearest evidence I have that the leak was real.

**Blocker 5 — the published migration snippet was broken.** CONSUMERS §11b's
`tomllib.loads` one-liner parses the WHOLE overlay, but §11a explicitly
sanctions operator content anywhere else in that Jinja template, so any real
overlay raises `TOMLDecodeError` on it — while CIU's own reader slices the
block first. Replaced with a `read_ciu_identity` helper that mirrors the
shipped reader and distinguishes absent from indeterminate, plus the six-fact
shell-name↔snake_case mapping table (three facts were missing from §11b
entirely) and the ambient-override consequence.

**Blocker 4's doc half.** SPEC S16.10 step 1 directly contradicted S3.1c in
the same document; S8.7, S6.4a, S2.1, the S16 authority table, the shared-infra
add/join, the budget survey, the `worktree up`/`exec` child environment, S16.9's
labels and the identity-completeness interlock were all still describing
`ciu.env` as a read source. Swept, each with the "until CIU-75" marker so the
history stays legible. CONSUMERS' five stale paragraphs likewise, including
`:487` which told a consumer `ciu.env` "must exist" when the table is what must.

**dstdns re-audited exhaustively** (`dstdns@96fcf762`), because CIU-82 tells
that repo to migrate on the strength of my inventory. Three importers of the
vendored stub, not one — `config_helper.py:30`, `url_builder.py:18` (missed
first time), and a `tests/smoke` probe whose `sys.path` hack has never worked —
plus the sibling `config_constants.py` stub, six whole-file `source` sites, and
**no key-extraction shell site at all**: the one grep recipe lives in a handoff
doc and greps `CIU_INSTANCE_ID`, a key that has never existed. CIU-82 and §11b
both corrected. Also recorded: dstdns's `$VAR` templates are *more* correct
after this change, not less, so nobody migrates them needlessly.

Suite: `3405 passed`, coverage `100%` line+branch, exit 0.

**Gate at this commit:** run against `c979de02`; verdict, artifact and hash
match in `ciu-P42-REPORT.md` §6.

---

## Entry 8 — round 2 — `docs(ciu): CIU-75 review round 2 -- finish the documentation sweep`

**Verdict was ACCEPT-conditional: the code stands, the doc sweep did not.**
Round 1 corrected the SPEC and CONSUMERS passages I happened to look at.
`git diff --name-only 815c50d6 HEAD -- ciu/docs/` proved the point — `CONFIG.md`
and `FEATURES.md` had never been opened at all. Sampling a sweep is not a
sweep, so this round re-grepped **every** `.md` under `docs/` plus the root
`README.md` and judged each hit individually.

**Corrected, each verified at source first:**

| where | was | now |
|---|---|---|
| SPEC S16 `worktree rm` | runs clean "under that worktree's own `ciu.env`" | its own generated facts; and it REFUSES a checkout whose table carries no identity, which the old sentence did not mention at all |
| SPEC S16.9 lease `holder` | "the `INSTANCE_ID` from the workspace's own `ciu.env`" | `instance_id` from the table; the `DEVCONTAINER_NAME` half is a MACHINE fact and correctly stays on `ciu.env` |
| SPEC S11 catalog | "S8.7 compose-naming refusals (missing/key-less `ciu.env`…)" | the table — it contradicted my own rewritten S8.7 in the same document |
| SPEC S6.4b `--vanilla` | "`ciu.env` is the workspace identity a retry … resolve from" | the table (CONSUMERS' twin sentence was fixed in round 1; this one was missed) |
| SPEC S8.2, S16 create/adopt | compose env "includes the sourced `ciu.env`"; "generates identity-only `ciu.env`" | both records named, with the seed |
| CONFIG.md hook context | `identity_unreadable` "`True` only when `ciu.env` is present but unreadable" | the overlay table, with the four unreadable forms enumerated and the "a hook needs no change" line |
| CONFIG.md ×4 more | layer model, file table, reference render, `worktree up`/`exec` | corrected |
| FEATURES.md, ARCHITECTURE.md, CIU.md, CIU-DEPLOY.md, DESIGN-GUIDE.md ×3 | "under its own `ciu.env`" and friends | corrected |

CONFIG.md's was the one worth care: it is what hook authors actually read, and
SPEC S9.3 and CONSUMERS §10 had both been fixed while it was left saying the
flag is about a file it is no longer about.

**Not just corrections — the missing WHY.** `DESIGN-GUIDE.md` had no section
for this decision, though AGENTS.md makes that document the home of the
reasoning (README=WHAT, DESIGN-GUIDE=WHY, CONSUMERS=HOW). Added as the sequel
to CIU-60's own section: why the reader scans CIU's block instead of rendering
the chain, why the seed had to override unconditionally ("the record is
authoritative *unless* your shell disagrees" is not an authority), and the
generalizable lesson — **a cutover is complete when the old source cannot
influence the answer, not when every direct read has been rewritten.**

**The published helper (blocker 5's fix) was itself incomplete and is now
executed, not eyeballed.** It returned the facts dict raw, missing the
`isinstance(value, str)` check the shipped reader has — so it implemented
three of S3.1c clause 4's four indeterminacy cases while its own docstring
claimed all four. Added. Then I extracted the snippet from the Markdown and
RAN it: absent → `{}`, operator content with no table → `{}`, a real record
written by the shipped writer → byte-equal to `read_generated_facts`, and all
four unreadable forms → `ValueError`. That run also corrected the framing:
whole-file `tomllib` fails on operator content only when that content is not
*itself* valid TOML (Jinja inside a quoted string parses fine; a `{% if %}`
line, an unquoted `{{ … }}` value or a bare `$VAR` do not). §11b now says that
precisely instead of overclaiming.

**CIU-82 corrected again.** `scripts/devcontainer-exec.sh:83-104`
(`get_network_name`, called at `:148`/`:183`) sources `ciu.env` specifically to
fetch `DOCKER_NETWORK_INTERNAL` and hard-fails when absent — live code
consuming one identity fact, the most migration-relevant shell site there is,
and missing from both earlier audits. It escaped because the source target is
the variable `"$env_file"`, not the literal — recorded in the entry as the
lesson for the next sweep. Count corrected from "six places" to **nine
`source` statements across eight files**, re-verified line by line at
`dstdns@e1712adc`, in the backlog and in CONSUMERS §11b.

**Two findings filed rather than fixed** (both verified at source; neither is
this package's to carry, and both would have meant a source change in a
docs-only round): **CIU-84** — `ciu check --json` still writes `[INFO]` to
stdout before the document, via `deploy.py:4322`'s unconditional
`info("Active service profile(s): …")`; pre-existing, the same hazard CIU-75
fixed one layer down. **CIU-85** — `_clean_in` skips the
`_CIU_IDENTITY_ENV_KEYS` strip its two siblings perform, so the caller's
`CIU_SERVICES_PROFILE` (in that tuple, but not an overlay fact and so not
covered by the identity overwrite) leaks into the child `ciu clean`; plus
`PUBLIC_FQDN`'s absence from that tuple. Both are neutralized in practice
today — incidentally, not by design, which is exactly why they are worth an
entry.

`CHANGES.md` records the DESIGN-GUIDE addition and the sweep. No `src/` file
was touched in this round; the suite is `3405 passed`, 100% line+branch.
