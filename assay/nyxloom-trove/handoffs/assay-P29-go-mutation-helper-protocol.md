---
schema_version: 1
id: assay-P29-go-mutation-helper-protocol
project: assay
title: "A bounded Go helper emits byte-exact mutation sites, never file copies"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P28-real-go-r1-srdm-qualification]
session: fresh
scope:
  touch: ["cmd/assay-go-helper/**", "gate/go/**", "src/assay/adapters/go.py", "tests/fixtures/go/**", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/cli.py", "src/assay/registry.py", "src/assay/config.py", "src/assay/canary.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/schemas", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "The helper accepts one closed bounded request and returns one closed success-or-error frame containing at most max_sites small byte-span descriptors; neither response nor Python collection contains one full source copy per candidate"
    negative: "A 64 MiB source with 10,001 sites attempts to serialize or retain 10,001 mutated_text values"
    gate: tester-unified
  - id: O2
    observable: "Go parser/token positions produce deterministic changed-line sites for compare-swap, boolop-swap and bool-const-flip; every one-span splice preserves all other bytes and parses/formats as valid Go"
    negative: "Regex matching mutates a comment/string, character offsets corrupt UTF-8, or whole-file gofmt changes unrelated bytes"
    gate: tester-unified
  - id: O3
    observable: "Go implements P21's already-frozen selected-operator/max+1 MutationSite contract exactly, without a private API or any change to Python/core behavior"
    negative: "Go gets a parallel unbounded shape, filters only after full discovery, or edits the frozen Python/core contract to fit the helper"
    gate: tester-unified
  - id: O4
    observable: "Unknown/duplicate fields, invalid base64/UTF-8/Go, invalid lines/operators/limits, oversize input/response, helper nonzero, and malformed frames become typed discovery failures and never NO_MUTANTS"
    negative: "Malformed helper output is accepted as an empty site list or an unbounded stdin read"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a valid Go operator span cannot be obtained from go/parser plus go/token without text guessing"
  - "P21's common MutationSite contract cannot express a syntax-derived Go site without revision"
mutexes: []
---

# P29 — Go mutation-helper protocol

The claim to attack: **Assay can discover bounded, valid Go mutation sites
without embedding Go syntax in its core or multiplying a large source file by
the candidate ceiling in memory.**

## Dispatch contract

- Contract class: **2b — complex solution-bearing execution** (`implement-4`
  when deployed; frontmatter names today's live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Opus xhigh implementer → a fresh
  Opus xhigh independent reviewer session**.
- Readiness: **PROVISIONAL until P28 merges, then JIT-FREEZE REQUIRED.** Sol must
  land the compiling Go helper/adapter skeleton, protocol goldens, UTF-8 span fixtures,
  and the bounded-memory controlled attack before implementation.
- Implementer freedom: private Go visitor and buffering decomposition only.
  P21's common site type/method, framing, codes, bounds, sort key, byte
  units, operators, and no-full-mutants discovery rule are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P29-go-mutation-helper-protocol`
on branch `feat/assay-P29-go-mutation-helper-protocol`.

## Context to read first

1. P21's final `MutantOutcome` byte-span/replacement-hash identity and cap.
2. P23's mutation collection/execution seam and P27's effective-PATH Go adapter
   construction/image. P30, not this package, enables Go R2 or runs mutants.
3. P21's final `MutationSite`, `generate_mutation_sites`, collection bounds,
   Python parity manifests, and direct tests; plus Go's current `UNSUPPORTED`.
4. Go `parser`, `ast`, `token`, and `format` from P27's pinned toolchain.
5. Decisions A-112–A-115/A-157–A-160 and the post-series review's warning that
   `64 MiB × 10,001 mutated_text` is not a bound.

## Implementation packet (normative)

### Frozen in-process contract consumed from P21

Implement exactly P21's landed `generate_mutation_sites(text, lines, *,
operators, limit)` and `MutationSite` shape. Do not edit `adapters/base.py`,
`adapters/python.py`, or `mutation.py`. Go returns at most `limit` descriptors,
already unique and sorted by P21's byte-based key. It applies the selected
operator set during AST traversal, not after producing an unbounded catalogue.
No `mutated_text` appears on the wire or in discovery. P30 applies one site to
one fresh P22 snapshot only when the candidate is submitted.

P21 leaves Go's method on the common union but unconditionally returns the
adapter-wide `"UNSUPPORTED"` marker, which renders payload-free
`INCONCLUSIVE/MUTATION_UNSUPPORTED` (A-183). P29 replaces that body with helper
sites/errors; it does not reinterpret capability absence as an empty success.
P27's effective-PATH preflight owns a genuinely absent helper before this
method is called, and P30 alone registers the completed Go R2 path.

### Go helper wire grammar

The offline `assay-go-helper` reads one bounded JSON object from stdin and writes
one JSON frame to stdout. Stdout contains nothing else; diagnostics go to stderr.
All objects are closed and integers reject booleans/floats.

```json
{"schema_version":1,"source_b64":"<base64 exact UTF-8>",
 "changed_lines":[7,11],
 "operators":["compare-swap","boolop-swap","bool-const-flip"],
 "max_sites":51}
```

```json
{"schema_version":1,"ok":true,"sites":[
 {"start_byte":83,"end_byte":84,"replacement_b64":"PD0=","line":7,
  "operator":"compare-swap","description":"< to <="}
]}
```

```json
{"schema_version":1,"ok":false,
 "error":{"code":"INVALID_GO","message":"<bounded stable diagnosis>"}}
```

Error codes are exactly `INVALID_REQUEST`, `INVALID_UTF8`, `INVALID_GO`,
`LIMIT_EXCEEDED`, and `INTERNAL`. Valid domain failures return the error frame;
failure to launch, nonzero exit, truncated output, invalid JSON, wrong union
branch, or unknown field is a Python-side helper-boundary failure. None maps to
an empty success.

Fixed bounds:

- decoded source: `64 * 1024 * 1024` bytes, no NUL;
- raw request: `4 * ceil(MAX_SOURCE_BYTES / 3) + 1024 * 1024` bytes;
- changed lines: sorted/unique/positive, at most 1,000,000 entries;
- operators: order-preserving subset of the four v4 names, no duplicates;
- `max_sites`: `1..10_001`;
- response: `64 * 1024 + max_sites * 512` bytes; descriptions are selected
  from the fixed replacement table, never source-derived; and
- helper applies a bounded reader (`limit+1`) before JSON decode and Python does
  the same before response decode. Limit failure is loud, never truncation.

The response contains descriptors only—never original or mutated source.

### Go site semantics

| operator | token replacement |
|---|---|
| `compare-swap` | `==↔!=`, `<↔<=`, `>↔>=` |
| `boolop-swap` | `&&↔||` |
| `bool-const-flip` | `true↔false` |
| `falsy-swap` | no Go site; never reinterpret nil/zero |

Use `go/parser`, `go/ast`, and `go/token` positions to locate only the operator
or literal token. The token's own start line must be in `changed_lines`. Ignore
lookalikes in comments/strings, `_test.go` targets, and canonical generated
files. For each descriptor, apply its single splice to original bytes, parse the
full result, and call `format.Source` only as a validity check whose output is
discarded. All non-span bytes must remain equal. Stop at `max_sites` in the
fixed global sort order and return the max+1 sentinel honestly.

### Prepared proof and traceability

The JIT carve commits valid/error wire goldens; two same-line operators; a
continuation-line token; multibyte text before the token; comment/string
lookalikes; generated/test/invalid sources; every limit boundary; a 64 MiB sparse
source with 10,001 sites; and P21's unchanged Python candidate manifests. Memory is
proved structurally (response contains only bounded descriptors and collection
holds sites). Peak-memory may be recorded as non-gating diagnostic evidence;
no machine-dependent RSS threshold decides the test.

| work | owner | oracle | controlled break |
|---|---|---|---|
| common site conformance/no core drift | Go adapter + frozen P23 tests | O3 | parallel API, filter late, or alter core/Python |
| request/response/error boundary | helper/Go adapter | O1/O4 | unbounded read or empty-on-error |
| Go AST/token spans | helper | O2 | regex, character offsets, or use formatted output |
| aggregate bounds | helper/mutation | O1/O3/O4 | retain full copies or discover beyond sentinel |

## Work

1. Add the offline Go helper and build it into P27's Assay-owned image without
   changing shared images or the registered gate.
2. Implement the closed wire union, bounded reads/writes, stable errors, parser/
   token site discovery, fixed operators, validation-only formatting, and sort.
3. Make `GoAdapter` call the helper through an injected/resolved executable and
   validate every field again before returning common sites. It still advertises
   only R1 in the registry; P30 advances capability.
4. Add all prepared fixtures and controlled breaks, including combined UTF-8 +
   same-line + max+1 and malformed-frame + oversized-source attacks.
5. Prove P21's Python/core files and candidate manifests remain unchanged.
6. Run the real gate and record wire examples, binary hash, candidate manifests,
   optional non-gating peak-memory evidence, and controlled-break counts.

## Test constraints copied from AUTHORING.md §3b

- No sleeps, elapsed-time verdicts, or tiny timeouts; use deterministic input and
  real process completion.
- Restore environment and isolate helper processes/files per test.
- Assert bytes, spans, order, frames, bounds and exact Python parity—not call
  counts or “did not raise”.
- No weakened assertions or coverage-evasion pragmas.
- No network/ambient tool selection/current files; toolchain, executable, input,
  process and filesystem are explicit.

## Scope / forbid

This package discovers and validates mutation sites only. It does not enable Go
R2, classify `go test`, change v4, execute a mutant, edit canary/config/CLI/
registry, or modify consumer projects. P30 owns real R2 integration.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do not
improvise a workaround.
