---
name: nyxloom-pack
description: Assemble a verbatim orientation pack for a nyxloom package (E-002/E-006 curation rules) — read-list derivation, stamp header, delegation prompt. Use after a carve is written, before dispatch, in any nyxloom-registered project.
---

> **Tool versions as of last verified update (2026-09-03):** a project-local
> script, not an external tool dependency (dstdns example: `nyxloom-trove/
> orientation/pack.py {build,delta,verify,score}`, `build --role` accepting
> `implementer|carver|reviewer`). Re-verify against the project's own
> `pack.py --help` if this drifts; it's a per-project script so it can change
> without an upstream release to track. If a project has no such script yet,
> writing one (mirroring dstdns's) is a reasonable first adoption step —
> file a vbpub backlog entry if the mechanics turn out to be generic enough
> to belong in nyxloom itself rather than reimplemented per project.

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
Assembly is a SCRIPT, not an agent (dstdns: `nyxloom-trove/orientation/pack.py`).
It derives the read-list from the handoff itself and concatenates verbatim
content — zero model tokens, nothing to hallucinate. Never delegate assembly to
an agent, and never retype file content.

```bash
# implementer / carver pack (FULL edit set + context slices + ledger D-sections
# + gate-adjacent blocks + forbid names)
python3 <trove>/orientation/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role implementer --dry-run   # inspect first
python3 <trove>/orientation/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role implementer             # writes <slug>/

# reviewer pack (E-006 variant: diff files FULL at the tip + per-file diffs +
# prior-round LOG/REPORT + standing set + PRE-TABULATED consumer sweep)
python3 <trove>/orientation/pack.py build \
  --handoff <trove>/handoffs/<pkg>.md --role reviewer --range main...<branch>

python3 <trove>/orientation/pack.py verify  <out-dir>                 # ALWAYS before committing
python3 <trove>/orientation/pack.py delta   --handoff <pkg>.md --since <rev>   # E-005
python3 <trove>/orientation/pack.py score   <out-dir> --transcript <jsonl>     # E-006 Task B
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
- Commit the pack; the implementer reconciles stamp→input_revision drift itself.
- Still hand-authored: `sweep-tables.md` when the carve rests on a controller-context
  sweep for a NON-reviewer role (the reviewer's sweep is generated).
