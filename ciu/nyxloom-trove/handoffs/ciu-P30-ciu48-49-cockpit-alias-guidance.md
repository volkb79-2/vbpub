---
schema_version: 1
id: ciu-P30-ciu48-49-cockpit-alias-guidance
project: ciu
component: templates+docs
title: "CIU-48/CIU-49: ciu ships no default for Compose hostname: or topology.services.*.internal_host (both are entirely consumer-declared) -- the ciu-actionable fix is a correctly-qualified hostname: line in ciu's own ciu-init scaffold template, plus explicit DESIGN-GUIDE/CONFIG.md guidance making the qualified pattern the prescribed default and naming the bare-alias hazard by name, so a consumer copying ciu's own guidance never reproduces it"
tier: implement-1
input_revision: "27d0d32c"
source: {kind: backlog, ref: "KNOWN_ISSUES_TODO_BACKLOG.md#CIU-48, #CIU-49, filed at vbpub 4ccf7d4d from dstdns's §3.6 investigation"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/templates/stack.compose.yml.j2"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONFIG.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P30-ciu48-49-cockpit-alias-guidance-LOG.md"
  forbid:
    - "src/ciu/deploy.py"
    - "src/ciu/engine.py"
    - "src/ciu/worktree.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-scope-decision-recorded
    observable: "The LOG explicitly records this scoping finding, made BEFORE any file was touched: `grep -rn 'hostname:' src/` (ciu's own source tree) returns ZERO hits -- ciu's own `ciu init`-shipped scaffold (`src/ciu/templates/stack.compose.yml.j2`) does not set a compose `hostname:` field at all today, and `topology.services.*` (S4.16/S7.4) is ENTIRELY consumer-declared config ciu only READS (`src/ciu/secrets/providers.py:56-70`) -- ciu ships no default value for `internal_host` anywhere in its own templates (`src/ciu/templates/global.defaults.toml.j2` has no `[topology]` block). The 31 hand-authored compose templates and the hand-maintained `internal_host` override the CIU-48/CIU-49 filing cites are ALL in the DSTDNS repository, not reachable or editable from this vbpub/ciu session. This package's actual scope is therefore narrower than a literal reading of 'implement CIU-48/49' -- a shipped scaffold-template improvement plus explicit documentation guidance, NOT a fix propagated into every existing consumer's own templates (that is dstdns's own follow-up work in its own repo)."
    negative: "claiming or implying in any doc/LOG language that this package closes dstdns's actual operator pain by itself; silently assuming a ciu-shipped default exists somewhere and 'fixing' something that was never there"
    gate: "tester-unified"
  - id: O2-scaffold-hostname
    observable: "`src/ciu/templates/stack.compose.yml.j2` (the `ciu init`-generated stack compose template) gains an explicit `hostname:` line on its scaffolded `app` service, using the SAME identity variables the template's existing `container_name:` line already uses (`{{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}` -- copy this exact expression, do not introduce a new one). A stack freshly scaffolded via `ciu init` therefore gets a correctly-qualified `hostname:` by default; this does not retroactively change any EXISTING consumer's already-authored templates (ciu cannot reach those)."
    negative: "inventing a different identity expression than the one container_name: already uses (two independently-typed copies of the same derivation is the exact staleness hazard CIU-48 exists to close); applying this to any OTHER existing scaffold file beyond stack.compose.yml.j2"
    gate: "tester-unified"
  - id: O3-design-guide-hazard-named
    observable: "docs/DESIGN-GUIDE.md gains a new section (place it near the existing 'Why there is no compose project without -p (CIU-46 cutover)' section, since both concern container/network identity qualification) explaining WHY a bare Compose `hostname:` or a bare `topology.services.*.internal_host` is dangerous: Docker independently registers BOTH a container's `hostname:` value and its compose service KEY as network-resolvable DNS aliases; when two CIU-deployed instances of the same stack shape coexist on a shared/joined network (the exact ciu worktree + shared-infra scenario), a bare alias resolves to whichever instance's container Docker's resolver happens to answer with -- non-deterministic from the caller's perspective. Name this the §3.6 cockpit-alias-ambiguity hazard (matching the filing's own terminology) so a reader can find the same term in the backlog history. State plainly what this section does NOT cover: Compose's automatic bare service-key alias itself (CIU-51, a separate, v8-scale, NOT-backported item) is NOT eliminated by anything in this package -- only the two consumer-controllable value defaults (hostname:, internal_host) are addressed here."
    negative: "implying this package eliminates the bare service-key alias Compose always creates (that's CIU-51, explicitly out of scope and not a backport); a vague warning with no concrete before/after example"
    gate: "tester-unified"
  - id: O4-config-md-prescription
    observable: "docs/CONFIG.md's existing `[topology.services.<name>]` section (S4.16/S7.4, search for it) is extended with an explicit MUST-level prescription: `internal_host` values SHOULD be qualified with `{{ deploy.project_name }}-{{ deploy.environment_tag }}-<service>` (the exact `container_name()` derivation, cite it) rather than a bare service name, with a one-line reason and a link to the new DESIGN-GUIDE section (O3). The existing worked example at this section (`internal_host = \"ciudemo-dev-vault\"`) is confirmed to already look qualified -- if you find it does NOT actually demonstrate the pattern clearly (e.g. it's ambiguous whether 'ciudemo-dev' is project-environment_tag or something else), make it unambiguous; do not leave an example a reader could misread as endorsing a bare form."
    negative: "a documentation change that only adds prose without correcting or clarifying the existing worked example if it turns out to be genuinely ambiguous"
    gate: "tester-unified"
  - id: O5-consumers-example
    observable: "docs/CONSUMERS.md gets one worked example (or an addition to an existing relevant one) showing the correctly-qualified pattern for BOTH a stack's own `hostname:` in its compose template AND a `topology.services.<name>.internal_host` declaration, framed as 'what to write when authoring your own stack' -- since ciu cannot generate these for existing consumers, CONSUMERS.md's worked-example role (per AGENTS.md's three-docs rule: CONSUMERS.md is the HOW, something an adopter can paste) is exactly where this belongs."
    negative: "duplicating DESIGN-GUIDE's WHY reasoning here instead of linking to it (AGENTS.md: 'a README feature links to its DESIGN-GUIDE section rather than re-arguing the rationale there')"
    gate: "tester-unified"
  - id: O6-backlog-disposition
    observable: "KNOWN_ISSUES_TODO_BACKLOG.md's CIU-48 and CIU-49 rows are updated to a status that accurately reflects what shipped: NOT 'FIXED' (which would wrongly imply dstdns's own 31 templates are now fixed), but something like 'PARTIAL -- ciu-side scaffold default + prescriptive documentation shipped; propagating the corrected pattern into existing hand-authored consumer templates (dstdns and any other consumer) is that consumer's own follow-up, out of reach from ciu's own release'. CHANGES.md Unreleased entry states the same distinction plainly."
    negative: "marking either row FIXED in a way that overclaims what a ciu release alone accomplishes"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the existing scaffold template test(s) (grep tests/tests/ for whatever exercises `ciu init`'s generated stack.compose.yml.j2) assert an exact byte-for-byte template content that your new hostname: line would break in a way that reveals the test itself should change -- update the test to match the corrected template (this is expected, not a blocker) but note it in the LOG; BLOCKED only if updating the test would itself require touching a forbidden file"
mutexes: [merge-lane]
review_focus:
  - "confirm the O1 scoping finding is actually true at your own commit -- re-run the greps yourself, don't trust this handoff's claim uncritically (this wave has a running theme of backlog filings whose illustrative examples didn't quite match shipped reality)"
  - "the new hostname: line uses the EXACT SAME expression as the existing container_name: line -- confirm by reading both lines side by side in the diff, not just that 'a hostname line exists'"
  - "the CONFIG.md worked example is genuinely unambiguous after this package, not just prose asserting it is"
---

# ciu-P30 — CIU-48/CIU-49: cockpit-alias guidance (scaffold + docs)

## Context to read first

1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-48` and `#CIU-49` (full filed text, already
   available in this session's history — search the file for the headings) — the
   dstdns investigation, the empirical `docker run`/`nslookup` reproduction, and the
   illustrative (but NOT ciu-shipped-template-matching, see O1) proposed contracts.
2. `src/ciu/templates/stack.compose.yml.j2` (the whole file, ~30 lines) — the
   `container_name:` line whose expression you copy for the new `hostname:` line.
3. `src/ciu/templates/global.defaults.toml.j2` — confirm it has no `[topology]`
   block (grounds O1).
4. `docs/CONFIG.md` — search for `[topology.services.<name>]` (S4.16/S7.4) — the
   section you extend.
5. `docs/DESIGN-GUIDE.md` — read the "Why there is no compose project without `-p`
   (CIU-46 cutover)" section in full as your placement/tone precedent for the new
   §3.6 section.
6. `docs/CONSUMERS.md` — skim its existing worked-example style (any section) to
   match its "paste this" tone per AGENTS.md's three-docs convention.
7. `src/ciu/deploy.py:138-153` (`container_name()`, READ-ONLY — forbidden file) —
   confirm the exact derivation you're citing/mirroring in docs and the template.

## Work

1. Confirm and record the O1 scoping finding in the LOG FIRST, before any edit.
2. Add the `hostname:` line to the scaffold template (O2).
3. Add the DESIGN-GUIDE section naming the hazard (O3).
4. Extend CONFIG.md's topology.services section (O4).
5. Add the CONSUMERS.md worked example (O5).
6. Update backlog/CHANGES.md with an accurate, non-overclaiming disposition (O6).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P30-ciu48-49-cockpit-alias-guidance-LOG.md`, commit, exit.
