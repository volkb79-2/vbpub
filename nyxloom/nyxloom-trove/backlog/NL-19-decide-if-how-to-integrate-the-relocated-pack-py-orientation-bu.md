---
kind: backlog-entry
schema_version: 1
id: NL-19
title: "Decide if/how to integrate the relocated pack.py orientation-builder into the nyxloom CLI"
status: open
type: "feature"
severity: "medium"
provenance: "dstdns controller session, 2026-09-22, pack.py relocation (vbpub@7e2aedff)"
filed_date: "2026-09-22"
---

## Observed mechanism

`tools/pack.py` (role-aware orientation-pack builder: `build|delta|verify|score`,
`build --role implementer|carver|reviewer`) was just relocated here (commit
`7e2aedff`) from `dstdns/nyxloom-trove/orientation/pack.py`, where it had been
written and left despite implementing a fully generic nyxloom mechanism
(E-002/E-006 curation rules: derive a role-tailored, verbatim read-list from a
handoff's frontmatter + "Context to read first" section, concatenate FULL file
content with zero model tokens and zero hallucination risk). Every
nyxloom-registered project doing carve→dispatch could use this today; only
dstdns actually does.

It is currently invoked by an absolute cross-repo path
(`python3 /workspaces/vbpub/nyxloom/tools/pack.py ...`), the same convention
`cgprofile`/`pwmcp-fetch` already use — functional, but not a first-class
`nyxloom` verb, and not discoverable via `nyxloom --help`.

## Why nyxloom should own this decision

This is the same class of finding the estate's own cross-repo convention names
explicitly: "a script written locally in [a consumer project] that duplicates
or approximates something a vbpub CLI tool could do generically is itself a
backlog candidate on that tool." The tool now physically lives in nyxloom;
whether it becomes a real `nyxloom pack build/verify/delta/score` verb (with
config-driven trove-path resolution instead of `<trove>` CLI args) or stays a
standalone script every project points at by path is a design call for
nyxloom's own maintainers, not something a consuming project's session should
decide unilaterally.

## Two concrete genericization gaps found during the move (not fixed, named here)

