# Review of nyxloom (draft 1) — and why draft 2 exists

Date: 2026-07-15 · Reviewer: frontier session (Fable 5) · Scope: full read of
`nyxloom/{README.md,docs/ARCHITECTURE.md,docs/SPEC.md,docs/MIGRATION.md,docs/ROADMAP.md,schemas/*}`
against the evidence base (`docs/controller-workflow-v2.md` incl. deciding log
P51–P85, `docs/ai-cli-controller-guide.md`, `dstdns/docs/ai-dev/*`, live
DECISIONS-INBOX usage).

## Verdict

Draft 1's **domain model and invariants are right and should be kept**. Its
**delivery shape is wrong for this team**: it specifies a platform (daemon,
SQLite-authoritative event store, four fixture-tested adapters, importer,
shadow mode) whose value arrives at phase 4 of 7, while the things that burn
tokens and cause incidents *today* (the LLM controller session, prose-only
carve rules, marker-file locks) have cheaper, earlier fixes. Draft 2 keeps the
state model, stop outcomes, review invariants, and security boundary largely
verbatim, and inverts storage and delivery: files are the database, a stateless
tick is the daemon, lint is the guarantee.

## What draft 1 gets right (adopted into draft 2, mostly verbatim)

- Orchestration is deterministic software; models only where judgment pays
  (ARCHITECTURE.md §1). Same conclusion as v2 §1 — correct.
- Task/attempt/gate/lease/evidence as **orthogonal** state (ARCHITECTURE.md §3).
  This fixes a real defect in v2 §9, where one per-handoff machine conflates
  task fate with attempt fate (BLOCKED is both).
- The seven typed **carver stop outcomes** (SPEC.md §10) — the correct answer
  to "when does review-derived work stop queuing": proposals never
  self-dispatch; admission is policy, not reviewer enthusiasm.
- The spec/roadmap **sufficiency audit list** (SPEC.md §10).
- Review invariants: independent gate, exact-commit binding, findings taxonomy,
  revalidation after review fixes, serialized merge (SPEC.md §7).
- The **security boundary** (ARCHITECTURE.md §8, SPEC.md §14) — unusually
  complete; adopted wholesale, one addition (notification injection, F8).
- Zero-AI dashboard commitment; Claude Remote Control positioned as an operator
  surface, not the bus (ARCHITECTURE.md §7).
- Currency/estimated-vs-actual cost discipline (SPEC.md §9).
- Non-goals, especially "manual merge may remain the preferred policy".

## Findings

### F1 · structural — dual-source handoffs reintroduce drift by design
`README.md:57-73` and `MIGRATION.md:97-104` keep `docs/handoff/*.md` (human
contract) **and** `.nyxloom/handoffs/<task>.json` (machine metadata). Two
files describing one contract is drift by construction — in a system whose own
migration story is a drift audit. The P69 incident (a carve instructing an
implementer to perform a reviewer-only action) happened *inside one file*;
splitting contract from metadata doubles the surface. The existing md
blockquote header is already machine-parsed; the fix is to formalize it, not to
fork it. **Draft 2:** one Markdown file with schema-validated YAML frontmatter;
no sidecars.

### F2 · structural — daemon-first is the riskiest component built first
`nyxloomd` (ARCHITECTURE.md §Control plane, ROADMAP phases 2–4) owns subprocess
supervision, liveness, leases, and crash recovery — the hardest-to-verify
component in the design, and its own SPOF (a daemon dying mid-wave is an
incident; state must survive it, hence the event-sourcing weight). But the
system's real cadence is minutes-to-hours: worker legs run 20–80 min and
completions stagger (v2 §11 — this is *why* the controller sleeps on
notifications). At that cadence a **stateless reconciler tick** (cron/timer
every 2–5 min: scan files → fire due transitions → append events → exit) covers
every daemon duty. Crash-safety becomes free — a dead tick is a missed tick;
the next one heals. Exit-code capture, the one thing polling can't see, moves
into a ~20-line **per-attempt wrapper** (run CLI → tee log → write typed
receipt with `$?` → release lease), whose kernel-guaranteed lifecycle is the
supervision. v2 §5.4's stall check is *already* file-based (log mtime,
`/proc`, `docker top`) — the daemon's liveness signal was reconstructible from
disk all along, which is precisely the property that makes ticks sufficient.

