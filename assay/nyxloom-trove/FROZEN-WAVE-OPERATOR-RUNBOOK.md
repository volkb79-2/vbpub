# Assay P20-P32 frozen-wave operator runbook

This is the executable semi-manual entry point for the first frozen-orientation
pilot. The design contract is
`nyxloom/docs/frozen-orientation-fork-workflow.md`; the Luna instructions are
`FROZEN-WAVE-CONTROLLER-PROMPT.md`. Current Git, handoffs, and registered gates
remain authoritative.

## Roles and durable identities

| identity | purpose | lifecycle |
|---|---|---|
| `L-assay-wave-0` | Luna high mechanical controller | resume between packages; no product rulings |
| `C-sol-0` | Sol xhigh JIT carver/design authority | resume this existing thread; validate every delta |
| `I-sonnet-0` | Sonnet xhigh package-neutral implementer base | immutable; fork children only |
| `I-opus-0` | Opus xhigh package-neutral implementer base | immutable; fork children only |
| `R-opus-0` | Opus xhigh package-neutral reviewer base | immutable; fork children only |

Fresh implementer and reviewer **children** are created for every handoff. Only
the package-neutral bases and the small Luna controller persist. The evolving
Sol carver persists for the opposite reason: it owns design continuity and is
reconciled against Git before each JIT carve.

## State directory

Use `/workspaces/vbpub/.worktrees/_control/assay-P20-P32` and no other implicit
location. It contains:

```text
bases.yaml                 generated current base registry, schema v2
invocations.jsonl          append-only one-object-per-request measurements
briefs/Pxx.yaml            reviewer-adjudicated immutable package brief
carver/Pyy.md              generated route/checkpoint packet for the next JIT carve
runs/Pxx.md                controller outcome and exact OIDs
```

State files contain no product truth. Anything with lasting meaning is promoted
by Sol into a handoff, decision, specification, or epoch document before the
affected dispatch.

## One package

1. On clean shared `main`, record the full HEAD and confirm the next handoff in
   `handoffs/README.md`.
2. Resume `L-assay-wave-0` with the controller prompt plus the run input block:
   handoff, exact file, immediate successor, expected full HEAD, epoch, base ids,
   prior brief paths, and state directory.
3. If Luna returns `ROUTE_TO_SOL`, bring `carver/Pyy.md` to `C-sol-0`. Sol checks
   its acknowledged OID-to-HEAD diff, runs the JIT carve, commits durable
   changes, and records `READY` or `NOT_READY`. Resume Luna with the new full
   HEAD and carver report. Do not create execution children for `NOT_READY`.
4. Luna creates any missing package-neutral bases, then forks exactly one fresh
   implementer child in `.worktrees/Pxx` from the assigned base.
5. Luna verifies implementation Git/gate evidence and forks one fresh Opus
   reviewer child. The reviewer performs blind findings before seeing the
   implementer narrative, then reconciles/fixes in the same child.
6. Luna runs the registered gate independently and serially merges `--no-ff`
   only on acceptance. It records exact OIDs and request usage.
7. The reviewer adjudicates successor candidates. Luna expires/discards
   mechanical dispositions and writes the next `carver/Pyy.md`; Sol promotes or
   compresses the rest during the next JIT carve.
8. End the package run. Reuse Luna and the immutable bases for the next package;
   never reuse an implementation/review child.

## Exact P20 pilot start after the JIT-freeze commit

P20's Sol JIT step is already complete. Its READY evidence is
`reports/assay-P20-JIT-CARVE.md`; do not route it back to Sol unless the main
diff after this commit changes a P20 contract, asset, source owner, or gate.

1. In `/workspaces/vbpub`, require a clean shared checkout and record current
   truth:

   ```sh
   git status --short
   git branch --show-current
   git rev-parse HEAD
   ```

   Stop if status is nonempty or the branch is not `main`. Call the resulting
   full OID `P20_WAVE_HEAD`; it is the commit containing the P20 JIT freeze, not
   P20's earlier `input_revision` anchor.
2. Start one **fresh Luna high** session. Paste the entire
   `FROZEN-WAVE-CONTROLLER-PROMPT.md`, then append this filled run block with
   the literal `P20_WAVE_HEAD` value:

   ```text
   RUN INPUT — BEGIN
   current handoff: P20
   current handoff file: assay/nyxloom-trove/handoffs/assay-P20-repository-artifact-boundary-integrity.md
   immediate successor file: assay/nyxloom-trove/handoffs/assay-P21-verdict-v4-evidence-contract.md
   expected current main HEAD: <P20_WAVE_HEAD>
   epoch: 0
   evolving carver identity: C-sol-0 (this existing Sol thread; P20 JIT is already READY)
   carver last acknowledged main OID: <P20_WAVE_HEAD>
   implementer base for the assigned model: MISSING
   reviewer base: MISSING
   prior adjudicated implementer brief: NONE
   prior adjudicated reviewer brief: NONE
   controller state directory: /workspaces/vbpub/.worktrees/_control/assay-P20-P32
   experiment condition: warm-fork
   RUN INPUT — END
   ```

3. Luna must first return its preflight facts: observed full HEAD, P20 READY
   report/asset hashes, selected route `Sonnet xhigh -> fresh Opus xhigh`, state
   directory, and whether `I-sonnet-0`/`R-opus-0` were created. Do not accept a
   worktree or implementation child before those facts agree with Git.
4. When Luna asks to create the immutable bases, allow exactly two top-level
   fresh Claude sessions: Sonnet xhigh implementer orientation and Opus xhigh
   reviewer orientation. Each must end at `READY_TO_FORK`, be recorded in
   schema-v2 `bases.yaml`, and remain unmodified. P20 then runs only in forked
   children.
5. Keep this Sol thread available as `C-sol-0`; it has no ordinary P20 turn.
   After Opus accepts/repairs and Luna merges P20, Luna writes the adjudicated
   P20 brief and `carver/P21.md`. Bring that packet plus the exact pre-merge to
   post-merge OID range back here for P21 JIT carving.

If the CLI cannot actually fork from an immutable top-level session, or Luna
cannot obtain/cache-account its real provider session id, stop the pilot and
record `NOT_READY_WORKFLOW`; do not silently substitute a fresh full-orientation
implementation and call it the warm-fork condition.

## Cache and experiment discipline

- Record provider usage per request, not only totals. Cache-read tokens are the
  hit evidence; agent prose is not.
- The Claude TTL clock starts when the request starts and slides on every hit.
  Keepalive scheduling uses that timestamp. Confirm the actual TTL class in
  `usage.cache_creation`; do not assume an internal subagent has the top-level
  session's TTL.
- Stable serialized base bytes remain reusable even as Git changes. Every child
  must inspect the orientation-OID-to-current-HEAD diff and reread relevant
  changed files. Rotate only for a deliberate epoch or documented health/drift
  trigger.
- Include at least one comparable fresh session and one cold-after-TTL child in
  the pilot. Otherwise the run can report cache usage but cannot estimate the
  frozen-base counterfactual.
- Keep request/tool/model/effort options byte-stable where possible and record
  CLI/system/tool fingerprints. A silent tool-definition change can destroy the
  prefix before user content begins.

## Sol continuity and compaction

Continue `C-sol-0` indefinitely while it can verify its last acknowledged OID
and recover required rationale from Git plus the Luna checkpoint. Automatic
Codex compaction is opaque model context reduction, not a new source of truth.
Before an epoch or suspected compaction boundary, Luna prepares a carver packet;
after resume, Sol validates it. If the session cannot do so, start `C-sol-1`
from a compact current orientation and record the epoch transition.
