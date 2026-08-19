# W2 (B004) — frozen carve assets

Captured 2026-08-17 by the controller, on branch `assay-B004-provenance-verified`,
as the evidence behind **A-275** and **A-276**. These are evidence: they are never
edited to make a check pass. If ciu's behaviour changes, capture a NEW asset and
record the change — do not rewrite these.

Both documents are `ciu provenance --json` output at **ciu 6.0.3**.

| file | sha256 | `overall` | what it proves |
|---|---|---|---|
| `ciu-provenance-live-mismatch.json` | `78a433755ff569a91d1afeee6f552392b51401f97196ace1a13e4368b7aa3cce` | `mismatch` | A-275(b): the green verdict is unreachable on a live host |
| `ciu-provenance-green-reference.json` | `b3243683e2942301151923da5680152318c88a04c17873f10b74935f85d98761` | `verified-match` | A-275's correction: a real green document exists, so a green-path oracle need not fabricate one |

## `ciu-provenance-live-mismatch.json`

Captured by running `ciu provenance --json` in `/workspaces/dstdns` against the
live instance `dstdns-98535c`. **Exit status 2**, not 0 — recorded because the
carve brief originally reported exit 0, which was `head`'s status read through a
pipe. The exit code is not the verdict and must not be used as one.

20 containers: 16 `unlabelled`, 4 `mismatch`. The four labelled ones carry
**upstream vendor** revisions — `otel-aggregator` and `otel-collector-node` at
`1400269f8ace841f8d0492f4f9c6c7f305f95268`, `postgres` at `refs/heads/master`,
`skywalking-ui` at `9fc54aa114c2b00ac9af791d14f2b5ae009bacc5` — none of which is
a dstdns commit. ciu compares *every* running container's
`org.opencontainers.image.revision` against its own repository's short hash, so
`overall` is pinned at `mismatch` regardless of what dstdns builds.

`tree_state` was `clean` at capture, but note that three invocations minutes
apart during the carve returned three different documents: dstdns has a
concurrent committer, and `containers: null` and `not-verified-dirty` were both
observed spontaneously. A consumer must treat this document as a point-in-time
reading.

## `ciu-provenance-green-reference.json`

Copied verbatim from
`ciu/nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-verified-match.json`.

It is **not** a fabrication of the kind wave 1's lesson 5 warns about.
`ciu/tests/tests/test_ciu_provenance_json.py:78` asserts that ciu's own
producer's `result.to_dict()` equals this document, and the P01 handoff records
it at `sha256 b3243683e2942301151923da5680152318c88a04c17873f10b74935f85d98761`
— which is the hash measured here, independently, at capture time. So it is
producer-pinned by the tool that emits it.

This is what corrects the carve's claim that B004's PASS branch has no witness
anywhere. It has one; what it does not have is a witness a **live host** can
produce, and that is the half that still blocks adoption.
