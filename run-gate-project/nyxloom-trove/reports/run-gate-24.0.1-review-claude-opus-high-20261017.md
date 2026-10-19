# Run-Gate 24.0.1 candidate — independent review and scoped repair

- Reviewer: Claude Opus 5.5 (high), 2026-10-17
- Checkout: `.worktrees/run-gate-2401-candidate-20261017`, CIU `run-gate-2401-candidate`
  (`ciu worktree inspect`: ready, branch matches, clean)
- Reviewed revision: `eeb4e461beb2b0d0259e781ef4f9522f06a5c60d`
- Base: `run-gate-v24.0.0` → `d3e7d11010650e2c9ebd6a9387ce4b2a020bb5d9`
- Source of the port: `e2f1563e22db24c49c6165e764e179de067c16a4`
- No tests or gates were run (by instruction). The only checks were static:
  `py_compile`, `bash -n`, and ruff `F`. Ruff found two pre-existing findings
  that this work did not touch: `test_release_candidate.py` F841 (`python`
  unused) and the `rf""` F541 in `_build_cli_extended_source_wheel`.

## Verdict

**BLOCKED (merge only, not this release gate):**
`cmru/tests/test_tester_unified_image.py` is outside this task's scope. It still
pins the pre-candidate `tester-unified/Dockerfile` RUN text. See F3. All in-scope
findings below are fixed.

## Method

I diffed the candidate against the tag, not against its commit message. The port
is a whole-tree copy: `git diff e2f1563 HEAD -- run-gate-project tester-unified`
is empty. So any tag-side change to those trees that `e2f1563` lacked was
reverted. Since the merge base `24e9d87d6`, the tag has three such commits:
`82df6f0f1` and `d3e7d1101` (the CMRU-prepared `CHANGES.md`) and `7a9717e9d`
(a test fix). Both of the latter were lost.

## Findings

### F1 — High (release blocker), fixed: published 24.0.0 history deleted
The candidate's `CHANGES.md` had lost `<!-- cmru: release history -->`, the
generated `## [24.0.0] - 2026-10-17` section, and its `source-end` marker. Its
`[Unreleased]` section again held the full 24.0.0 pending notes, including the
"**24.0.0:** consumer scripts…" breaking change. CMRU's 24.0.1 preparation would
have republished every 24.0.0 note as 24.0.1 and dropped the released section.
**Fix:** restored the file byte-for-byte from the tag (verified with `diff` from
the history marker to EOF). `[Unreleased]` now holds only the entries that are
new since the tag. The seven carried entries (RG-98/99/100/101/103, the
docker-cli capability install, the docs backslash) match the candidate's text
exactly (verified with `diff`). One new entry covers this repair.

### F2 — High (release blocker), fixed: v24.0.0 release-note test reverted
The candidate had reverted `7a9717e9d`, bringing back a test that looks for the
24.0.0 note between `[Unreleased]` and the history marker. CMRU's preparation
empties that region before `selftest` runs, so the registered release gate
would fail. This is the exact failure `7a9717e9d` fixed. **Fix:** reapplied
`7a9717e9d` (applied cleanly at offset +433). With F1 in place, the note appears
exactly once, inside the dated 24.0.0 section, which is what that test requires.

### F3 — Merge blocker, BLOCKED (out of scope)
`BLOCKED: cmru/tests/test_tester_unified_image.py` still pins the old
`latest) set -- --pointer-url "${CLI_EXTENDED_POINTER_URL}" ;;` body in
`TESTER_REQUIREMENTS_GENERATION_RUN`, inside `CMRU_PRE_SENSITIVE_RUN_BODIES`.
The candidate Dockerfile's body adds `--expect-sha256` and a trailing digest
check, so CMRU's suite will fail against this tree. `e2f1563` changed that test
by +227/-13 for this reason; the port did not bring that change. The correct fix
is a `cmru/**` edit, which this task forbids. Reverting the Dockerfile instead
would drop the digest requirement. Run-Gate's `selftest` lane does not run
CMRU's tests, so this blocks merging the tree wherever CMRU is gated, not the
Run-Gate release gate. When the test is ported, also re-check its Dockerfile
parser against the `ARG`/`LABEL` pair added after `FROM` (F4).

