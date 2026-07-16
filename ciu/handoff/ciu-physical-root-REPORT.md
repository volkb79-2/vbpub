# REPORT — ciu-physical-root-preset-env fix

Branch: `fix/ciu-physical-root-preset-env`
Worktree: `/workspaces/vbpub/.worktrees/ciu-physical-root`

## Root cause (confirmed)

**Candidate (a) — pre-set-env contamination — confirmed. Candidate (b) ruled out.**

`_detect_physical_repo_root` (`src/ciu/workspace_env.py:397-426`, pre-fix) returned a pre-set
`$PHYSICAL_REPO_ROOT` env var **unconditionally**, before ever consulting
`/proc/self/mountinfo`. The live devcontainer's `~/.bashrc:173-175` auto-`source`s
`${REPO_ROOT}/ciu.env` whenever `REPO_ROOT` is set:

```bash
if [[ -n "${REPO_ROOT:-}" && -f "${REPO_ROOT}/ciu.env" ]]; then
    source "${REPO_ROOT}/ciu.env"
fi
```

dstdns is this devcontainer's primary workspace, so `REPO_ROOT=/workspaces/dstdns` is set early
in every login shell, and dstdns's own `ciu.env` (which contains `export
PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"`) gets sourced, leaving `PHYSICAL_REPO_ROOT`
pinned to dstdns's host path in every subsequent shell — including one that later `cd`s into
`vbpub/nyxloom` (a sibling repo, nested inside `vbpub`) and runs `ciu env generate` there. The
pre-set (stale, cross-repo) env var then won unconditionally over the correct,
freshly-read mountinfo signal.

Mountinfo itself (candidate b — the pre-2026-07-15-fix docker-ps fallback) is **not** the live
cause: it already resolves nyxloom's physical root correctly when reached (proven below), matching
the handoff's own observation that the sibling `nyxloom/ntfy/.ciu/ciu.compose.overlay.yml` already
carries the correct physical root.

### Reproduction

```bash
cd /workspaces/vbpub/.worktrees/ciu-physical-root/ciu
PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -c "
import os
from pathlib import Path
import ciu.workspace_env as we

repo_root = Path('/workspaces/vbpub/nyxloom')

print('=== (i) PHYSICAL_REPO_ROOT UNSET (mountinfo path) ===')
os.environ.pop('PHYSICAL_REPO_ROOT', None)
result = we._detect_physical_repo_root(repo_root)
print('detected physical_root:', result)
print('network:', we._compute_network_name(result))

print()
print('=== (ii) PHYSICAL_REPO_ROOT SET to dstdns (contamination) ===')
os.environ['PHYSICAL_REPO_ROOT'] = '/home/vb/volkb79-2/dstdns'
result2 = we._detect_physical_repo_root(repo_root)
print('detected physical_root:', result2)
print('network:', we._compute_network_name(result2))
"
```

Output (against the PRE-FIX code, run first to confirm the bug before patching):
```
=== (i) PHYSICAL_REPO_ROOT UNSET (mountinfo path) ===
detected physical_root: /home/vb/volkb79-2/vbpub/nyxloom
network: {'REPO_NAME': 'nyxloom', 'INSTANCE_ID': '1dd3d1', 'DOCKER_NETWORK_INTERNAL': 'nyxloom-1dd3d1-network'}

=== (ii) PHYSICAL_REPO_ROOT SET to dstdns (contamination) ===
detected physical_root: /home/vb/volkb79-2/dstdns
network: {'REPO_NAME': 'dstdns', 'INSTANCE_ID': '98535c', 'DOCKER_NETWORK_INTERNAL': 'dstdns-98535c-network'}
```

(i) proves mountinfo alone is correct; (ii) reproduces the exact live bug (`REPO_NAME=dstdns`,
`INSTANCE_ID=98535c`, `DOCKER_NETWORK_INTERNAL=dstdns-98535c-network` — byte-identical to the
broken `/workspaces/vbpub/nyxloom/ciu.env`). Full details + additional runs in
`handoff/ciu-physical-root-LOG.md`.

## Code change

`src/ciu/workspace_env.py`, `_detect_physical_repo_root` (lines ~397-441 post-fix). Full diff:

```diff
 def _detect_physical_repo_root(repo_root: Path) -> Path:
+    """... (docstring explaining the refined contract) ..."""
     physical_root = os.environ.get("PHYSICAL_REPO_ROOT")
-    if physical_root:
-        return Path(physical_root).resolve()
+    preset_path = Path(physical_root).resolve() if physical_root else None

     via_mountinfo = _physical_root_from_mountinfo(repo_root)
+
+    if preset_path is not None:
+        if via_mountinfo is None or via_mountinfo == preset_path:
+            return preset_path
+        print(
+            f"ciu: ignoring pre-set PHYSICAL_REPO_ROOT={preset_path} for "
+            f"repo_root={repo_root} — inconsistent with the mountinfo-derived "
+            f"physical root {via_mountinfo}. Using the mountinfo-derived value "
+            "(the pre-set env is likely stale/cross-repo shell state, e.g. "
+            "inherited from a different repo's sourced ciu.env). Unset "
+            "PHYSICAL_REPO_ROOT or update it if this override is intentional.",
+            file=sys.stderr,
+        )
+        return via_mountinfo
+
     if via_mountinfo is not None:
         return via_mountinfo

     # Contract 2 fallback (mountinfo yielded nothing): existing
     ...
```