1. **`ABS_REPO_PREFIX = "/workspaces/dstdns/"`** (pack.py, module-level
   constant) — hardcoded absolute path used to strip a repo prefix off
   absolute file paths seen in a transcript (`score`'s read-set matching) and
   elsewhere. Breaks for any project not checked out at exactly that path.
   Needs deriving from the actual repo root (e.g. `git rev-parse
   --show-toplevel` of the target project) instead of a literal.

2. **`score` subcommand hard-requires a co-located `jsonl-metrics.py`**
   (`_load_metrics()`/`transcript_readset()`, both do
   `Path(__file__).resolve().parent / "jsonl-metrics.py"`, no override).
   `jsonl-metrics.py` deliberately stays in dstdns
   (`nyxloom-trove/orientation/jsonl-metrics.py`) per a separate, still-
   standing cross-repo placement decision (documented in the user's own
   cross-repo `CLAUDE.md` layer) — not moved here in the same pass, to avoid
   unilaterally reopening that decision. Consequence: `score`'s two tests are
   `xfail`-marked in `tools/test_pack.py` here (reason cites this entry), and
   `score` will fail with a clear `PackError` for any project (including
   dstdns, if invoked from nyxloom's copy) that doesn't have `jsonl-metrics.py`
   sitting next to `pack.py`.

3. **`PACK_OUTPUT_RE`** (`^nyxloom-trove/orientation/[^/]+/(pack\.md|read-
   list\.txt)$`) hardcodes the `nyxloom-trove/orientation` trove-path literal
   for excluding a package's own generated pack files from its "consumer
   sweep" — same shape as #1, needs a project-supplied trove-root instead.

## Proposed contract / options (not decided — the actual ask)

- **(a) Keep standalone, fix the three literals to accept a `--repo-root`/
  `--trove-root` (or derive via `git rev-parse --show-toplevel` +
  `nyxloom.toml`'s own trove-path config).** Smallest change; every project
  keeps invoking it by cross-repo path.
- **(b) Wire as a real `nyxloom pack` verb.** Bigger: needs `nyxloom.toml`
  read for trove-root resolution, a decision on where `jsonl-metrics.py`
  moves (with it, to make `nyxloom pack score` universally work, vs. leaving
  `score` dstdns-only), and whether `pack.py`'s CLI surface (`build/delta/
  verify/score` + all its flags) maps cleanly onto nyxloom's existing verb
  conventions.
- **(c) Leave as-is.** Zero-risk, but the two genericization gaps stay latent
  defects for the next project that tries to reuse it.

## Behavioral oracles this would need (once a direction is picked)

- A controlled wrong implementation: hardcode a SECOND absolute path (e.g. a
  vbpub-repo-relative one) instead of genuinely deriving the root — the
  existing `test_pack.py` suite (32 tests, now living alongside it) already
  exercises path-normalization behavior (`normalize_read_path`,
  `test_score_folds_worktree_path_duplicate_into_canonical_read`) and would
  need extending to run against a repo root OTHER than `/workspaces/dstdns/`
  to prove the fix is real, not just a renamed constant.
- If wired as a CLI verb: a project with no `nyxloom.toml` trove-path
  configured must fail loudly (§4.2a-equivalent: no silent default), not
  silently assume `<repo>/nyxloom-trove/orientation`.

## Spec section that owns this behavior

None yet — no nyxloom SPEC.md/ARCHITECTURE.md section currently describes
orientation-pack assembly as a first-class nyxloom capability (it lives only
in the `nyxloom-pack` skill's own prose). Whichever option is picked should
add one.

## Updates

**2026-09-22** — jsonl-metrics.py moved alongside pack.py (2026-09-22), same reasoning: a generic Claude Code transcript-metrics tool with one dstdns-hardcoded constant, REPO_ROOT_PREFIXES = ("/workspaces/dstdns/",) -- identical shape to pack.py's ABS_REPO_PREFIX. Both files now co-located at tools/{pack,jsonl-metrics}.py, which incidentally resolved the two xfail tests filed at the original move (score's co-location requirement is satisfied again) -- test_pack.py is 33/33 passing, no xfails remain. The genericization-gap options (a/b/c) in this entry's body apply identically to both hardcodes now.

**2026-09-22** — Operator proposal, 2026-09-22 (worktree-local pack placement): agreed the
carve-stage-before-worktree-exists rationale for today's shared-trove default,
but observed that in practice a pre-built pack is mostly useful to
implementer/reviewer agents, and by the time either runs, its own worktree
already exists. Proposal: default `--out-dir` to a worktree-local path (e.g.
`.worktrees/<branch>/tmp/`) instead of `<repo>/nyxloom-trove/orientation/<slug>/`,
so a pack's lifetime is scoped to the package that consumes it rather than
persisting indefinitely in the shared trove tree (relevant to this entry's own
option (a)/(b) trove-root discussion, and a mitigation for the B101
repository-snapshot-budget growth problem dstdns is separately tracking, since
fewer trove-tree writes over time means a smaller git-ancestor-history object
closure for lanes that snapshot the whole repo).

Not implemented — noted here as a direction only. `pack.py`'s own docstring and
the `cmd_build` out_dir default now carry a pointer comment to this entry
(vbpub tools/pack.py), and both nyxloom-pack SKILL.md copies (vbpub canonical +
dstdns vendored) note it too.

Also flagged for awareness, not yet detailed: a future `cli-extended` adoption
is coming up per the operator, which should be weighed together with any
out-dir/trove-root redesign here rather than decided in isolation. No further
detail available on `cli-extended` at filing time -- whoever picks this entry
back up should ask the operator for specifics before assuming a shape.

**2026-09-22** — Effectiveness-analysis sub-thread started (operator request, 2026-09-22):
nyxloom-trove/reports/NL-19-pack-orientation-effectiveness/README.md has the
methodology, eligibility table, and a corrected finding worth flagging
directly: ALL of Wave B2 T1 (P194/195/196/197/198/199/201) actually DID have
at least one pack.py-built orientation pack (implementer, several also
reviewer) -- contradicting an assumption that T1 used no pack. T2 (P202/P203,
in flight) is the one with NO pack.py orientation found. Also: jsonl-metrics.py
cmd_curve gained a --raw flag this session (full per-call (idx,ts,context)
series, JSON-only) to support an x=call_idx/y=context plot -- cmd_boundaries
already provided the semantic-checkpoint overlay, no gap there. See the
report folder for full detail; remaining work (matching packs to actual
consuming subagent transcripts, running curve/boundaries/score per package,
rendering the plot) is queued there, not yet executed.
