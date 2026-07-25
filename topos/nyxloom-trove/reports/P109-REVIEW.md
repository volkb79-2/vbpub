# P109-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P109-action-safety-coverage
**HEAD:** f315c6d5 (confirmed)
**Verdict:** **APPROVED**

## Preflight

- `pwd`: `/workspaces/vbpub/.worktrees/feat/topos-P109-action-safety-coverage`
- Branch: `feat/topos-P109-action-safety-coverage`
- HEAD: `f315c6d5` — matches required ✓
- `git status`: clean ✓

## Gate evidence (from LOG/REPORT, mechanically consistent)

Two complete xdist runs: **2,088 passed, exit 0** (57s / 64s).

```
kill_ops.py:    ml=[], mb=[], el=63,  eb=24
owner_safety.py: ml=[], mb=[], el=150, eb=64
target_record_sha256=1e8d018816b6b29ff1677dbb0f6882396c48a49af71033111539318174937bee
```

Both runs: literal intersections empty, whole-file records empty, record hash
identical. O1/O3 satisfied. 18 collected cases, 2,070 + 18 = 2,088. ✓

No product source, gate, dependency, pragma, or omit changes. ✓

## Literal line/arc verification (nl -ba against source)

All lines verified against source at HEAD:

| File | Line | Source |
|------|------|--------|
| kill_ops.py | 48 | `raise ValueError("signal must be a non-empty string")` |
| kill_ops.py | 58 | `raise ValueError(` |
| kill_ops.py | 98 | `raise ValueError("target must be a non-empty string")` |
| kill_ops.py | 103 | `raise ValueError(f"invalid kill kind: {kind}")` |
| kill_ops.py | 135 | `raise ValueError(f"invalid kill kind: {kind_str!r}")` |
| kill_ops.py | 235 | `lines.insert(3, "WARNING: KILL signal causes data loss")` |
| owner_safety.py | 117 | `return ""` |
| owner_safety.py | 151 | `return {}` |
| owner_safety.py | 153 | `return None` |
| owner_safety.py | 156 | `return {}` |
| owner_safety.py | 158 | `return None` |
| owner_safety.py | 241 | `step = f"use 'docker compose' in the project..."` |
| owner_safety.py | 254 | `return f"container is owner-managed..."` |
| owner_safety.py | 353 | `from topos.collect.dockerjoin import default_docker_inspect` |
| owner_safety.py | 355 | `return default_docker_inspect(target)` |

All 15 lines and 15 branch pairs covered with exact behavioral assertions.

## Test quality audit (12 functions, 18 collected cases)

| Test | Assertion type |
|------|---------------|
| `test_signal_must_be_a_nonempty_string` [2] | Exact `ValueError` message |
| `test_unknown_sig_prefixed_signal_has_exact_refusal` | Exact `ValueError` message with SIG prefix |
| `test_kill_target_must_be_a_nonempty_string` [2] | Exact `ValueError` message |
| `test_build_kill_argv_rejects_a_nonkill_action_kind` | Exact `ValueError` message with enum repr |
| `test_build_kill_preview_rejects_an_unknown_kind` | Exact `ValueError` message with raw repr |
| `test_render_kill_preview_includes_the_exact_force_warning` | Complete multiline text with KILL warning, argv, force flag |
| `test_nonstring_owner_detail_is_discarded_exactly` | Exact `OwnerDetection(owner="ciu", ambiguous=False, detail="")` |
| `test_config_and_label_shapes_have_exact_verdicts` [5] | Exact `OwnerSafetyRefusal` dataclass or `None` + `calls == ["accepted-target"]` |
| `test_compose_with_no_safe_display_detail_uses_project_instruction` | Exact `OwnerSafetyRefusal` with project-level message |
| `test_defensive_unknown_owner_message_is_exact` | Exact defensive message text |
| `test_nameless_identity_is_matched_by_canonical_id` | Exact `OwnerSafetyRefusal` with canonical-id message |
| `test_default_owner_inspect_delegates_once_and_returns_payload` | `is expected` + `calls == ["accepted-target"]` |

## Specific quality checks

### Direct private-helper test (`_owner_message`)
The `test_defensive_unknown_owner_message_is_exact` test calls `_owner_message`
directly with a `future-owner` detection that cannot be reached through the
public `detect_owner` path. The self-review justifies this as "pins the
helper's explicit fail-closed contract." This is acceptable: `_owner_message`
has a stable string contract (returns exact message text), and testing it
directly proves the defensive fallback without fabricating unreachable state
in the public API. ✓

### Inject-seam delegation proof
The `_evaluate` helper proves every `evaluate` call makes exactly one
`inspect("accepted-target")` call (`assert calls == ["accepted-target"]`).
The `test_default_owner_inspect_delegates_once_and_returns_payload` test
proves the production `default_owner_inspect` delegates to
`default_docker_inspect` exactly once with the correct target. Both satisfy
O2's causal-proof requirement. ✓

### No hollow/weak/duplicate tests
All 18 collected cases assert complete values (exact strings, dataclasses,
or `None`). Zero substring, membership, non-None, range, len-only, or
assertion-free bodies. Zero `pass`. No duplicates — each parametrized case
exercises a distinct input shape. ✓

### No over-mocking
The only mocked object is the downstream Docker-inspect seam
(`default_docker_inspect`), patched via `monkeypatch` to prove delegation
without host access. No target function (`evaluate`, `detect_owner`,
`validate_signal`, `build_kill_*`) is mocked. ✓

### Scope
No product source, gate, dependency, pragma, omit, or non-P109 file changes. ✓
