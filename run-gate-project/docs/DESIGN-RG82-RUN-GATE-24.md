# DESIGN RG-82: run-gate 24.0.0 on cli-extended

Status: design for implementation (carve), 2026-10-07. Base commit: `vbpub@4670f53a6` (main). Author role: design carver; no code changed.

Operator decisions D1-D6 are final and implemented here, not re-argued:

- D1 full cli-extended adoption, whole grammar re-registered (CX-D1, CX-D12, backlog RG-82, AC-01..AC-25).
- D2 no stub, no zero-install script; `run-gate <lane>` everywhere; the 18 symlinks and dstdns's vendored copy go; RG-51 "zero installs" is withdrawn.
- D3 semver only; `__revision__` dropped.
- D4 per-repo version floor in `run-gate.toml`.
- D5 self-test runs the worktree source, not the installed wheel.
- D6 release 24.0.0 (major).

Operator resolutions of the decision asks (2026-10-07, after the carve; final, folded into the sections below):

- Q1-Q5, Q7, Q8 accepted as recommended in section 8: cli-extended 0.4.0 ships first; `requires-python >=3.13`; an internal error exits 2; `--list` and `--check-env` are removed, not aliased; `requires_run_gate` becomes mandatory in 25.0.0; library controls are documented as inert for lane output; project discovery is CWD-only.
- Q9: the canonical semver version is the only version. Every `revision` key and the cgprofile `--meta` key `run_gate_revision` are renamed to `run_gate_version`; `__revision__` disappears everywhere.
- D7 (supersedes the Q6 recommendation and the original 6.4 install row): the cockpit provides the wheels, exactly as it provides ciu. The mdt devcontainer image bakes cli-extended and run-gate from sha256-pinned releases (new package M). dstdns's `env-workspace-setup-generate.sh` installs nothing for run-gate: its sibling-checkout `pip install` block is commented out and run-gate is required on PATH, as ciu already is. That script never installed ciu (it only checks `command -v ciu`, `:88-90`).
- Integration branch `rg82-run-gate-24` accepted.

Citation convention: `run-gate.py:N` means `run-gate-project/run-gate.py` at the base commit. Statements marked **(inference)** were not directly verified in code. Line numbers move as packages land; implementers re-locate by the quoted symbol.

---

## 0. Findings that shape the design (read first)

1. **cli-extended has no dynamic or catch-all verb.** `CliRegistry.build()` makes `subparsers = parser.add_subparsers(dest="verb", metavar="VERB", required=True)` and one subparser per registered `VerbSpec` (`libraries/cli-extended/src/cli_extended/parser.py:1580-1720`, `CliRegistry.build`, the `add_subparsers` call). `run_cli` only special-cases `help`, `version`, delegates (`parser.py` `run_cli`, `command_form`). An unknown first token is an argparse "invalid choice" error, exit 2. `run-gate <lane>` therefore needs a **library change request (LCR-1)**, section 2.3. This is a separate prerequisite package in `libraries/cli-extended` that ships as cli-extended **0.4.0**.
2. **The library's `doctor` verb returns exit 1 on any failed check** (`doctor.py` `_make_handler`: `return 1 if counts["fail"] else 0`), while run-gate's closed table (RG-78) requires 2 for a failed operation (`run-gate.py:7833`, `return 2 if fail_n else 0`, mapped through `operation_result` at `:322`). It also prints one line per check, but run-gate's doctor prints one line per record. **LCR-2** (section 2.4) adds `fail_exit_code` and per-check detail lines.
3. **The library boundary does not preserve the closed exit table by itself.** `run_cli` maps `KeyboardInterrupt` to 130 and returns a `SystemExit` int unchanged (`parser.py` `run_cli`, the `except KeyboardInterrupt` and `except SystemExit` arms). run-gate today maps both to ERROR 2 (`run-gate.py:12672-12677`, "interrupted-before-a-closed-result"), and SIGTERM raises `SystemExit(128+15)` (`:12618-12622`). So every verb handler must own that mapping itself (section 3).
4. **dstdns already consumes the installed console script**, not a vendored copy: no `run-gate.py` file exists in `/workspaces/dstdns`; its pointers and docs mostly say `run-gate <lane>` (`nyxloom-trove/nyxloom.toml:200,272,340,363,399,444,486`). What remains in dstdns is stale `./run-gate.py` prose and, critically, `env-workspace-setup-generate.sh:95-107`, which runs `pip install <sibling checkout>/run-gate-project`. With a `cli-extended` dependency that line would resolve the unclaimed name from an index (CX-D2 dependency-confusion hole). Resolved by D7: the block is commented out and the mdt image provides both wheels (package M). Section 6.4 and package K.
5. **The estate already installs `run-gate` as a console script in the cockpit** (`~/.venv/bin/run-gate`, 23.10.0; read-only `pip list` in this session, together with cli-extended 0.3.0). `find_project_dir` (`run-gate.py:11395-11404`) resolves the config from "the directory of the invoked script" first, CWD second. After D2 there is no symlink to be "beside", and under `python -m run_gate` `sys.argv[0]` is the module path inside run-gate-project, which holds its own `run-gate.toml`. The "beside the script" rule must be deleted: **CWD only** (section 2.7).
6. **`__revision__` is written into persisted and cross-tool documents**, not only printed (sites in section 4.4). One of them, the `--meta` key `run_gate_revision`, belongs to the cgprofile daemon interface (`nyxloom-trove/RG55-INTERFACE-CONTRACT.md:91`, copy at `scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md:91`). The contract says "Unknown keys are stored verbatim, never rejected", and I found no reader of the key in `scripts/cgroup-profiler/lib/*.py` or `cgprofile.py` (**inference**: grep of `run_gate_revision` shows only tests, fixtures and docs). The rename is therefore safe at runtime.
7. **No estate lane name collides with a new verb name** (grep of every `run-gate*.toml`, `assay.toml` and `/workspaces/dstdns/run-gate.toml` for `run|lanes|list|check-env|skills|help|version|doctor|history|footprint|status|admission|validate-pointers|migrate-modes`: zero hits).
8. **Argparse behaviour was measured**, not assumed. A required `lane` plus `nargs="*"` `lane_args` handles `gate --worktree X -- a b`, `--worktree X gate -- a`, `gate -- --version` correctly on Python 3.13.5 and 3.14.7; `nargs=REMAINDER` does not (it swallows options). 3.11 and 3.12 were not available to test; resolved by Q2: `requires-python >=3.13`.

---

## 1. Current grammar inventory (generated from `_dispatch`, `run-gate.py:11771-12600`)

Parser: `RunGateArgumentParser(add_help=False, prog="run-gate")` (`:11774`; class `:338-351`, whose `error()` prints the headline, usage and `run-gate: error: ...`, then raises `GateError("invalid command line: ...")`). Everything after the first `--` is split off before parsing as `lane_args` (`:11848-11856`). One shared namespace serves all modes; "mode" is selected by the overloaded positional `lane` (`:11775`) plus the flag-style modes `--version`, `--list`, `--check-env`. Refusals are `fail()` = `GateError` = ERROR 2 (`:724-726`).

Closed exit table (RG-78): `VERDICT_EXIT_CODES` PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET_EXCEEDED 4 (`:165-171`); `GateError.exit_code = 2`, `GateNotRunError` 3, `GateInfraError` 2 (`:542-565`); `finish()` returns `result.gate_exit_code` (`:299-325`); non-lane operations map any nonzero handler code to ERROR 2 (`operation_result`, `:322-327`).

### 1.1 Positionals, modes and verbs

| # | Form (today) | Defined at | Behaviour | Exit codes |
|---|---|---|---|---|
| M1 | `run-gate <lane> [flags] [-- ARGS]` | `:11775`, dispatch `:12132-12600` | Resolve project dir (`:11969`), load config, run lane through bare-host/exec/container runner; a `kind="sequence"` lane goes to `_dispatch_sequence` (`:12137`, def `:11614`). Prints `run-gate: lane 'X' verdict V; ...` (`finish`, `:299-325`). | 0 PASS, 1 FAIL, 2 ERROR (config, usage, infra, internal), 3 NOT_RUN (`NOT_RUN_REASONS`, `:172-178`), 4 BUDGET_EXCEEDED |
| M2 | `run-gate` (no lane, no mode) / `--help` / `-h` | `:11843`, `:12043-12046` | Prints `usage(lanes, inherited)` (`:11159`): headline, grammar block, lane table, flag table. **Needs a config**; without one `fail("no run-gate.toml found ...")` (`:11975-11980`). | 0; 2 without config |
| M3 | `--version` | `:11779`, `:11864-11866` | Prints `run-gate rev <__revision__>`; PID-1 guard exempts it (`:12635`). No config read. | 0 |
| M4 | `--list` | `:11781`, `:12122-12124`, `cmd_list` `:11131` | `name<TAB>kind<TAB>environment` per lane to stdout (contract for `tools/buildkite/pipeline.sh:128`), imported-lane notice on stderr. A stray `<lane>` positional is silently ignored. | 0; 2 |
| M5 | `--check-env` | `:11782`, `:12125-12131`, `cmd_check_env` `:5689` | Advisory env-drift sweep plus assay toolchain probe, `--worktree` aware. A stray `<lane>` is silently ignored. | 0; 2 (`broken`, `:5689+`) |
| V1 | `doctor [--worktree P]` | `:12059-12067`, `cmd_doctor` `:7185` | RG-9 preflight; starts short read-only probe containers; prints one `run-gate: doctor: [OK/WARN/FAIL/SKIP/INFO] topic: detail` line per record. | 0; 2 if any FAIL (`:7833`) |
| V2 | `history [LANE] [--worktree P] [--json]` | `:12068-12101`, `cmd_history` `:5057` | Reads the store, runs nothing. `--worktree` is validated (`:11983-11993`). | 0; 2 |
| V3 | `footprint [LANE] [--worktree P] [--json] [--write] [--include-failed] [--lane L ...]` | `:12102-12121`, `cmd_footprint` `:5241` | Distils history to `run-gate.footprint.json`; `--write` refuses when nothing is measured. | 0; 2 |
| V4 | `status [--worktree P] [--json]` | `:12007-12046`, `cmd_status` `:7055` | RG-64/RG-80 inflight, exec locks, admission tickets. Refuses 16 other flags by name (`:12008-12020`). | 0; 2 |
| V5 | `admission set --max-concurrent N [--replace] [--unreadable-policy unbudgeted\|refuse]` / `admission show` | `:12047-12058`, `_dispatch_admission` `:11478` | `set` publishes the daemon-wide gate count cap; `show` prints it. Refuses most run flags (`:12048-12056`). Docker-down is NOT_RUN `environment-down` (`:11498-11500`). | 0; 2; 3 |
| V6 | `validate-pointers FILE [--root DIR]` | `:11965-11982`, `cmd_validate_pointers` `:7834` | RG-2: certify every run-gate pointer in a consumer TOML against SSOT lanes. Needs no project config. | 0; 2 (defects) |
| V7 | `migrate-modes PATH` | `:11880-11919`, `migrate_environment_modes` `:1551` | Mechanically inserts explicit environment `mode`. Accepts only the path (`:11883-11888`). | 0; 2 |

The reserved-verb set is `{"doctor","validate-pointers","history","footprint","migrate-modes","admission","status"}` (`:5788-5789`); `_validate_lane` refuses a lane with such a name (`:904-909`).

### 1.2 Options (every `add_argument`, `:11774-11843`)

