# nyxloom-P102 — retroactive adversarial code review

**Commit under review:** `a530c75c` — "docs(nyxloom): nyxloom-P102 -- sync USAGE.md's CLI table + flag stale roadmap claims" (2026-09-08)
**Reviewer:** fresh adversarial session, no prior P102 context
**Review date:** 2026-09-08
**Baseline for code checks:** `main` @ `2fc23421` (live tree, not the historical commit)
**Status of the change:** ALREADY MERGED to `main`. This review is the retroactive
substitute for the pipeline step that was skipped; any finding here needs a
follow-up correction package, not a revert.

## Verdict

**ACCEPT-conditional.**

The change is a genuine net improvement and its central factual claims verify:
all six new CLI table rows are accurate against live `cli.py`, `gate` really is
a zero-subcommand reserved verb, the CR-NN "nothing changed since 2026-08-04"
assertion holds, the commit is genuinely docs-only, and the markdown table is
structurally intact. Nothing here is a correctness regression and nothing
warrants reverting.

It is conditional on **F1**, which is a verifiably false factual assertion
introduced by this commit's own new content, in the very document it was written
to correct — the banner tells readers that exactly one line below it is stale
when at least four more sites carry the same retired references, one of them
marked "✅ **DONE**". A reader who trusts the banner is actively misled, which is
worse than the pre-P102 state where no banner invited that trust. F2 is a
secondary overstatement in the same banner. F3–F5 are minor defects in new
content. F6–F7 are pre-existing defects this review surfaced; they are not
P102's fault but belong in the same follow-up.

---

## Findings

### F1 — MAJOR: the stale-notice banner's "only the State line is wrong" is false

`nyxloom/docs/plan-next-batches.md:11-13` (added by this commit) asserts:

> Everything else below (BATCH A-E) is historical record of what shipped
> through 2026-07-26 and still accurate for that window; only the "State"
> line's GA1/gate-verify claim is now wrong.

That is wrong. The same retired `gate verify` / `cmd_gate_verify` / GA4
references appear at **four more sites below the banner**, none of them the
"State" line:

- `plan-next-batches.md:294` — "`gate_canary.py` + `cmd_gate_verify`. ✅ **DONE
  (merge `a8ac7b3b`, 2026-07-25)**" — reads as a currently-shipped fact.
- `plan-next-batches.md:296` — "`cmd_gate_verify` `coverage-floor:` line gated
  on the assert being declared".
- `plan-next-batches.md:300-301` — the whole **GA4** bullet: "cadence knob
  (`gate_verify_interval_days`) + a reconcile item running `gate verify` per
  project".
- `plan-next-batches.md:340` — "the background-thread-plus-drain shape GA4's
  gate-verify cadence already proved".

The `✅ DONE` markers at :294 are the sharp edge: a reader is told by the banner
that everything outside the State line is still accurate, then reads a
green-checked claim that `cmd_gate_verify` shipped and is live. It no longer
exists (see F-evidence under §Verification 2).

This is the exact defect species P102 was written to eliminate, reintroduced by
P102's own scoping sentence.

**Prescription:** replace the "only the 'State' line's GA1/gate-verify claim is
now wrong" clause with an enumerated list of the stale sites (State line, :294,
:296, :300-301, :340), or drop the absolute scoping claim entirely in favour of
"several claims below reference the retired GA1/GA4 toolkit — treat every
`gate verify` / `cmd_gate_verify` / GA4 mention below as historical." Preferred:
enumerate, since the doc is explicitly a historical record and per-site
annotation is cheap.

### F2 — MINOR-MAJOR: "The GA4 daemon verify-cadence is likewise gone" is overstated

`nyxloom/docs/plan-next-batches.md:10` states the GA4 cadence is "likewise gone".
The *behaviour* is gone; the *configuration surface* is not, and it is still
publicly settable and schema-validated:

- `src/nyxloom/config.py:169` — `gate_verify_interval_days: int = 0` is a live
  field on the policy config, with a 14-line comment block (`config.py:162-168`)
  still describing GA1's `nyxloom gate verify` probe as the thing it schedules.
