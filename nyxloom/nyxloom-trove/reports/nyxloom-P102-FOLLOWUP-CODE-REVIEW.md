# nyxloom-P102 follow-up — adversarial code review

**Commit under review:** `302d551c` — "docs(nyxloom): nyxloom-P102 follow-up -- fix retroactive review findings F1-F5, F7" (2026-09-08)
**Reviewer:** fresh adversarial session, no prior P102 or P102-follow-up context
**Review date:** 2026-09-08
**Worktree:** `/workspaces/vbpub/.worktrees/nyxloom-p102b-followup/nyxloom`
**Method:** blind pass first — `git show 302d551c` read and every claim in it
re-derived against live source *before* opening
`nyxloom-trove/reports/nyxloom-P102-CODE-REVIEW-RETROACTIVE.md`. The retroactive
review was read only afterwards, to check coverage of its F1–F7 against findings
already formed independently.

## Verdict

**ACCEPT-conditional.**

Every substantive fix in this commit verifies. All six F7 flag additions are
byte-exact against live `cli.py` argparse; F3's `--project` claim is exactly
right (seven subcommands, all seven wired); F4's four replacement archive paths
all exist on disk and are genuinely the records they are cited for; F5's code
spans are all closed on their opening line; F1's enumeration is **complete** —
my own independent grep found exactly the five sites the banner names and no
sixth; F2's softened text does not overclaim, and NL-14's body checks out
line-for-line against `config.py`, `reconcile.py`, the schema, and
`tests/legacy_planner.py`. `INDEX.md` is byte-identical to a fresh regeneration
(verified by actually running the generator, not by eye). Scope is exactly the
five expected files, all `.md`.

It is conditional on **FF-1** only: this commit — whose own stated purpose
includes fixing an unresolvable path reference (F4) — ships a *new* unresolvable
path reference in `NL-13`. It is a one-path-segment fix.

FF-2 and FF-3 are non-blocking observations; neither needs to land before merge.

---

## Findings

### FF-1 — MINOR (blocking, one-segment fix): `NL-13` cites a path that does not exist

`nyxloom-trove/backlog/NL-13-gate-scaffold-py-s-dockerfile-template-still-emits-the-retired.md:40`:

```
(`nyxloom-trove/archive/nyxloom-P102-CODE-REVIEW-RETROACTIVE.md`, F6),
```

That file does not exist. The retroactive review lives at
**`nyxloom-trove/reports/nyxloom-P102-CODE-REVIEW-RETROACTIVE.md`** — it was
committed there one commit earlier (`e2be652b`, `git log --name-only` confirms
the single path), and `ls nyxloom-trove/archive/ | grep -i P102` returns nothing:
no P102 file has been archived. The `archive/` directory holds the P98/P99/P100/
P101/P103 package sets, which is presumably where the wrong segment came from.

This is the same defect species as F4 (a checked-in doc pointing at something a
reader cannot open), reintroduced inside the commit that fixed F4 — and it
matters more than usual here, because NL-13 is a backlog entry whose whole value
to a future carver is the provenance trail back to the finding.

Note the commit *message* carries the same wrong path twice (header paragraph and
the F6 paragraph). Commit messages are immutable and not worth a rewrite; the
shipped file is what needs correcting.

**Prescription:** in `NL-13:40`, change `nyxloom-trove/archive/` →
`nyxloom-trove/reports/`. Do **not** pre-emptively "fix" it by writing the future
archive location — if the P102 package files are archived later, that move is the
step that updates inbound citations. Nothing else in either new backlog entry
cites a path (`NL-14` names only `src/` and `tests/` files, all verified present).

### FF-2 — MINOR (non-blocking, pre-existing): the banner scopes P98 to "the GA1/GA4 toolkit", but P98 also deleted the coverage/mutation/canary toolkit, and three sites below still cite it

`docs/plan-next-batches.md:5` — "which retired the GA1/GA4 toolkit" — and the
enumeration at :15 — "Every mention of `gate verify` / `cmd_gate_verify` / GA4
below" — both frame P98's retirement as the gate-verify feature only.

