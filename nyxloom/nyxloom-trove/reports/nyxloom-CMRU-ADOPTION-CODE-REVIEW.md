# Adversarial code review — `8277ebbd` "adopt cmru release orchestration, ship the wheel via mdt"

**Reviewer:** fresh adversarial reviewer (no authoring context)
**Date:** 2026-09-08
**Worktree:** `/workspaces/vbpub/.worktrees/nyxloom-cmru-adoption` (branch `nyxloom-cmru-adoption`)
**Stakes:** ACCEPT authorises `cmru release --project nyxloom` — a real git tag, a real GitHub Release, and real GHCR image pushes. Findings are weighted accordingly.

---

## VERDICT: **ACCEPT-conditional**

The three-file diff is substantively correct, the dependency graph is topologically valid, and the
`[steps.run-tests]` change is a genuine strict improvement that I re-verified by running the gate
myself (PASS, exit 0). **But `cmru release --project nyxloom` must NOT be run next.** Two blockers
(F1, F2) stand between this commit and a safe release, and F2 is a defect *in this diff* whose
consequence is permanently unrecoverable once the tag is pushed.

Conditions, in order:

1. **F1** — merge to `main` and push before any release attempt (release reads `origin/main`).
2. **F2** — drop the `!` from the commit subject, or release with an explicit `--set-version`.
   `nyxloom-v1.0.0` is the wrong version and cannot be taken back.
3. **F4/F5** — swap the `[steps.push]` command order and add `required_env` (cheap; removes the
   partial-release failure mode entirely).
4. **F6** — declare a governed builder before building images on this host.

F3, F7, F8, F9 are informational/follow-up and do not block.

---

## Scope check (prompt item 7)

`git show --stat 8277ebbd` — exactly the three claimed files, nothing else:

```
 cmru.orchestration.toml                          | 17 +++++++++++------
 modern-debian-tools-python-debug/pip/wheels.list |  1 +
 nyxloom/cmru.toml                                |  8 +++++++-
```

**PASS.** No stray edits.

---

# BLOCKERS

## F1 — The commit is not on `origin/main`; the release would fail with the exact error it exists to fix

`cmru release` does not release your working tree. It fetches `origin/main` and cuts an isolated
worktree from it (`cmru/src/cmru/cli.py:2313-2320`, `cmru/src/cmru/transaction.py:213`). The
`--allow-uncommitted` help text says so outright: *"...local uncommitted changes (which origin/main
won't include)"*.

Verified:

```
$ git merge-base --is-ancestor 8277ebbd origin/main
NO -- 8277ebbd is NOT on origin/main

$ git show origin/main:cmru.orchestration.toml | grep nyxloom
(nyxloom ABSENT from origin/main orchestration)

$ git log -1 --format='%h %s' origin/main
2fc23421 chore(ciu): prepare release inputs
```

So `cmru release --project nyxloom` run right now reads an orchestration file with no `nyxloom`
entry and fails with **"Unknown or non-orchestrated project: nyxloom"** — verbatim the failure this
commit was written to eliminate.

Also note `assert_local_main_not_ahead` (`transaction.py:100-113`) raises if local `main` is *ahead*
of `origin/main`; behind is only a warning (`cli.py:2315-2319`).

**Prescription:** merge `nyxloom-cmru-adoption` into `main` and push. Only then release.

---

## F2 — `nyxloom-v1.0.0` is a self-inflicted, wrong, and permanently unrecoverable version

`cmru status --project nyxloom` reports `major` / `nyxloom-v1.0.0`, which I reproduced. The prompt
asked whether a scanner bug inflated it. It did not — but the input is wrong.

Across `nyxloom-v0.3.0..HEAD -- nyxloom/` there are **279 commits, 0 `BREAKING CHANGE:` footers, and
exactly 2 `!`-marked subjects**:

```
feat(nyxloom)!: adopt cmru release orchestration, ship the wheel via mdt   <-- THIS commit
feat(cmru)!: adopt strict portable project contracts                       <-- a cmru commit
```

