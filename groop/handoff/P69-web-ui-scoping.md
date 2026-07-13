# P69 - Web UI over daemon API: scoping and analysis

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P52 (merged), P63 (merged)   <!-- reads the merged read-API surface; does NOT need P67 merged, it reads P67's handoff -->
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none   <!-- writes only new docs + new handoff drafts; touches no source -->
> **Escalate-if:** the analysis concludes the P52/P63/P67 read surface cannot serve a browser frontend without protocol changes (that is a real finding — write it, do not design around it)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **product-goal-driven**.
Standing user priority (docs/ROADMAP.md, 2026-07-13): "get the product in front
of users with a web UI." ROADMAP promotes the Web UI out of the "Optional
plugins" bucket into a real DAG node (P69, depends on P67). The area is
unscoped — framework, page inventory, auth/redaction UX, and the gateway's
actual API shape are all undecided — so per §8 this carve is a small
SCOPING/ANALYSIS package, not a sight-unseen implementation handoff. Its output
is the input to the real implementation carves.
-->

## Goal

Answer, with evidence from the merged code, **what a groop web UI should be and
what it needs from the daemon** — so that the implementation packages that follow
can be carved with real contracts instead of guesses. This package ships
**documents and handoff drafts, no product code.**

The one-sentence framing: the daemon already serves typed, bounded, sensitivity-
tagged reads (P52 envelope, P63 typed client, P67 gateway in the queue). A web UI
is the first consumer that a *non-operator* will look at. Decide what it shows,
what it must never show, and what is missing to build it.

## Deliverables (all under `groop/docs/` or `groop/handoff/`)

1. **`groop/docs/WEB-UI-SCOPING.md`** — the analysis. Sections:
   - **Read-surface gap analysis.** Walk the five P52 ops (`hello`, `current`,
     `history`, `entity`, `health`) and the P63 typed client methods, and state
     for each: does a browser UI need it, and is what it returns sufficient?
     Name every gap concretely (e.g. "no server-push, so the UI must poll
     `current`; P68 subscribe would fix it"). This is the section that must be
     grounded in the actual code, not in what the docs claim.
   - **Page inventory.** The smallest set of screens that makes the product
     useful to someone who is not already a groop CLI user. Ground each page in
     an existing surface (the TUI's table/tree/drill-down/banner already encode
     years of product decisions — reuse them, do not reinvent). For each page:
     which daemon ops feed it, at what refresh rate, and what the response size
     is at gstammtisch scale (~89 entities; a full frame is ~447 KB — cite P53's
     measurement, do not re-derive).
   - **Sensitivity / redaction UX.** CONTRACTS.md §10 defines a three-level
     closed enum (`public`/`operational`/`sensitive`). A browser UI is the first
     consumer where "who is looking at this screen" is a real question. State
     what the default redaction posture should be and how the UI surfaces a
     redacted value (never a silent blank — the P58 review's standing lesson:
     redaction replaces with a typed marker, it does not drop the key).
   - **Trust boundary.** The daemon socket is `0660 root:groop` on localhost.
     A web UI implies a listening HTTP port. State explicitly what P67's gateway
     must enforce (bind address, authn, CSRF/origin, read-only-ness) for this to
     not be a regression of the v1 trust boundary. If P67's *carved handoff*
     under-specifies any of these, say so — that is a finding, and it goes back
     into P67 before P67 is dispatched.
   - **Framework/stack options.** At most three candidates, each with: build/
     toolchain cost, whether it adds a Node dependency to a Python project,
     packaging story (does `pip install groop[web]` still work?), and how it is
     tested deterministically in this repo's pytest suite. Recommend one. The
     repo's standing bias is stdlib-first and dependency-light; a
     server-rendered or single-static-file option deserves a serious look before
     a full SPA toolchain.
2. **Draft handoff headers for the implementation packages** — a section at the
   end of `WEB-UI-SCOPING.md` proposing the successor packages (title, one-line
   goal, `Tier`, `Depends-on`, rough size). Do **not** write the full handoff
   bodies; the carver does that from this analysis.
3. **`DECISIONS-INBOX.md` entries** for every question that is a *product* call
   rather than an engineering one (controller-workflow-v2 §8 "decisions inbox").
   At minimum expect: framework choice, auth posture, and whether the web UI is
   in-scope for the v2 tag or a post-v2 surface. Each entry: question, why it
   matters, options + trade-offs, recommendation, context pointers, resume
   prompt. If `groop/docs/DECISIONS-INBOX.md` does not exist, create it using the
   schema in `dstdns/docs/ai-dev/DECISIONS-INBOX.md`'s header as the reference.
   **Never block on an answer** — record the assumption you carved under and move
   on.

## Context To Read First (bounded)

`groop/CONTRACTS.md` §10 (read API envelope, sensitivity enum),
`groop/src/groop/daemon/api.py`, `groop/src/groop/daemon/client.py` (the P63 typed
methods), `groop/docs/DAEMON.md`, `groop/handoff/P67-versioned-read-http-gateway.md`
and `groop/handoff/P68-versioned-current-subscribe-client.md` (queued, not merged
— you are analyzing what they promise), `groop/docs/ROADMAP.md` §P69, and the TUI
surfaces you are proposing to mirror (`src/groop/ui/table.py`, `banner.py`,
`drill.py`) for the page inventory. Do **not** read DAMON/BPF/actions/squeeze code
— the web UI v1 is read-only and none of it is in scope.

## Required Contracts

- **No source changes.** Not one line under `src/groop/`. If you find a bug while
  reading, write it in the analysis; do not fix it here.
- **Every claim about the daemon surface is grounded in the merged code**, with a
  `file:line` pointer. "The API returns X" must be checkable. This package exists
  precisely because nobody has audited that surface from a frontend's point of
  view; an analysis that restates the docs is worthless.
- **No framework is installed, vendored, or pinned.** This is a recommendation,
  not a spike.
- Read-only v1: the analysis must not propose mutation/action surfaces in the web
  UI. Actions have a root/admin/typed-confirmation/audit posture (P21/P46) that a
  browser does not get to shortcut. State this as an explicit non-goal.

## Acceptance Oracles

This package's output is prose, so the oracles are about falsifiability:

1. The gap analysis names **at least one concrete gap** in the current read
   surface with a `file:line` citation, or states explicitly (with citations)
   that there is none. "It looks sufficient" is a fail.
2. Every page in the inventory maps to named daemon ops and a response-size
   estimate at 89 entities. A page with no data source is a fail.
3. The trust-boundary section states a concrete verdict on P67's carved handoff:
   sufficient as written, or here are the N things it must add before dispatch.
4. Each `DECISIONS-INBOX.md` entry carries a recommendation, not just options —
   the user is delegating the analysis, not the opinion.
5. The framework section names the packaging consequence for `pip install groop`
   (does the wheel grow? does a Node toolchain become a build dependency?).

## Out Of Scope

- Any implementation, prototype, mockup code, or `npm install`.
- Changing P67/P68 handoffs directly (propose changes; the carver applies them).
- Auth/identity *implementation* (P67 owns the gateway's posture; you specify what
  the UI needs from it).
- Mutation/action surfaces, DAMON control, squeeze — read-only v1, hard line.
- Mobile/responsive design, theming, i18n.

## Gates

No test suite to run (no source change). Instead:

```bash
git diff --check
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error   # must still be green: proves you changed no source
```

The REPORT states which environment the suite ran in and confirms the diff touches
only `groop/docs/**` and `groop/handoff/**`.