P98's own carve title is broader: *"Retire the **coverage/mutation/canary
toolkit** + GA1/GA4 gate-verify feature"*
(`nyxloom-trove/archive/nyxloom-P98-retire-toolkit-gate-verify.md:5`). Its
`scope.touch` deletes `src/nyxloom/coverage_gate.py`,
`src/nyxloom/mutation_gate.py` and `src/nyxloom/gate_canary.py` outright, and
drops `changed-line-coverage` from the scaffolded asserts as "no longer
measured". I confirmed all three modules are gone from `src/nyxloom/`.

Three sites below the banner reference that half and are **not** covered by the
banner's `gate verify`/`cmd_gate_verify`/GA4 enumeration, because they do not
contain those strings:

- `plan-next-batches.md:410` — the BATCH-D "Mutation fan-out" work item proposing
  to "parallelize `mutation_gate` per-mutant". The module no longer exists, so the
  work item is not merely historical, it is unbuildable as written.
- `plan-next-batches.md:370` and `:423` — `test_mutation_gate.py` flake
  watch-items. That test file was deleted by P98.
- `plan-next-batches.md:334` — "shallow = missing `changed-line-coverage`", a
  rigor claim about an assert P98 stopped measuring.

This is **not** a regression introduced by `302d551c`, and the banner's own hedge
— "Known sites, found by grep, not necessarily exhaustive" (:17) — is honest and
is exactly what stops this from being an F1 repeat. The banner is materially
better than what it replaced. But the surviving sentence "Everything else below
(BATCH A-E) is historical record of what shipped through 2026-07-26 and still
accurate for that window" (:24-25) is doing more work than it should: :410 is a
*forward* proposal, not a record of what shipped, so "accurate for that window"
does not save it.

**Prescription (defer to a later docs pass, or fold in if the FF-1 fix is being
made anyway):** widen the enumeration's opening from
"`gate verify` / `cmd_gate_verify` / GA4" to also name the deleted
`coverage_gate.py` / `mutation_gate.py` / `gate_canary.py` toolkit and the
`changed-line-coverage` assert, and add :334, :370, :410, :423 to the known-sites
list. Cheap, and it closes the last quadrant of P98's retirement.

### FF-3 — NIT (non-blocking): two inherited-but-unverifiable details in `NL-14`

Both are carried verbatim from the retroactive review's F2 text rather than
re-derived, and both are harmless, but a future carver will trip on the first:

1. `NL-14:31-33` — "only a packaging listing in an `egg-info/SOURCES.txt`
   artifact". There is no `.egg-info` anywhere in this worktree
   (`find . -maxdepth 3 -name "*.egg-info"` → empty;
   `grep -rn legacy_planner --include=SOURCES.txt .` → empty). The *substantive*
   claim is correct and I verified it independently: `grep -rn "legacy_planner"
   src/` returns nothing at all. The parenthetical describes a build artifact
   that may exist in some checkouts and not others — it should either be dropped
   or marked as environment-dependent.

2. `NL-14:47-52` (option 1, "retire the fields entirely") says to "update or
   retire whatever `tests/legacy_planner.py`'s frozen snapshot check was actually
   protecting". It does not mention that P98's carve lists
   `tests/legacy_planner.py` under `scope.forbid` as **ABSOLUTE**, with the
   reasoning that the file is a byte-identical copy of `reconcile.py` @
   `052857ae` and that
   `tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point`
   asserts that byte-identity — i.e. editing the file to accommodate a change *is*
   the defect that check exists to catch. NL-14's phrasing is defensible (it says
   update the *check*, not the file), but it is ambiguous enough that a carver
   could read it as licence to edit the frozen module. Worth one sentence.

   Relatedly, NL-14's option-1 oracle (`grep … src/nyxloom/` returns zero hits)
   is necessary but not sufficient: deleting the fields from `config.py` /
   `reconcile.py` makes `legacy_planner.py:2123-2128` raise `AttributeError` at
   runtime, since it reads them off the production dataclass instances. The entry
   does gesture at this in its second oracle; making the coupling explicit would
   save the carver a discovery round.

**Prescription:** append a `note` to NL-14 via `exec-nyxloom.py backlog note`
rather than editing the entry body, if it is touched at all. Non-blocking.

