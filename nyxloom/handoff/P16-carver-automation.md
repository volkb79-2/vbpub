# P16 — carver automation: queue refill + narrative summaries + headroom

> Tier: sonnet · Date: 2026-07-15 · User directives (this session): (2)
> carve authority configurable per-project via UI, default factory =
> carve-branch-then-human-admit (safe), our use = same; carve-ahead count
> configurable. (3) after each carve the carver emits a NARRATIVE summary
> (reflection on the review/merge results it saw, what it carved and why,
> and how it reads the roadmap/backlog headroom) — persisted, shown
> interleaved in the UI handoff list at the times summaries arrived, behind
> a UI toggle. Read handoff/STANDING.md first.

## Owned files
- `src/nyxloom/reconcile.py` (carve-trigger planning),
  `src/nyxloom/daemon.py` (CarveDispatch execution + summary persistence
  + endpoints), `src/nyxloom/render.py` (summary interleave + toggle),
  `src/nyxloom/config.py` (ONLY: add Policy fields `carve_ahead_target:
  int = 5`, `carve_authority: str = "branch"` [branch|main|files],
  `headroom_warn: int = 5`; and the CarveSummary dataclass if you keep it
  here — else put it in types.py which is otherwise frozen: prefer a small
  dataclass local to reconcile/daemon).
- tests: test_reconcile.py, test_daemon.py, test_render.py additions;
  optionally test_carver.py.

## Behavior

1. **Trigger** (reconcile): count admissible ready tasks (state in
   {CARVED, QUEUED, NEEDS_DECISION} that are not decision-held). If <
   policy.carve_ahead_target AND an active milestone admits work (proxy for
   now: at least one non-terminal task OR carve_ahead_target>0 and no
   SPEC_ATTENTION 'roadmap-exhausted' open) AND no carver attempt already
   in flight AND budget allows -> emit CarveDispatch(project). At most one
   carver in flight per project (a carve slot, like the existing single
   wave-review pattern).
2. **CarveDispatch execution** (daemon): dispatch a FRONTIER carver leg
   (tier 'frontier-review' route, role CARVER) via the wrapper, with a
   packet that gives it: the four carve sources (v2 §8 — review-derived
   follow-ups from recent REVIEW_RECORDED, backlog file, roadmap/gap
   files from project product_sources, standing product goal), the current
   queue, and the REQUIRED OUTPUT CONTRACT below. carve_authority routes
   the output:
   - `branch`: carver works on a `carve/<project>-<seq>` branch off main,
     commits new handoff md files there, does NOT merge. Daemon emits
     CARVE_OUTCOME + a NEEDS_OPERATOR notification "carve ready: N packages,
     headroom <h>" — a human admits by merging (the tick then materializes
     them). (DEFAULT.)
   - `main`: carver commits carves to main directly (lint-gated); tick
     materializes next pass.
   - `files`: carver writes files uncommitted; tick materializes (reads
     files); no git.
3. **REQUIRED carver output contract** (stated in the packet; the carver
   writes it to `<reports_dir>/CARVE-<seq>.md` AND as the final receipt):
   a CarveSummary with fields — `carved` (list of new task ids + one-line
   why + source-kind each), `review_reflection` (what the recent
   reviews/merges revealed about quality/gaps), `headroom_estimate` (int:
   ~how many more carve-able packages exist before ROADMAP_EXHAUSTED /
   SPEC_GAP), `headroom_rationale` (one paragraph: how the carver reads the
   roadmap/backlog runway), `outcome` (one of the 7 v2 §8 outcomes). The
   daemon parses this receipt into a CARVE_OUTCOME event payload (typed
   fields only for any notification; the free-text reflection is persisted
   for the dashboard but NEVER sent to a notification channel — injection
   boundary).
4. **Headroom alert**: if headroom_estimate < policy.headroom_warn -> the
   CARVE_OUTCOME event also flags it and pushes SPEC_ATTENTION
   {reason: 'headroom-low', detail: 'N packages left'} (typed only).
5. **Persistence + UI interleave** (render): CarveSummaries are stored as
   files under the state dir (`$XDG_STATE/nyxloom/<project>/carves/
   <seq>.json` — daemon writes them; NOT in the repo unless authority=
   branch/main puts the md there too). index.html: a toggle (checkbox,
   vanilla JS, default OFF) "show carve summaries" that, when on,
   interleaves each summary as a distinct row/card positioned by its
   timestamp among the task rows (sorted by time) — showing carved ids,
   the reflection text (html-escaped), headroom estimate, outcome. Off =
   today's pure task list.

## Oracles
1. Trigger: queue below carve_ahead_target with no carver in flight ->
   exactly one CarveDispatch; queue at/above target -> none; carver already
   ACTIVE -> none (slot). carve_authority plumbs to the execution branch
   (test each of branch/main/files produces the right git/file effect with
   a fake carver receipt).
2. Summary parse: a fake carver receipt (CarveSummary JSON) -> CARVE_OUTCOME
   event with typed fields; reflection persisted to the carves/ dir;
   headroom < warn -> SPEC_ATTENTION headroom-low pushed; assert the
   reflection TEXT never appears in any NOTIFICATION_* payload.
3. Render interleave: two persisted summaries + tasks -> toggle-off index
   has no summary text; toggle-on has both summaries positioned by
   timestamp; reflection html-escaped; no innerHTML.
4. Config: carve_ahead_target/carve_authority/headroom_warn read from
   Policy; UI POST to set carve_authority per project (reuse P15's config
   endpoint pattern if merged; else a POST /api/config/policy key) ->
   project.toml updated, CONFIG_CHANGED event.
5. Full suite green.

## Rules
STANDING.md applies. Coordinate with P15's daemon.py/render.py/config.py
changes: P15 lands first (shared files) — REBASE onto it (read the current
files at start; if P15 endpoints exist, extend them, do not duplicate). Do
not commit. REPORT to handoff/reports/P16-REPORT.md; receipt-only final.
