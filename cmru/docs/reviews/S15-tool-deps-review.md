READY WITH CORRECTIONS

# Adversarial review — cmru package C (S15 tool dependencies) + A/B integration

Reviewed in `/workspaces/vbpub/.worktrees/cmru-KI-12-16` against
`git diff HEAD~3` (commits `cab5da5d` A, `8248fcc2` B, `9c056def` C), with the
whole of `tool_deps.py`, the config/dependencies/cli halves, SPEC S2.6 + S15,
and all four new/changed test files read in full. The controller's
verifications (full suite 1592/100%, the verb's real-world three-line output
and both exit codes, tool-edge exclusion from ordering, the A/B ground in
`KI-12-16-review.md`) were not repeated — except one deliberate re-measurement
of the verb noted below. All probes were read-only against `/workspaces`;
scratch lived in `/tmp/cmru-c-review/`. Network use was limited to read-only
GETs against the GitHub API and one run of the read-only `cmru tool-deps` verb.

Verdict rationale: two blocking findings, both in package C, both small and
local. Neither can falsely block the estate's actual release path (the
orchestrated preflight) — B1 fails *open* and B2 is reachable only through the
verb's single-project invocation — and the real estate was measured healthy
end-to-end, so "ready with corrections" rather than "not ready". Both must
land before this merges: B1 is the fourth instance of the recurring
narrower-than-claimed defect class, already enshrined in a test.

---

## 1. Blocking findings

### B1 — FOURTH INSTANCE: an inaccessible repository (HTTP 404 on the release list) is certified as "no published release exists yet … bootstrap state", permanently masking every real mismatch under a false diagnosis

**What is wrong.** `_github_get_json` maps 404 → `None` ("cleanly absent"),
and `_list_releases` folds that `None` into an empty release list
(`batch = data or []`). That mapping is correct for
`/releases/tags/{tag}` ("this exact release is absent") but wrong for the
**list** endpoint: `GET /repos/{owner}/{repo}/releases` returns 404 only when
the *repository* is unreachable — nonexistent, renamed, owner typo in
`[github]`, or private queried without credentials (and `tool_deps` never
sends credentials; see the note below). A real repository with zero releases —
the actual S15.4 bootstrap state — returns **200 with `[]`**, never 404. So
404-on-list can *never* mean "nothing published yet", yet that is exactly what
the outcome asserts.

**The measurement.** Wire semantics, with paired controls:

```
$ curl -sS -o real_repo.json  -w "..." "https://api.github.com/repos/volkb79-2/vbpub/releases?per_page=1"
real vbpub /releases?per_page=1 -> HTTP 200
$ curl -sS -o absent_repo.json -w "..." ".../repos/volkb79-2/this-repo-does-not-exist-cmru-review/releases"
absent repo /releases -> HTTP 404      body: {"message": "Not Found", ...}
$ curl -sS -o norelease_repo.json -w "..." ".../repos/octocat/Hello-World/releases"
octocat/Hello-World /releases -> HTTP 200   body: [ ]        <- the genuine bootstrap state
```

End to end (`/tmp/cmru-c-review/probe_404.py`), the real vendored pin against
a nonexistent repo — the same wire response a private repo or an owner typo
produces:

```
cmru: tool dependency assay@1.0.0 (tools/assay/assay-1.0.0.pyz)
  integrity    PASS -- tools/assay/assay-1.0.0.pyz bytes match the recorded sha256
  authenticity UNRESOLVED (no-release) -- no published release exists yet for project 'assay'
               (tag prefix 'assay-v'); bootstrap state -- nothing published to compare the pin against yet
  freshness    UNRESOLVED (no-release) -- ...same...
is_blocking: False
```

