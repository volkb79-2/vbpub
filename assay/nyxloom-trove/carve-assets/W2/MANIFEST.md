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

It is **not** a fabrication of the kind wave 1's lesson 5 warns about — see the
addendum below for the ciu 7.10.1 re-capture.
`ciu/tests/tests/test_ciu_provenance_json.py:78` asserts that ciu's own
producer's `result.to_dict()` equals this document, and the P01 handoff records
it at `sha256 b3243683e2942301151923da5680152318c88a04c17873f10b74935f85d98761`
— which is the hash measured here, independently, at capture time. So it is
producer-pinned by the tool that emits it.

This is what corrects the carve's claim that B004's PASS branch has no witness
anywhere. It has one; what it does not have is a witness a **live host** can
produce, and that is the half that still blocks adoption.

---

## Addendum — `ciu-provenance-live-mismatch-ciu-7.10.1.json` (Wave D, 2026-09-02)

DA-D7 requires the ciu assets to be RE-CAPTURED before anything is built
against them: ciu has moved 6.0.3 → **7.10.1** and now emits
`schema_version: 2`. Captured here, per this file's own rule — a NEW asset,
never a rewrite of the two above.

| file | sha256 | size | `schema_version` | `overall` |
|---|---|---|---|---|
| `ciu-provenance-live-mismatch-ciu-7.10.1.json` | `e7fa23dab5cc5e08e2d8156c82a16c2f4ed2742c9b9657805c96508ba68765af` | 3512 | **2** | `mismatch` |

Captured by running `ciu provenance --json` in `/workspaces/dstdns` against
the same live instance `dstdns-98535c`. **`ciu version` reports `ciu 7.10.1`;
the verb exits 2, stderr empty.** Read-only, exactly as DA-D7 states.

**The measured delta, which is smaller than DA-D7 assumed and is the finding
generation 5 should build on.** Diffed field-by-field against
`ciu-provenance-live-mismatch.json` (the frozen 6.0.3 / schema-1 capture of
the SAME instance):

- **Top-level keys: identical** — `schema_version`, `instance`,
  `commit_under_test`, `tree_state`, `containers`, `overall`. None added,
  none removed.
- **Per-container keys: identical** — `name`, `image`, `labelled_revision`,
  `status`.
- **Status vocabulary observed: identical** — 20 containers, 16 `unlabelled`
  and 4 `mismatch`, in BOTH documents. **`unlabelled` is therefore NOT new in
  schema 2**; the 6.0.3 asset already carries sixteen of them. The wave
  prompt's phrasing ("`schema_version: 2` **with** an `unlabelled` container
  status") reads as if the status arrived with the bump. It did not.
- **`overall`: `mismatch` in both.**
- The only schema-relevant change is the integer itself, **1 → 2**.
- Everything else that moved is a fact about dstdns and its vendor images,
  not about ciu's document shape: `commit_under_test` `016a2674` →
  `a9b10791`; four `labelled_revision` values changed because the upstream
  images moved (`otel-aggregator`/`otel-collector-node`
  `1400269f…` → `f8178323…`, `skywalking-ui` `9fc54aa1…` → `19ca126d…`);
  six `image` fields flipped between a tag and a bare image id; and one
  container was swapped in dstdns's own compose
  (`dstdns-98535c-webapp-ui` → `dstdns-98535c-pwmcp`).

**What this means for the carve.** W2 §5.4's `schema_version` check (carve
line 230, "`schema_version` is the integer `1`" → `ERROR`/`FORMAT_MISMATCH`)
is the one place the bump bites. Whether the adjudicator should accept `{1,
2}`, accept `2` only, or accept a declared version is a product call the
carve does not answer and this addendum deliberately does not decide.

**Still true, and still the blocker:** `overall` is pinned at `mismatch` on
this host for the reason the 6.0.3 note gives — ciu compares every running
container's `org.opencontainers.image.revision` against its own repository's
short hash, and the labelled ones carry upstream vendor revisions. So the
green-path oracle still has no live-host witness, and
`ciu-provenance-green-reference.json` remains the only real `verified-match`
document available.