| Option | Line | Meaning today | Honoured / refused today |
|---|---|---|---|
| `lane` (nargs `?`) | 11775 | mode or lane selector | all |
| `target` (nargs `?`) | 11776 | verb argument: history/footprint LANE, admission set\|show, validate-pointers FILE, migrate-modes PATH | silently ignored for lane runs, `--list`, `--check-env` |
| `--version` | 11779 | store_true, "print the run-gate revision" | any position |
| `--list` | 11781 | mode | beats lanes |
| `--check-env` | 11782 | mode | beats lanes |
| `--root DIR` | 11785 | validate-pointers only | refused by status (`:12010`); otherwise ignored silently |
| `--worktree P` | 11788 | judged tree | refused by migrate-modes (`:11884`), admission (`:12050`); honoured by run, doctor, check-env, history, footprint, status |
| `--base REF` | 11789 | RG-26 comparison base for a lane that delegates it | run path; refused by status/admission/migrate-modes; silently ignored elsewhere |
| `--lock-wait D` | 11793 | max wait for a lane lock, default `10m` (`:11861`) | refused for None/doctor/history/footprint/validate-pointers/status (`:11961-11964`) |
| `--admission-wait D` | 11795 | RG-80 slot wait | run only (`:11873-11878`) |
| `--override-admission` | 11797 | run beyond the cap, disclosed | run only (`:11873-11878`) |
| `--replace` | 11799 | admission set only | refused outside admission (`:11871-11872`) |
| `--max-concurrent N` | 11801 | admission set only; int | same |
| `--unreadable-policy P` | 11803 | admission set; choices `unbudgeted`, `refuse` | same |
| `--json` | 11805 | machine output | honoured by history/footprint/status and by a lane result; refused for doctor, validate-pointers and None (`:11950-11956`); `--list --json` is not refused (**inference**: the guard only tests `args.lane`) |
| `--write` | 11808 | footprint only | refused elsewhere (`:11957-11960`) |
| `--include-failed` | 11812 | footprint only | refused elsewhere (`:11939`) |
| `--lane L` (dest `footprint_lanes`, append) | 11815 | footprint --write merge | refused elsewhere (`:11941-11947`); requires `--write`; exclusive with the positional |
| `--reuse-from PATH` | 11819 | RG-66 assay R2 reuse | refused for verbs (`:11934-11938`); non-assay lane refused later (`:12159-12163`) |
| `--rejudge ID` (append) | 11822 | RG-66 selective rejudge | same |
| `--rejudge-outcome B` | 11825 | requires `--rejudge` (`:11948-11949`) | same |
| `--allow-dirty` | 11827 | skip the tool's clean-tree refusal | run; refused by status/admission |
| `--allow-main-checkout` | 11828 | sets `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` (`:11858-11862`, const `:373`) | run |
| `--fresh` | 11833 | RG-35 replace the left-over container | refused for verbs (`:11920-11926`) and for non-ephemeral lanes |
| `--dry-run` | 11837 | RG-8 rehearse every preflight, start no judged lane | honoured on the run path (and in sequence member argv, `:11700-11720`); refused by status/admission/migrate-modes; silently ignored by doctor, history, footprint, validate-pointers, list, check-env |
| `--help`, `-h` | 11843 | store_true | M2 |
| `-- ARGS...` | 11848-11856 | selective command-lane arguments (RG-66 request) | needs `accepts_args = true` on a command lane; refused for sequences, assay lanes and verbs (`:11927-11933`, `:12138-12158`) |

### 1.3 Wrapper behaviour around the dispatch (`main`, `:12630-12677`)

- PID-1 guard: when `os.getpid() == 1` and `--version` is absent, print the `--init` remedy and return ERROR (`reason pid1-without-init`) (`:12635-12644`, RG-84).
- SIGTERM handler `_sigterm_as_exit` raises `SystemExit(128+signum)` (`:12618-12622`); restored in `finally`.
- `--json` is detected by `"--json" not in option_argv` before parsing (`:12646+`). When present, stdout is captured; the lane's JSON goes to stdout and ordinary run output to stderr, while history/footprint/status replay their own output.
- `except (SystemExit, KeyboardInterrupt)` becomes `LaneResult("ERROR", reason="interrupted-before-a-closed-result")` (`:12672-12677`).
- `_dispatch` itself: `except GateBudgetExceeded`, `except GateError` (prints `run-gate: <msg>` to stderr, builds ERROR/NOT_RUN), and `except BaseException` that flushes the run record and re-raises (`:12540-12616`).

---

## 2. Target grammar on cli-extended

### 2.1 Library capability audit (what exists, what does not)

Verified in `libraries/cli-extended/src/cli_extended` at 0.3.0:

| Need | Library support | Evidence |
|---|---|---|
| Real verbs, positionals, options, constraints | yes: `VerbSpec`, `ArgumentSpec`, `OptionSpec`, `Requires`, `Conflicts`, `RequiresChoice` | `parser.py:117-420`, `constraints.py` |
| Nested group (`admission set\|show`) | yes: `VerbSpec(delegate=RegisteredCli)` and a child `CliRegistry`; the delegate must use the same `unexpected_exceptions` policy | `parser.py` `build`, `skills.py:708+` |
| `--dry-run` | yes: `VerbSpec(dry_run=True, mutating=True)`; the consumer must NOT declare `--dry-run` itself; `dry_run_help` is a single line | `parser.py:349-380`, `build` |
| `--yes` | added automatically to every `mutating` verb unless `confirmation_required=False` | `parser.py` `confirmation_enabled` |
| `--json`, `--progress`, `--traceback`, `--quiet`, `--debug`, `--verbose`, `--log-level`, `--debug-raw`, `--color/--no-color`, `--help`, `--version` | library-owned; a consumer declaring any is refused (AC-05) | `_common_option_specs`, `parser.py:516-620` |
| Version | `CliIdentity.resolve(name, long_name, command, distribution, version_file, version_probe)`; no literal fallback; all given sources must agree | `identity.py:99-158` |
| Skills / doctor | `register_skills_verbs(registry, package, resource_dir)`, `register_doctor(registry, checks, options=...)` | `skills.py:708`, `doctor.py:205` |
| Test helpers | `invoke_script`, `invoke_module` (mandatory `home=`), `make_invoker`, `assert_cli_contract` | `testing.py:109-340` |
| **Catch-all / default / dynamic verb** | **none** | `build` registers only named subparsers; `run_cli` has no fallback |
| **Doctor exit code other than 1** | **none** | `doctor.py` handler |
| **Per-check detail lines in human doctor output** | **none** (`details` is JSON only) | `doctor.py:149-204` |

### 2.2 Verb and option table (final)

Identity: `CliIdentity.resolve(name="RUN-GATE", long_name="per-project gate entrypoint", command="run-gate", distribution="run-gate")`. Registry: `CliRegistry(identity, prog="run-gate", description=..., unexpected_exceptions="report", identity_banner="never", error_help="usage", logging_logger="run-gate")`. `identity_banner="never"` and `error_help="usage"` keep errors short, as today's `run-gate: <msg>` lines are.

| Verb | Positionals | Options (library-owned in italics) | Flags on the verb | Group |
|---|---|---|---|---|
| **`run`** (fallback verb, LCR-1; bare `run-gate LANE ...` selects it; `run-gate run LANE` is the explicit spelling of the same call; lanes named like a verb are refused at load, so there is no escape case) | `lane` (required), `lane_args` (`nargs="*"`, only legal after `--`; the handler refuses extra tokens given without `--`) | `--worktree PATH`, `--base REF`, `--lock-wait D`, `--admission-wait D`, `--override-admission`, `--allow-dirty`, `--allow-main-checkout`, `--fresh`, `--reuse-from PATH`, `--rejudge ID` (append), `--rejudge-outcome BUCKET`; *`--json`*, *`--dry-run`* | `mutating=True`, `dry_run=True`, `confirmation_required=False`, `include_progress=False`, `expensive=True`; constraint `Requires("--rejudge-outcome", ("--rejudge",), reason)` | MODIFICATION |
| **`list`** | none | *none*; output byte-identical to `--list` today | `include_json=False`, `include_progress=False` | EXPLORATION |
| **`lanes`** (new; the human lane table that `usage()` printed, `:11204-11246`) | none | none | `include_json=False` | EXPLORATION |
| **`check-env`** | none | `--worktree PATH` | `include_json=False` | EXPLORATION |
| **`doctor`** (library verb via `register_doctor`, LCR-2) | none | `--worktree PATH` (through `options=`), library `--check NAME` (repeatable), *`--json`* | exit 2 on any failed check (LCR-2) | MAINTENANCE |
| **`history`** | `lane` (optional) | `--worktree PATH`, *`--json`* | | EXPLORATION |
| **`footprint`** | `lane` (optional) | `--worktree PATH`, `--write`, `--include-failed`, `--lane L` (append, dest `footprint_lanes`), *`--json`* | constraint `Requires("--lane", ("--write",), reason)`, `Requires("--include-failed", ...)` is NOT declared (it is legal without `--write`, `:11939`); `--lane` plus positional stays a handler refusal (positionals cannot be named in a constraint) | MIXED (mutating only with `--write`; `mutating=False`, because the library has no per-option mutation flag. Documented in the verb description.) |
| **`status`** | none | `--worktree PATH`, *`--json`* | | EXPLORATION |
| **`admission`** (delegate group) | child `set`: none; child `show`: none | `set`: `--max-concurrent N` (int, **required**), `--replace`, `--unreadable-policy {unbudgeted,refuse}`; `show`: none | `set`: `mutating=True`, `confirmation_required=False`, `dry_run=False`, reason recorded for AC-12: it is an explicit operator verb that publishes a host object and is already a deliberate, named act; `show`: read-only | MAINTENANCE |
| **`validate-pointers`** | `file` (required) | `--root DIR` | `include_json=False` | MAINTENANCE |
| **`migrate-modes`** | `path` (required) | none | `mutating=True`, `confirmation_required=False`, `dry_run=False` (reason: it rewrites one named file in place and is the documented one-shot migration; the file is under version control) | MAINTENANCE |
| **`skills`** (library delegate: `install`, `uninstall`, `check`, `list`) | per library | per library | AC-19 | MAINTENANCE |
| `help`, `version` | reserved by the library | | | |

Reserved lane names become exactly the verb names: `{"run","list","lanes","check-env","doctor","history","footprint","status","admission","validate-pointers","migrate-modes","skills","help","version"}`. The module constant `_RESERVED_POINTER_VERBS` (`:5788`) is renamed `RESERVED_VERBS`, built from the registry's verb names plus `help`/`version` (single source), and `_validate_lane` (`:904`) refuses all of them.

Global (root-level) consumer options: none. Library controls (`--log-level`, `--quiet`, `--debug`/`--verbose`, `--debug-raw`, `--color`/`--no-color`, `--traceback`) become accepted on every verb. They are inert for lane output (run-gate prints with `print`, not `runtime.output`); this is documented in the verb help text, not hidden (ask Q7).

Handler contract: handlers receive `(args, runtime)`. `args` keeps today's attribute names (`lane`, `target` is replaced by `lane`/`file`/`path`/`action`, `worktree`, `base`, `lock_wait`, `admission_wait`, `override_admission`, `allow_dirty`, `allow_main_checkout`, `fresh`, `reuse_from`, `rejudge`, `rejudge_outcome`, `json`, `dry_run`, `write`, `include_failed`, `footprint_lanes`, `max_concurrent`, `replace`, `unreadable_policy`, `root`, `lane_args`). Defaults are the old defaults. Domain code that takes the old namespace (`_admit_lane`, `_dispatch_sequence`, `preflight_inflight_assay_contract`, `:11535`, `:11614`, `:8512`) keeps working unchanged.

### 2.3 LCR-1: fallback verb (separate prerequisite package in `libraries/cli-extended`)

API sketch:

```python
@dataclass(frozen=True)
class VerbSpec:
    ...
    fallback: bool = False     # at most one per registry; incompatible with delegate=

# RegisteredCli gains:
fallback_verb: str | None
def parse_args(self, argv: Sequence[str] | None = None) -> argparse.Namespace: ...
#   same normalisation as run(); used by tests and by embedders, no handler runs
```

Rules (normative):

1. `CliRegistry.register`: a second `fallback=True` verb raises `ValueError`; `fallback=True` together with `delegate=` raises `ValueError`; the fallback verb must declare at least one `ArgumentSpec` (the name token) or `ValueError`.
2. Normalisation, applied at the top of `run_cli` and in `RegisteredCli.parse_args`, before `parser.parse_args`:
   - `tokens = leading option scan` using the root parser's options **union** the fallback verb's own options (`_leading_option_remainder(argv, parser, extra=fallback_parser)`), so `run-gate --worktree X --base B lane` is recognised. The scan keeps today's rules: `--` or an unknown option returns `None` (no rewrite); `--help`/`--version` first returns `None` (library handles them).
   - If `tokens` is a non-empty list and `tokens[0]` is not a registered verb name (including delegates) and not `help`/`version`, or if `tokens == []` and `argv` is non-empty, the effective argv is `[fallback_name, *argv]`. The verb name goes **first** because options before the verb are not accepted by the root parser; the verb's parser accepts every option in any position.
   - `argv == []` still prints help (exit 0), as now.