### F4 — Medium, fixed: base image neither validated nor bound to the image
`RUN_GATE_TESTER_UNIFIED_BASE_IMAGE` was passed to the builder unchecked, and
nothing confirmed that the image used it. Buildx only warns about an unconsumed
`--build-arg`. If the `ARG` were renamed, the build would use the default base
while the command and the receipt named another: a false pass of the
"binds the image to the declared base" oracle.
**Fix:**
- The Dockerfile re-declares the `ARG` after `FROM` and sets the label
  `org.vbpub.tester-unified.base-image`.
- `build-image.sh` compares that label with `--base-image`, or with the
  Dockerfile default when the flag is omitted.
- `prepare` refuses a malformed reference (whitespace, leading `-`) and checks
  the label through `_require_base_image_label`.

### F5 — Medium, fixed: invalid overrides refused only after Docker work
An unknown mode or a missing `latest` digest was detected inside
`_tester_image_build_command`, after `cmru handler wheel-build`, which runs a
wheel-builder container. **Fix:** `_tester_image_options()` validates all three
values in `prepare` before any build, and the validated values are passed on. A
parametrized test makes `subprocess.run` and the source build `pytest.fail` on
any call, and asserts that no receipt is written.

### F6 — Low/Medium, fixed: published digest silently ignored outside `latest`
With `RUN_GATE_TESTER_CLI_EXTENDED_SHA256` set and the mode unset, `pinned`, or
`source`, the digest was dropped and the Dockerfile pin (or the source digest)
was installed. Nothing reported the conflict. The builder already refuses the
equivalent flag combination; the consumer now does too
(`… is valid only with …=latest`).

### F7 — Low, fixed (hardening): source wheel copied by a second read
`shutil.copy2` re-read the build output after hashing it. The Dockerfile's
`sha256sum` would have caught a change in between, so this was a refusal, not a
false pass. The code now writes the hashed `payload` bytes.

### Confirmed sound (no change)
- `latest`: `fetch-cli-extended.py --expect-sha256` refuses a pointer that names
  other bytes before downloading, then verifies the downloaded bytes. The
  Dockerfile verifies the file again before install.
- The label is built from the same ARG that the install `RUN` verifies.
- `prepare` compares the label with the digest taken from the exact builder
  command; `pinned`/default compares with the Dockerfile pin.
- Source mode checks the cmru floor, the version (`+bootstrap.source`), the
  METADATA name and version, refuses a different-named stale `local-wheel`
  input, and replaces its own earlier wheel. `local-wheel/*.whl` is ignored by
  git and whitelisted for Docker.

## Changed files
- `run-gate-project/CHANGES.md` — restored tag history; `[Unreleased]` holds
  only the new entries.
- `run-gate-project/tests/test_run_gate.py` — `7a9717e9d` reapplied.
- `run-gate-project/tools/release_candidate.py` — F4–F7.
- `run-gate-project/tests/test_release_candidate.py`:
  - refusal-before-build matrix (8 cases);
  - requested-base accept test, and refusal when the base differs;
  - checks of `_expected_base_image` and the base label;
  - check that the real Dockerfile and builder carry the base label;
  - existing `prepare` tests stub the base label and clear ambient overrides.
- `run-gate-project/CONSUMERS.md`, `run-gate-project/docs/DESIGN-GUIDE.md`.
- `tester-unified/Dockerfile`, `build-image.sh`, `README.md`,
  `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`.

## Residual risks
- ~~`prepare(allow_first_release=True)` treats a checkout with no visible
  `run-gate-v*` tags as a first release.~~ **Fixed in the follow-up below (F8).**
- Source mode relies on `cmru handler wheel-build` deleting `dist/*.whl` first.
  It does today (`handlers.py` `cmd_wheel_build`). The version string
  `<floor>+bootstrap.source` is identical across commits, so if that cleanup were
  removed, a stale same-named wheel would be accepted.