---

## Verification performed

Each of the ten attack items from the review brief, with evidence. All source
line numbers are against the live tree at `302d551c`.

### 1. F1 — is the banner's enumeration actually complete? — **PASS**

I ran my own sweep before reading the retroactive review's F1:

```
grep -n "gate verify\|cmd_gate_verify\|gate_verify\|GA4\|GA1\|gate-verify" docs/plan-next-batches.md
```

Hits below the banner (banner occupies :3-33): **:37, :39, :309, :311, :315-316,
:355** — and nothing else. Mapped against the banner's five named sites:

| Banner clause (`:17-24`) | Real site | Verified |
|---|---|---|
| "the 'State' line below" | :35-41; GA1 at :37 ("A/F/G/GA1/GA2/D-part-1 merged") and :39 ("verifiable (`nyxloom gate verify`, GA1)") | **exact** |
| "the BATCH B bullet citing `gate_canary.py` + `cmd_gate_verify` (marked `✅ DONE`)" | :309, inside the **GA2b** bullet under `## BATCH B` (heading at :304); text is "`gate_canary.py` + `cmd_gate_verify`. ✅ **DONE (merge `a8ac7b3b`, 2026-07-25):**" | **exact** — it is a BATCH B bullet, and the `✅ DONE` marker is really there |
| "the `cmd_gate_verify` `coverage-floor:` line a few lines after it" | :311 — "`cmd_gate_verify` `coverage-floor:` line gated on the assert being declared" | **exact**; :311 is two lines after :309, so "a few lines after it" holds |
| "the whole GA4 bullet describing the `gate_verify_interval_days` cadence + a reconcile item running `gate verify` per project" | :315-316 — "**GA4** — carver periodic gate re-verify: cadence knob (`gate_verify_interval_days`) + a reconcile item running `gate verify` per project" | **exact**, near-verbatim |
| "a later mention of 'the background-thread-plus-drain shape GA4's gate-verify cadence already proved.'" | :355-356 (BATCH D) — "Converted to the background-thread-plus-drain shape GA4's gate-verify cadence already proved:" | **exact** modulo the source ending in a colon where the banner's quotation closes with a period — a punctuation nit, not a misquote of substance |

The retroactive review's F1 named the same four post-State sites at pre-fix line
numbers :294, :296, :300-301, :340. The banner expansion added 15 lines
(+29/−14 on this file); 294+15=309, 296+15=311, 300+15=315, 340+15=355 — the
mapping is exact, confirming nothing was dropped or invented in translation.

**Independent conclusion: the enumeration is complete for the strings it scopes
itself to.** The residual gap is the *scoping* itself, not the enumeration —
see FF-2.

The `✅ DONE` trap F1 was written about is now defused twice over: the banner
explicitly says "do not trust an individual `✅ DONE` marker next to one" (:16-17)
*and* names the specific `✅ DONE` bullet.

### 2. F2 — is the softened banner text accurate, and does NL-14 hold up? — **PASS**

Banner now reads (`:9-13`): "The GA4 daemon verify-cadence no longer fires — its
`gate_verify_interval_days` knob (`config.py`) and `days_since_gate_verify` input
(`reconcile.py`) survive as dead, schema-validated configuration surface a
project can still set with no effect (worth its own cleanup backlog entry; not
done here)."

Every clause verified against live source:

- `src/nyxloom/config.py:169` — `gate_verify_interval_days: int = 0`, a live
  field on the policy dataclass. Confirmed by `sed -n '165,173p'`.
- `src/nyxloom/schemas/nyxloom-config.schema.json:253-256` —
  `"gate_verify_interval_days": { "type": "integer", "minimum": 0 }`. Confirmed;
  "schema-validated" and "a project can still set it with no effect" are both
  literally true.
- `src/nyxloom/reconcile.py:879` — `days_since_gate_verify: float | None = None`.
  Confirmed.
- "no longer fires": the only branching consumer is
  `tests/legacy_planner.py:2123-2133` (`gate_verify_interval = inp.cfg.policy.
  gate_verify_interval_days` → `if gate_verify_interval > 0:` →
  `VerifyGate(...)`). Confirmed test-only; `grep -rn "legacy_planner" src/`
  returns nothing.