assay verifiably has published releases (the control 200 above; the verb run
below authenticates `assay-v1.0.0`'s bytes). The message asserts a fact about
assay's release history that was never observed; what was actually verified
is only "the list endpoint answered 404".

**Why this is the failure shape being hunted.** SPEC S15.5 requires "an
unexpected HTTP status" to be `unresolved (network-error)`; S15.4 defines
`no-release` as "the tag prefix has zero published releases". 404-on-list is
neither — it is classified as the *benign* bootstrap outcome, exactly the
`assay-v-v1.0.0` defect's clothes ("a genuine mismatch wearing the bootstrap
outcome's clothes"), reintroduced one layer down. Consequence if shipped: the
day the repo is renamed, made private, or `[github] owner` is mistyped,
authenticity and freshness silently stop being checked **forever** — every
release preflight prints "bootstrap state" on an estate with years of
releases, `is_blocking` stays False, and a tampered or stale vendored
artifact sails through with a message that tells the operator nothing is
wrong and nothing needs investigating. `cmru tool-deps --refresh` misdiagnoses
identically ("cannot refresh: no published release exists yet"). And
`test_list_releases_treats_a_404_as_empty` enshrines the misclassification as
intended behaviour.

**Smallest fix.** In `_list_releases`, a `None` from `_github_get_json` on
page 1 raises
`NetworkUnavailable(f"repository {owner}/{repo} not found or not accessible")`
— producing `unresolved (network-error)` with a truthful detail (still
non-blocking, per S15.5's design). Replace
`test_list_releases_treats_a_404_as_empty` with a test asserting the raise.
(A `None` on page ≥ 2 — repo vanishing mid-pagination — should raise too, not
truncate.) Related, one line of hardening worth taking at the same time:
`tool_deps` never sends the token that `load_config` already resolves into
`github_config.token`, so a private estate repo can *only* ever land in this
404 path; passing `Authorization: Bearer <token>` when one is configured
makes the check work at all there and also lifts the 60-req/h unauthenticated
rate ceiling.

### B2 — The verb's documented single-project invocation reports "authenticity FAIL (unknown-provider)" — a hard, un-overridable FAIL — for a pin the estate run proves authentic

**What is wrong.** `verify_project` turns a provider absent from the loaded
`projects` mapping into `CheckOutcome("fail", "unknown-provider", ...)` for
both authenticity and freshness, with a comment claiming this is "defensive
depth for direct callers …, not a reachable path through the CLI". It is
reachable through the CLI: `tool_deps_main --config` help offers "Path to
cmru.toml or cmru.orchestration.toml", and `config_names.py`'s contract makes
the *project* document the default selection when cmru runs from a project
directory. A project-document load contains only that one project, so every
declared provider is "unknown".

**The measurement.** The same pin, same machine, same minute:

```
$ python -c "from cmru.cli import main; main(['tool-deps','--config','.../cmru.orchestration.toml'])"
  integrity    PASS
  authenticity PASS -- recorded sha256 matches the published 'assay-1.0.0.pyz' bytes for 'assay-v1.0.0' ...
  freshness    FAIL (stale) -- pinned version 1.0.0 is behind the highest published assay release 2.1.0
exit=2   (matches the controller's run exactly)

$ python -c "from cmru.cli import main; main(['tool-deps','--config','.../cmru/cmru.toml'])"
  integrity    PASS
  authenticity FAIL (unknown-provider) -- tool dependency provider project 'assay' is not part of the loaded estate configuration
  freshness    FAIL (unknown-provider) -- ...same...
exit=2
```

**Why it blocks.** This is a refusal firing on a legitimate state (the
verb's own documented invocation, and the estate's own portability constraint
— S2: "a project stays portable to a fresh repository root" — quoted inside
`_parse_tool_dependencies` itself). Worse, it wears the authenticity check's
clothes: "authenticity FAIL" asserts a verified fact about the artifact when
nothing about the artifact was checked — the exact conflation S15.3 forbids
("every reported line names exactly which one it is"), with no override
because authenticity failures deliberately have none. An operator scripting
`cmru tool-deps` from inside `cmru/` gets a permanent exit-2 "authenticity
FAIL" on a healthy pin. The release preflight is *not* affected (it always
loads the orchestration document, where `dependencies.build_report` rejects a
genuinely unknown provider at load time — verified by reading `load_config`
and by `test_tool_dependency_on_unknown_project_is_rejected`).

**Smallest fix.** In `tool_deps_main`, when a declared provider is absent
from the loaded mapping, exit 2 with a *config-level* `[ERROR]` naming the
real cause ("provider prefixes live in the estate configuration; run with
`--config cmru.orchestration.toml`") instead of emitting per-check FAIL
outcomes — and correct the "not a reachable path through the CLI" comment.
Keep `verify_project`'s defensive fail for direct API misuse if desired, but
relabel it so it cannot read as an authenticity verdict.

---

## 2. Non-blocking findings

### N1 — `_get`'s exception net misses `http.client.HTTPException`; the escape converts "could not check" into a retained-worktree release failure

Measured (`/tmp/cmru-c-review/probe_local.py`, with a URLError control
proving the net works for the intended classes):

```
URLError -> NetworkUnavailable (control OK)
IncompleteRead ESCAPES as IncompleteRead -> uncaught traceback in the release child
BadStatusLine ESCAPES as BadStatusLine
```

`IncompleteRead` is what `response.read()` raises on a truncated download
(so a truncated body can never silently hash-mismatch — good — but it also
never becomes `unresolved`); `BadStatusLine` comes from proxies emitting
garbage (urllib wraps only `OSError` into `URLError`). In the release child
an uncaught exception is not a `ReleasePlanRefused`: no `mark_plan_refused`,
so the parent's `plan_was_refused` branch is skipped and the generic
`rc != 0` branch retains the worktree and prints "Release transaction failed;
retained … for inspection/resume" (cli.py parent block) — a transient network
hiccup during plan computation produces the mid-release-failure experience the
A-review's C2 was filed to kill, resurfacing through S15's new network
surface. Fix: add `http.client.HTTPException` to `_get`'s except tuple (one
line, one test).

### N2 — Path validation checks the raw string but stores the stripped one: `" /etc/passwd"` passes and escapes the project root

`_parse_tool_dependencies` validates `Path(path)` (raw) but stores
`path.strip()`. Measured (`/tmp/cmru-c-review/probe_path.py`), with the bare
absolute path as the must-fail control:

```
control OK: '/etc/passwd' rejected (exit 2)
PROBE: ' /etc/passwd' ACCEPTED; stored path='/etc/passwd'; project_root/path -> /etc/passwd
```

`Path(" /etc/passwd")` is not absolute (first part `" /etc"`), so the check
passes; the stored stripped value *is* absolute, and `project_root /
dependency.path` discards the root entirely. Violates S2.6's "MUST NOT escape
the project root". The estate's reference grammar (assay's closed A-210
`_validate_attestation_dir`) validates the raw string and never strips, so it
cannot diverge this way. (`" ../x"` is caught — `".."` survives as a part.)
First-party config, so severity is bounded; the fix is to strip first and
validate exactly the value that will be stored (or reject surrounding
whitespace outright, as assay's grammar effectively does).

### N3 — freshness `unpublished-version` claims more than was computed, and `--allow-stale-tool-deps` waives more than its documentation says

The `pinned_key > highest_key` branch reports "pinned version X **is not
among published** releases" — but the code only established "above the
highest *stable* release" (`resolve_latest_release` skips
drafts/prereleases). A pinned GitHub *prerelease* that verifiably exists is
labeled "not among published releases" by freshness while the very next call
(`releases/tags/{tag}`, which does return prereleases) finds it and
authenticates its bytes. Also `is_blocking` lets `--allow-stale-tool-deps`
override *any* freshness fail, including `unpublished-version`, while the
flag's help and S15.7 describe it as "behind the highest published release" /
"stale" only. Low reach — cmru itself never publishes prereleases (verified:
`release.py` only ever *filters* the flag) — but both message and flag doc
should match the computation. The brief's pin-ahead-of-latest question,
answered: freshness `fail (unpublished-version)` + authenticity
`fail (version-not-published)` (404 on the exact tag), so a pin ahead of
latest is blocked with no override — correct and accurately worded for the
yanked-release case, which is the realistic way to reach it.