Neither is a break in nyxloom's public surface:

- The first is **this commit itself**. Its own message justifies the `!` as: *"`[steps.run-tests]`'s
  change to a real gate means any future push ... may now legitimately fail on coverage or canary
  grounds."* That is a **CI gate-strictness** change. Conventional Commits' `!` denotes a breaking
  change to the *consumer-facing API*. Nobody importing `nyxloom` or invoking the `nyxloom` CLI is
  affected by a stricter release gate. The marker is misapplied.
- The second is a **cmru-scoped** commit that merely touched paths under `nyxloom/`. It is not a
  break in nyxloom's API either.

So the entire MAJOR bump rests on two mis-attributed markers, one of which the diff under review
introduces itself.

**Why this is a blocker and not a nit — the tag is minted and pushed BEFORE build/publish, and is
never rolled back.** Release order (`cli.py:1512-1574`):

```
1517  _prepare_release_projects(...)      # steps.prepare + CHANGES.md + commit
1520  _run_release_gates(...)             # steps.run-tests
1522  transaction.promote_workspace(...)  # ff-push source to origin/main
1544  release_cmd(...)                    # <-- CREATES the annotated tag
1552  _push_tags(repo_root, [tag])        # <-- PUSHES the tag to origin
1556  _run_project_steps(..., [build_step, "push"], ...)   # build, THEN publish
```

`revert_promotion` (`transaction.py:1071-1109`) is a source-tree `git revert` push only.
`cmru/docs/RELEASE-TRANSACTIONS.md:184-186` is explicit: *"tags/GitHub Releases/ghcr pushes aren't
reverted by a source-tree `git revert`."* Tag deletion exists only in the separate `cleanup` verb
(`cli.py:955-956`).

Secondary concern: nyxloom's own `wheels.list` entry (added by this very diff) describes the project
as *"CLI/dev-tooling only — the resident nyxloomd daemon is offline/not deployed."* Jumping 0.x → 1.0.0
publicly signals a stable, committed API for something the commit simultaneously documents as not
deployed.

**Prescription (pick one):**
- Amend the subject to `feat(nyxloom):` (no `!`) before merging — `cmru status` then computes a
  MINOR bump to `nyxloom-v0.4.0`; **or**
- release explicitly: `cmru release --project nyxloom --set-version 0.4.0`.

Either way, rehearse with `cmru release --project nyxloom --dry-run` first — it prints
`[DRY] Would tag: <tag>` and performs no commits, gates, tags, builds or publishes
(`cli.py:2481-2491`).

---

# MAJOR

## F3 — `artifacts = ["wheel"]` would NOT defer the OCI push; it controls nothing

This is the direct answer to the prompt's most consequential question, and it corrects the premise.

`ProjectConfig.artifacts` is **purely declarative**. `cli.py:58-60` documents it as *"Declared
released-output vocabulary. It is descriptive and retention-facing; **an artifact name never selects
a runner or a publication implementation**."* SPEC S-REL.2 (`cmru/docs/SPEC.md:302-306`): *"It never
produces a command."*

It is read by exactly two validators — `_parse_release_policy` (`cli.py:313-327`, membership in
`{"wheel","bundle","tarball","oci-image"}`) and `_parse_artifacts` (`config.py:230-245`, non-empty)
— and by **zero** runtime consumers. cmru therefore does not require Docker/buildx for an
`oci-image` project, does not verify an image was pushed, and does not validate the Release
afterwards. (The `--retain-artifacts-on-release` flag keys off the different field
`[project.release].artifact_dirs`, `transaction.py:726-735`.)

There is also **no `--steps` / `--only` flag on `cmru release`** (full flag list at
`cli.py:2176-2225`).

**Consequence:** editing `artifacts = ["wheel", "oci-image"]` → `["wheel"]` would be a **no-op that
changes nothing** — both `build-push.py` invocations would still run and still push to GHCR. To
genuinely defer OCI you must remove/comment the two `build-push.py` commands from `[steps.build]`
and `[steps.push]` (`nyxloom/cmru.toml:48` and `:55`).

