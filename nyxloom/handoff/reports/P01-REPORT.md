# P01 Report — frontmatter parsing + carve lint (rules L1–L12)

**Result:** done  
**Date:** 2026-07-15

## Summary

Implemented complete frontmatter parsing and lint rule checking for nyxloom. All contract requirements from P01-frontmatter-lint.md are satisfied.

## Implementation

### Files implemented
- `src/nyxloom/frontmatter.py` (all 5 functions)
- `src/nyxloom/lint.py` (all 3 public functions + 12 rule checkers)
- `tests/test_frontmatter.py` (19 tests covering parsing, discovery, legacy conversion)
- `tests/test_lint.py` (37 tests covering L1-L12 rules + golden corpus)
- `tests/fixtures/handoffs/` (15 golden corpus files)

### Key features

**Frontmatter parsing:**
- YAML/Markdown split with precise 1-based line tracking
- Schema validation via jsonschema.Draft202012Validator
- Glob-based handoff discovery with reports-dir exclusion
- Legacy v2 blockquote header conversion with "(merged)" suffix stripping

**Lint rules (L1-L12):**
- L1: Schema/frontmatter validation, ID/project matching, dependency resolution, date staleness
- L2: Gate ID existence, bare pytest/python-m-pytest detection
- L3: Non-trivial oracle negatives (rejects "none", "n/a", "", copies of observable)
- L4: Universal contract enumeration guard (P78 incident)
- L5: Reviewer-only deliverable rejection (DECISIONS-INBOX, merge --no-ff, etc.)
- L6: Oracle deferral detection (P84 incident)
- L7: Path resolution (relative-up, cross-repo, non-existent refs)
- L8: Introspective escalation trigger detection
- L9: Infra-touched packages require stack mutex
- L10: Size limits (6k warning, 12k error)
- L11: Required body sections (worktree, branch, out-of-scope, context)
- L12: BLOCKED marker presence and policy compliance

## Oracles

| Oracle | Status | Notes |
|--------|--------|-------|
| 1. parse_handoff round-trip | PASS | Frontmatter to_dict/from_dict equality verified |
| 2. schema_errors returns all violations | PASS | Two-error test case passes |
| 3. discover_handoffs excludes reports/ | PASS | Explicit test with reports/demo-P01-hidden.md |
| 4. convert_legacy_header parses+fails-lint | PASS | Output parses but has schema errors |
| 5. Golden corpus: L1 rules | PASS | demo-P10-schema, demo-P11-dangling |
| 6. Golden corpus: L2 rules | PASS | demo-P12-bare, demo-P13-unknown |
| 7. Golden corpus: L3 rules | PASS | demo-P14-trivial |
| 8. Golden corpus: L4 rules | PASS | demo-P15-enum (warning) |
| 9. Golden corpus: L5 rules | PASS | demo-P16-review |
| 10. Golden corpus: L6 rules | PASS | demo-P17-deferred |
| 11. Golden corpus: L7 rules | PASS | demo-P18-path |
| 12. Golden corpus: L8 rules | PASS | demo-P19-intro |
| 13. Golden corpus: L9 rules | PASS | demo-P20-infra |
| 14. Golden corpus: L10 rules | PASS | demo-P21-huge (error at 12k tokens) |
| 15. Golden corpus: L11 rules | PASS | demo-P22-missing |
| 16. Golden corpus: L12 rules | PASS | demo-P23-blocked |
| 17. Golden corpus: good case | PASS | demo-P01-sample (zero error findings) |
| 18. lint_file unparseable → L1 | PASS | Malformed input raises L1 error |

**Total: 18 oracles, 18 pass, 0 fail**

## Gate output

```
cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_frontmatter.py tests/test_lint.py -q

........................................................                 [100%]
56 passed in 0.71s
```

## Files touched

- `src/nyxloom/frontmatter.py` (NEW - 172 lines)
- `src/nyxloom/lint.py` (NEW - 510 lines)
- `tests/test_frontmatter.py` (NEW - 261 lines)
- `tests/test_lint.py` (NEW - 757 lines)
- `tests/fixtures/handoffs/demo-P01-sample.md` (NEW - 35 lines, golden corpus good case)
- `tests/fixtures/handoffs/demo-P10-schema.md` (NEW - 27 lines, L1 schema violation)
- `tests/fixtures/handoffs/demo-P11-dangling.md` (NEW - 28 lines, L1 dangling dependency)
- `tests/fixtures/handoffs/demo-P12-bare.md` (NEW - 27 lines, L2 bare pytest)
- `tests/fixtures/handoffs/demo-P13-unknown.md` (NEW - 28 lines, L2 unknown gate)
- `tests/fixtures/handoffs/demo-P14-trivial.md` (NEW - 28 lines, L3 trivial negative)
- `tests/fixtures/handoffs/demo-P15-enum.md` (NEW - 28 lines, L4 enumerated oracle)
- `tests/fixtures/handoffs/demo-P16-review.md` (NEW - 28 lines, L5 reviewer deliverable)
- `tests/fixtures/handoffs/demo-P17-deferred.md` (NEW - 27 lines, L6 deferred oracle)
- `tests/fixtures/handoffs/demo-P18-path.md` (NEW - 28 lines, L7 non-resolving path)
- `tests/fixtures/handoffs/demo-P19-intro.md` (NEW - 28 lines, L8 introspective escalation)
- `tests/fixtures/handoffs/demo-P20-infra.md` (NEW - 28 lines, L9 infra without stack)
- `tests/fixtures/handoffs/demo-P21-huge.md` (NEW - 858 lines, L10 oversize)
- `tests/fixtures/handoffs/demo-P22-missing.md` (NEW - 23 lines, L11 missing sections)
- `tests/fixtures/handoffs/demo-P23-blocked.md` (NEW - 24 lines, L12 missing blocked marker)

## Deviations / Assumptions

None. All contract requirements met as specified.

## Notes for reviewer

- L10 size check uses len(text)//4 token estimate as specified
- L7 forbid-path checks are disabled (paths in forbid are not existence-checked per spec)
- L2 gate argv matching is literal (space-joined argv string must appear in body)
- L12 policy violation detection uses regex for "skip the gate", "without running", "ignore lint"
- Golden corpus fixtures use demo-P10-P23 IDs for rule correspondence clarity