### N4 — Pre-existing, found by the S15.6 probe: one old test makes a real network connection during pytest

Full suite under a socket-blocking plugin (control below proves the blocker):

```
$ python /tmp/cmru-c-review/netblock_control.py
control OK: NETWORK CALL DURING TESTS: connect to ('140.82.121.6', 443)
$ python -m pytest -p netblock -q        (PYTHONPATH=/tmp/cmru-c-review, from cmru/)
FAILED tests/test_release_final_contracts.py::test_release_publish_rejects_response_without_upload_coordinate
== 1 failed, 1591 passed, 2 skipped ... in 24.52s ==     exit=1
```

The offender predates this branch (commit `80fbe7e1`; untouched by
HEAD~3..HEAD): it stubs `get_release_by_tag` but lets `publish` fire a real
request at api.github.com, and passes today for the wrong reason (the live
error path also exits 1). Out of this package's scope, but it falsifies the
estate-wide "the test suite stays hermetic" sentence S15.6/README lean on, so
it deserves its own small fix. The new code keeps its promise — see §3.

### N5 — Cosmetics / test gaps

* `test_tool_dependency_check_is_never_called_when_nothing_changed` asserts
  the helper **is** called (once, with `{}`) while its name and the module
  docstring say "never even called". The behavioural claim that matters (zero
  network calls) is true; the test's own name asserts the opposite of its
  assertion — rename it ("…is_called_with_an_empty_scope…") or hoist the
  emptiness check.
