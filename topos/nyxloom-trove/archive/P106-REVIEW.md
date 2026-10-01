# P106-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P106-daemon-deploy-coverage
**HEAD:** 0db2a364 (confirmed)
**Verdict:** **APPROVED**

## Independent gate verification (two xdist runs)

Run 1: **2052 passed, exit 0** in 65s. Run 2: **2052 passed, exit 0** in 65s.

```
lit_lines=[] lit_arcs=[]
whole_lines=[] whole_br=[]
exec_lines=195 exec_br=20
PASS: whole-file 100%
```

**PARITY CONFIRMED** — identical records both runs. O1/O4 satisfied.
6 functions = 6 cases, 2052 total (2046 + 6). `git diff --check`: clean.

## Literal line/arc verification

All 15 lines and 5 branch pairs verified against source:

| Line | Source |
|------|--------|
| 67 | `except OSError as exc:` |
| 68 | `checks.append(` |
| 82 | `checks.append(` |
| 102 | world-writable message |
| 103 | world-writable remedy |
| 141 | non-member message |
| 180 | `checks.append(` |
| 210 | `except OSError as exc:` |
| 211 | `can_connect = False` |
| 212 | `checks.append(` |
| 328 | `except KeyError:` (uid) |
| 329 | `return str(uid)` |
| 335 | `except KeyError:` (gid) |
| 336 | `return str(gid)` |
| 344 | `json.dumps(..., sort_keys=True)` |

| Arc | Direction | Test |
|-----|-----------|------|
| 81->82 | not-directory -> check | Regular-file preflight test |
| 101->102 | world-writable -> check | World-writable preflight test |
| 134->141 | non-member -> check | Regular-file preflight test (user not in group) |
| 179->180 | non-socket -> check | Regular-file preflight test |
| 558->560 | command is None -> skip | Install-plan text test with `command=None` |

## Test quality audit (all 6 exact structural equality)

| Test | Assertion | Causal proof |
|------|-----------|-------------|
| Unavailable runtime/group/socket | Complete `DaemonPreflightReport` with 3 `PreflightCheck` entries + `path_stat.call_args_list` | `Path.stat` raised OSError, `grp.getgrnam` raised KeyError |
| Regular file runtime + socket | Complete report with 3 checks (not-directory, non-member, non-socket) + `path_stat.call_args_list` | `Path.stat` returned `S_IFREG`, `_user_label`/`_group_label` injected, `grp.getgrnam` returned group |
| World-writable + connect error | Complete report with 4 checks + `can_connect.assert_called_once_with` + `path_stat.call_args_list` | `Path.stat` returned `S_IFDIR|0o777` + `S_IFSOCK|0o660`, `_can_connect` raised OSError |
| Identity label fallbacks | Exact numeric strings "4242"/"4343" + `get_user.assert_called_once_with` / `get_group.assert_called_once_with` | `pwd.getpwuid`/`grp.getgrgid` raised KeyError |
| Canonical preflight JSON | `render_preflight_json` == `json.dumps(expected, sort_keys=True)` — full canonical JSON | Real `DaemonPreflightReport` constructed |
| Install plan text | Exact multiline text including "note: No command is required" | `DaemonInstallPlan` with `command=None`, `warning="No command is required"` |

All 6 tests use complete dataclass/dict/text equality. Zero substring,
membership, non-None, range, len-only, or assertion-free bodies.

## Specific concern verification

### Global Path.stat patch isolation
All three preflight tests use `with patch.object(Path, "stat", ...)` inside
`with` blocks. The patch is context-managed and restored on exit. Verified:
no global `Path.stat` leakage across tests. ✓

### Arc 558->560 (no-command)
Line 558: `if step.command:` — False branch (command is None/falsy) skips
line 559. The install-plan test has `command=None` → arc 558->560 exercised.
Rendered text: "note: No command is required" (from `step.warning` at line
560). ✓

### Test file size (470 lines)
Six tests produce 470 lines because each preflight test asserts a complete
`DaemonPreflightReport` dataclass with multiple `PreflightCheck` entries
and 20+ fields. The JSON test asserts a complete canonical object. No
repetition — each test exercises distinct code paths (OSError, regular
file, world-writable, identity fallback, JSON, install text). ✓

### Scope
No product source, status, CLI, gate, dependency, pragma, or omit changes. ✓
No host-proc, sleep, random, fixed `/tmp`, live passwd/group, or host-service
reliance. ✓
