# P01 — frontmatter parsing + carve lint (rules L1–L12)

> Tier: haiku · Depends-on: none · Read first: handoff/STANDING.md,
> src/nyxloom/frontmatter.py + src/nyxloom/lint.py (docstrings = the
> normative interface), src/nyxloom/types.py, docs/SPEC.md §6,
> tests/conftest.py (SAMPLE_HANDOFF).

## Owned files
- `src/nyxloom/frontmatter.py`, `src/nyxloom/lint.py`
- `tests/test_frontmatter.py`, `tests/test_lint.py`
- `tests/fixtures/handoffs/` (the golden corpus you create)

## Objective
Implement both stub modules exactly per their docstrings. The lint rules
encode real incident classes (P69/P78/P84 in the rule comments) — the golden
corpus is the regression suite for those incidents.

## Golden corpus (create each file; names are contract)
Each `bad-*` fixture is SAMPLE_HANDOFF-shaped with ONE seeded defect; the
test asserts the named rule fires (and, for `error` rules, that
`has_blocking` is True) AND that no OTHER error-severity rule fires on it:

| fixture | must trigger |
| --- | --- |
| `good-sample.md` (conftest SAMPLE_HANDOFF verbatim) | zero error findings |
| `bad-schema.md` (missing `tier` key) | L1 |
| `bad-dangling-dep.md` (`depends_on: [demo-P99-ghost]`) | L1 |
| `bad-bare-pytest.md` (body fence: `pytest tests/ -q`) | L2 |
| `bad-unknown-gate.md` (oracle gate `no-such-gate`) | L2 |
| `bad-trivial-negative.md` (oracle negative `n/a`) | L3 |
| `bad-P78-enumerated-oracle.md` (body: "assert every audit record field"; oracle observable lists `` `outcome` `` and `` `stderr` ``) | L4 (warning) |
| `bad-P69-reviewer-deliverable.md` (body instructs writing to DECISIONS-INBOX.md) | L5 |
| `bad-P84-deferred-oracle.md` (oracle observable: "the reviewer will validate the venv build") | L6 |
| `bad-P69-nonresolving-path.md` (source.ref `../dstdns/docs/spec.md`) | L7 |
| `bad-introspective-escalation.md` (escalate_if: "reflect whether this suits your expertise") | L8 |
| `bad-infra-no-stack.md` (scope.touch `infra/deploy.yml`, no stack mutex) | L9 |
| `bad-oversize.md` (>48k chars body) | L10 error |
| `bad-missing-sections.md` (body lacks worktree/branch/out-of-scope/context mentions) | L11 |
| `bad-missing-blocked.md` (body lacks `BLOCKED:`) | L12 |

L5/L6 negative guard: `good-sample.md`'s body ALREADY contains the string
"BLOCKED:" inside the standing rule sentence — your L5 'do not'/'never'
exemption logic and L12 detection must both pass on it.

## Oracles
1. `parse_handoff(good)` returns a Frontmatter whose `to_dict()` round-trips
   `from_dict` equal; body preserved byte-exact; `body_start_line` correct
   (assert exact int). Negative: file without leading `---` raises
   HandoffParseError naming the path.
2. `schema_errors` on a doc with TWO violations returns BOTH (sorted),
   not just the first.
3. `discover_handoffs(sample_project)` finds `demo-P01-sample.md` and
   excludes a file you create under `handoff/reports/`.
4. `convert_legacy_header` on a v2 blockquote-header document (compose one
   in the test from the format in frontmatter.py's docstring) yields a
   document that `split_frontmatter` parses, maps Tier→tier,
   Depends-on 'P53 (merged)'→['P53'], Serialize-with P02→
   mutexes ['serialize-P02'], and that lint then REJECTS (TODO oracles) —
   parse-not-pass, asserted explicitly.
5. Every golden-corpus row above, as its own test (parametrize).
6. `lint_file` on an unparseable file returns exactly one finding, rule L1,
   severity error.

## Guidance
- yaml.safe_load only. For line attribution keep a key→line map from the raw
  frontmatter text (regex `^<key>:` scan is sufficient — nested keys may
  report the parent key's line).
- L2 fence scan: iterate fenced blocks (``` delimited); "preceded within 3
  lines by a declared gate's argv rendering" means: any line among the 3
  above the fence contains the space-joined argv of some cfg gate. Keep it
  that literal.
- L7: paths to be created are exempt — only `source.ref` and body references
  must resolve; scope.touch entries are checked only for `../`/absolute
  escapes, not existence.
- Findings order: by rule id then line; stable.