* Preflight FAIL lines print via `log_info` (and only the first line of the
  multi-line `render_status` gets the `[INFO] ` prefix); the refusal summary
  is the only `[ERROR]`. A FAIL detail line at INFO level is easy to lose in
  a long release log.
* `status` accepts and silently ignores `--allow-stale-tool-deps` (shared
  parser); `--refresh` silently ignores `--json` and
  `--allow-stale-tool-deps`.
* The release preflight always uses the 10 s default timeout (`verify_project`
  called without `timeout=`); only the verb exposes `--timeout`. Acceptable —
  note only.
* `resolve_latest_release` matches tags by `startswith(prefix)`: a future
  project whose prefix extends another's (e.g. `cmru-vX…`) would poison the
  shorter prefix's freshness (a text token sorts above every numeric in
  `_semver_key`). No colliding prefixes exist today — checked all eight
  declared prefixes (`cmru-v`, `assay-v`, `ciu-v`, `topos-v`, `pwmcp-v`,
  `tls-edge-v`, `nyxloom-v`, `modern-debian-tools-python-debug-v`) — and
  `release.py`'s `resolve_latest` shares the behaviour, so this is an estate
  naming-convention hazard, not a C regression.
* Mutation survivors: (a) the verb's `--project` *filtering* is untested —
  mutating `for name in selected` to `for name in projects` survives (only
  the unknown-name error is covered); (b) no test distinguishes semver-key
  equality from string equality for the pin, so mutating
  `pinned_key == highest_key` to `dependency.version == highest` survives
  (matters only for non-canonical spellings like `1.0` vs `1.00`).

---

## 3. What I tried to break and could not

* **Authenticity really compares bytes, never names/versions.** The live verb
  run downloaded the published `assay-1.0.0.pyz` through the
  `browser_download_url` 302 redirect and matched digests (PASS); a
  same-named asset with different content is `fail (hash-mismatch)` and a
  release with zero assets is `fail (asset-missing)` (unit tests — both
  fail-closed and accurately worded); the filename picks *which* asset to
  download, nothing more (code + `test_verify_reports_hash_mismatch…`).
  GitHub forbids duplicate asset names within a release, so "multiple
  matching assets" cannot arise. An asset added/replaced after the pin was
  recorded is hashed and mismatches → FAIL, not unresolved.
* **The sidecar question.** S15 never reads `*.sha256` sidecars — integrity
  is toml-digest vs bytes; the test step's `sha256sum -c` is sidecar vs
  bytes. If the two records disagree, exactly one of the two checks fails
  (whichever record mismatches the bytes); no conflation. Measured today:
  vendored bytes = sidecar = toml digest =
  `6224f784…515bf` = published bytes.
* **The `-v-v` regression stayed fixed.** `provider.prefix + version`
  reconstructed `assay-v1.0.0` against the real estate and found the release
  (authenticity PASS in my rerun, reproducing the controller's output and
  exit 2 byte-for-byte at decision level).
* **`-latest` pointers.** The thin pointer tag is `<project>-latest`
  (release.py), which cannot match the `<project>-v` marker.
* **Semver (attack 4).** Measured: `1.10.0 > 1.9.0` True, `r10 > r2` True.
  `2.1.0-rc1` sorts *above* `2.1.0` (anti-semver), but prerelease-flagged
  releases never enter the candidate set, so the inversion is unreachable for
  the highest-release computation; the reachable residue is N3's message.
