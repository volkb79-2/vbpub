---
kind: backlog-entry
schema_version: 1
id: NL-2
title: "AUTHORING.md's tier worked example ('implement-2') and prose are unroutable against live routes.toml — no mechanical check exists"
status: fixed
type: "bugfix"
severity: "medium"
provenance: "dstdns P130/P132/P133 carve-reviews, decisions.md D-187"
filed_date: "2026-08-25"
closed_date: "2026-09-03"
closed_reason: "Shipped as nyxloom-P100, merged 480ef39f. AUTHORING.md stale tier example fixed; L14 lint rule validates fm.tier against live Routes.load().tiers. Post-merge gate PASS."
---

## Observed mechanism

`AUTHORING.md`'s frontmatter worked example (Level 2 section) prints:

```yaml
tier: implement-2                      # live capability band, not a model name
```

and its own prose states: "Only `implement-1` and `implement-2` are deployed
today... `tier` drives the routing matrix." A carver following this guidance
literally stamps `tier: implement-2` into a handoff's frontmatter — but
`implement-2` is not a key in the live `~/.local/state/nyxloom/routes.toml`.
Confirmed keys there: `flash-high`, `flash-max`, `terra-med`, `luna-high`,
`sonnet5-high`, `frontier-review`, `haiku-low`, `free-high`. The doctrine's
own worked example is unroutable.

## Reproduction (three independent, source-grounded occurrences, same session)

All three in dstdns, 2026-08-25, three different fresh Opus carvers, no
communication between them:

1. `dstdns-P130-service-identity.md` shipped `tier: sonnet-xhigh` (a
   provider+effort string, not a routes.toml key at all — a different wrong
   guess, but same root cause: no mechanical check existed).
2. `dstdns-P133-api-contract-freeze.md` shipped `tier: opus-xhigh` (same
   pattern).
3. `dstdns-P132-worker-io-execution-repair.md` shipped `tier: implement-2` —
   this one copied AUTHORING.md's OWN printed example verbatim, and it's
   also wrong.

Each was only caught by a fresh adversarial carve-reviewer manually checking
`routes.toml`, once per package — a human/review-catch cost paid three times
for the identical defect class in one session.

## Why nyxloom owns this, not the consumer repo

`AUTHORING.md` is canonical doctrine that ships with the nyxloom product
(dstdns's own `CLAUDE.md` explicitly does not copy it locally, precisely so
it never goes stale independently) — but the doctrine text itself is stale
relative to the live `routes.toml` it's supposed to route into. No amount of
consumer-side carve discipline fixes a doctrine document giving a concretely
wrong worked example. This is a nyxloom-side documentation/validation gap.

## Proposed contract

Two independent, complementary fixes:

1. **Fix the stale doctrine text.** `AUTHORING.md`'s Level 2 worked example
   and its "Only implement-1 and implement-2 are deployed today" prose need
   to match whatever the CURRENT live `routes.toml` keys actually are (or
   name a stable indirection that doesn't drift — e.g. point at
   `routes.toml` itself rather than embedding a literal example that can go
   stale independently).
2. **Structural fix (the reviewer's own suggestion, endorsed here): a
   `nyxloom lint` rule validating a handoff's `tier` value against the live
   `routes.toml` keys**, refusing loudly on an unroutable value rather than
   accepting any string matching a loose pattern. This converts a
   review-catch (expensive, human, non-deterministic — caught 3/3 this
   session only because the reviewer happened to check, not because
   anything forced it) into a mechanical lint failure (cheap, deterministic,
   catches it before a carve is even frozen).

## Behavioral oracle, including a controlled wrong implementation

- **Oracle:** `nyxloom lint <handoff>` on a handoff whose `tier` is not a key
  in the resolved `routes.toml` MUST fail, naming the invalid value and (if
  helpful) the nearest valid keys.
- **Negative (what a broken/incomplete fix looks like):** a lint rule that
  only checks `tier` is a non-empty string, or checks it against a
  hardcoded list of "known good" tier names baked into the lint code itself
  rather than reading the live `routes.toml` — that reintroduces the exact
  same staleness class one level down (the hardcoded list drifts from
  `routes.toml` instead of the doc drifting from it). The fix must READ
  `routes.toml`, not encode a copy of its keys.
- **Positive fixture:** a handoff with `tier: sonnet5-high` (a real key) →
  lint passes on this field.
- **Negative fixtures:** `tier: implement-2`, `tier: sonnet-xhigh`,
  `tier: opus-xhigh` (all three real occurrences from this session) → lint
  fails, naming the bad value.

## Spec section that owns the behavior

`AUTHORING.md` Level 2 ("making it nyxloom-compatible"), the `tier` field
definition and its worked example. `nyxloom lint`'s validation logic (wherever
`handoff-frontmatter.schema.json` is checked) is the mechanical owner of the
structural fix.

## Provenance

dstdns `nyxloom-trove/decisions.md` D-187 (P132 disposition, names all three
occurrences); the three carve-review files:
`dstdns/nyxloom-trove/reviews/dstdns-P130-carve-review-r1.md` (B3),
`dstdns/nyxloom-trove/reviews/dstdns-P133-carve-review-r1.md` (F4),
`dstdns/nyxloom-trove/reviews/dstdns-P132-carve-review-r1.md` (F2).
Commit `dstdns@ba357118` records the disposition.