- `src/nyxloom/schemas/nyxloom-config.schema.json:253-256` — `gate_verify_interval_days`
  is still a declared property (`"type": "integer", "minimum": 0`), so a project
  can set it in its `nyxloom.toml` today and it will validate cleanly while doing
  absolutely nothing.
- `src/nyxloom/reconcile.py:879` — `days_since_gate_verify: float | None = None`
  is still a live field on the reconcile input dataclass (`reconcile.py:870-879`).

What is genuinely gone is the planner rule that consumed them: the only code that
reads `policy.gate_verify_interval_days` and branches on
`inp.days_since_gate_verify` now lives in `tests/legacy_planner.py:2123-2149`,
which is test-only — `grep -rn "legacy_planner" src/` returns no import from the
package (only an `egg-info/SOURCES.txt` packaging listing).

So the banner is right that no cadence fires, but a reader acting on "likewise
gone" would not expect to find a live, settable, schema-blessed knob. This is
also a real (if latent) code-hygiene issue: a config field that silently no-ops.

**Prescription:** soften the banner to "the GA4 daemon verify-cadence no longer
fires — its `gate_verify_interval_days` knob and `days_since_gate_verify` input
survive as dead configuration surface (see F2 follow-up)". Separately, file a
backlog entry to either remove `gate_verify_interval_days` from `config.py` +
`nyxloom-config.schema.json` + `reconcile.py`, or add an explicit deprecation
warning when a project sets it non-zero.

### F3 — MINOR: the `backlog` table row omits `--project`, present on all seven subcommands

`nyxloom/docs/USAGE.md:190` enumerates eight flags for `backlog new` and the
per-subcommand flags for the other six verbs, but never mentions `--project`.