* **Attack-2 states that classify correctly:** 403 (rate limit) / 500 /
  redirect-to-garbage / empty body / unparseable JSON → `NetworkUnavailable`
  → `unresolved (network-error)`, non-blocking (code + tests); tag-endpoint
  404 → `fail (version-not-published)` — accurate (a draft release is
  genuinely not published); truncated download cannot silently mismatch
  (it raises — N1's escape, never a wrong digest); malformed digest fields
  die at config load (exit 2, parametrized tests).
* **Attack-3 legitimate states that are NOT blocked:** genuine bootstrap
  (200 + `[]` → `unresolved (no-release)`, non-blocking — unit test, and the
  wire state confirmed via octocat/Hello-World); a new project whose prefix
  has no releases yet (same path); an offline operator (URLError →
  unresolved, non-blocking, `--dry-run` included); a no-op run (empty
  `release_names` → the helper iterates nothing → zero network calls); a
  changed project with nothing declared (empty tuple → zero network calls;
  real-helper test). `--allow-stale-tool-deps` threads release→check and
  nothing else (wiring test).
* **Blocking policy is inversion-proof:** the `is_blocking` parametrization
  pins all ten combinations (integrity/authenticity never overridable,
  freshness only with the flag, unresolved never blocks).
* **A+B+C integration.** A tool-dep refusal raises `ReleasePlanRefused` →
  `log_error` + `mark_plan_refused` + exit 1, and the parent's
  `plan_was_refused` branch discards the worktree like a success (no retained
  worktree, no KI-15 backup to clean — `push_backup_branch` runs only *after*
  both preflights); the wiring test asserts the mark and no traceback.
  Dry-run parity (KI-14): the check runs before the dry-run branch, identical
  in both modes. Transaction naming and the KI-13 unchanged-reason lines are
  upstream of the S15 call and untouched by it.
* **Hermeticity of the new code (S15.6).** The four S15-related test files:
  103 passed with all non-local sockets blocked (0.37 s); every test reaches
  the network only through the module's own `urlopen`/`_get` seams,
  monkeypatched. No import of `tool_deps` exists anywhere in `src/` except
  `cli.py`'s two lazy dispatch sites, and none in tests outside its own two
  files. The single full-suite network call is pre-existing (N4).
* **Config validation (attack 5).** Unknown keys, missing keys, non-string /
  empty fields, uppercase project ids, self-declaration, duplicate provider,
  bad digests, plain absolute and `..` paths: all exit 2 (tests, plus my
  control probe). A path that is a directory or missing yields integrity
  `fail (missing-file)` — blocking, message slightly loose for the directory
  case ("does not exist"). Symlinks are not policed (a vendored symlink's
  target is hashed); acceptable for first-party config, worth a line in S2.6
  if it ever matters.

## 4. Measurements not already shown above

* Verb vs estate, my rerun (read-only, network):
  `python -c "from cmru.cli import main; main(['tool-deps','--config','<worktree>/cmru.orchestration.toml'])"`
  → the three-line status (integrity PASS / authenticity PASS / freshness
  FAIL stale 1.0.0-behind-2.1.0) + `[ERROR] … 1 of 1 declared dependency is
  blocking …`, exit 2 — matching the controller's record. The
  `--allow-stale-tool-deps` variant was not re-run (controller-verified).
* Repo visibility: the 200 from the unauthenticated list call (B1 control)
  and the successful unauthenticated asset download together confirm
  `volkb79-2/vbpub` is public today — which is the only reason B1 is latent
  rather than live.
* `sha256sum cmru/tools/assay/assay-1.0.0.pyz` →
  `6224f784f96f5ad9d10264a69dd69594639959c5eda847dcede822a7adc515bf`,
  equal to the sidecar and the declared digest.
* S15-file suite under the socket block:
  `pytest tests/test_tool_deps.py tests/test_cli_tool_deps_wiring.py
  tests/test_config_tool_dependencies.py tests/test_dependencies.py -p netblock`
  → `103 passed`, exit 0 (measured on an unpiped run).
* Full suite under the socket block: `1 failed, 1591 passed, 2 skipped`,
  exit 1 — the one failure pre-existing (N4).
* Probe scripts and raw outputs retained in `/tmp/cmru-c-review/`
  (`probe_local.py`, `probe_path.py`, `probe_404.py`, `netblock.py`,
  `netblock_control.py`, `verb_real.out`, `verb_proj.out`,
  `pytest_full_netblock.out`).
