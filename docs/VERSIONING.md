# Versioning & Reproducible Builds

How every published artifact in `vbpub` is versioned and how to cut a release.
This replaced the old clock-derived `YYYYMMDD` scheme (see "Why we changed" below).

## TL;DR

- Versions are **SemVer derived from git tags** by [setuptools-scm]. You never edit a
  version by hand — you create a tag.
- **MAJOR** = breaking change, **MINOR** = backwards-compatible feature, **PATCH** = fix.
- Untagged commits build as `X.Y.Z.devN+g<sha>` — unique per commit, static once built.
- Wheels are **reproducible**: same commit + pinned toolchain + `SOURCE_DATE_EPOCH`
  → identical sha256.

## Per-package tags

Each distribution has its own tag prefix (one shared git history, four version lines):

| Distribution   | Tag prefix         | Example tag           | `setuptools_scm` pretend-version env var          |
|----------------|--------------------|-----------------------|---------------------------------------------------|
| `ciu`          | `ciu-v`            | `ciu-v2.0.0`          | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CIU`          |
| `pwmcp-shared` | `pwmcp-shared-v`   | `pwmcp-shared-v0.1.0` | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PWMCP_SHARED` |
| `pwmcp-client` | `pwmcp-client-v`   | `pwmcp-client-v0.1.0` | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PWMCP_CLIENT` |
| `pwmcp-server` | `pwmcp-server-v`   | `pwmcp-server-v0.1.0` | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PWMCP_SERVER` |

Each `pyproject.toml` filters to its own prefix via `[tool.setuptools_scm]`
(`tag_regex` + `git_describe_command --match`), so a commit touching only `pwmcp` never
bumps `ciu`'s release number — only its `.devN+g<sha>` suffix advances. Release numbers
move **only** when you cut a new `<dist>-v*` tag.

**ciu MAJOR tracks the SPEC MAJOR.** `ciu/docs/SPEC.md` is the contract; a breaking SPEC
change bumps ciu's MAJOR. ciu is seeded at `v2.0.0` to match SPEC `2.0.0`.

## Cutting a release

```bash
# 1. Be on a clean tree at the commit you want to release.
git status --porcelain            # must be empty

# 2. Tag the distribution(s) you are releasing.
git tag -a ciu-v2.1.0 -m "ciu 2.1.0"
git push origin ciu-v2.1.0

# 3. Build + publish via the orchestrator (sees the tag → clean SemVer wheel).
python3 release-runner.py --project ciu
```

The orchestrator ([release-manager `resolve_versions_from_git`]) detects HEAD is exactly on
`ciu-v2.1.0`, exports `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CIU=2.1.0`, and the build produces
`ciu-2.1.0-py3-none-any.whl`.

### What gets published

- A **clean release** (`X.Y.Z`) → an immutable GitHub Release tagged `<dist>-v<version>`
  **plus** the moving `<dist>-latest` release.
- A **dev/dirty build** (`X.Y.Z.devN+g<sha>`) → **only** the moving `<dist>-latest` release
  (no per-commit version tag).

Consumers that pin `<dist>-latest` therefore keep working across both paths.

## Reproducible builds

Two builds of the same commit must be byte-identical. The levers:

1. **Pinned build toolchain** — each `pyproject.toml` `[build-system].requires` pins exact
   versions (`setuptools==…`, `wheel==…`, `setuptools_scm==…`).
2. **`SOURCE_DATE_EPOCH`** — set to the HEAD commit time, clamping zip timestamps. The
   orchestrator exports it; for standalone runs the step runner derives it from git.

Verify (must print identical hashes):

```bash
git stash -u                                  # tree MUST be clean (else +dirty, non-reproducible)
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
rm -rf /tmp/b1 /tmp/b2
(cd ciu && python3 -m build --wheel --outdir /tmp/b1)
(cd ciu && python3 -m build --wheel --outdir /tmp/b2)
sha256sum /tmp/b1/ciu-*.whl /tmp/b2/ciu-*.whl   # the two hashes MUST match
```

## Build & publish mechanics

- Build frontend: **`python -m build --wheel`** (isolated, honors `SOURCE_DATE_EPOCH`).
- `setuptools-scm` writes a generated `src/<pkg>/_version.py` at build time (gitignored).
  The runtime `__version__` reads it, then falls back to installed metadata — **no
  import-time clock**, so `__version__` always equals the wheel's `METADATA` version.
- Publishers upload the **already-built** wheel (never rebuild) and read the version from the
  wheel's `METADATA` — the release tag always matches the artifact exactly.

## Rules & edge cases

- **Build releases from a clean tree.** Any modified file anywhere makes setuptools-scm append
  `+d<date>` / `.dirty` to *every* package (it inspects the whole worktree), which is
  non-reproducible. CI should refuse to release a dirty tree.
- **`+local` segments** (`2.1.0.dev3+g<sha>`) are fine for GitHub Releases; PyPI would reject
  them (we don't publish to PyPI). Release tags sanitize `+`→`-`.
- **Docker images** carry `org.opencontainers.image.revision` (HEAD sha) and `.created`
  (RFC3339 of `SOURCE_DATE_EPOCH`); their date-style tags are commit-derived. Full
  byte-reproducible images (base-image `@sha256` pinning) are future work.

## Why we changed

The old scheme versioned every artifact from `datetime.now()`:
- non-reproducible (the wheel zip embedded "now");
- `ciu.__version__` recomputed the date *at import*, drifting from the wheel's `METADATA`;
- same-day rebuilds reused the date and silently overwrote the release.

Git-tag SemVer + `SOURCE_DATE_EPOCH` fixes all three: the version is tied to the commit, it's
static once built, and the artifact is reproducible.

[setuptools-scm]: https://setuptools-scm.readthedocs.io/
[release-manager `resolve_versions_from_git`]: ../release-manager/src/release_manager/cli.py