In `src/nyxloom/cli.py:2053-2096`, the helper `_add_project_arg(p)` (defined at
`cli.py:2053-2055`) adds `--project` (`default=None`, "registered project id
(default: discover from cwd)") to **every one** of the seven backlog
subcommands: `new` (:2058), `promote` (:2071), `note` (:2075), `set-status`
(:2080), `list` (:2088), `show` (:2092), `index` (:2096).

This is inconsistent with the table's own established convention — `tick
[--project]` (`USAGE.md:173`), `doctor [--project]` (:166), `status [--project]`
(:167) and the row's own sibling `finding ... list [--project]` (:189) all
surface it. `--project` is the flag that decides which project's backlog is
mutated; omitting it from a row documenting seven mutating verbs is the least
affordable omission in the row.

**Prescription:** add `[--project]` to the `backlog` row, e.g. prefix the verb
list with a note that `--project` (default: discover from cwd) applies to every
backlog subcommand, rather than repeating it seven times in an already-long cell.

*(Not a finding, recorded for completeness: the row also does not enumerate the
`choices=` sets for `--type` `{feature,bugfix}` (`cli.py:2060`), `--severity`
`{low,medium,high}` (:2061) and `set-status`'s `status` positional
`{open,carved,fixed,withdrawn,obsolete}` (:2082-2083). The table's compressed
one-line-per-verb style omits choice sets elsewhere too — cf. `onboard`'s row at
:185 vs. its three `choices=` args at `cli.py:1973-1979` — so this is
house-consistent, not a defect.)*

### F4 — MINOR: an agent-private memory filename leaked into a checked-in repo doc

`nyxloom/docs/plan-next-batches.md:15` contains:

> and [[nyxloom-p97-testing-code-removal-thread]] / the nyxloom-P100/P101
> archive entries for the rest of that thread

Two problems:

1. **The target is not in the repo.** `find /workspaces/vbpub -name "*p97*"`
   returns only unrelated `topos/` files (`topos-P97-coverage-quickwins.md` and
   its reports — a different project's P97). The actual referent is
   `/home/vscode/.claude/projects/-workspaces-vbpub/memory/nyxloom-p97-testing-code-removal-thread.md`,
   an agent-private memory file. No reader of this repository — human or future
   agent without that memory dir — can resolve it.
2. **`[[…]]` is not GFM.** Double-bracket wiki-link syntax has no meaning in
   GitHub-flavoured Markdown; it renders literally as `[[nyxloom-p97-testing-code-removal-thread]]`,
   so it is not even a broken link, just noise.

**Prescription:** replace with a real in-repo pointer. The thread's durable
records are all present under `nyxloom/nyxloom-trove/archive/` — e.g.
`nyxloom-P98-retire-toolkit-gate-verify.md`, `nyxloom-P99-l10-per-project-thresholds.md`,
`nyxloom-P100-tier-routes-toml-validation.md`, `nyxloom-P101-retire-tier-band.md`.
Cite those paths instead. Worth treating as a standing rule: checked-in docs must
never reference `~/.claude/.../memory/` entries.

### F5 — MINOR: a code span broken across lines renders the archive path with an embedded space

`nyxloom/docs/plan-next-batches.md:13-14`:

```
> line's GA1/gate-verify claim is now wrong. See `nyxloom-trove/archive/
> nyxloom-P98-retire-toolkit-gate-verify.md` for the full retirement record,
```

The backtick code span opens on :13 and closes on :14. Markdown collapses the
intervening newline (and blockquote `> ` prefix) to a single space, so this
renders as `nyxloom-trove/archive/ nyxloom-P98-retire-toolkit-gate-verify.md` —
a path with a space in it, not copy-pasteable and not matching anything on disk.

The path itself is correct: `nyxloom/nyxloom-trove/archive/nyxloom-P98-retire-toolkit-gate-verify.md`
exists (47923 bytes, dated Sep 3 03:06) and is genuinely the P98 retirement
record, so the *claim* is sound — only the rendering is broken.

**Prescription:** keep the code span on one line even if it overruns the
paragraph's wrap width; never wrap inside backticks.

### F6 — FOLLOW-UP (pre-existing, outside this diff): `gate_scaffold.py` still emits `nyxloom gate verify` into every scaffolded Dockerfile

Not introduced by P102, but directly in scope for the defect P102 was hunting,
and materially worse than either docs instance because it is *generated output
handed to users*:

`src/nyxloom/gate_scaffold.py:88-90`, inside the Dockerfile template returned to
`nyxloom onboard --scaffold-gate`:

```
# below before building or trusting it. Once adjusted, verify it actually
# rejects broken code with `nyxloom gate verify <project>` (GA1) -- this
# scaffold does not prove itself correct.
```

Every project scaffolded from this template gets a checked-in Dockerfile
instructing the operator to run a command that exits 2 with a help dump
(`cli.py:2147-2149`). The same module's own docstring at `gate_scaffold.py:19-21`
already knows better — "nyxloom-P98 retired GA1's `nyxloom gate verify`
cross-check -- Assay's own R2/R3 mechanisms supersede it" — so the file
contradicts itself: the prose was updated by P98, the emitted template was not.

**Prescription:** file a backlog entry against nyxloom. Fix `gate_scaffold.py:89`
to point at adopting `run-gate`/Assay (matching the module docstring at :19-21).
Worth a repo-wide `grep -rn "gate verify"` as part of that package to catch any
remaining emitters.

### F7 — FOLLOW-UP (pre-existing): the CLI-table "sync" was additive only; six pre-existing rows remain stale

P102 added six missing verb groups but did not re-verify the twenty-three rows
already present. Auditing the whole table against `cli.py` turns up six rows that
under-document existing flags:

| `USAGE.md` line | Row | Missing vs. `cli.py` |
|---|---|---|
| :166 | `doctor [--project] [--rebuild [--write]]` | `--liveness` (`cli.py:1835-1839`, CR-16 healthcheck fast path) |
| :174 | `decide <project> <D-id> --choose` | `--note` (`cli.py:1887`) |
| :178 | `merge <project> <task> [--commit]` | `--force` (`cli.py:1911-1912`, operator gate override) |
| :180 | `pause` / `resume <project> [task]` | `--force` on `resume` (`cli.py:1930-1936`, RP03 drift-scan override) |
| :185 | `onboard <project_folder> [--maturity --docs --mode --scan --questionnaire --check-gate]` | `--scan-path` (`cli.py:1977`), `--scaffold-gate` (`cli.py:1991`) |
| :186 | `free-models list \| refresh` | `--source` on both (`cli.py:2004`, :2007), `--dry-run` on `refresh` (`cli.py:2008`) |

`merge --force`, `resume --force` and `onboard --scaffold-gate` are the three
that matter most — all are behaviour-changing operator overrides that are
currently undiscoverable from the reference table. Note that `doctor --liveness`
is already documented in `cli.py`'s own handler docstring (`cli.py:408`), so
`USAGE.md` is the only place it is missing.

**Prescription:** fold into the same follow-up package as F1–F5; a complete
table-vs-`cli.py` sync is one pass, and doing it twice is the waste.

---

## Verification performed

Each of the seven attack items from the review brief, with evidence.

### 1. Byte-accuracy of the six new rows against live `cli.py` — PASS

Extracted every `add_parser`/`add_argument` for the six groups from
`src/nyxloom/cli.py` on `main` @ `2fc23421` and diffed against the table cells.

| Row (`USAGE.md`) | `cli.py` definition | Result |
|---|---|---|
| :172 `auth show \| bootstrap [--operator] \| rotate [--operator] [--force]` | :1867-1878 — `show` (no args), `bootstrap --operator`, `rotate --operator --force` (`store_true`) | **exact** — all three subcommands, all three flags, correct optionality |
| :179 `gate` — reserved, no subcommands | :1918-1919 `add_parser("gate")` + bare `add_subparsers(dest="gate_cmd")` with zero `add_parser` calls; handler :2147-2149 prints help, returns 2 | **exact** |
| :187 `capability-map refresh [--dry-run] [--emit-findings PROJECT]` | :2012-2021 — `refresh` only; `--dry-run` (`store_true`), `--emit-findings` (`metavar="PROJECT"`, `default=None`) | **exact**, incl. the `PROJECT` metavar |
| :188 `route doctor [--no-probe]` | :2023-2030 — `doctor` only; `--no-probe` (`store_true`), help text "schema validation only (offline-safe)" | **exact**, and the row's gloss matches the help string |
| :189 `finding record --project --kind --title [--body] [--field KEY=VALUE]... [--task-id] [--severity] \| list [--project] [--kind]` | :2032-2048 — `record` with `--project/--kind/--title` all `required=True`, `--body` (`default=""`), `--field` (`action="append"`, `metavar="KEY=VALUE"`), `--task-id`, `--severity` (`default="info"`); `list` with `--project`, `--kind` | **exact** — required vs. optional correctly rendered by bracket convention, repeatability correctly rendered by `...` |
| :190 `backlog new … \| promote \| note \| set-status \| list \| show \| index` | :2050-2096 | **incomplete — see F3** (`--project` on all seven) |

No flag named in any of the six rows is absent from the code; no wrong default;
no wrong required/optional status. Five of six rows are complete; the sixth
(`backlog`) is accurate in what it says and incomplete in what it omits.

### 2. `plan-next-batches.md` banner factual accuracy — PARTIAL PASS

Verified independently rather than trusting the banner:

- **"`nyxloom gate verify` (GA1) no longer exists" — TRUE.**
  `grep -rn "gate_verify\|cmd_gate_verify" src/nyxloom/` returns no command
  implementation: only the dead config field (`config.py:169`), the dead
  reconcile input (`reconcile.py:879`), the schema property
  (`nyxloom-config.schema.json:253`), two prose comments, and the stale scaffold
  template string (F6). There is no `cmd_gate_verify` function anywhere in `src/`.
- **"`gate` is now a reserved top-level verb with zero subcommands" — TRUE.**
  `cli.py:1918-1919` registers the parser and an empty subparser namespace;
  `cli.py:2147-2149` handles `args.cmd == "gate"` by printing help to stderr and
  returning 2. The in-code comment at `cli.py:1915-1917` independently
  corroborates the P98 attribution.
- **Archive path `nyxloom-trove/archive/nyxloom-P98-retire-toolkit-gate-verify.md`
  — EXISTS and is on-topic.** Present at 47923 bytes (Sep 3 03:06), alongside the
  full P98 package set (CARVE-REVIEW, CODE-REVIEW, four FIX-VERIFICATIONs, LOG,
  REPORT). The name and the accompanying package files confirm it is the GA1
  retirement record the banner claims. Rendering of the citation is broken (F5).
- **"The GA4 daemon verify-cadence is likewise gone" — OVERSTATED (F2).**
- **"only the 'State' line's GA1/gate-verify claim is now wrong" — FALSE (F1).**

### 3. The CORE-REDESIGN note's "no CR-NN item has changed since 2026-08-04" — PASS

`git log` on `nyxloom/nyxloom-trove/reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md`
shows the last content change before P102 was `55d0f017` (2026-08-04, "forcing-function
re-check (session handoff, no code)"); every other touch is also dated 2026-08-04.

`git log --since=2026-08-04 --all --grep="CR-[0-9]"` returns only two post-2026-08-04
hits, and neither is a CR-NN package: `52dad492` (2026-09-03) is P100 tagging an
L14 broad-except census classification, and `24084a5f` (2026-09-08) is a P103
carve repairing round-3 blockers — both matched the grep incidentally on
unrelated `CR-`-shaped text. All genuine CR-NN merges (CR-08 Slice 5 `2bfb9df6`,
CR-11b `c050d63f`, CR-14a `85552b89`, …) predate or fall on 2026-08-04.

The note's claim holds. Its characterisation of the P97-P101 thread as "touching
different files outside this program's charter" is also correct.

### 4. Scope creep / unintended changes — PASS

`git show --stat a530c75c` → exactly three files, `35 insertions(+), 1 deletion(-)`:

- `nyxloom/docs/USAGE.md` | 6 +
- `nyxloom/docs/plan-next-batches.md` | 20 +-
- `nyxloom/nyxloom-trove/reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md` | 10 +

Nothing else touched. Read the full diff including context: the single deletion is
the original `**State:** vbpub/nyxloom \`main\` @ \`8af765b7\`, tree clean, daemon
\`nyxloom-prod-nyxloomd\`` line, replaced by a two-line reflow that preserves every
token and adds only the `(as of 2026-07-25, see stale-notice above)` qualifier.
No content lost. No whitespace or formatting damage to any surrounding line in any
of the three files; all other hunks are pure insertions with unmodified context.

### 5. Markdown / structural integrity — PASS

Counted unescaped pipes per row across the whole table (`USAGE.md:163-191`) after
stripping `\|`: **every one of the 29 rows has exactly 3 unescaped pipes**, i.e.
exactly two cells, matching the `|---|---|` header. No row is corrupted.

The six new rows correctly escape every intra-cell alternation pipe as `\|`
(:172, :189, :190), which is required inside table cells under GFM even within
code spans — and is consistent with the pre-existing `free-models list \| refresh`
row at :186. Backticks are balanced in all new cells. Em-dashes and forward
slashes in the new `gate` cell (:179) are inert.

The one markdown defect found is in the other file, not the table: F5.

### 6. Does the commit's own "Docs-only; no code, schema, or test changes" hold? — PASS

Confirmed. All three touched paths are `.md` under `nyxloom/docs/` and
`nyxloom/nyxloom-trove/reports/`. No `.py`, no `.json`/schema, no `tests/`, no
`pyproject.toml`, no gate config. The claim is exactly true.

### 7. Has anything since 2026-09-08 already re-staled this content? — PASS

`git log a530c75c..HEAD -- nyxloom/src/nyxloom/cli.py` → **empty**. No CLI change
has landed since the merge, so `USAGE.md`'s table remains current as of `main`
@ `2fc23421` (2026-09-08 06:34), modulo the omissions in F3 and F7 that were
already true at merge time.

The 18 nyxloom commits since `a530c75c` are the P103 package (carve rounds,
`[governance]` declaration on the ciu roots, retiring the fictional
`nyxloom.slice`, LOG/REPORT/review, archival) plus the NL-12 backlog filing.
P103 touches ciu governance config and trove docs — it adds no CLI subcommand and
no argparse argument, so it does not invalidate the table.

---

## Recommended follow-up

One small docs package, `nyxloom-P104`-shaped, carrying F1–F5 (all in the two
`docs/` files P102 touched) and F7 (the rest of the table sync). F6 and the F2
dead-config-surface cleanup are code changes against `src/nyxloom/` and should be
filed as separate backlog entries rather than folded into a docs package:

- **F6** — `gate_scaffold.py:89` emits a retired command into scaffolded Dockerfiles.
- **F2 (code half)** — `gate_verify_interval_days` / `days_since_gate_verify` are
  live, settable, schema-validated, and inert; remove or deprecate.

Neither blocks anything currently in flight.