### F3 · structural — daemon-managed leases are weaker than flock(2)
ARCHITECTURE.md:129-133 correctly rejects timestamp marker files
(`.STACK_LOCK`) but replaces them with daemon bookkeeping — which dies with the
daemon and needs its own recovery protocol (SPEC.md §8 "lease recovery MUST
confirm process identity"). On a single host — the pilot's declared boundary
(SPEC.md §12) — `flock(2)` held by the attempt wrapper gives kernel-enforced
mutual exclusion with **automatic release on process death**: strictly stronger
than marker files *and* daemon state, with zero recovery code. Counted
resources = N slot files in a lease directory. Cross-project = shared
`$XDG_STATE_HOME/nyxloom/leases/`. **Draft 2:** flock-based leases; a
daemon lease table only if multi-host ever happens (out of scope by draft 1's
own spec).

### F4 · structural — SQLite-as-authority inverts inspectability
ARCHITECTURE.md:37-39 makes SQLite "the durable source for events and current
projections". But the actors that must read state cheaply are humans and AI
sessions — for whom plain files are free (grep/Read) and a database needs a
tool. At this scale (hundreds of events per wave) JSONL scans are microseconds.
**Draft 2:** append-only `events.jsonl` + small per-task statefiles are
authoritative; SQLite, if it ever appears, is a rebuildable index. Side
benefit: the whole "event replay rebuilds projections" test (SPEC.md §15.3)
shrinks to trivial code.

### F5 · correctness leverage — the empirical carve lessons are not encoded
The deciding log contains generalizable, partly machine-checkable rules that
draft 1 reduces to generic audit prose:
- P78: an oracle that enumerates fields silently becomes the contract → lint
  "every/all X" contracts whose oracle lists a subset.
- P84: never let acceptance be discharged by the reviewer → lint oracle text
  for deferral phrases; oracles must be executable in the implementer's
  declared environment.
- P69: no reviewer-only deliverables in implementer scope; all referenced
  paths must resolve from repo root (or be vendored).
- P51/§7: `escalate_if` triggers must be mechanical, not introspective.
- P85: self-review findings scored as *acted-upon*, not *mentioned* — a typed
  field, or the trial metric flatters itself.
These belong in a `lint` gate at carve time and as typed review-finding fields
— the incident corpus becomes the lint's golden tests. **Draft 2:** SPEC §6
lint rules L1–L12; the carve cannot commit red.

### F6 · model gap — no wave entity
v2 §6 batches ≤3 diffs per frontier review session; the review packet, the
carve that follows, and the session cost are per-wave facts. Draft 1's model
(SPEC.md §4–5, event.schema.json:66-104) has no grouping, so batch review cost
can't be attributed and the packet has no identity. **Draft 2:** `wave_id` on
events/attempts; WAVE_OPENED/WAVE_CLOSED events.

### F7 · feasibility gap — cost capture is asserted, not designed
"Adapters normalize usage/cost" appears without a single concrete mapping.
Whether this is even possible per CLI decides the dashboard's cost pane:
claude `-p --output-format json` emits usage + `total_cost_usd`; codex prints
token totals in exec output; opencode session storage has usage; reasonix logs
DeepSeek API usage fields. Each needs a verified extractor and an honest
`basis: actual|estimated|unknown` tag. **Draft 2:** ARCHITECTURE §6 table +
`routes.toml` carries `usage_source` per route.

### F8 · product gap — notification/decision loop left abstract
The user's actual ask — "notify dev, discuss questions" — needs a concrete
loop, not an adapter interface. **Draft 2:** typed events → ntfy/webhook push
(content built from typed fields only — never raw agent text, closing a
prompt-injection→push-notification exfil channel draft 1 doesn't name);
click-through to the dashboard entry; `nyxloom decide D-0XX --choose b`
records, releases `Depends-on: D-0XX` holds, appends the event; the inbox
entry's resume prompt is the bridge into a user-initiated Claude session
(mobile/web/Remote Control). Also: a daily digest mode — per-event push does
not scale to human attention.

### F9 · regression vs v2 — carver/reviewer session affinity dropped
v2 §2 has evidence-backed reasoning for carving *in the reviewer's warm
context* (cheaper and better; the only point where a growing context pays).
Draft 1 generalizes the carver into a separately dispatched role and loses the
hint. **Draft 2:** affinity is a scheduling *hint* (`carve_affinity:
reviewer-session`), not architecture — preserved, optional.

### F10 · schema nits (fix regardless of which draft proceeds)
- `handoff.schema.json:99-101` — `session_hint: fresh|resume-affinity` loses
  the v2 semantics "resume *which* session"; needs an affinity key.
- `handoff.schema.json:164-168` — `serialize_with` as peer task IDs is
  pairwise and ages badly; named mutex groups scale (`Stack: exclusive` is
  then just mutex `stack`).
- `handoff.schema.json:151-163` — `uniqueItems` on an array of objects is a
  no-op guarantee; key by resource id.
- `event.schema.json:26-30` — `sequence` required but scoping (global vs
  per-project) undefined; per-project is the useful one.
- Event types: no SPEC_ATTENTION, PAUSE_SET/CLEARED, WAVE_*, PROGRESS_RECORDED.
- `project.schema.json` — no notification config, no digest policy, no pause.

### F11 · missing operator controls
No pause. The user's emergency brake today is killing sessions. `nyxloom
pause [--project|--task X]` writing a flag the tick honors is ~free and belongs
in the MVP. Same for a `doctor` command (draft 1's one-time import/drift audit
reframed as an always-available linter over in-place files).

### F12 · migration is heavier than evolution
Because draft 1 introduces a second store, it needs importer → drift audit →
shadow → cutover (MIGRATION.md §2–5). If the md files *are* the store (F1/F4),
there is nothing to import: lint runs on files where they live, `status`/
`render` read them in place, and each automation step (dispatch, then review
orchestration) replaces one manual duty at a time with per-step rollback
("stop the timer"). Draft 1's shadow-mode *idea* survives as draft 2's M1:
dashboard runs read-only against live md state while the Sonnet controller
still drives.

## Also noted (workflow, independent of either draft)

- Typed completion receipts: implementers already write LOG/REPORT prose; a
  10-line JSON receipt (result, per-oracle pass/fail, files touched, usage)
  makes exit classification deterministic. One template edit, immediate payoff,
  and it de-risks every adapter.
- Protected-branch hardening (no force-push to main, require `--no-ff`) is
  cheap insurance the current workflow relies on discipline for.
- v2 prose is becoming its own risk: 648 lines that every frontier session must
  honor. P69 and P84 were both violations of rules that existed in prose.
  Rules should graduate from deciding-log prose into lint/template/config, and
  the doc should shrink as the tool grows.