**My recommendation is not to defer.** See "The judgment call" below.

## F4 — `[steps.push]` ordering maximises blast radius; pwmcp does it the safe way round

`nyxloom/cmru.toml:53-56`:

```toml
[steps.push]
commands = [
  { label = "nyxloom: publish wheel",    argv = [... "wheel-publish" ...] },   # creates the GitHub Release (irreversible)
  { label = "nyxloom: push OCI images",  argv = ["python3", "build-push.py", "--push"] },   # the failure-prone one
]
```

pwmcp — the estate's proven OCI precedent, and the file nyxloom's `build-push.py` is visibly modelled
on — orders these the other way (`pwmcp/cmru.toml:58-63`): image push first, bundle publish second.

Commands run sequentially and a non-zero exit aborts the step (`runner.py:382`). Combined with F2's
no-rollback finding, an OCI failure leaves **tag pushed + GitHub Release published + no images**.
`RELEASE-TRANSACTIONS.md:120-123` names this the current recovery limit (KI-06): *"a post-tag
publication failure is not an automatic retry... do not assume a plain resume will publish an
existing tag."*

**Prescription:** swap the two commands so the OCI push runs first.

## F5 — `[steps.push]` declares no `required_env`

cmru has a first-class fail-fast mechanism for exactly this, and mdt uses it
(`modern-debian-tools-python-debug/cmru.toml:86`):

```toml
required_env = ["GITHUB_USERNAME", "GITHUB_PUSH_PAT"]
```

It is the **only** `required_env` in any `cmru.toml` estate-wide. nyxloom's push step has none, so
credential validation happens inside `build-push.py:76-87` (`require_push_environment()`) — which
runs *after* `wheel-publish` has already created the Release.

To be clear about actual risk today: **credentials are present and will be injected.**
`apply_release_env` (`cli.py:470-496`) exports `GITHUB_USERNAME` = `[github].owner`, `GITHUB_REPO`,
`GITHUB_OWNER_TYPE`, and `GITHUB_PUSH_PAT` sourced from root `cmru.secret.toml` `[github].token`
(confirmed present; values not inspected). Every subprocess inherits them (`runner.py:338,350`), and
**both** the build and push steps receive them (`cli.py:1389-1390`). `require_push_environment()`
fails **loudly** — `fail()` prints `[ERROR] ...` to stderr and raises `SystemExit(1)`
(`build-push.py:41-43, 84-87`). There is **no silent no-op / no partial-push-exit-0 path.** So the
prompt's worst case for finding 5 does not materialise.

Note one trap that does *not* bite here: `apply_release_env` explicitly `pop`s `GITHUB_TOKEN`
(`cli.py:483`) and exports only `GITHUB_PUSH_PAT`. `build-push.py:78` reads
`GITHUB_PUSH_PAT or GITHUB_TOKEN`, so it is satisfied — but any future code reading `GITHUB_TOKEN`
alone would find nothing.

**Prescription:** add
`required_env = ["GITHUB_USERNAME", "GITHUB_REPO", "GITHUB_OWNER_TYPE", "GITHUB_PUSH_PAT"]`
to `nyxloom/cmru.toml [steps.push]`.

## F6 — Uncapped, ungoverned image build on a host shared with a production game server

`nyxloom/build-push.py:112-116`:

```python
def _bake(extra: list[str], version: str) -> None:
    argv = ["docker", "buildx", "bake", "-f", str(BAKE_FILE), "all", *extra]
```

**No `--builder`.** And `nyxloom/cmru.toml` declares neither of the two governance mechanisms its
siblings use:

- pwmcp declares `[project_metadata.builder]` (name/memory/cpu_shares/cpu_quota) and its
  `build-push.py:92-183` *creates and verifies* a governed `docker-container` builder, then bakes
  with `--builder <name>` (`:200`, `:227`), failing if Docker did not apply the limits (`:183`).