- The base label records the reference, not a digest. A mutable base tag still
  resolves at build time; the receipt's image ID is the immutable identity.
- None of the new tests, nor the edited ones, have been executed. The controller's
  registered `selftest` release gate is the first run.

## Follow-up (same day): F8 — first-release tag ambiguity (24.0.0 review L1)

- Reviewed revision: `5d5800bce067e56ab9b89f0c3f63aebc693cfbc7` (clean, CIU
  `ready`).
- Decision: **fixed, fail-closed, in Run-Gate scope.** CMRU does not prove the
  scoped origin inventory to `prepare`, and it passes `prepare` no tag fact.
  `prepare` now reads the authority itself. That is a READ from origin, not a
  local fallback and not a new fact across the CMRU boundary, so this is not
  BLOCKED.

### Producer-to-consumer path (traced in the installed CMRU that runs the release)
The release runs the installed `cmru 6.1.1.dev434+ga1ad46444`
(`/home/vscode/.venv/.../cmru`), not this checkout's `cmru/` source.

1. `cli.py:6712-6714` calls
   `detect_changed_projects(..., require_pushed_baseline=True)` in the
   transaction checkout, which in turn calls `version.py:216`
   `_latest_tag_for_prefix(prefix="run-gate-v")`
   (`run-gate-project/cmru.toml:40`). That function:
   - lists local tags at `version.py:241-244`, ignoring the return code;
   - takes the highest origin tag from `_highest_remote_tag_for_prefix`
     (`version.py:178-213`), and refuses if it is higher than the local
     candidate (`version.py:256-264`);
   - requires the local candidate to be pushed at the same commit
     (`_tag_pushed_to_origin`, `version.py:114`).
2. Both comparisons use CMRU's `_semver_key` (`release.py:66`), which sorts
   suffixed tags *above* the stable release (`24.0.0-r2 > 24.0.0`). The check
   is "local maximum ≥ origin maximum under CMRU ordering". It does not show
   that this checkout holds origin's stable `run-gate-vX.Y.Z` set. Example: a
   pushed `run-gate-v25.0.0-rc1` held locally, with stable `run-gate-v24.0.0`
   missing locally, passes CMRU's check. Run-Gate would then see no stable tag.
3. The result goes nowhere. `_run_release_gates` (`cli.py:4498-4522`, called via
   `_run_release_gates_on_exact_head`, `cli.py:3114`) passes the step only
   `protected_env={"CMRU_RELEASE_GATE_COMMIT": gate_commit}`.
   `run-gate-project/cmru.toml:86` runs `python tools/release_candidate.py
   prepare` on the host. `prepare` (`release_candidate.py:706`) re-reads tags
   later, and nothing ties that read to CMRU's earlier one. Also,
   `CMRU_RELEASE_GATE_COMMIT` is only an environment variable: anyone can set it
   to `HEAD` by hand.
4. So the inventory CMRU verified does **not** reach `prepare`. Neither its
   exact Run-Gate namespace nor any binding to this candidate is guaranteed.

Side observation, CMRU's own code (I did not edit it; for the controller to file
in CMRU's backlog after checking `cmru/CHANGES.md`): `version.py:241-245`
ignores a failed `git tag --list`. A local failure then looks like "no local
tags": absence read as emptiness. The origin comparison usually turns that into
a refusal, but with a misleading "fetch tags" remedy.

### The four states, and what `prepare` now does
`_release_baseline()` (`release_candidate.py:216`) is used only when
`prepared_release_version(allow_first_release=True)` is called, that is, from
`prepare`. It uses `_origin_release_tags()` (`release_candidate.py:171`), which
runs `git ls-remote --exit-code --tags origin 'refs/tags/run-gate-v*'` with
`GIT_TERMINAL_PROMPT=0` and a 120 s timeout, and parses the output strictly.