- Repo-wide sweep for both symbols returns exactly: `reconcile.py:877,879`,
  `nyxloom-config.schema.json:253`, `config.py:169`,
  `tests/legacy_planner.py:323,325,364,872,874,2123,2128,2149`,
  `tests/test_gap_audit.py:82`. No production consumer. **No overclaim.**

The phrasing is well-calibrated: it does not say the fields are *used*, and it
does not say GA4 is intact. "worth its own cleanup backlog entry; not done here"
is honest about the docs/code scope split.

NL-14's body: every line citation above matches. Its `tests/legacy_planner.py:2123-2149`
span is right (the read at :2123, the branch at :2124-2131, the
`actions.extend(gate_verify_actions)` at :2151 with its GA4 comment at :2147-2149).
Its framing of the two options is appropriately open rather than prescriptive.
Two nits recorded as FF-3; neither is an exaggeration or a false claim.

### 3. F3 — `backlog [--project]` — **PASS**

`USAGE.md:190` now opens `backlog [--project] new <title> …` and closes with
"`--project` (default: discover from cwd) applies to every subcommand."

Against `src/nyxloom/cli.py`:

- `_add_project_arg(p)` defined at `:2053-2055`:
  `p.add_argument("--project", default=None, help="registered project id (default: discover from cwd)")`.
  The USAGE gloss "(default: discover from cwd)" is a **verbatim** quote of the
  live help string.
- Called on **seven** subparsers, one per backlog verb — `new` (:2058),
  `promote` (:2071), `note` (:2075), `set-status` (:2080), `list` (:2088),
  `show` (:2092), `index` (:2096).
- `grep -n "backlog_subs.add_parser"` returns exactly seven parsers: `new`
  (:2057), `promote` (:2070), `note` (:2074), `set-status` (:2079), `list`
  (:2087), `show` (:2091), `index` (:2095).

Seven subcommands, seven `_add_project_arg` calls, no eighth verb and no
un-wired verb. "applies to every subcommand" is **exactly** true — not
approximately. The prefix-once-rather-than-repeat-seven-times rendering is the
retroactive review's own preferred prescription and keeps the already-long cell
readable.

### 4. F4 — wiki-link removed, replacement paths real — **PASS**

`grep -n "\[\[" docs/plan-next-batches.md` → no hits. The
`[[nyxloom-p97-testing-code-removal-thread]]` wiki-link is gone.

All four replacement paths verified present on disk **and** on-topic:

| Cited at | Path | On disk | On-topic |
|---|---|---|---|
| :27 | `nyxloom-trove/archive/nyxloom-P98-retire-toolkit-gate-verify.md` | yes | yes — its own frontmatter title is "Retire the coverage/mutation/canary toolkit + GA1/GA4 gate-verify feature"; cited as "the full retirement record" ✓ |
| :28 | `nyxloom-trove/archive/nyxloom-P99-l10-per-project-thresholds.md` | yes | yes — L10 per-project thresholds, part of the P97 thread ✓ |
| :29 | `nyxloom-trove/archive/nyxloom-P100-tier-routes-toml-validation.md` | yes | yes — cited for "tier/routes.toml validation" ✓ |
| :30 | `nyxloom-trove/archive/nyxloom-P101-retire-tier-band.md` | yes | yes — cited for "`_TIER_BAND` retirement" ✓ |

The trailing gloss "(tier/routes.toml validation, `_TIER_BAND` retirement)" maps
to P100 and P101 respectively and is accurate for both. P99 is in the list
without a gloss, which is fine — the sentence covers "the rest of that thread".

Every path is repo-relative from `nyxloom/`, resolvable by any reader with a
checkout, with no dependence on an agent-private memory directory. The F4 defect
is fully closed.

### 5. F5 — no code span wraps across a line — **PASS**

Mechanical check over the whole banner (`:1-34`):

