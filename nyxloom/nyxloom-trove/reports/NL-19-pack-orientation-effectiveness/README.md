# Pack-orientation effectiveness — investigation (NL-19)

Folded into `nyxloom-trove/backlog/NL-19-...md` (this analysis is a sub-thread of
that entry, not a separate backlog item). Started 2026-09-22 on operator
request, during the same session that relocated `pack.py`/`jsonl-metrics.py`
from dstdns into this repo.

## Question

Does a `pack.py`-built orientation pack measurably help a dispatched
implementer/reviewer, and if so how much — using real transcript data from
Wave B2's actual dispatches, not the `score` subcommand alone (its
methodology has known limitations — see `pack.py`'s own `# score (E-006 Task
B)` header comment, `tools/pack.py:~1580`, added this session before trusting
its numbers here).

## Eligibility (finding #1 — corrects an assumed premise)

`pack.py` replaced `pack.sh` on **2026-08-20** (dstdns commit `363c7f7a`); any
dispatch before that date used the older, less-curated `pack.sh` mechanism and
is out of scope for a `pack.py`-specific effectiveness read.

**Every Wave B2 T1 package (P194, P195/→P201, P196, P197, P198, P199, P201)
was dispatched AFTER that date (2026-09-17/18) and DID have at least one
`pack.py`-built orientation pack** — this corrects the working assumption
that "the T1 agents used no pack." Evidence: the pack outputs themselves
still exist in this session's own scratchpad (`pack.py`/`SKILL.md` both say
never to commit them — they're gitignored build output — but nothing deletes
the scratchpad copy), each carrying pack.py's own stamp header
(`Built by nyxloom-trove/orientation/pack.py`, a git rev, and the source
handoff path):

| Package | Role | Pack(s) found | Stamp rev(s) | Handoff input_revision |
|---|---|---|---|---|
| P194 | implementer | `pack-p194`, `p194-pack-v1/v2/v3` (3 successor rebuilds) | `4b1b0588`, `78fa5224`×2, `58a01881` | `1a402ef5` |
| P194 | reviewer | (built later, main...p194-b2-io-fault range — not saved to scratchpad under a name captured here; see CONTROLLER-BRIEF.md:14178) | — | — |
| P195 | implementer | `pack-p195-b2-ctl` | `279eb902` | (P201 handoff — see note) |
| P196 | implementer | `pack-p196-b2-db-ops` | `f29bc646` | `2601b36e` |
| P197 | implementer | `pack-p197-b2-cli` | `5443d640` | `b5f9a00f` |
| P197 | reviewer | `p197-review-pack` | `c6d34f38` | `b5f9a00f` |
| P198 | implementer | `pack-p198-b2-web` | `8bc81dc2` | `8b02270f` |
| P198 | reviewer | `p198-review-pack` | `3696b0f8` | `8b02270f` |
| P199 | implementer | `pack-p199-b2-io-main` | `9a6b3ec8` | `079aba7c` |
| P199 | reviewer | `p199-review-pack` | `b532db44` | `079aba7c` |
| P201 | implementer | `p201-pack`, `p201-pack-v2`, `p201-pack-v3` (2 successor rebuilds) | `b7a1a4f6`, `3e4dc914`, `139e4f77` | `ce0cc7ca` |
| **P202** | implementer | **none found** — checked scratchpad and both `.worktrees/p202-b2-io-dns/`, `.worktrees/p203-b2-io-http/` | — | — |
| **P203** | implementer | **none found** | — | — |

Note on P195/P201: `pack-p195-b2-ctl`'s handoff path is
`dstdns-P201-b2-ctl-lanes-and-loops.md` — P195 and P201 share the same
underlying controller/lanes-and-loops handoff lineage (P201 is P195's
continuation package after a scope split), so what looks like a P195-labeled
pack is actually an early P201 pack build. Treat P195/P201 as one lineage for
this analysis, not two independent data points.

**Consequence for the analysis**: T1 is NOT a no-pack control group — it is
the pack.py-oriented group. **T2 (P202, P203), still in flight this session,
is the one with NO pack.py orientation** (dispatched directly from the
handoff without a pre-built pack) — if a with/without comparison is wanted,
T2 vs. T1 is backwards from what "T1 = no pack" would have implied, but
right-way-round for "T1 = with pack, T2 = without" once this correction is
applied. This should be confirmed with the operator before drawing
conclusions — it's plausible T2 was dispatched without a pack simply because
of session time pressure near a compaction boundary, not as a deliberate
control condition, which would make it a poor comparison (confounded with
"later in a long session, less controller attention," not just "no pack").

## Implementer vs. reviewer differentiation

Mechanically derivable two ways, both confirmed working on the packs above:
1. **The pack's own stamp/section header** — `pack.py build --role reviewer`
   packs are built `--range main...<branch>` and their first content section
   is a diff, not the handoff; `--role implementer` packs open with the full
   handoff text (`=== nyxloom-trove/handoffs/... ===` as the first section,
   confirmed in the P194/P196/P197/P198/P199/P201 rows above).
2. **The scratchpad directory naming convention** already in practice this
   session (`pack-p<NNN>-<slug>` = implementer, `p<NNN>-review-pack` =
   reviewer) — informal, but consistent across all 5 reviewer packs found.

## What jsonl-metrics.py already offers (checked this session, before filing
a capability gap)

- `detect_boundaries()` / `cmd_boundaries` (`tools/jsonl-metrics.py:1254`) —
  **already** produces exactly the "semantic boundary/checkpoint" annotation
  requested: `gate_green` / `gate_red` / `gate_unknown`, `commit`,
  `edit_cluster_end`, `log_report_write`, each tagged with `call_idx` AND the
  `context` size at that call. No gap here — use this directly for the
  requested checkpoint overlay.
- `growth_table()` / `cmd_curve` (`tools/jsonl-metrics.py:189`) — **had** a
  gap: only a 5-point percentile summary (10/25/50/75/100% of calls), no full
  per-call series, even though the underlying `Call` objects already carry
  `(idx, ts, context)` per call. **Closed this session**: `cmd_curve` gained
  `--raw` (requires `--json`), emitting the full `raw_series` list — smoke-
  tested against a real subagent transcript
  (`agent-a0e35d5c6fa87d9b2.jsonl`, 82 calls, verified output shape). This is
  the x=call_idx / y=context data source for the requested plot; combine with
  `cmd_boundaries`' output (same transcript) for the checkpoint overlay.
- No dedicated `jsonl-metrics.py` test file exists yet (pre-existing gap,
  unrelated to this session's `--raw` addition — noted, not fixed here).

## `pack.py score` — do not trust at face value

Full critique now lives in `tools/pack.py`'s own `# score (E-006 Task B)`
header comment (added this session) — six issues, most importantly: `score`
defines "used" by the presence of a redundant Read/Bash re-fetch of content
already inline in the pack, so a **perfectly-curated pack that needed zero
follow-up reads scores as 100% "unused"** — the algorithm's worst score for
the best outcome. Any retrospective run using `score` numbers must cite this
limitation alongside the number, not present it standalone. Alternatives
considered there: citation-based scoring against the agent's own final
diff/REPORT, extending the readset to native Grep/Glob tool_use blocks
(closes one of six issues, not all), outcome-based comparison via
`curve`/`boundaries` rather than input-side file-touch presence (preferred
direction), or each package's own self-reported "E-002 telemetry" section.

## Data-gathering status (this session)

Session directory with subagent transcripts confirmed present and countable:
`~/.claude/projects/-workspaces-dstdns/23ce0da8-6cb7-4d7f-8558-15167b1f67f9/subagents/`
— 531 `agent-<id>.jsonl` files, one per dispatched Agent-tool subagent this
session (matches the cross-repo memory finding that these persist
independently of the dispatching session's own compaction).

**Not yet done**: matching each pack above to its actual consuming agent-id's
transcript (the pack directories found don't self-record which dispatch
consumed them — that has to come from cross-referencing dispatch timestamps/
prompts against the 531 transcripts), then running `jsonl-metrics.py curve
--raw --json` + `cmd_boundaries` + `pack.py score` for each matched pair, and
rendering the actual plot data. This is a mechanical, high-tool-call-volume
task better suited to a dedicated fresh agent than continued inline
investigation — queued as the next step.