| State | Before | Now |
|---|---|---|
| Origin truly has no stable tag | first release | first release, only on exit 2 with empty output, or a fully parsed listing with no stable tag (prereleases do not count) |
| Local `git tag --list` fails | refusal (`_git` raises) | unchanged refusal, before origin is consulted; tested explicitly |
| Local listing succeeds but tags are missing or older than origin's | **first release / stale baseline (false pass)** | refused: `origin has stable release tag <tag> but this checkout's newest is <tag\|none>; fetch the release tags` |
| Origin unreadable, malformed, or out of namespace | n/a (origin never read) | refused as indeterminate. Covers exit ≠ 0/2, exit 2 with refs, exit 0 with no refs, a bad line or object ID, a foreign ref, a timeout, and a missing `git` |

The version-newer refusal is unchanged: the prepared version must be greater
than the newest local stable tag (tested at 24.0.0 → refused, 24.0.1 →
accepted). A local-only tag above origin's only makes that check stricter. The
ordinary `prepared_release_version()` and `_latest_release_tag()` still read
local tags only. The gate container's selftest calls them (`7a9717e9d`'s
test), and a test asserts that this path never queries origin.

**Legitimate first release, constructed:** the real-Git test creates a bare
`origin` with no tags. A clone of it reaches `_release_baseline() is None` and
is accepted. The same construction with `run-gate-v23.10.0`/`v24.0.0` on origin
and a `--no-tags` clone is refused until `git fetch --tags origin`. After that
it returns `("run-gate-v24.0.0", "24.0.0")`. This test exercises the real
`git ls-remote` input contract, not a fake.

**Producer probe (read-only, 2026-10-17):** in this checkout,
`git ls-remote --exit-code --tags origin 'refs/tags/run-gate-v*'` exited 0 and
listed 18 tags, each with its `^{}` line (`run-gate-v24.0.0` → peeled
`d3e7d1101…`). The local listing also holds 18 tags. A non-matching pattern
exited 2. The 24.0.1 release should therefore find a non-stale baseline. It now
adds one origin read to `prepare`, the same remote CMRU queries moments earlier.

### Coverage gate (`coverage_gate.py:361-385`), not changed
The coverage gate runs inside the selftest container and still reads local tags
only. Inside the release transaction, its first-release `SKIPPED` path is now
reachable only after `prepare` showed that origin has no stable tag (or after a
tag was deleted locally between the two steps). The container mounts the same
repository refs, and `selftest` runs only after `prepare` and `verify-running`
(`run-gate.toml:57`). A tagless clone running `run-gate selftest` *outside* a
release still reports `diff-coverage SKIPPED` with exit 0. That is labelled as a
skip, not a release certification. Binding it to a receipt-recorded "origin
shows no stable tag" fact would need a receipt-schema change; I left it as a
residual.

### F8 changed files
- `run-gate-project/tools/release_candidate.py`: added `_stable_release_tags`,
  `_origin_release_tags`, `_release_baseline`; `prepared_release_version`
  uses them when `allow_first_release` is set; docstring updated.
- `run-gate-project/tests/test_release_candidate.py`: replaced the old
  "empty local means first release" test with:
  - first release accepted only when origin shows no stable tag (2 cases);
  - the ordinary path never queries origin;
  - indeterminate origin refused (8 cases);
  - stale or missing local tags refused (3 cases);
  - version-newer refusal preserved;
  - a failing local tag listing refused;
  - the real-Git test of a tagless clone against a real origin.
- `run-gate-project/docs/DESIGN-GUIDE.md`, `CONSUMERS.md`, `README.md`,
  `CHANGES.md`.

### F8 residual risks
- The release now needs origin to be readable from `prepare` on the host. A
  network or authentication failure refuses the release rather than skipping
  the check, which is intended. `origin` is the same remote CMRU's plan step
  reads.
- The origin read and the local read are two moments; a concurrent tag push or
  delete between them is not serialized. CMRU creates the new tag only after
  the gate.
- Static checks only (`py_compile`, ruff `F`: only the two pre-existing
  findings). Nothing was executed.
