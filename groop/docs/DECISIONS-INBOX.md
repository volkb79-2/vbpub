# Decisions inbox — product calls awaiting the user

Purpose: record product decisions without blocking engineering analysis.  An
OPEN entry is carved around under its stated assumption; it becomes DECIDED
when the decision and where it was encoded are recorded.

Entry schema: ID · date · raised-by · status (OPEN / DISCUSSING / DECIDED /
DROPPED) · question · why it matters · options with trade-offs · recommendation
and reasoning · context pointers · resume prompt · (when closed) decision
record.

---

## D-001 · 2026-07-13 · P69 web-UI scoping · OPEN

**Question:** Should browser v1 use a dependency-free single static client, a
server-rendered Python surface, or a full SPA framework?

**Why it matters:** This determines whether a Node toolchain and lockfile enter
a Python-first release path, how `groop[web]` is packaged, and the test stack.

**Options:** (a) single static HTML/CSS/ES module: smallest build and
supply-chain surface, but manual UI composition; (b) server-rendered Python:
no Node, but makes the gateway a renderer and may add template complexity;
(c) Vite/React SPA: strong component ecosystem, but adds Node, a JS dependency
tree, generated-assets policy, and browser tooling.

**Recommendation:** (a).  The first UI is four read-only pages over a bounded
JSON API, so plain browser APIs meet the need and preserve core `pip install
groop` as dependency-light.  Revisit only when demonstrated interface
complexity makes the manual client costly.

**Context pointers:** `docs/WEB-UI-SCOPING.md` “Framework and stack options”;
`handoff/P69-web-ui-scoping.md` deliverable 1.

**Resume prompt:** “Discuss D-001 in `groop/docs/DECISIONS-INBOX.md`: choose
the v1 web stack and explicitly accept or reject a Node build dependency.”

## D-002 · 2026-07-13 · P69 web-UI scoping · OPEN

**Question:** Who is allowed to view browser telemetry, and how will that
identity be authenticated at the HTTP gateway?

**Why it matters:** The daemon's `0660 root:groop` Unix socket conveys local
group access; HTTP cannot infer a browser user's identity from the gateway
process.  The answer controls whether sensitive metrics and unclassified frame
metadata can be displayed.

**Options:** (a) trusted local operator only: loopback gateway plus local
reverse-proxy authentication; low initial scope, no remote direct listener;
(b) named authenticated users with redaction roles: useful shared dashboard,
but requires identity/session/role implementation; (c) anonymous LAN/public
dashboard: simplest access but unacceptable exposure unless the API has a
separate fully public projection, which it does not today.

**Recommendation:** (a) for v1, with an authenticated principal and default
redaction above `operational`; grant `sensitive` only explicitly.  No
non-loopback listener until an authenticated TLS reverse-proxy deployment is
specified.

**Context pointers:** `docs/WEB-UI-SCOPING.md` “Sensitivity and redaction UX”
and “Trust boundary”; `CONTRACTS.md` §10; `handoff/P67-versioned-read-http-gateway.md`.

**Resume prompt:** “Discuss D-002 in `groop/docs/DECISIONS-INBOX.md`: define
the v1 viewer, authentication owner, and whether that viewer may see sensitive
metrics and frame metadata.”

## D-003 · 2026-07-13 · P69 web-UI scoping · OPEN

**Question:** Is the read-only web UI required for the v2 tag, or is it a
post-v2 surface delivered after the daemon/gateway work?

**Why it matters:** This determines release acceptance scope, sequencing, and
whether P69b/P69c are release blockers rather than follow-up product work.

**Options:** (a) v2 tag requirement: fulfills the stated browser-product goal
in the release, but makes gateway security and web validation release-critical;
(b) post-v2: ship the daemon/read API earlier, but defer the first non-CLI user
experience; (c) phased: v2 requires the trusted local overview only, with
history/detail and streaming post-v2.

**Recommendation:** (c).  Make the trusted, read-only overview and its gateway
boundary a v2 requirement; deliver entity history/detail and P68 live updates
as fast-follow.  This makes the product visible without tying the tag to an
unproven richer UI.

**Context pointers:** `docs/ROADMAP.md:144-166`;
`docs/WEB-UI-SCOPING.md` “Smallest useful page inventory” and “Draft successor
handoff headers”.

**Resume prompt:** “Discuss D-003 in `groop/docs/DECISIONS-INBOX.md`: choose
whether the v2 tag requires the overview, the full four-page read UI, or no web
surface.”