```
awk 'NR<=34{n=gsub(/`/,"`"); if(n%2!=0) print NR": ODD ("n")"}' docs/plan-next-batches.md
```

→ **no output**. Every line in the banner carries an even number of backticks,
so no span opens on one line and closes on another.

Read line-by-line as well, because even parity alone would not catch a span that
opens and closes across two lines each with its own odd count — it does not
occur. The specific F5 case is fixed at `:27`: the whole path
`` `nyxloom-trove/archive/nyxloom-P98-retire-toolkit-gate-verify.md` `` now sits
on one line. :28, :29, :30 do the same for the three new paths, each deliberately
overrunning the paragraph's wrap width rather than breaking inside backticks —
which is precisely the retroactive review's prescription.

One construct worth confirming rather than flagging: `:19-20` reads "the
`cmd_gate_verify`" / "`coverage-floor:` line". These are **two adjacent spans**,
each opened and closed on its own line, rendering as
`cmd_gate_verify` `coverage-floor:` with a separating space. That is correct and
matches the source text at :311, which itself has two adjacent code spans.

### 6. F7 — six flag additions vs. live argparse — **PASS (6/6 exact)**

| `USAGE.md` | Claim | Live `cli.py` | Result |
|---|---|---|---|
| :166 | `doctor … [--liveness]`, "CR-16 fast healthcheck path" | `:1835-1837` `--liveness`, `action="store_true"`; the CR-16 attribution and "fast path for a healthcheck" framing come from the in-code comment at `:1831-1834` and the help string | **exact**, incl. the CR-16 reference |
| :174 | `decide <project> <D-id> --choose [--note]` | `:1887` `decide_parser.add_argument("--note", ...)` — value-taking, optional; `--choose` at `:1886` is `required=True`, correctly rendered unbracketed | **exact** |
| :178 | `merge … [--commit] [--force]`, "operator override to record it even if the pre-merge gate fails" | `:1911-1912` `--force`, `store_true`, help "Record the merge even if the pre-merge gate fails (operator override; F)." | **exact**; gloss is a faithful paraphrase |
| :180 | `resume … [--force]`, "RP03 operator override to clear a project-level pause despite drift; no effect on task-level resume" | `:1930-1936` on `resume_parser` only; help "PACKAGE RP03: resume a project-level pause even when the pre-resume drift scan finds (or fails to complete) drift … no effect on task-level resume" | **exact**, incl. RP03 and the task-level caveat |
| :185 | `onboard … [--scan-path … --scaffold-gate]` + `--scaffold-gate` gloss | `:1977` `--scan-path` (`action="append"`, `dest="scan_paths"`, `metavar="PATH"`); `:1991-1997` `--scaffold-gate` (`store_true`), help "write a reviewable gate-runner Dockerfile and a `[gates.*]` skeleton … a review skeleton … not a guaranteed-working gate; adopt run-gate+assay after adjusting it" | **exact**; the gloss is near-verbatim from the help string, and flag order in the bracket group matches declaration order (`--scan-path` :1977 before `--scan` :1979) |
| :186 | `free-models list [--source] \| refresh [--source] [--dry-run]` | `:2004` `list --source`; `:2007` `refresh --source`; `:2008` `refresh --dry-run` (`store_true`, `dest="dry_run"`) | **exact** — correctly scopes `--dry-run` to `refresh` only, and `--source` to both |

No invented flag. No flag on the wrong subcommand. No value-taking flag rendered
as a switch or vice versa where the table's convention distinguishes them.

**On the brief's specific `pause`/`resume` challenge:** `--force` is on
`resume_parser` **only**. `pause_parser` (`:1922-1924`) declares exactly
`project` and an optional `task`, no flags. The row is
`` `pause` / `resume <project> [task] [--force]` ``. The shared `<project> [task]`
positionals *do* distribute across the slash, so a reader following that pattern
could plausibly distribute `[--force]` too — the syntax cell alone is ambiguous.
It is disambiguated by the description cell, which names the flag as
`` `resume --force` `` explicitly rather than as a bare `--force`. I judged this
**adequate, not a finding**: the row is a pre-existing combined row, the
prose is unambiguous, and splitting the row would be a larger change than the
ambiguity warrants. Recording the reasoning so the judgment is auditable rather
than silent.

### 7. NL-13 / NL-14 body accuracy — **PASS on substance, one path defect (FF-1)**

**NL-13** — every code citation verified:

- `gate_scaffold.py:88-90` — the quoted three-line block is exactly right:
  `:88` "# below before building or trusting it. Once adjusted, verify it actually",
  `:89` "# rejects broken code with `nyxloom gate verify <project>` (GA1) -- this",
  `:90` "# scaffold does not prove itself correct." It is inside the f-string
  returned by the Dockerfile-template function (the `return f'''\` is at `:85`),
  so "generated output handed to end users" is accurate, not rhetorical.
- `cli.py:1918-1919` — `gate_parser = subparsers.add_parser("gate")` +
  `gate_parser.add_subparsers(dest="gate_cmd")`, zero `add_parser` calls on it.
  Confirmed.
- `cli.py:2147-2149` — `elif args.cmd == "gate":` / `parser.print_help(sys.stderr)`
  / `return 2`. Confirmed; "returns 2" is exact.
- `gate_scaffold.py:19-21` — the self-contradiction claim holds: the docstring
  reads "nyxloom-P98 retired GA1's `nyxloom gate verify` cross-check -- Assay's
  own R2/R3 mechanisms supersede it", three lines of prose that the module's own
  emitted template contradicts. Confirmed verbatim.
- Its oracle is well-framed — "zero hits *inside any string literal emitted as
  generated output*, as opposed to zero hits overall, which would also delete
  legitimate historical/prose mentions like the module's own docstring" is a
  genuinely careful distinction and exactly the trap a naive `grep -rn "gate
  verify"` fix would fall into.
- No exaggeration found. Severity `medium` / type `bugfix` / component
  `onboarding` are all defensible.
- **Defect:** the provenance path at `:40` — see FF-1.

**NL-14** — see §2 above; all line citations verified. Two nits at FF-3. Severity
`low` is right (latent hygiene, no user-visible misbehaviour beyond a silent
no-op). Frontmatter on both entries is well-formed (`kind`, `schema_version`,
`id`, `title`, `status: open`, `type`, `severity`, `component`, `provenance`,
`filed_date`) and both are consistent with the sibling NL-10/NL-11/NL-12 entries.

Neither entry claims to fix anything, and neither overstates urgency — both
correctly identify themselves as pre-existing since P98 (2026-09-03), not
introduced by P102.

### 8. Scope + `INDEX.md` byte-equality — **PASS**

`git show --numstat --format="" 302d551c`:

```
7	7	nyxloom/docs/USAGE.md
29	14	nyxloom/docs/plan-next-batches.md
2	0	nyxloom/nyxloom-trove/backlog/INDEX.md
78	0	nyxloom/nyxloom-trove/backlog/NL-13-….md
83	0	nyxloom/nyxloom-trove/backlog/NL-14-….md
```

Exactly the five expected files, nothing else. All five are `.md`. No `.py`, no
`.json`, no schema, no `tests/`, no gate config, no `pyproject.toml`.

The USAGE.md hunk is 7/7 — seven rows modified in place, zero rows added or
removed, so the table's row count and structure are untouched (29 data rows
before and after, `:163-191`). The `plan-next-batches.md` hunk is +29/−14, all
inside the banner block; nothing below `:34` is touched, which is what makes the
enumeration's line-number mapping in §1 hold.

**`INDEX.md` regeneration — verified by execution, not inspection.** Recorded
`md5sum` (`e3d3e06bbdd3d132d9b23ec71033b46f`), ran
`python3 exec-nyxloom.py backlog index`, re-hashed
(`e3d3e06bbdd3d132d9b23ec71033b46f` — identical), then confirmed
`git diff -- nyxloom-trove/backlog/INDEX.md` is **empty**. The committed
`INDEX.md` is byte-identical to what the generator produces right now. The
"never hand-edited" rule the file's own USAGE row states was respected, and the
NL-13/NL-14 row placement (after NL-12, before the `fixed` NL-1 block) is the
generator's own ordering, not a manual insertion.

Working tree confirmed clean before and after — no unrelated dirty files, and
the regeneration left nothing behind.

### 9. New inaccuracies introduced? — **PASS with one residual (FF-2)**

Read the full 33-line banner and the full 29-row CLI table fresh, as new content.

Banner: no false claim found. The three claims most at risk of being wrong all
verify — "`gate` is now a reserved top-level verb with zero subcommands"
(`cli.py:1918-1919`, handler `:2147-2149`); "gate execution and trustworthiness
verification live entirely in each project's own `run-gate.py`/Assay lane"
(consistent with `gate_runner.py` being the only surviving execution path per
P98's `scope.forbid`); "Assay's R2/R3 supersede GA1's purpose" (matches
`gate_scaffold.py:19-21` and `cli.py:1915-1917`, two independent in-code
corroborations). The `nyxloom-P97 thread (2026-09-03: P98/P99/P100/P101)`
framing matches the four archive records that exist.

Table: re-derived every *unmodified* row's flags against `cli.py` as well, not
just the seven touched ones — no additional drift found. Rows `:172` (`auth`),
`:179` (`gate`), `:187` (`capability-map`), `:188` (`route doctor`), `:189`
(`finding`) all still match live argparse. `git log 302d551c..HEAD --
nyxloom/src/nyxloom/cli.py` is empty, so nothing has re-staled the table since.

The one residual is FF-2 — the deleted coverage/mutation/canary toolkit half of
P98, at `:334`, `:370`, `:410`, `:423`. Not introduced here, explicitly hedged by
the banner, and non-blocking.

Markdown structural check on the modified table rows: all seven modified rows
carry exactly 3 unescaped pipes (2 cells), matching the `|---|---|` header. Every
intra-cell alternation pipe in `:186` and `:190` is escaped `\|`. Backticks
balanced in all seven cells. No row corrupted.

### 10. Gate evidence — **docs-only, so PASS means "broke nothing", not "content correct"**

`.assay/verdict-tester-unified.json` is present (3188 bytes, Sep 8 07:14, four
minutes after the commit at 07:10), declaring the tester-unified lane
(`/opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom`),
`argv_modified: false`, assay 4.0.0.

Per §8 the commit touches five `.md` files and zero Python, so **no test in that
lane can observe this change at all**. The PASS is meaningful evidence of exactly
one thing — that nothing outside `docs/` and `nyxloom-trove/` was touched, and no
test that reads a trove file (e.g.
`tests/test_core_characterization.py`, which asserts a `nyxloom-trove/reports/`
inventory against the real tree) was broken by the new backlog files. It is **not**
evidence that any sentence in the banner, any table row, or either backlog entry
is factually true. That is established only by the manual re-derivation in §1-§9
above, which is why every claim in this review carries its own file:line.

I did not re-run the gate: it would consume a container slot for a docs-only diff
whose lane cannot observe the change, against the estate's one-gate-at-a-time
host-load rule.

---

## Summary

| Retroactive finding | Prescription followed? | Independently verified? |
|---|---|---|
| F1 (MAJOR, false scoping claim) | yes — enumerated, not blanket-scoped | **yes**, enumeration complete for its scope (§1) |
| F2 docs half (overstatement) | yes — softened, no overclaim | **yes** (§2) |
| F2 code half | filed as NL-14, not fixed — correct, it is a `src/` change | **yes**, body accurate (§2, §7) |
| F3 (`backlog --project`) | yes | **yes**, 7/7 subcommands (§3) |
| F4 (wiki-link) | yes — 4 real archive paths | **yes**, all exist and on-topic (§4) |
| F5 (wrapped code span) | yes | **yes**, all spans line-local (§5) |
| F6 | filed as NL-13, not fixed — correct, `src/` change | **yes**, body accurate but see FF-1 (§7) |
| F7 (6 stale rows) | yes | **yes**, 6/6 byte-exact (§6) |

Six of six fixes land correctly and no fix introduced a factual regression in the
documents themselves. The single blocking item is a wrong path segment in a new
backlog entry.

**ACCEPT-conditional on FF-1** — change `nyxloom-trove/archive/` to
`nyxloom-trove/reports/` at `NL-13:40`. With that one edit, this is a clean
ACCEPT and the commit is good to merge. FF-2 and FF-3 do not block and are better
handled by a later docs pass and a `backlog note` respectively.
