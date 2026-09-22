---
name: nyxloom-pack
description: Assemble a verbatim orientation pack for a nyxloom package (E-002/E-006 curation rules) — read-list derivation, stamp header, delegation prompt. Use after a carve is written, before dispatch, in any nyxloom-registered project.
---

> **Tool versions as of last verified update (2026-09-22):** relocated here
> from dstdns (`nyxloom-trove/orientation/pack.py`) — it was always a generic
> nyxloom mechanism, just originally written and left inside the one project
> that used it first. Lives at `tools/pack.py` (+ `tools/test_pack.py`),
> `{build,delta,verify,score}` with `build --role` accepting
> `implementer|carver|reviewer`. **Not yet wired into the `nyxloom` CLI
> itself** — that integration (a `nyxloom pack` verb vs. staying a standalone
> script every project invokes by absolute cross-repo path, matching how
> `cgprofile`/`pwmcp-fetch` are consumed today) is an open decision, tracked
> in the nyxloom backlog (filed 2026-09-22 alongside this move — check
> `nyxloom-trove/backlog/` for the current entry before assuming either
> direction). Two known genericization gaps discovered during the move, both
> named in that entry: `ABS_REPO_PREFIX` hardcodes `/workspaces/dstdns/`, and
> the `score` subcommand hard-requires a co-located `jsonl-metrics.py` (which
> stays in dstdns per a separate, still-standing cross-repo placement
> decision — `score` is `xfail`-marked here for exactly that reason, not
> broken by the move itself). Re-verify against `tools/pack.py --help` if
> this drifts.

> **Canonical, repo-agnostic skill.** The pipeline SHAPE is universal to any
> nyxloom-registered project; substitute the target repo's own trove paths.
> Examples below use dstdns's conventions — read them as illustrations, never
> run a dstdns gate line against another repo.

# Orientation pack assembly

Target: `<trove>/orientation/<slug>/{pack.md,read-list.txt}` (+
`sweep-tables.md` when the carve rests on a measured sweep — persist those tables
verbatim from controller context, they exist nowhere else).

## Curation rules (measured, E-002/E-006/E-007)
- **FULL files for the edit set**; generous slices for read-only context. State in
  the header: slices of edit targets are comprehension-only (Edit needs byte-exact
  current content — "content right, bytes wrong" is a measured failure class).
- **Gate-adjacent artifacts must be in the pack**: assay lanes, completeness-oracle
  machinery, coverage-gate lists, conftest fixtures the oracles couple to.
- **Standing docs reviewers always fetch**: the project's GUIDE §2–3-equivalent,
  the relevant decisions-ledger entries, prior-round LOG/REPORT (for reviewers),
  the doc being rewritten.
- **Reviewer-tailored variant** (E-006: 75% of an implementer pack is dead weight
  for a code reviewer): diff-changed files + prior LOG/REPORT + standing
  cross-reference set; drop comprehension slices; pre-tabulate the consumer sweep.

## Mechanics
Assembly is a SCRIPT, not an agent: `vbpub/nyxloom/tools/pack.py`, invoked by
its cross-repo path from any consuming project (same convention as
`cgprofile`/`pwmcp-fetch`) until the CLI-integration backlog item is decided.
It derives the read-list from the handoff itself and concatenates verbatim
content — zero model tokens, nothing to hallucinate. Never delegate assembly to
an agent, and never retype file content.

```bash
# implementer / carver pack (FULL edit set + context slices + ledger D-sections
# + gate-adjacent blocks + forbid names)
python3 /workspaces/vbpub/nyxloom/tools/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role implementer --dry-run   # inspect first
python3 /workspaces/vbpub/nyxloom/tools/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role implementer             # writes <slug>/

# reviewer pack (E-006 variant: diff files FULL at the tip + per-file diffs +
# prior-round LOG/REPORT + standing set + PRE-TABULATED consumer sweep)
python3 /workspaces/vbpub/nyxloom/tools/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role reviewer --range main...<branch>

python3 /workspaces/vbpub/nyxloom/tools/pack.py verify  <out-dir>                 # ALWAYS before committing
python3 /workspaces/vbpub/nyxloom/tools/pack.py delta   --handoff <pkg>.md --since <rev>   # E-005
python3 /workspaces/vbpub/nyxloom/tools/pack.py score   <out-dir> --transcript <jsonl>     # E-006 Task B
```

- `--extra <path[:a-b]>` adds anything the derivation cannot see: the builder scrapes
  only frontmatter `scope` and the **"Context to read first"** section, so a slice
  named solely in a contract item or an oracle must be added by hand. Slice line
  numbers are expanded to enclosing def/class / TOML-table / markdown-heading
  boundaries automatically.
- `--dry-run` prints the file list + token estimate; run it first and read the
  **SIZE WARNING** (large packs list the largest sections). Trim with the sizing
  rule: narrow for one long implementer, broad only across many short forks.
- `verify` re-derives nothing — it byte-diffs every FULL section against the blob at
  the stamp, checks section count vs read-list, and reports stamp-vs-HEAD drift.
  A non-zero exit means the pack lies; fix it before committing.
- Writing refuses to clobber an existing `pack.md` without `--force` (hand-built
  packs are evidence).
- `score` measures a finished run: used / unused / missing against the agent's real
  read-set — feed the "missing" column back into the derivation rules.
- **Do NOT commit `pack.md`/`read-list.txt`/`sweep-tables.md`.** They are
  fully regenerable build output (byte-identical from the same handoff at the
  same commit), not evidence — a project's `.gitignore` should exclude
  `<trove>/orientation/*/{pack.md,read-list.txt,sweep-tables.md,pack-delta.md}`.
  The implementer reconciles stamp→input_revision drift from the live handoff,
  not from a committed pack; if a pack genuinely needs to be preserved for
  debugging a bad outcome, attach it to the package's own REPORT instead of
  committing it as a tracked file.
- Still hand-authored: `sweep-tables.md` when the carve rests on a controller-context
  sweep for a NON-reviewer role (the reviewer's sweep is generated).