3. Help: the catalog lists the fallback verb with the behaviour label `default verb` and the usage line `run-gate [options] LANE ...`; `help run` works.
4. `surface`: the exported route record carries `"fallback": true` **only** for that verb (key absent otherwise), so every existing consumer manifest stays byte-identical (CX-D5); `library_contract` stays 1; manifest schema is unchanged. The spec/review text generator states "a first token that is not a verb selects this verb". `surface check`, `audit` and `assert_cli_contract` keep working (tests in the library cover each).
5. Errors: normalisation never swallows an error. `run-gate docter` becomes `run docter` and fails inside the handler with `unknown lane 'docter'` (exit 2), as today.

Tests required (library, 100% line and branch under its registered `r0-r1`; planted-bug evidence in the REPORT): fallback with leading options, with `=` form, with `--` after the lane, with an unknown leading option (no rewrite, exit 2), a verb name winning over the fallback, `run LANE` explicit, `help run`, `parse_args` equals what `run` executes, a registry with two fallbacks refused, a fallback with `delegate=` refused, surface export has the key only when true (and a consumer without fallback is byte-identical), controlled wrong implementation "rewrite puts the verb after the options" must fail the leading-option test.

### 2.4 LCR-2: doctor exit code and detail lines

```python
def register_doctor(registry, checks, *, description=..., options=(),
                    fail_exit_code: int = 1) -> None   # 1..255, else ValueError

@dataclass(frozen=True)
class CheckResult:
    status: str; summary: str; remedy: str | None = None
    details: Mapping[str, Any] = ...
    lines: tuple[str, ...] = ()      # single-line strings, human output only
```

Human output prints each `lines` entry indented four spaces under the check line (before `remedy`). JSON output adds a `"lines"` key to a check **only when non-empty** (existing doctor JSON for other tools unchanged). Contract version stays 1; ships in cli-extended 0.4.0 together with LCR-1. Library `CHANGES.md` carries both under "Contract and upgrade notes" (hand-written section, not `[Unreleased]`, per KI-30).

Release: after the controller merges, `cmru release` cuts cli-extended **0.4.0** and deploys it (cockpit venv, tester-unified pin, section 6.3). Every later package declares `cli-extended>=0.4.0` in run-gate's `pyproject.toml`.

### 2.5 Old-to-new compatibility position (every break)

D2/D6 allow breaks; each is listed. "Refused" means exit 2 from the library parser (usage error) unless noted.

| Old form | New form | Break? |
|---|---|---|
| `./run-gate.py <lane> ...` | `run-gate <lane> ...` | **break**: the file and 18 symlinks are gone (D2). Invocation by path from another CWD no longer works; run from the project directory (section 2.7). |
| `run-gate <lane> [--worktree P] [--allow-dirty] [--base R] [--fresh] [--lock-wait D] [--admission-wait D] [--override-admission] [--allow-main-checkout] [--reuse-from P] [--rejudge ID] [--rejudge-outcome B] [--dry-run] [--json] [-- ARGS]` | identical | no (same spellings, any order incl. options before the lane) |
| `run-gate <lane> <extra>` (extra positional silently ignored) | refused: "lane arguments go after `--`" | **break** (closes a masked default) |
| `--list` | `list` | **break** |
| `--check-env [--worktree P]` | `check-env [--worktree P]` | **break** |
| `--list` or `--check-env` combined with a stray lane | refused | **break** |
| `--version` printing `run-gate rev 55` | `--version` prints `run-gate 24.0.0` (library `version_line`) | **break** (D3); `version` verb also works |
| `--help`, `-h`; help needed a config and printed the lane table | `--help`/`help`/no args print static grammar help, exit 0 with or without a config; `-h` removed; lane table moves to `lanes` | **break** (`-h`, content) |
| `--json` on `doctor` refused | accepted (library doctor JSON) | additive |
| `--json` honoured silently with `--list` (inference) | refused for `list` | **break** (formalises the stated rule at `:11950-11956`) |
| `--dry-run` silently ignored by doctor/history/footprint/validate-pointers/list/check-env | refused by those verbs (`--dry-run is not supported for verb`) | **break** |
| `--base`, `--allow-dirty`, `--fresh`, `--lock-wait`, `--worktree`... silently ignored by verbs that do not use them | refused where the verb does not declare them | **break** |
| `--root` accepted everywhere | `validate-pointers` only | **break** |
| options before a non-lane verb: `run-gate --worktree P doctor` | refused (the root parser has no `--worktree`); write `doctor --worktree P` | **break** (estate usage found: none) |
| `run-gate --worktree P` with no lane printed usage, exit 0 | usage error "lane is required", exit 2 | **break** |
| usage errors: headline plus usage plus `run-gate: error:` plus `run-gate: invalid command line: ...` | library usage line plus `Run 'run-gate --help' for full help.`, exit 2 | text break, code same |
| PID-1 guard applied before parse, even to `--help` | applies to every verb handler; `--version`, `--help`, `help` work as PID 1 | benign |
| `__revision__` printed in headers (`run-gate: rev 55 \| lane ...`) | `run-gate: 24.0.0 \| lane ...` | **break** (text) |
| persisted JSON `revision` (int), `run_gate_revision`, `owner_version "run-gate-rev-55"` | `run_gate_version` (string), `owner_version "run-gate-24.0.0"` (section 4.4) | **break** (schema key) |
| config accepted without a floor | accepted, `doctor` WARNs (section 4.3) | no |
| library controls `--quiet`, `--debug`, `--traceback`, `--color`, `--log-level` | newly accepted on every verb | additive |
| `skills` verb group, `lanes`, `run` | new | additive |
| Python | `requires-python` raised from `>=3.11` to `>=3.13` (Q2 accepted) | package C sets it in `pyproject.toml`; release note lists it as a break |

### 2.6 Argument-level refusals that remain in handlers

The library constraint model cannot name positionals or lane kinds, so these stay as `fail()` (ERROR 2) in the verb handlers, message text unchanged unless it names an old spelling: `--reuse-from/--rejudge/--rejudge-outcome` with a non-assay lane (`:12159-12163`), `--fresh` on a non-ephemeral lane (`:12175+`), `lane_args` on a sequence, assay or non-`accepts_args` lane (`:12138-12158`), `--lane` together with the positional on `footprint` (`:11945`), `lane_args` given without `--` (new), budget-string validation of `--lock-wait`/`--admission-wait` (`:11862-11870`, `_validate_budget` `:855`).

### 2.7 Project-directory discovery (decision, D2 consequence)