New precedence: **pre-set env (only if consistent with mountinfo, or mountinfo absent) →
mountinfo → docker-ps devcontainer-origin label → identity.** The legitimate manual-override use
case (native host, mountinfo unreadable) is fully preserved — only a pre-set value that
*disagrees* with a freshly-read mountinfo signal for the *same* `repo_root` is overridden, with a
stderr warning naming the ignored value.

## Oracle evidence

**O1 — nested contamination is corrected, not honored.**
`tests/tests/test_physical_root_mount_table.py::TestPresetEnvConsistency::test_preset_env_ignored_when_inconsistent_with_repo_root`:
preset `PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns`, `repo_root=/workspaces/vbpub/nyxloom`,
mountinfo maps `/workspaces/vbpub` → `/home/vb/volkb79-2/vbpub` (nyxloom has no dedicated mount,
longest-match resolves through the parent bind). Asserts `result ==
Path("/home/vb/volkb79-2/vbpub/nyxloom")` and `result != Path("/home/vb/volkb79-2/dstdns")`, plus
the stderr warning names both `PHYSICAL_REPO_ROOT` and `dstdns`. PASSED.
Also proven end-to-end via `TestRegressionBoundNestedPresetEnvContamination
::test_generate_ciu_env_nyxloom_shaped_ignores_contaminating_preset` (full `generate_ciu_env`
pass, real mountinfo-parsing path, no `_detect_physical_repo_root` monkeypatch). PASSED.

**O2 — consistent preset, or mountinfo-absent preset, still wins (manual override preserved).**
- `TestPresetEnvConsistency::test_preset_env_wins_when_consistent_with_mountinfo`: preset
  `/home/vb/volkb79-2/vbpub` == mountinfo-derived value for `/workspaces/vbpub` → returned as-is.
  PASSED.
- `TestFallbackWhenMountinfoYieldsNothing::test_preset_env_still_wins_over_mountinfo` (reconciled):
  preset `/explicit/override`, `repo_root=/workspaces/totally-unmounted-repo` (no mountinfo entry)
  → preset honored unconditionally (mountinfo yields nothing to contradict it). PASSED.

**O3 — `generate_ciu_env` for a nyxloom-like layout yields correct identity, never dstdns.**
`TestRegressionBoundNestedPresetEnvContamination
::test_generate_ciu_env_nyxloom_shaped_ignores_contaminating_preset`: repo_root shaped
`.../workspaces/vbpub/nyxloom`, contaminating preset = dstdns's physical root, mountinfo maps only
the parent `vbpub` bind (no dedicated nyxloom mount — mirrors the live layout exactly). Generated
`ciu.env` asserted to contain `PHYSICAL_REPO_ROOT=".../vbpub/nyxloom"`, `REPO_NAME="nyxloom"`, and
explicitly asserted to NOT contain `REPO_NAME="dstdns"`, `INSTANCE_ID="98535c"`, or
`dstdns-98535c-network`. PASSED.

**O4 — docs match SPEC.md's precedence + the new rule.**
`docs/CIU.md:543` (provenance table row) and `docs/CONFIG.md:324` (provenance table row) both
updated to state the full 4-step precedence (pre-set-if-consistent → mountinfo →
docker-ps-devcontainer-origin → native-identity), matching `docs/SPEC.md:125` (already correct,
unchanged). Each doc also gained an explanatory paragraph describing the consistency-check
rationale and the contamination scenario it guards against. Neither doc row now claims
"docker-ps only."

## Gate result

```
cd /workspaces/vbpub/.worktrees/ciu-physical-root/ciu
PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests -q
```
```
931 passed in 4.08s
```

Optional coverage floor run (`/workspaces/vbpub/.venv/bin/python run-ciu-tests.py`):
```
Required test coverage of 75% reached. Total coverage: 76.00%
931 passed in 5.71s
```

**Environment note (not a code issue, disclosed per the review-checklist's
environment-specific-claims caution):** the shared `/workspaces/vbpub/.venv` was initially missing
three of ciu's own **declared** dependencies (`tomli_w`, `Jinja2` — both in `pyproject.toml`
`[project.dependencies]` — plus `pytest-cov` for the optional coverage run), causing ~94
collection/test failures across unrelated modules (`test_ciu_config_model.py`,
`test_spec_contracts.py`, etc.) before any of my edits. Confirmed pre-existing and unrelated to
this fix by `git stash`-ing my change and re-running: 93 failures with dependencies installed
minus my one intentionally-changed test = same failure set. Installed the three packages into the
shared venv (`pip install tomli_w "Jinja2>=3.1.2" pytest-cov`) to restore the declared dependency
closure; this is an environment fix, not a code change, and touches no forbidden files. After
that, the only failure was `test_preset_env_still_wins_over_mountinfo` — the one test this handoff
explicitly asks to reconcile — and it now passes with the updated fixture.

## Files touched

- `ciu/src/ciu/workspace_env.py` — the fix (`_detect_physical_repo_root`)
- `ciu/tests/tests/test_physical_root_mount_table.py` — reconciled
  `test_preset_env_still_wins_over_mountinfo`; new `TestPresetEnvConsistency` (O1/O2) and
  `TestRegressionBoundNestedPresetEnvContamination` (O3) classes
- `ciu/docs/CIU.md` — provenance table row + explanatory paragraph
- `ciu/docs/CONFIG.md` — provenance table row + explanatory paragraph
- `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` — new CIU-10 entry (status board + detail section)
- `ciu/handoff/ciu-physical-root-LOG.md`, `ciu/handoff/ciu-physical-root-REPORT.md` — this
  handoff's own artifacts

No stack artifacts (nyxloom/ntfy/ciu.env, overlays, etc.) were committed — only ciu code/tests/docs,
per scope.