- mdt sets `BUILDX_BUILDER = "mdt-governed-v1"` in `[env]` (`cmru.toml:21`) plus explicit
  `MDT_BUILDER_MEMORY`/`CPU_QUOTA` (`:22-26`).

nyxloom sets neither, so it inherits the ambient default. Verified active default:

```
$ docker buildx ls
NAME/NODE   DRIVER/ENDPOINT   STATUS    BUILDKIT
default*    docker
```

— the `docker` driver, with no memory or CPU ceiling. The `all` group builds **two** targets
(`docker-bake.hcl:59`): `nyxloomd` (a ~280 MB mdt base + venv) and `nyxloom-agent-cli`
(`node:26-slim` plus three pinned npm CLIs — codex 0.145.0, reasonix 1.17.12, opencode 1.18.1), and
the release runs `--build` then `--push` (a second bake pass).

This is precisely the failure mode the estate's host rule exists for: 8 cores shared with a
production game server, where load hit **85 on 2026-09-02** from an uncapped gate container. Observed
load during this review was already 5.8-9.1.

**Prescription:** add a `[project_metadata.builder]` block mirroring pwmcp's and pass `--builder` in
`_bake()`, or at minimum set `BUILDX_BUILDER` in `[env]`; and run the release when the host is quiet.

---

# MINOR

## F7 — structlog is **one upstream minor release** from breaking the mdt build

The commit claims the deps are *"already present in requirements/toolkit.txt, verified — no new deps
needed."* Presence is real; **version compatibility is accidental**, and closer to the edge than the
claim implies.

`modern-debian-tools-python-debug/requirements/toolkit.txt:57-59` declares all three with **no
version constraint at all**:

```
57  structlog             # structured logging
58  PyYAML                # YAML parse/emit
59  jsonschema            # JSON-Schema validation for interactive inspection
```