`find_project_dir()` (`:11395-11404`) becomes: return `Path.cwd()` iff `cwd/run-gate.toml` is a file, else `None`. The `Path(sys.argv[0]).absolute().parent` candidate is deleted (no symlink-beside rule; and under `python -m run_gate` it would resolve run-gate-project's own config). Error text: `no run-gate.toml found in the current directory (<cwd>); run run-gate from the project directory that holds it`. `--worktree` keeps its RG-47/RG-65 effective-project-dir logic (`:11994-12006`) unchanged. No ancestor walk, no `--project` option (ask Q8).

---

## 3. Exit-code preservation under `unexpected_exceptions="report"`

### 3.1 Mechanism

All handlers go through ONE wrapper, `closed_result(fn)` in `run_gate_cli.py`, which is today's `main()` body (`:12630-12677`) moved verbatim and made verb-generic:

```
handler(args, runtime):
    1. PID-1 check (not for --version; help/version never reach handlers)  -> finish(LaneResult("ERROR", reason="pid1-without-init", _json=args.json))
    2. install SIGTERM -> SystemExit(128+signum); restore in finally
    3. if args.json (verbs that declare it): capture stdout like today (:12646+), replay rules unchanged
    4. try: result = fn(args)            # fn returns LaneResult, never an int
       except (SystemExit, KeyboardInterrupt): result = LaneResult("ERROR", reason="interrupted-before-a-closed-result")
       except Exception as exc:          # NEW boundary, see 3.3
            if args.traceback: raise     # library prints the stack; process exit 1 (debug only)
            print(f"run-gate: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
            result = LaneResult("ERROR", reason="internal-error")
    5. return finish(result)             # exit 0/1/2/3/4 from the closed table
```

`fn` is the existing per-verb body: `_dispatch` is split (package B) into `verb_run`, `verb_list`, `verb_lanes`, `verb_check_env`, `verb_history`, `verb_footprint`, `verb_status`, `verb_admission_set/show`, `verb_validate_pointers`, `verb_migrate_modes`, each returning `LaneResult`. The existing `except GateBudgetExceeded/GateError/BaseException` arms (`:12540-12616`) stay inside `verb_run` (they build the lane `LaneResult` and flush the run record). `_dispatch(argv, *, _admission_ticket, _admission_managed)` survives as a thin compatibility shim for the 43 in-process test call sites (`run_gate._dispatch`): it calls `cli.parse_args(argv)` (LCR-1) and the matching `verb_*`. The sequence-member recursion (`:11722`) builds a namespace from a factory `lane_request(**overrides)` instead of re-parsing argv.

### 3.2 Library-owned exits (the only documented deviations from the table)

| Source | Exit | Why accepted |
|---|---|---|
| `--help`, `help`, no args, `--version`, `version` | 0 | not operations |
| usage error from the library parser | 2 | equals ERROR |
| `skills install\|uninstall\|check\|list` failure | 1 (library) | library-owned verb; never invoked by daemons; listed as the exemption in the exit-table test |
| `--traceback` on an unexpected exception | 1 (interpreter) | debug aid only |

### 3.3 Why a new `except Exception` boundary maps to 2, not 1

Today an unexpected non-`GateError` exception escapes `_dispatch` and `main` (the arm at `:12672` only catches `SystemExit`/`KeyboardInterrupt`), so Python prints a traceback and exits 1, which collides with "lane FAIL". With `unexpected_exceptions="report"` alone the library would also print `[ERROR]` and exit 1. The closed table says an internal error is ERROR (2); the backlog oracle for RG-82 requires that "a controlled wrong implementation that maps an unexpected exception to exit 1 where the table says another code fails the exit-table test". The wrapper therefore catches `Exception` itself (ask Q3 confirms). The library policy `report` stays configured (it is what provides `--traceback`, AC-10) and is reached only for exceptions raised outside a handler.

### 3.4 Classification of every broad handler site

`except Exception` / `except BaseException` in `run-gate.py` and `run_gate_admission.py` at the base commit. Class K = keep unchanged (deliberate lane-isolation or degrade guard, already emits a visible warning or documented null); R = keep (cleanup then re-raise); T = keep (translate then re-raise); B = boundary (moves into the wrapper). None may be converted to `CliFailure`; none is removed.

| Site | Class | What it guards | Evidence |
|---|---|---|---|
| `run-gate.py:706` | T | `run_lane_with_cgroup_event_guard`: on runner failure, read cgroup events, raise `LaneCgroupEventError from exc` or re-raise | `:700-714` |
| `:2078`, `:2113` | K | `ProfilerClient._ctl` never raises; degrades to "profiling unavailable" (contract Sec 1.3) | docstring text at `:2078-2084` |
| `:2491` | K | basic-path sampler docker-exec: all-null reading, never an exception | `:2491-2498` |
| `:2726`, `:2751` | K | profile identity probes degrade to a reason string | `:2720-2760` |
| `:4115` | K | history recording is best effort; one WARNING line, verdict unchanged (R-04) | `:4108-4125` |
| `:7718` | K | doctor: `docker ps` failure becomes a WARN record | `:7713-7722` |
| `:9709`, `:10836` | K | profiler tick must never break the wait loop; warning recorded once | B1d comments |
| `:9795`, `:10854`, `:11108` | K | profiling cleanup must not pre-empt the lane's exit code; WARNING line | B1d comments |
| `:10504`, `:10720`, `:10964` | K | `start_lane_profiling` call-site defence; mode becomes disabled, warning kept | B1d comments |
| `:11048` | K | `os.wait4` failure on the lane's child degrades profiling mode, `proc.wait()` continues | `:11046-11060` |
| `:1579` | R | atomic config write: unlink the temp file, re-raise | `:1575-1585` |
| `:3561`, `:3571`, `:3637` | R | lock acquisition: close fds, re-raise | lock helpers |
| `:5482` | K | output-tee thread stores the error; `join()` prints a WARNING | `:5482-5490` |
| `:11736` | R | `_dispatch_sequence`: flush the run record, re-raise | `:11736-11738` |
| `:12608` | R then B | `_dispatch`: `flush_run_record(record, error=exc)`; re-raise. The re-raise now lands in the wrapper's arms (SystemExit/KeyboardInterrupt, then Exception) | `:12608-12616` |
| `run_gate_admission.py:1042` | R | release the half-acquired ticket, re-raise | `:1036-1048` |
| `main()` `except (SystemExit, KeyboardInterrupt)` | B | moves into the wrapper (step 4) | `:12672-12677` |

Oracle (package E): an exit-table test that registers a stub verb handler raising each of `RuntimeError`, `KeyboardInterrupt`, `SystemExit(143)`, `GateError`, `GateNotRunError`, `GateBudgetExceeded` and asserts exit 2, 2, 2, 2, 3, 4 and one stderr line; a controlled wrong implementation (wrapper removed, library `report` only) must fail it with exit 1/130/143.

---

## 4. Version and floor design (D3, D4)

### 4.1 Version source (D3)

- Delete `__revision__` (`run-gate.py:17`) and every use (section 4.4). Add `run_gate_identity()` (lazy import, `functools.cache`) and `run_gate_version()` returning `identity.version`; both in `run_gate.py`.
- `run_gate_identity()` = `CliIdentity.resolve(name="RUN-GATE", long_name="per-project gate entrypoint", command="run-gate", distribution="run-gate")`. No literal fallback (AC-01). A `VersionLookupError` (not installed as a distribution) is reported once as `run-gate: run-gate is not installed as a distribution; install the wheel (python -m pip install --no-index --find-links <dir> run-gate)` with exit 2 (ERROR; exit 3 is NOT_RUN in this tool, unlike cmru).
- The wheel's version keeps coming from setuptools-scm (`pyproject.toml` `[tool.setuptools_scm]`, tag regex `run-gate-v*`); the two-tier text in `pyproject.toml:3-19`, `cmru.toml:21-22`, `SPEC.md:24,131-135,1207-1218,2334-2343`, `CONSUMERS.md:21,1650-1682`, `README.md:7,195-199,492-499,533` is rewritten to one tier.

### 4.2 The floor key (D4)

- **Key:** top-level `requires_run_gate` (snake_case, to match every other key in the file: `schema_version`, `forward_env`, `accepts_args`...). Allowed in `run-gate.toml` and in `run-gate.root.toml` (add to the allowed-key set in `_validate_config`, `run-gate.py:1353`).
- **Value:** a string matching `^>=\s*(\d+)\.(\d+)\.(\d+)$`, e.g. `requires_run_gate = ">=24.0.0"`. Floors only, because the estate policy is `>=` floors with a documented reason. Any other operator or shape (`==`, `~=`, ranges, `24`, pre-release suffix) is a configuration error: `<file>: requires_run_gate must be a single ">=X.Y.Z" floor (got <repr>)`.
- **Comparison:** the running version is `run_gate_version()`; take its leading `^(\d+)\.(\d+)\.(\d+)` as an integer triple and compare tuples. Pre-release, dev and local segments are ignored (`24.0.1.dev3+gabc` and `0.0.0+tester.unified` parse as their triples), no third-party `packaging` dependency. A running version with no leading triple is an error. A floor of `>=24.0.0` is therefore satisfied by `24.0.0.dev1`; this is deliberate leniency for development builds and is stated in the docs.
- **Effective floor:** both the project file's and the nearest root file's floors are checked independently; the refusal names the file that declares the unmet floor.
- **Where checked:** inside `_validate_config(cfg, path, central=...)` (`run-gate.py:1350`), i.e. on every config load: lane runs, `list`, `lanes`, `check-env`, `doctor`, `history`, `footprint`, `status`, `admission`, `validate-pointers` (through its project loads). Not checked by `--version`, `help`, `skills`, `migrate-modes`, or `validate-pointers`'s own file read.
- **Refusal:** `GateError` (ERROR, **exit 2**), one stderr line, no run record, no lock taken:

  `run-gate: <path>: requires_run_gate = ">=24.0.0" but this is run-gate 23.10.0; install run-gate >=24.0.0 (python -m pip install --no-index --find-links <dir holding run_gate-24.0.0-py3-none-any.whl and cli_extended-*.whl> "run-gate>=24.0.0"; release assets: https://github.com/volkb79-2/vbpub/releases/tag/run-gate-v24.0.0)`

  (The tag in the URL is the floor's own `X.Y.Z`.) Exit 2, not 3: NOT_RUN reasons are a closed set about lane preconditions (`NOT_RUN_REASONS`, `:172-178`); a too-old tool is a configuration/environment error, the same class as the unknown-key refusal an old 23.x tool gives on seeing the new key (`_check_keys`, `:753-757`: "unknown key(s) requires_run_gate"). Both old and new tools therefore refuse with 2.
- **Key absent, transition default:** during 24.x an absent key means "no floor" and the run proceeds; `doctor` gains the check `version-floor` that reports WARN `no requires_run_gate declared in <file>; declare ">=24.0.0"` (OK when satisfied, FAIL when unsatisfied). In **25.0.0** an absent key becomes the error `<file>: requires_run_gate is required (declare the minimum run-gate this repo needs)`. Package D/J/K add the key to every estate and dstdns config in the same change, and an estate-sweep test in run-gate-project (`tests/test_estate_floor.py`, scans `../*/run-gate.toml`, `../scripts/*/run-gate.toml`, `../libraries/*/run-gate.toml` and `../run-gate.root.toml` when present) fails on any config without the key, so the transition default is exercised only by external repos.
- **Exemption:** `run-gate-project/run-gate.toml` itself declares no floor: the tool under test defines its own version and the image carries `0.0.0+tester.unified` metadata (section 5), which no floor can satisfy. The estate-sweep test lists this one exemption by name.

### 4.3 Estate floor values

`requires_run_gate = ">=24.0.0"` in every estate `run-gate.toml` (19 files, section 6.1) and in `/workspaces/dstdns/run-gate.toml`. Rationale comment in each: "grammar `run-gate <lane>`; `./run-gate.py` removed in 24.0.0".

### 4.4 Persisted and printed `__revision__` consumers

| Site | Today | New |
|---|---|---|
| `run-gate.py:335` `cli_headline` | `RUN-GATE rev N - per-project gate entrypoint` | deleted (library headline `RUN-GATE 24.0.0 - per-project gate entrypoint`) |
| `:2632` footprint hint JSON / `--meta` | `"run_gate_revision": N` | `"run_gate_version": "24.0.0"`; update both copies of RG55-INTERFACE-CONTRACT (`:91`), `scripts/cgroup-profiler/docs/CONSUMERS.md:256`, `ATTACH-GUIDE.md:233`; cgprofile tests and fixtures that send `run_gate_revision` are renamed to `run_gate_version` too (Q9: no `revision` left in the estate; the daemon stores unknown keys verbatim, so this is a text change only) |
| `:3812` run record | `"revision": N` | `"run_gate_version": "<semver>"`; readers tolerate a missing key |
| `:4225`, `:4251` inflight schema drift messages | `(rev N)` | `(run-gate <semver>)` |
| `:4845` aborted record copy | `pending.get("revision")` | `pending.get("run_gate_version")` (older int-`revision` records yield `None`) |
| `:5082`, `:5091` history JSON/table | `"revision"`, `run-gate rev N - lane invocation history` | `"run_gate_version"`, `run-gate <semver> - lane invocation history` |
| `:5183`, `:5224`, `:5291` footprint manifest | `"revision": N` and the merge key list | `"run_gate_version"`; merge list updated; tracked manifests with `"revision": 42` (`run-gate.footprint.json:110`, others) still load (nothing validates the key), and package J1 renames the key in every tracked manifest so none is left (Q9) |
| `:10458`, `:10772` inflight records | `"revision": N` | `"run_gate_version"` |
| `:10201`, `:10275`, `:10378`, `:10661`, `:10917` run header lines | `run-gate: rev N \| lane ...` | `run-gate: <semver> \| lane ...` |
| `:11522` admission publish | `owner_version=f"run-gate-rev-{N}"` | `f"run-gate-{semver}"` (`run_gate_admission.py:439-467` passes it through as the ticket owner string; no reader found, **inference**) |
| `:11865`, `:11978` | `--version` line, no-config message | library `--version`; message text without revision |
| tests | `run_gate.__revision__` x16 (`tests/test_run_gate.py:668,685,3282,3285,10575,11651,13270,14166,16065,18567,22065,23092,24552`) | `run_gate.run_gate_version()`; fixtures `tests/fixtures/rg55/status-v1.json:22`, `nyxloom-trove/fixtures/rg55/status-v1.json:22` are renamed to `run_gate_version`, and so are the tests that read them (Q9). Afterwards, `grep -rn 'run_gate_revision\|__revision__' --include=*.py --include=*.json --include=*.toml` outside historical reports and archives returns nothing; package C carries this as an oracle |

---

## 5. Self-test mechanics (D5)

### 5.1 Layout (decided)

- `git rm run-gate-project/run_gate.py` (the symlink, `pyproject.toml:42-45`), then `git mv run-gate-project/run-gate.py run-gate-project/run_gate.py`. The 12.7k-line core stays ONE module named `run_gate` so the 25k-line suite's `monkeypatch.setattr(run_gate, ...)` keeps patching the globals the functions read (a package split would break that; **inference** from `tests/conftest.py:30-41` and 300 `run_gate.main([...])` calls).
- New module `run_gate_cli.py`: identity, registry, `closed_result`, verb specs and handlers (section 2-3), `build_cli()` factory, `main()`. `run_gate.main(argv=None)` stays as the console entry `run-gate = "run_gate:main"` and delegates (`from run_gate_cli import main`). `run_gate_admission.py` unchanged apart from the owner string.
- New package `run_gate_skills/` (`__init__.py` empty, `skills/run-gate-cli/SKILL.md` moved from `.claude/skills/run-gate-cli/`): `importlib.resources.files("run_gate_skills")/"skills"`. A plain module cannot carry package data (AC-19), and a data dir next to a top-level module would land in `site-packages/skills` (**inference** from importlib.resources behaviour for non-packages). `register_skills_verbs(registry, package="run_gate_skills")`.
- `pyproject.toml`: `dependencies = ["cli-extended>=0.4.0"]` with the reason comment (CX-D1, LCR-1/2); `py-modules = ["run_gate", "run_gate_cli", "run_gate_admission"]`; `packages = ["run_gate_skills"]` plus `package-data` for `skills/*/*`; `[tool.cli-extended]` table (package G); description text without "zero-install script"; the header comment block replaced (RG-14 two-tier text deleted). No `sys.path` or `PYTHONPATH` onto the library anywhere (AC-25). Delete the `sys.path.insert` at `run-gate.py:150-154` and the "stdlib only - fresh clone, zero installs" comments at `:16` and `:119-122` (RG-51/RG-40 text, withdrawn).

### 5.2 How run-gate's own lanes run source, not the wheel

The launcher for a run-gate-project gate is `python -m run_gate <lane>`. In the tester-unified container `/opt/tester-venv/bin/python -m run_gate` puts the current directory (`run-gate-project`, bind-mounted worktree) first on `sys.path`, so `run_gate`, `run_gate_cli`, `run_gate_admission` come from the worktree, while `cli_extended` and the `run-gate` **distribution metadata** come from the image (section 6.3). This is the ciu/cmru pattern ("Gate runs execute against the BOUND worktree via PYTHONPATH", `tester-unified/Dockerfile:36-38`; cmru lane `PYTHONPATH=src ...`, `cmru/run-gate.toml` coverage lane).

| Item | Change |
|---|---|
| `run-gate-project/run-gate.toml` `[lanes.selftest]` (`:48`) | argv unchanged except the coverage gate call: `... tools/coverage_gate.py --repo . --base main --coverage-json coverage.json --source run_gate.py --source run_gate_cli.py` (no `run-gate-project/` prefix, same reason as the existing comment `:31-42`) |
| `tools/coverage_gate.py` `--source` (`:486`, default `run-gate-project/run-gate.py`) | becomes repeatable (`action="append"`) with default `["run-gate-project/run_gate.py", "run-gate-project/run_gate_cli.py"]`; `tests/test_coverage_gate.py:34-42,279-515` rewritten (they use the literal `run-gate.py`) |
| `[lanes.gate-full]` (`:133`) | `argv = ["bash","-c","/opt/tester-venv/bin/python -m run_gate selftest && /opt/tester-venv/bin/python -m run_gate --base {base} assay-r1 && /opt/tester-venv/bin/python -m run_gate assay-r3"]`. Nested calls must use the worktree source, not the installed tool (**decision**; the lane runs `bare-host`) |
| `[lanes.assay-r1/r2/r3]` | unchanged: `assay.toml` `source_roots = ["."]` already judges every module; `tools/canary-run.sh:114,137,147` retargets `run-gate.py` to `run_gate.py` (the mutated file) |
| `cmru.toml` `[steps.run-tests]` (`:90`) | `argv = ["cmru","tester-gate","--cwd",".","--","/opt/tester-venv/bin/python","-m","run_gate","selftest"]`; the header comments `:56-73` lose "zero installs" and "python3 run-gate.py selftest" and keep the cmru>=6.0.0 / `--init` dependency |
| Release notes env (`cmru.toml:38`) | text rewritten: "stdlib-only, no dependencies" is false (depends on cli-extended); "canonical artifact remains the SCRIPT" deleted |
| `cmru.orchestration.toml` (`:190-192`) | `depends_on = ["cmru", "cli-extended"]`; regenerate the dependency graph block with `cmru dependencies --write` (the pyproject guard requires the edge, `:145-151` comments) |
| tests | `tests/test_run_gate.py:50-62` loader: `_TOOL = RUN_GATE_DIR / "run_gate.py"`, `sys.path.insert` of `RUN_GATE_DIR` stays in the test file only (`# cli-extended: allow-path-assertion` is not needed; it is the project's own dir, not the library). `run_tool` (`:499-501`) and the six `_TOOL_INVOKE` subprocess sites (`:5216,6450,6793,13215,13376,20598`), `:752`, `:4562,4728,4737`, `tests/test_run_gate_admission.py:880-882,945,982,1000` become `invoke_module("run_gate", argv, home=tmp_path/"home", cwd=proj, pythonpath=[RUN_GATE_DIR], ...)` (fixes the RG-83 residue: AC-23). The neutral-symlink indirection (`_TOOL_INVOKE_DIR`) is deleted. `TestWheelPackaging` (`:9262-9420`) is rewritten: the wheel holds `run_gate.py`, `run_gate_cli.py`, `run_gate_admission.py`, `run_gate_skills/**`; the console script runs `list` against a fixture project; the install step uses `--no-index --no-deps` plus the image's `cli_extended`; asserts the dependency `cli-extended>=0.4.0` |

### 5.3 Floor interplay in the self-test

The image's `run-gate` dist is `0.0.0+tester.unified`; its own config has no floor (section 4.2). Floor behaviour is unit-tested in-process by monkeypatching `run_gate.run_gate_version` and with fixture projects; no test depends on the real installed version string (tests read it with `importlib.metadata.version("run-gate")`).

### 5.4 Dev loop before release (no installed 24.x exists yet)

- run-gate-project lanes during packages B-G run with the **installed 23.10.0** launcher (`run-gate --worktree <wt> selftest` from `run-gate-project`), which is valid because run-gate-project's config declares no floor and the lane argv is plain `python -m pytest` inside the image; the image must already carry cli-extended >= 0.4.0 (package D, built with `CLI_EXTENDED_RESOLVE=local` until 0.4.0 is released).
- Other projects' lanes cannot be launched by 23.10.0 once they carry `requires_run_gate` (unknown key). Packages J/K therefore verify with `tester-unified/run --workdir <project> -- /opt/tester-venv/bin/python -m run_gate list|validate-pointers|<lane> --dry-run`, and the controller runs real lanes after 24.0.0 is deployed.
- The integration branch for packages C-L is `rg82-run-gate-24` (like `cli-extended-unified`); `main` is never in a state where configs name `run-gate` but the tool is the old one.

---

## 6. Migration inventory

Searched (read-only): `/workspaces/vbpub` (excluding `.worktrees/` and `.git/`) and `/workspaces/dstdns` for `run-gate.py`, `./run-gate.py`, `run_gate.py`, `__revision__`, `run-gate rev`, `run_gate_revision`, plus the bare console form with the old flags `--list` and `--check-env`. Raw counts: 444 non-historical lines in vbpub, ~50 in dstdns.

Replacement rules (exact strings):

| Rule | Before | After |
|---|---|---|
| R1 | `./run-gate.py <args>` (shell or prose) | `run-gate <args>` |
| R2 | `./run-gate.py --list`, `run-gate --list` | `run-gate list` |
| R3 | `./run-gate.py --check-env [--worktree B]`, `run-gate <lane> --check-env` | `run-gate check-env [--worktree B]` |
| R4 | argv list `["./run-gate.py", "<lane>"]` | `["run-gate", "<lane>"]` |
| R5 | conjunction string `./run-gate.py a && ./run-gate.py --worktree {worktree} --base {base} b` | `run-gate a && run-gate --worktree {worktree} --base {base} b` (inside run-gate-project's own `gate-full`: `/opt/tester-venv/bin/python -m run_gate ...`, section 5.2) |
| R6 | committed symlink | `git rm` |
| R7 | prose naming the file `run-gate.py` (comments "parsed ONLY by run-gate.py") | `run-gate` |
| R8 | `__revision__` / "copy-drift marker" / "zero installs" prose | version floor text (`requires_run_gate`, section 4.2) |
| R9 | `run-gate rev N` expected outputs | `run-gate <semver>` |
| R10 | config gains `requires_run_gate = ">=24.0.0"` as the second top-level key after `schema_version = 1` | |

### 6.1 vbpub: files, lines, kind, replacement

Symlinks (kind: symlink, rule R6; verified by `find -lname '*run-gate*'`): `topos/run-gate.py`, `nyxloom/run-gate.py`, `pwmcp/run-gate.py`, `mattermost-server/run-gate.py`, `modern-debian-tools-python-debug/run-gate.py`, `plesk-mailbox-create/run-gate.py`, `cmru/run-gate.py`, `ciu/run-gate.py`, `shared-ramdisk-depot-manager/run-gate.py`, `scripts/damon-analysis/run-gate.py`, `scripts/debian-install-v2/run-gate.py`, `scripts/cgroup-profiler/run-gate.py`, `scripts/telegram/run-gate.py`, `scripts/gstammtisch-guide/run-gate.py`, `scripts/netcup/run-gate.py`, `assay/run-gate.py`, `libraries/worktree/run-gate.py`, `libraries/cli-extended/run-gate.py` (18), plus `run-gate-project/run_gate.py -> run-gate.py` (replaced by the real file, section 5.1). Also check `.gitignore`/`.dockerignore` entries: `scripts/cgroup-profiler/.dockerignore:21` lists `run-gate.py` (change to nothing; keep `run-gate.toml`).

Config, release steps (kind: config; executable):

| File:line | Today | After |
|---|---|---|
| `nyxloom/cmru.toml:44` | `argv = ["./run-gate.py", "tester-unified"]` | R4 |
| `pwmcp/cmru.toml:37` | `["./run-gate.py", "r1"]` | R4 |
| `modern-debian-tools-python-debug/cmru.toml:66` | `["./run-gate.py", "release"]` | R4; and `scripts/test_release_flow.py:38` asserts `["run-gate", "release"]` |
| `libraries/cli-extended/cmru.toml:50-51` | `["./run-gate.py","r0-r1"]`, `["./run-gate.py","r3"]` | R4 |
| `scripts/cgroup-profiler/cmru.toml:55-56` | same shape | R4 |
| `cmru/cmru.toml:51,53` | `["./run-gate.py","gate-provisional"]` and the comment "Revert argv to [\"./run-gate.py\", \"gate\"]" | R4 both |
| `run-gate-project/cmru.toml:90` | `["cmru","tester-gate","--cwd",".","--","./run-gate.py","selftest"]` | section 5.2 |

Config, daemon pointers (kind: config; certified by `validate-pointers`):

| File:line | After |
|---|---|
| `topos/nyxloom-trove/nyxloom.toml:72,84` | `cd {worktree}/topos && exec run-gate --worktree {worktree} <lane>` (R1) |
| `nyxloom/nyxloom-trove/nyxloom.toml:85` (comments `:70,75`) | R1, R7 |
| `ciu/nyxloom-trove/nyxloom.toml:58` | R1 |
| `assay/nyxloom-trove/nyxloom.toml:178,193,203` | R1 |
| `ciu8/nyxloom-trove/nyxloom.toml:35` (comment) | R1 |

Config, lane conjunctions and comments in `run-gate.toml` (kind: config):

| File:line | After |
|---|---|
| `run-gate-project/run-gate.toml:133` | section 5.2 |
| `scripts/cgroup-profiler/run-gate.toml:106` (comments `:6,12,32,62`) | R5 |
| `scripts/damon-analysis/run-gate.toml:82` (comment `:22`) | R5 |
| `scripts/gstammtisch-guide/run-gate.toml:78` (comment `:24`) | R5 |
| `scripts/debian-install-v2/run-gate.toml:143` (comment `:19`) | R5 |
| `libraries/cli-extended/run-gate.toml:52` | R5 |
| `cmru/run-gate.toml:157,160-162` (comments), `ciu/run-gate.toml:7`, header lines 2 of the other 14 files, `run-gate.root.toml:3-4` | R7 |
| every `run-gate.toml` (19 files, list in section 0.7 counts) | R10 |

Code and tests outside run-gate-project (kind: invocation / test):

| File:line | Today | After |
|---|---|---|
| `cmru/tools/run_release_gate.py:484` | `["./run-gate.py","--worktree",str(repo_root),lane]` with `cwd=repo_root/"cmru"` | `["run-gate","--worktree",str(repo_root),lane]`; update `cmru/tools/check_assay_baseline.py:136` text and the tests that assert the argv (**find by grep `run-gate.py` in `cmru/tests`, `cmru/tools/test_*.py`**: only `test_mutation_campaign.py:74-87`, which is a synthetic symlink fixture in a temp repo and needs NO change) |
| `ciu/tests/tests/test_ciu_worktree.py:956-980` | loads `ciu/run-gate.py` by path (`spec_from_file_location`) to test the real `ciu.worktree-instance.json` reader | `rg = importlib.import_module("run_gate")`; `pytest.skip("run_gate is not installed")` only on `ModuleNotFoundError`; the tester-unified image carries the dist (6.3), so inside the gate it runs. Docstring text updated (R8) |
| `assay/gate/tests/test_cgroup_parent.py:90-104` | regex `run-gate\.py\s+([^&;]*)$`, message "no run-gate.py invocation" | regex `\brun-gate\s+([^&;]*)$`, message "no run-gate invocation"; expected tokens unchanged |
| `assay/tools/self-qualification-gate.sh:71,81`, `assay/gate/tests/test_self_lane.py:386` | text `./run-gate.py tester-unified first` | R1 (the test pins the die message; change both together) |
| `run-gate-project/tools/buildkite/pipeline.sh:14,16,40,45,61,66,70,98,128-129,139,142,155,193,205` and `bk-lane.sh:16` | `[ -x "$project/run-gate.py" ]`, `cd "$project" && ./run-gate.py --list`, emitted step `./run-gate.py <lane>` | `command -v run-gate >/dev/null && [ -f "$project/run-gate.toml" ]`; `(cd "$project" && run-gate list)`; emitted step `run-gate <lane>`; messages R1/R2. `tests/test_buildkite_tools.py:7,49-51,92-162,304-311` stub a `run-gate.py` directory and assert `"./run-gate.py selftest"`: rebuild the stub as a `run-gate` executable on a scratch `PATH` and assert `run-gate selftest` |
| `run-gate-project/tests/test_tester_unified_launcher.py:336` and `tester-unified/README.md:64`, `tester-unified/docs/CONSUMERS.md:9,10,43` | `tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest` | `tester-unified/run --workdir run-gate-project -- /opt/tester-venv/bin/python -m run_gate selftest` (and `--base BASE_SHA assay-r1`, `doctor` likewise); change test and docs together |

Docs (kind: doc; rule per line: R1, R2, R3, R7, R8 as the text dictates; no semantic rewrite beyond the rule unless noted):

- `AGENTS.md:186` (estate rule: "use `./run-gate.py <lane>`" becomes "use `run-gate <lane>` from the project directory; `run-gate list` discovers lanes"), `README.md:45`.
- Per-project READMEs with the canonical sentence "`./run-gate.py` is the canonical test entrypoint": `topos/README.md:504`, `nyxloom/README.md:265,272`, `pwmcp/README.md:309-316`, `ciu/README.md:451`, `cmru/README.md:696`, `assay/README.md:285,997,1090-1141`, `plesk-mailbox-create/README.md:167`, `shared-ramdisk-depot-manager/README.md:199`, `modern-debian-tools-python-debug/README.md:697-712`.
- Docs: `nyxloom/docs/USAGE.md:109`, `nyxloom/docs/CONSUMERS.md:541`, `nyxloom/docs/CLI-REFERENCE.md:100,532,799` (prose naming the file), `nyxloom/docs/plan-next-batches.md:8`, `nyxloom/reference/TESTING-METHODOLOGY.md:493-501,758` (the `run-gate.py:3316` citations describe historical code: leave, only `:758` changes), `pwmcp/docs/CONSUMERS.md:6,13`, `pwmcp/docs/DESIGN-GUIDE.md:3`, `cmru/docs/CONSUMERS.md:684-711`, `cmru/docs/CONTRIBUTING.md:34`, `cmru/docs/DESIGN-GUIDE.md:525,546,594`, `cmru/docs/RELEASE-TRANSACTIONS.md:502`, `cmru/docs/SPEC.md:2919-2953`, `cmru/docs/UPGRADING-6.0.md:10`, `cmru/docs/plan-ki24-get-py-enroll.md:214`, `assay/docs/CONSUMERS.md:71,129,197,421,2346-2352,4384-4401`, `assay/docs/DESIGN-GUIDE.md:2060,2211,2277`, `assay/docs/INTERNAL-CONSUMERS.md:29,45`, `ciu/docs/ARCHITECTURE.md:106`, `ciu/docs/CONSUMERS.md:948`, `libraries/cli-extended/README.md:612`, `libraries/cli-extended/docs/CONSUMERS.md:1360`, `libraries/cli-extended/docs/DESIGN-GUIDE.md:487`, `libraries/worktree/SPEC.md:117-126`, `libraries/worktree/CONSUMERS.md:91`, `scripts/cgroup-profiler/docs/CONSUMERS.md:358-360`, `scripts/cgroup-profiler/assay.toml:48`, `modern-debian-tools-python-debug/docs/CONSUMERS.md:303-312`, `modern-debian-tools-python-debug/host-setup/plan-host-setup-wizard.md:291`, `docs/BOOTSTRAP-FROM-ZERO.md:74`, `tester-unified` docs above.
- Skills: `nyxloom/.claude/skills/nyxloom-merge-p/SKILL.md:69` (`run-gate --list` becomes `run-gate list`); `run-gate-project/.claude/skills/run-gate-cli/SKILL.md:24,45` moves to `run_gate_skills/skills/run-gate-cli/SKILL.md` (AC-19) with R2/R3, the new verbs, and no `__revision__` text.
- Provenance (kind: provenance; **leave untouched**): everything under `*/nyxloom-trove/reports/`, `*/nyxloom-trove/archive/`, `*LOG.md`, `*REPORT.md`, `*-BRIEF-*.md`, `*REVIEW*`, `HANDOFF-P01-build-and-adopt-ciu*.md`, `docs/plan-post-cmru-estate-release.md`, `docs/PLAN-CIU-CMRU-WORKSPACE-INSTANCE.md`, `docs/REVIEW-CIU-CMRU-WORKSPACE-INSTANCE.md`, `ciu/docs/CIU-V8-*.md`, `ciu/CHANGES.md`, `assay/CHANGES.md`, `assay/nyxloom-trove/4-backlog.md` history entries, `scripts/LANE-MATRIX-PROGRESS.md`, `nyxloom/nyxloom-trove/decisions.md`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` history, `run-gate-project/CHANGES.md` history, `ciu8/nyxloom-trove/handoffs/*`. They describe what was true then; a one-line pointer is added to run-gate's CHANGES release note only.

run-gate-project's own files (kind: doc/config/code, package I): `README.md` (34 refs: `:7,33,89-90,195-199,274-307,347,468-533`), `CONSUMERS.md` (65 refs; sections "Step 1 copy or symlink", "drift marker", `--list`, `--check-env`, `admission`, `status`, `history`, `footprint`, `:1650-1682`), `SPEC.md` (`R-00` `:131-135`, `:24`, `:251,375,531,967,1207-1271,2334-2343`; `:375` is a loader snippet using `spec_from_file_location("rg","run-gate.py")` and becomes `import run_gate`), `LANE-AUTHORING.md:288-357`, `REMOTE-LANES-BUILDKITE.md:24-474`, `HANDOFF-P01-build-and-adopt-ciu.md` (provenance, leave), `tools/coverage_gate.py:5,45,415` (docstrings), `assay.toml:11` (comment), `pyproject.toml`, `cmru.toml`, `run-gate.toml` header `:2-47`, `CHANGES.md` (release note goes in the REPORT, `[Unreleased]` stays comment-only, KI-30).

### 6.2 `__revision__` consumers outside run-gate.py

`rg` found none outside run-gate-project and docs: `scripts/cgroup-profiler` tests send `run_gate_revision` ints in fixtures only (section 4.4). dstdns reads it nowhere in code; its docs mention it (section 6.4).

### 6.3 How the wheels reach the images and the cockpit

- **Cockpit venv (`/home/vscode/.venv`)**: wheels are installed with `/home/vscode/.venv/bin/python -m pip install ...` (AGENTS.md:139-148); today `run-gate 23.10.0` and `cli-extended 0.3.0` are there. Deploy order for 24.0.0: (1) cli-extended 0.4.0 wheel, (2) run-gate 24.0.0 wheel, both from the release assets, sha256-verified, `--no-index --find-links <cache>` (CX-D2); the bare `cli-extended` name must never reach an index.
- **tester-unified image** (`tester-unified/Dockerfile`): `cli-extended` comes from the pinned release (`ARG CLI_EXTENDED_WHEEL_URL`/`SHA256`, `:109-136`, `fetch-cli-extended.py`, `--min-version 0.3.0`); `ciu`, `cmru`, `assay`, `topos`, `nyxloom` are COPYed (`:41-45`) and cmru is built offline as `0.0.0+tester.unified` (`:139-142`). Package D: bump the pin and `--min-version` to 0.4.0 (URL and sha256 copied from `cli-extended-latest/latest.json` after the release; `CLI_EXTENDED_RESOLVE=local` with `tester-unified/local-wheel/` before it); add `COPY run-gate-project/ /src/run-gate-project/`; build `run-gate` offline like cmru with `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=0.0.0+tester.unified` and install it `--no-index --no-deps` after cli-extended; extend the build-time assertion (`:147-177`) to require `run-gate` metadata, `import run_gate_cli`, and `cli-extended` in its `requires`. `gen-requirements.py` already refuses/skips estate names (`ESTATE_INTERNAL` contains `run-gate`, `cli-extended`); add `run-gate-project` to `--build-requires` handling only if the wheel build needs setuptools-scm (it does: same set as cmru's, reuse `/tmp/internal-build-requirements.txt` with both pyprojects).
- **devcontainer image (mdt)**: the open feature request `modern-debian-tools-python-debug/TODO.md:147-172` (`RUN_GATE_WHEEL_*` build args, mirroring `CIU_WHEEL_*`, `docker-bake.hcl:329-344`, `Dockerfile:87-94`) now also needs `CLI_EXTENDED_WHEEL_*`. In scope (D7, package M): the image bakes cli-extended first and then run-gate, both sha256-pinned, into its tool venv. Nothing else installs them in a consumer: dstdns's env script stops installing run-gate (6.4).

### 6.4 dstdns (cross-repo notice; separate repo)

| File:line | Kind | Replacement |
|---|---|---|
| `env-workspace-setup-generate.sh:87-108` (`ensure_estate_tools`) | install (D7) | install nothing. Comment out the sibling-checkout block (`:93-107`) with a one-line pointer: "run-gate and cli-extended come from the cockpit image (mdt `RUN_GATE_WHEEL_*`/`CLI_EXTENDED_WHEEL_*`), like ciu". Keep only the `command -v run-gate` check and its existing warning when it is missing. The version floor is enforced by run-gate itself through `requires_run_gate`. Update the header comment (`:11-12`) to match. Delete the "reinstall run-gate after rebuild" doctrine (mdt TODO "dstdns deletes on ship") |
| `run-gate.toml:1-2` header, `:431,615` comments | config/provenance | R7; add `requires_run_gate = ">=24.0.0"` (R10); `:615` cites `run_gate.py:1612` (provenance, leave) |
| `README.md:149-154`, `docs/RUN-GATE.md:3-5,50-55`, `docs/DEPLOYMENT-GUIDE.md:702`, `docs/CORE-WORKFLOW-STATUS.md:236,245`, `docs/CONTAINER-DOCTRINE.md:39`, `docs/ENVIRONMENT-SETUP-UNIFIED.md:64` | doc | R1/R2; `docs/RUN-GATE.md` drops "copied from vbpub ... `__revision__` detects drift" (R8): the tool is a pip-installed wheel with a version floor |
| `AGENTS.md:320,339-340`, `docs/testing/running-tests.md:9,91` | doc | R2; the "keep installed run-gate current" line names the floor |
| `.claude/skills/run-gate-cli/SKILL.md:35,55`, `.claude/skills/nyxloom-merge-p/SKILL.md:70` | skill | replace the copied run-gate skill with `run-gate skills install` output (AC-19); nyxloom-merge-p line per R2 |
| `nyxloom-trove/nyxloom.toml:46,169` and the `[gates.*]` pointers | config/comment | pointers already use `run-gate`; comment `:169` (R7); `run-gate validate-pointers nyxloom-trove/nyxloom.toml` must stay OK |
| `tests/harness/conftest.py:1-40` | test | `import run_gate` first; the file-path fallback to a repo-root `run-gate.py` is deleted; the white-box module is skipped with the existing reason when `run_gate` or `cli_extended` is unimportable (the suite runs inside test-runner, where neither is installed; D-180) |
| `tests/config/test_run_gate_pointer_linkage.py:7-56` | test | its "no `./run-gate.py` reference" assertion stays (now also covers `./run-gate.py --list` forms); regenerate expectations if it pins `--list` |
| `tests/config/test_worktree_env_docs.py:470` | test | asserts a `run-gate rev` label in a doc: change to the new label (`run-gate <semver>`), see `nyxloom-trove/GUIDE.md:316` (`run-gate rev 46`, provenance of a measurement, leave) |
| `tests/config/test_*lane_declaration.py` docstrings (`:262,270,303`), `tools/test-runner/Dockerfile:173`, `ciu.global.defaults.toml.j2:1456`, `tools/test-runner/ciu.compose.yml.j2:3` | comment | R1/R7 |
| `nyxloom-trove/decisions.md`, `LESSONS.md`, `CONTROLLER-BRIEF.md`, handoffs, reviews | provenance | leave |

Cross-repo notice: a dstdns decision/backlog entry (not a vbpub edit) states: run-gate 24.0.0 is a breaking release; run-gate and cli-extended now come from the cockpit image (D7, package M) and dstdns installs neither; `--list`/`--check-env` spellings changed; floor key required by 25.0.0; commit hashes annotated `vbpub@<hash>`.

---

## 7. Package split

Common rules for every package: worktree via `ciu worktree create <name> --base <integration or main>`; commits per `AGENT-RULES-2026-10-07`; the registered gate decides; `CHANGES.md` `[Unreleased]` stays comment-only (note text goes in the REPORT); each package gets an independent adversarial review; implementers and reviewers are Sonnet, briefs leave no choice open. Sizes are estimates for ~60 tool calls; E and J1 are the largest and are pre-split where needed.

Dependency chain: `A -> (controller: merge A, release cli-extended 0.4.0, deploy)`; `B -> C -> D(local wheel) -> H -> E -> F -> G -> I`; `J1, J2` after E; `L` after I and J; `M` (mdt image) after L's wheel exists; `K` after M's image is the cockpit. Everything except A, M and K lives on the integration branch `rg82-run-gate-24` in vbpub, merged to `main` once, after I and J.

### A. LIB: cli-extended 0.4.0 (LCR-1 + LCR-2)

- Files: `libraries/cli-extended/src/cli_extended/parser.py` (`VerbSpec.fallback`, normalisation in `run_cli`, `RegisteredCli.fallback_verb`/`parse_args`, help label), `constraints.py` untouched, `surface.py`/`surface_cli.py`/`review.py` (route record key), `doctor.py` (`fail_exit_code`, `CheckResult.lines`), `audit.py` if a check names verbs, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `README.md`, `CHANGES.md` hand-written section, `tests/` (new tests per 2.3/2.4 plus `assert_cli_contract` fixtures), `docs/cli-surface.json` regenerated only if the library's own CLI changes.
- Oracles: section 2.3/2.4 test lists; a library surface manifest of a tool without a fallback verb is byte-identical before and after; planted bugs recorded.
- Gate: `cd libraries/cli-extended && run-gate --worktree <wt> r0-r1` then `r3`; `cli-extended audit` and `surface check` pass. R2 once on the integrated branch (program rule).
- Hand-off: controller merges, `cmru release` -> cli-extended 0.4.0 with `cli-extended-latest/latest.json`, deploys to the cockpit venv. Nothing else may assume 0.4.0 until then (dev uses a locally built wheel).

### B. SPLIT: pure refactor, old parser still in place

- Files: `run-gate-project/run-gate.py` (split `_dispatch` into `verb_*` functions returning `LaneResult`; `lane_request(**overrides)` namespace factory; `_dispatch_sequence` member recursion builds namespaces instead of argv (`:11700-11722`); `_dispatch(argv)` keeps its signature as parse-then-call), `tools/golden_dry_run.py` (new), `tests/test_golden_dry_run.py` (new, unit-level).
- `tools/golden_dry_run.py` contract: for a project dir and worktree, run `<launcher> list` and `<launcher> <lane> --dry-run --allow-dirty --worktree <wt>` for every lane (`list` kind, name, environment first), write `<out>/<project>/<lane>.txt` after normalisation: `rev N`/`<semver>` -> `<VERSION>`, the worktree path -> `<WT>`, the PID/timestamps/run-ids -> placeholders (patterns listed in the tool and unit-tested), `./run-gate.py ` -> `run-gate ` only for the post-migration comparison (flag `--normalise-invocation`). It never starts a judged lane; assay lanes need their read-only inventory probe container, so run the capture serialised under the shared lock and record any lane whose dry run needs an unavailable image as `SKIPPED (reason)` in the manifest.
- Baseline capture (before any behaviour change, with the installed 23.10.0): all 19 vbpub projects, ~56 lanes (counts from `grep '^\[lanes\.'`). dstdns (142 lanes) is captured in K.
- Oracles: full `selftest` green; baseline == re-capture on the unmodified refactor (byte-identical, normalised); a planted change in a verb function (for example swapping two admission refusals) makes the golden or the existing tests fail.
- Gate: `cd run-gate-project && run-gate --worktree <wt> selftest` (installed 23.10.0 launcher).

### C. LAYOUT + IDENTITY + PERSISTED KEYS

- Files: `run-gate.py` -> `run_gate.py` (git mv; section 5.1), `pyproject.toml`, `run_gate.py` (`__revision__` removal, `run_gate_identity()`/`run_gate_version()`, persisted-key table 4.4, `find_project_dir`, `sys.path` hack, "zero installs" comments, `_RESERVED_POINTER_VERBS` untouched yet), `run_gate_admission.py` (owner string), `tools/coverage_gate.py` (repeatable `--source`), `tools/canary-run.sh`, `run-gate.toml` (`selftest`, `gate-full`), `cmru.toml`, `cmru.orchestration.toml` (+ regenerated graph), `assay.toml` comment, `tests/*` (loader, `invoke_module` migration, `TestWheelPackaging`, 16 revision assertions, admission tests), `tests/fixtures`, RG55 contract docs (both copies), `scripts/cgroup-profiler/docs/CONSUMERS.md:256` and `ATTACH-GUIDE.md:233`.
- State after C: old argparse parser still serves; `--version` prints `run-gate <semver>` (the old flag, library not yet wired); the module imports `cli_extended` lazily (identity only). `python -m run_gate selftest` works inside the image once D lands.
- Oracles: golden dry run identical to B's baseline except the normalised header and invocation text; persisted-key tests (write then read, old int `revision` record tolerated); `find_project_dir` tests (CWD only; `python -m run_gate` from a fixture dir picks the fixture config, not run-gate-project's); wheel test installs and runs `run-gate list`.
- Gate: `selftest` (needs the image from D with cli-extended >= 0.4.0 for the identity import: C and D are developed together; D lands first with the local wheel, C's gate runs on it).

Dev order note: build D's image changes before running C's gate; D itself only needs C's pyproject to exist for its `COPY`/wheel build, so C's `pyproject.toml` commit comes first inside C.

### D. IMAGE: tester-unified

- Files: `tester-unified/Dockerfile`, `tester-unified/gen-requirements.py` (only if the closure needs it), `tester-unified/README.md`, `tester-unified/docs/CONSUMERS.md`, `tester-unified/local-wheel/` (not committed), `run-gate-project/tests/test_tester_unified_launcher.py` (doc assertion), image tests the project has.
- Contents: section 6.3. Build `tester-unified:local` once, foreground, under the shared lock; paste the build assertion output.
- Oracles: in the built image `python -c "import cli_extended, run_gate_cli; from importlib.metadata import version; print(version('run-gate'), version('cli-extended'))"` prints `0.0.0+tester.unified` and the 0.4.0 wheel version; `run-gate --version` works inside; a wrong sha256 build arg fails the build; the closure still contains no estate name.
- Gate: tester-unified's registered lane (read its `run-gate.toml`/`cmru.toml`; if it has none, the build assertion plus the launcher test is the gate, stated in the REPORT).

### H. FLOOR (`requires_run_gate`)

- Files: `run_gate.py` (`_validate_config` allowed key, parser/comparison/refusal functions, `doctor` check name reserved for package F), `tests/test_run_gate.py` (new class), `tests/test_estate_floor.py` (new), `README.md`/`CONSUMERS.md`/`SPEC.md` floor sections (also owned by I; H writes only the SPEC `R-` rule and a CONSUMERS paragraph).
- Oracles: table-driven comparison tests (`>=24.0.0` vs `23.10.0`, `24.0.0`, `24.0.1.dev3+gabc`, `0.0.0+tester.unified`, `24.1.0`; malformed floors refused; project and root floors both enforced and the refusal names the right file); exact refusal text and exit 2; absent key accepted in 24.x; a controlled wrong implementation (string comparison instead of integer, so `9.0.0` passes `>=24.0.0`) fails; an old-tool simulation is not possible, so the unknown-key behaviour is asserted against a fixture that removes the key from the allowed set.
- Gate: `selftest`.

### E. GRAMMAR on cli-extended (the core swap)

- Files: new `run_gate_cli.py` (identity, registry, verb specs per 2.2, constraints, `closed_result`, `build_cli`, `main`), `run_gate.py` (delete `RunGateArgumentParser` `:338-351`, `cli_headline`, `_dispatch` parser block `:11774-11860` and the `args.help`/`args.list`/`args.version` branches; `main`/`_dispatch` become shims; `find_project_dir` error text; `usage()` -> `lanes` text; `RESERVED_VERBS`; `cmd_list` unchanged), `tests/test_run_gate.py` (migrate every old form per 2.5: `["--list"]` x21 -> `["list"]`, `["--check-env"...]` x10, `["--help"]` x10 assertions on the new static help, `["--version"]` x6, the one `invalid command line` assertion; keep the other ~270 `run_gate.main([...])` calls as they are because the lane grammar is unchanged), new `tests/test_cli_contract.py` (`assert_cli_contract` at the rigor the library table defines for a multi-verb CLI, plus the exit-table test of 3.4, plus one test per row of 2.5 that must be refused), `tests/test_run_gate_admission.py`.
- Oracles: golden dry run (all 19 projects) and `list` output byte-identical to B's baseline after the 5 normalisations; every refusal keeps its exit code; the exit-table test with the controlled wrong implementation; `run-gate --worktree X lane`, `lane --worktree X`, `--base B --worktree X lane`, `lane -- a b`, `assay-r2 -- --version` (exit 2 as `tests/test_run_gate.py:24522`) all parse the same as before; a lane named like any reserved verb is refused at config load (`_validate_lane`), so `run-gate run <lane>` is only the explicit spelling of the same call and no lane needs it as an escape (no estate lane collides, section 0.7).
- Gate: `selftest`, then `assay-r1` (100% line and branch on the changed modules).

Split rule for E: if the implementer reaches ~120k context before the test migration is done, cut at "registry and handlers green; tests not yet migrated" and hand the migration to a fresh successor (LESSONS L23 brief).

### F. DOCTOR checks + SKILLS

- Files: `run_gate_cli.py` (register `doctor` with the 12 named checks, `register_skills_verbs`), `run_gate.py` (`cmd_doctor` split into `DoctorContext` with `record()` that appends records instead of printing, and 12 section functions), `run_gate_skills/` (SKILL.md moved and updated), `tests/test_doctor*.py` (existing doctor tests are in `tests/test_run_gate.py`: update their expectations).
- Check names (lowercase tokens): `docker`, `admission`, `environments`, `lane-declarations`, `git`, `assay-toolchain`, `git-config`, `images`, `footprint`, `profiler`, `coordination-locks`, `version-floor`; plus the library's automatic `skills`. Mapping: each check owns the records of the matching section of `cmd_doctor` (`:7185-7833`): docker (`:7227-7233`), admission (all `admission *` topics), environments (`lane ... environment`, `slice for env`, `slice unit`), lane-declarations (argv[0] RG-34, worker count RG-48, stall_timeout RG-58), git (`git`, host-lane git view RG-21, `mountinfo`), assay-toolchain (the per-assay-lane probe topics), git-config, images (`image <env>`), footprint (manifest, drift, source, peak-at-floor, staleness), profiler (`profile config`, `profiler daemon`, pressure/slice records), coordination-locks (`stale coordination locks`), version-floor (4.2).
- Mapping to `CheckResult`: status = `fail` if any record FAIL, else `warn` if any WARN, else `skip` if all records SKIP, else `ok` (INFO counts as ok); `summary` = `<N> records: <first FAIL or WARN topic>: <detail>` or `<N> records ok`; `lines` = one `[STATUS] topic: detail` string per record (LCR-2); `details = {"records": [{"status","topic","detail"}, ...]}`; the `--worktree` banner line becomes the first `lines` entry of the `git` check. Doctor exits 2 on any fail (`fail_exit_code=2`). Probe containers run only inside the checks that start them; `--check NAME` limits which checks (and therefore which probes) run.
- Oracles: each existing doctor scenario test still asserts the same record text (now in `lines`/`details`); `--json` document validates; `--check docker` starts no probe container (fake-docker call log empty); exit 2 on a FAIL record, 0 otherwise; `skills check` passes in a built wheel.
- Gate: `selftest`.

### G. SURFACE lifecycle + audit

- Files: `pyproject.toml` `[tool.cli-extended]` (id `run-gate`, factory `run_gate_cli:build_cli`, `review = "docs/cli-review.toml"`, `manifest = "docs/cli-surface.json"`, `spec = "SPEC.md"`, `findings = "docs/cli-review-findings.toml"`; modelled on `cmru/pyproject.toml:42-52`), `docs/cli-review.toml`, `docs/cli-surface.json`, `docs/cli-review-findings.toml`, `SPEC.md` generated region, `tests` (pytest plugin wiring, AC-22).
- Procedure: `cli-extended surface sync`, `surface pack`, the packaged `cli-extended-review` skill judges, decisions recorded, `surface check` passes; `cli-extended audit` reports no `fail`; each `manual` AC row (AC-02, 03, 06, 09, 11, 13, 14, 15, 21, 23) is answered in the REPORT with evidence. Recorded exceptions (AC-12 reasons in 2.2, AC-14: lane output is raw process output and uses `print`, AC-15: run-gate has its own progress watches and no spinner).
- Gate: `cli-extended audit`, `cli-extended surface check`, `selftest`.

### I. DOCS + buildkite tools (run-gate-project)

- Files: section 6.1 "run-gate-project's own files", `tools/buildkite/*.sh`, `tests/test_buildkite_tools.py`, SKILL text. Add a CONSUMERS "Install and version floor" section: install order, `run-gate --version`, `requires_run_gate`, the exact refusal text, "run from the project directory", migration table 2.5 verbatim.
- Oracles: `grep -rn 'run-gate\.py\|__revision__\|zero install' run-gate-project` (excluding provenance paths) returns nothing; docs examples are executed by a test that extracts fenced `run-gate ...` commands from README/CONSUMERS and runs `--help`-level parses with `parse_args` (no lane execution).
- Gate: `selftest`.

### J1. ESTATE configs and tools (vbpub)

- Files: section 6.1 symlinks (18 `git rm`), `cmru.toml` x6, `nyxloom.toml` x4, `run-gate.toml` conjunctions x5 and header comments x14, R10 key in 19 `run-gate.toml`, `run-gate.root.toml` comment, `cmru/tools/run_release_gate.py` and its tests, `modern-debian-tools-python-debug/scripts/test_release_flow.py`, `assay/gate/tests/test_cgroup_parent.py` and `assay/tools/self-qualification-gate.sh` + `test_self_lane.py`, `ciu/tests/tests/test_ciu_worktree.py`, `.dockerignore`.
- Oracles: `run-gate validate-pointers <each nyxloom.toml and cmru.toml>` OK (dev launcher, 5.4); golden dry run of all lanes after the symlinks are gone (using the dev launcher) equals the baseline after normalisation; the estate-floor sweep test (H) is green; `git ls-files -s | grep ^120000 | grep run-gate` returns nothing; `grep` finds no executable `./run-gate.py` outside provenance.
- Gate: `cmru`, `ciu`, `assay`, `nyxloom` etc. real lanes are NOT run on the integration branch (the old launcher cannot read the floor key); acceptance is the static and dry-run oracles above, then the controller runs one real lane per project after deploy (L).

### J2. ESTATE docs and skills (vbpub)

- Files: section 6.1 docs list and skills. Rule application only; no semantic edits. Oracle: the same grep as I over the whole repo (excluding provenance globs listed in 6.1) returns no `run-gate.py`.
- Gate: each touched project's doc-structure test if one exists (cmru has doc/spec tests; run them focused, serial, niced); the rest is grep.

### K. DSTDNS (separate repo `/workspaces/dstdns`; cross-repo notice)

- Files: section 6.4. Done in a dstdns worktree by a dstdns-side implementer with its own gate (`run-gate <lane> --worktree`, after the cockpit has 24.0.0, see L). The `env-workspace-setup-generate.sh` change (D7: install block commented out) is tested with its existing shell tests (find them with `grep -rn ensure_estate_tools`). A new test uses a fake `pip` shim and a PATH without `run-gate`, and asserts that the script never calls `pip install` for run-gate or cli-extended and prints the existing warning. Its controlled wrong implementation is the old sibling install, which must fail that test. K runs after M's image is the cockpit, so run-gate 24 is on PATH without the script.
- Oracles: golden dry run of all 142 dstdns lanes (captured with 23.10.0 before the cockpit upgrade, compared after) byte-identical after normalisation; `run-gate validate-pointers nyxloom-trove/nyxloom.toml` OK; `run-gate list` equals the baseline; `run-gate doctor` shows `version-floor` OK.
- Cross-repo notice: commit message and a decision-log entry referencing `vbpub@<hash>` of the release.

### L. RELEASE 24.0.0

- Steps (controller, in order): merge `rg82-run-gate-24` into `main` (`--no-ff`); verify on `main` with the dev launcher (`list`, `validate-pointers`, dry runs); `cmru release` for `run-gate-project` with `--set-version 24.0.0` (the first major, as the cmru.toml note says to pass a bare semver; never the prefixed tag) after cmru>=6.0.0 is installed; the release gate is the registered `[steps.run-tests]` (section 5.2) read in a separate step; publish wheel; deploy to the cockpit venv in the order cli-extended 0.4.0 then run-gate 24.0.0 with `--no-index --find-links`; verify `run-gate --version` prints `run-gate 24.0.0` and `which -a run-gate` shows only `~/.venv/bin` (check `~/.local/bin` for stale shims, memory `devcontainer-tool-installs-get-clobbered`); run one real lane per project (`cmru`: `installed-wheel` or the cheapest; `topos`: `py-compile`; `nyxloom`/`ciu`/`assay`: their cheapest declared lane; `mattermost-server` only per its own rules, never the live containers); mark RG-82 FIXED in `KNOWN_ISSUES_TODO_BACKLOG.md` with observed/fix/oracle; update CHANGES text in the REPORT only; then package M (mdt image) and, once that image is the cockpit, notify dstdns and run K. Rebuild tester-unified once more if its cli-extended pin or run-gate COPY moved after D.
- Release note must list the break table 2.5 and the persisted-key renames 4.4.

### M. MDT IMAGE: cockpit ships cli-extended and run-gate (D7)

- Files: `modern-debian-tools-python-debug/Dockerfile` (next to the ciu wheel install, `:87-94`), `docker-bake.hcl` (next to `CIU_WHEEL_*`, `:329-344`), the image's version report (`mdt version --json` or the image labels, whichever reports ciu today), `TODO.md:147-172` (mark the feature request FIXED with observed/fix/oracle and extend it with cli-extended), and the mdt README section that lists the shipped estate CLIs.
- Contract: add build args `CLI_EXTENDED_WHEEL_{TAG,ASSET_NAME,URL,SHA256}` and `RUN_GATE_WHEEL_{TAG,ASSET_NAME,URL,SHA256}`, mirroring every `CIU_WHEEL_*` arg. They are resolved the way ciu's are, by `scripts/resolve-devcontainers-release.py` from the `<tool>-latest/latest.json` pointer, so extend that resolver for both tools; never hand-copy the values. cli-extended already publishes `cli-extended-latest/latest.json` (`libraries/cli-extended/cmru.toml:10`). The run-gate release (`cmru handler wheel-publish --prefix run-gate`) must be checked for a `run-gate-latest/latest.json`; if it does not write one, package L adds it in the same way before M starts. The pinned versions are cli-extended 0.4.0 and run-gate 24.0.0. Each wheel is sha256-verified. Install order is cli-extended first, then run-gate, both `--no-index --no-deps` into the same tool venv that holds ciu. The build asserts `run-gate --version` equals the build arg and that `cli-extended` is installed at or above run-gate's declared floor.
- Oracles (from the mdt TODO): a fresh container with no finalize hook has `run-gate --version` on PATH, matching the build arg; a wrong sha256 build arg fails the build; the controlled wrong implementation "install run-gate without cli-extended" fails the build-time assertion.
- Gate: the mdt project's own registered build and test steps, run under the host load rules (one build at a time, `nice`/`ionice`, PSI check first). Rebuilding the devcontainer itself is an operator step; the package ends with an image tag and the controller's verification inside a container from it.
- Depends on L (the 24.0.0 wheel and its sha256). K depends on M.

---

## 8. Risks and decision asks

Only what D1-D6 do not decide. **All resolved by the operator on 2026-10-07** (see the header): Q1-Q5, Q7 and Q8 as recommended; Q9 rename everywhere, with no `revision` left; Q6 superseded by D7, so the mdt image ships both wheels in this program (package M) instead of a follow-up. The table is kept as the record of the options.

| # | Ask | Options | Recommendation |
|---|---|---|---|
| Q1 | Cross-repo ordering: LCR-1/2 require a cli-extended 0.4.0 release (merge, `cmru release`, deploy, tester-unified pin) before run-gate 24 can be gated in its real lane. | (a) release 0.4.0 first, as planned; (b) vendor a fallback shim in run-gate (violates CX-D1) | (a). Until released, packages C-G gate on the local-wheel image (D). |
| Q2 | Python floor. `requires-python >=3.11` today. The `lane` + `nargs="*"` pass-through was verified on 3.13 and 3.14 only. | (a) keep >=3.11 and derive `lane_args` from raw argv in the handler (design already does, argparse still needs to accept the tokens); (b) raise to >=3.13 | (b), since the estate runs 3.14 everywhere (devcontainer, tester-unified `python3.14`) and 3.11/3.12 could not be tested here. If (a), package E must add a 3.11/3.12 run of the grammar tests in an image that has them. |
| Q3 | Internal-error exit code: library `report` alone would exit 1; closed table says ERROR 2. | (a) map to 2 in the wrapper (this design); (b) library default 1 | (a), per the RG-82 oracle wording. Behaviour change: a bug that today exits 1 now exits 2 with one line. |
| Q4 | Hard removal of `--list`/`--check-env` vs one-major deprecation alias. D2/D6 allow breaks. | (a) remove; (b) hidden aliases printing a deprecation line | (a). Every in-estate user is migrated in the same release (6.1, 6.4); an alias keeps the old overloaded-positional grammar alive, which is what D1 removes. |
| Q5 | When `requires_run_gate` becomes mandatory. | 24.x optional with `doctor` WARN, 25.0.0 mandatory (this design) / mandatory immediately | 25.0.0 mandatory. The estate sweep test makes the estate complete in 24.0.0, so the optional window only covers external repos. |
| Q6 | Scope of the mdt devcontainer image shipping `run-gate` and `cli-extended` (`modern-debian-tools-python-debug/TODO.md:147-172`). | in 24.0.0 / follow-up | Follow-up. It is not needed to ship 24.0.0; dstdns env-setup (K) is. Note the dependency: any image baking run-gate must bake cli-extended first. |
| Q7 | Library controls (`--quiet`, `--debug`, `--color`, `--log-level`) are accepted but inert for lane output. | document inert / bridge `--quiet` to suppress run-gate's own banner lines | Document inert in verb help and the CONSUMERS section; bridging risks changing byte-identical dry-run output. |
| Q8 | Project discovery after D2: CWD only (this design) vs adding `--project DIR`. | CWD only / add option | CWD only. Every estate invocation already `cd`s first (pointers `cd {worktree}/proj && ...`); `--worktree` covers the remaining case. |
| Q9 | Persisted key rename `revision` -> `run_gate_version` and the cgprofile `--meta` key. | rename everywhere (this design) / keep `run_gate_revision` as a string | Rename and update both contract copies; the daemon stores unknown keys verbatim, verified by the contract text and by the absence of any reader in its source. A reviewer should re-verify with `grep -rn run_gate_revision scripts/cgroup-profiler --include=*.py` limited to non-test files. |

Residual risks (no decision needed, mitigated in the packages): (1) the 25k-line test suite has ~300 `run_gate.main([...])` sites, so E's test migration is the largest single cost; the lane grammar is unchanged, so only ~50 sites change. (2) Dry-run goldens need Docker for assay-lane inventory probes; they run serialised and any skipped lane is recorded with a reason. (3) `doctor` becoming 12 checks changes its human layout (record lines are kept as indented `lines`); anything parsing doctor stdout (`validate` scripts) must be found by grep before E, none was found in this carve. (4) A pre-24 `run-gate` meeting a 24 config refuses with the unknown-key message (exit 2) without the install hint; the CONSUMERS page and release note say so. (5) tester-unified's `run-gate` dist is `0.0.0+tester.unified`; nothing in the image may enforce a floor against it (the estate sweep test exempts only run-gate-project's own config).