(Only 1 of the file's 39 requirement lines carries any constraint.) nyxloom's `pyproject.toml:6`
requires `PyYAML>=6, jsonschema>=4, **structlog>=24,<27**`.

Installed here: `structlog 26.1.0`, `PyYAML 6.0.3`, `jsonschema 4.26.0`. All satisfy nyxloom today —
but structlog is at **26.x against an exclusive `<27` ceiling**. `wheels.list:3-4` documents that
wheels install via `pip install --no-index --find-links`, so pip cannot downgrade structlog from the
index: the day structlog 27.0 ships, the next mdt image rebuild resolves 27 and the nyxloom wheel
install **fails the mdt build**.

**Prescription:** constrain `structlog<27` in `toolkit.txt` (or lift nyxloom's ceiling deliberately).

## F8 — Undeclared artifact-level dependency on mdt (a lagging cycle)

The prompt asked whether anything in nyxloom's steps needs another first-party project released
first. It does, and `depends_on = ["cmru"]` does not say so.

`nyxloom/nyxloomd/Dockerfile:30`:

```
ARG NYXLOOM_BASE=ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest
```

So nyxloom's OCI image is `FROM` an **mdt** image, while mdt now depends on nyxloom's wheel. That is
a cycle at the artifact level, invisible in the declared graph.

It is **not a deadlock**, because the base is pulled by the floating `-latest` tag — the *previously
published* mdt image — not built in the same run. I confirmed that exact ref resolves
(`docker manifest inspect` → OK, anonymous). The stable consequence is that nyxloom images are always
one mdt generation behind, and a full-estate release ordering nyxloom before mdt bakes nyxloom images
on the older base.

`ciu` is not needed by any nyxloom step (`build-push.py` and `docker-bake.hcl` reference no ciu
artifact); `topos` likewise. So `depends_on` is not *wrong* for release ordering — it is just silent
about the image-layer edge.

**Prescription:** add a comment beside `[orchestration.project.nyxloom]` recording the
`FROM mdt:-latest` edge so the next person doesn't "fix" the ordering into a real cycle.

## F9 — mdt will bake a stale nyxloom **0.2.0** wheel until the release actually runs

`wheels.list:8-10` notes the entry resolves via the `<name>-latest` GitHub Release. That release
already exists and is **not** empty:

```
$ curl -sL .../releases/download/nyxloom-latest/latest.json
{"project":"nyxloom","version":"0.2.0","tag":"nyxloom-v0.2.0",
 "asset":"nyxloom-0.2.0-py3-none-any.whl", ...}
```

So the `nyxloom` line in `wheels.list` is **live now** and resolves to a wheel published 2026-07-30 —
it does not sit inert waiting for the release. A standalone mdt rebuild before the nyxloom release
ships 0.2.0 into the image. `project_order` fixes this on a full-estate run; a targeted mdt rebuild
does not.

---

# Verified correct — including hypotheses I raised and then disproved

Recorded deliberately, so the ACCEPT rests on checks that were actually run, not assumed.

**Orchestration registration (prompt 1) — correct.** Re-ran `cmru dependencies` read-only:

```
  nyxloom <- cmru  (declared release order)
  modern-debian-tools-python-debug <- ciu, cmru, topos, nyxloom  (declared release order)
    consumes first-party wheels: ciu, cmru, topos, nyxloom
PREFLIGHT: PASS
```

`nyxloom` is present in **both** `project_order` and `default_projects`, at index 4 — before
`modern-debian-tools-python-debug` (index 5). Topologically valid. The regenerated comment block
matches the tool's live output byte-for-byte, so it was genuinely regenerated, not hand-edited.

**mdt's widened `depends_on` (prompt 2) — inert.** `grep -rn "depends_on"
modern-debian-tools-python-debug/` returns **zero** hits: nothing in mdt reads the list, order-
sensitively or otherwise. cmru consumes it only for release ordering. No behaviour change beyond
"wait for nyxloom too".

**The `[steps.run-tests]` change (prompt 3) — a strict superset, and I ran it.** Old vs new judged
command:

```
old:  pytest tests -n auto -q                                              (exit code only)
new:  pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
      + assay R0 (tests-pass) + R1 (changed-line coverage, fail_under=100.0,
        allow_excluded=false, base=origin/main)
```

The new argv (`nyxloom/assay.toml:41`) is the old argv **plus** coverage flags, and adds an
independent judge. It is never weaker, and introduces no new way to block a release that the old
command would have passed *except* the coverage floor — which is the intended point of the change.

I ran `./run-gate.py tester-unified` myself from a clean tree (host load checked first: 3.63; run
under `nice -n 10 ionice -c3`; the gate container was the only run-gate container on the host):

```
tester-unified: PASS (exit 0)
  commit: 8277ebbde1ea28d667592b2d4c83a931fd5a2330
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
```

Verdict artifact: R0 `PASS` and R1 `PASS`, both `verified_by_assay: true`; judge provenance
`assay 4.0.0` zipapp, sha256 verified. The `assay-4.0.0.pyz` pin is present and its checksum
validated at gate start.

> **Honest caveat on R1.** The verdict shows
> `"considered": 0, "covered": 0, "executable": 0, "pct": 100.0`, base resolved to `2fc23421`
> (origin/main) by merge-base. This commit touches no file under `src/`, so the changed-line judge
> considered **zero lines** — the 100% is vacuous. R1 passed without exercising anything on this
> diff. R0 (the full suite) is what actually passed. The same will hold at release time, since
> cmru's own release commit is a CHANGES.md commit: the release gate is effectively "full suite
> green" plus a vacuous coverage check. That is still ≥ the old bare pytest, so the change remains
> equivalent-or-stricter — but "changed-line coverage was verified for this diff" would be an
> overclaim.

**Version derivation is correct — images will be tagged right.** I suspected the tag might be minted
*after* the build, which would tag images `0.3.0-N-gsha` instead of the release version. It does not:
the tag is created (`cli.py:1544`) and pushed (`:1552`) **before** the build/push steps (`:1556`), and
SPEC S-REL.4a states the cycle as *"prepare → gate → promote → tag → build → publish."* So
`resolve_version()`'s `git describe --tags --match 'nyxloom-v*'` (`build-push.py:57-62`) sees the new
tag on HEAD. No `NYXLOOM_VERSION` is injected by cmru — none is needed.

**Disproved: "the `docker` driver can't push."** F6 established the build runs on the default `docker`
driver, and I expected `bake --push` to fail on driver capability. Probed safely against an
unroutable registry:

```
#4 pushing localhost:1/reviewprobe:x with docker
#4 ERROR: Get "http://localhost:1/v2/": dial tcp [::1]:1: connect: connection refused
```

It reached the network layer — the driver **does** push. Only the (deliberately) unroutable endpoint
failed. F6 stands as a *governance* finding, not a functional one.

**Disproved: "run-gate breaks inside cmru's release worktree."** run-gate bind-mounts the fixed host
path `/home/vb/volkb79-2/vbpub`; if cmru cut its release worktree outside the repo, the gate could not
see the code. It does not — `transaction.py:213`: `parent = repo_root / ".worktrees"`, guarded by
`expected_parent` at `:672-674`. Inside the mount. No issue.

**GHCR / tag-collision surface (prompt 8) — clean.**
- `nyxloom-v1.0.0` does not exist locally or on origin. No collision.
- The `nyxloom-latest` tag/Release convention **already exists** (created 2026-07-27) and matches
  `ciu-latest`/`cmru-latest`/`assay-latest`. A release moves it — normal, not a disturbance. See F9
  for the one real consequence.
- GHCR packages `nyxloomd` and `nyxloom-agent-cli` **already exist and are accessible**, so
  `sync_ghcr_package_visibility` (`build-push.py:90-109`, packages list at `:34` — correctly matching
  the bake tags at `docker-bake.hcl:35,54`) operates on existing packages rather than brand-new ones.
- This is **not** nyxloom's first-ever GitHub Release: `nyxloom-v0.1.0` and `nyxloom-v0.2.0` already
  exist, and `nyxloom-v0.2.0`'s wheel has 10 downloads. The commit message's framing of a first-ever
  release is inaccurate; no downstream consumer is newly exposed. (dstdns's `.assay-inbox` pattern is
  assay-specific and untouched by a nyxloom release.)
- `cmru standards --project nyxloom` → *"1 project(s) conform"*, reproduced.

---

# The judgment call: full wheel+OCI, or wheel-only first?

**Recommendation: attempt the full wheel + OCI release — but rehearse the image build first, and fix
F4 before you do.** Deferring OCI is *not* worth it here, for three concrete reasons:

1. **The obvious way to defer doesn't work.** Per F3, setting `artifacts = ["wheel"]` changes
   nothing — the OCI commands still run. Deferring requires editing `[steps.build]`/`[steps.push]`,
   i.e. a second commit through review, then a *third* to restore them. That is more churn and more
   review surface than the risk it avoids.
2. **The credential risk the prompt worried about does not exist.** Credentials are present and
   injected into both steps, and the failure mode is a loud `SystemExit(1)`, never a silent no-op or
   a partial push at exit 0 (F5). The same token already pushes pwmcp and mdt images successfully.
3. **The residual risk is the image *build*, not the push** — an uncapped two-target build pulling
   three pinned npm CLIs (F6). That risk is fully retirable *before* any irreversible action.

So, de-risk instead of defer:

```bash
# 1. Merge + push (F1). Then, from the repo root:
cmru release --project nyxloom --dry-run          # prints "[DRY] Would tag: ..."; no side effects
cmru build   --project nyxloom                    # prepare -> run-tests -> build; NO tag, NO publish
```

`cmru build` (`cli.py:1401-1423`) runs the build step in an isolated snapshot worktree with no tag
and no publication — it proves the OCI build works end to end while every external action is still
reversible. If that is green, the only untested step left is `bake --push`, whose driver capability I
already verified and whose credentials are confirmed present.

Then release with the corrected version:

```bash
cmru release --project nyxloom --set-version 0.4.0
```

**Do not run the release before F1 and F2 are resolved.** F1 makes it fail outright; F2 makes it
succeed at a version that cannot be withdrawn.
