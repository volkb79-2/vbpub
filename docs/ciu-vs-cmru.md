# ciu vs cmru — roles, overlap, and the border

Two tools in this monorepo both "build things," which invites confusion. They sit on
**different loops** and almost never overlap in practice. This is the canonical map.

## TL;DR

- **ciu = inner loop (build-to-run).** Produce and run images/stacks **on this host**,
  for dev or deploy. Ephemeral, host-local, no git tags, no registry publish.
- **cmru = outer loop (build-to-release).** **Version, tag, build distributable
  artifacts, publish** (GitHub Releases / ghcr) across many independently-versioned
  products, and manage per-product "latest". Git/registry-authoritative.

**The one question that decides which tool owns an artifact:**

> **Is this artifact published for external consumption?** — yes → **cmru**; no → **ciu**.

## Side by side

| | **ciu** | **cmru** |
|---|---|---|
| Scope | one deployable stack | many independently-versioned products |
| Builds… | local images **to run here** (`ciu bake --load`) | distributable **artifacts to publish** (wheel / ghcr image / bundle / tarball) |
| Audience | this host (dev or deploy) | external consumers (Releases page / ghcr) |
| Lifecycle | build → run | version → tag → build → publish |
| Git | none — it acts on the deployed-to state | authoritative — tags, commits, `latest.json` |
| Config | stack manifests (`ciu.*.j2`, host profiles) | per-project `cmru.toml` + optional root `cmru.orchestration.toml` |
| Verbs | `env` `render` `up` `down` `health` `bake` `dev` `secrets` | `status` `release` `build` `publish` `resolve` `cleanup` |
| dstdns uses | ✅ `bake` + `up` + `dev` | ❌ never (it's a consumer, not a producer) |

## The "double bake" is not duplication

Both tools can trigger a docker build, which looks like waste. It isn't — there is **one
build definition** (`docker-bake.hcl`) and **two terminal actions** over it:

- `ciu bake [targets]` → `docker buildx bake --load` → image lands in the local daemon so
  `ciu up` / `ciu dev` can **run** it.
- an explicit CMRU project `build`/`push` contract → build the same targets → **push**
  to ghcr (+ commit manifests, when the project declares them). No run.

Same inputs → **bit-identical image** → "build locally to get the same image we publish, so
no surprises in prod" is **guaranteed by construction**, not hoped for.

> **Guardrail:** the thing to protect is not "stop building twice" — it's "never let a
> *second definition* appear." Both tools must drive **bake targets**, never hand-rolled
> `docker build` args. If two definitions diverge, "the image ciu runs" and "the image cmru
> ships" silently differ.

## ciu in detail (inner loop)

CIU renders and runs Docker Compose stacks from layered templates (config inheritance,
secrets, host-aware paths, multi-stack/multi-host). Relevant build verbs:

- **`ciu bake [targets] [--no-cache]`** — thin wrapper over `docker buildx bake --load`.
  No targets → bake `all`. Produces **local** images; the tag is local/dev, nothing is
  released.
- **`ciu dev <stack>`** (SPEC **S5a**) — the dev-loop runner. Renders, waits for
  `depends_on` health, runs ordered `prebuild` steps, then a long-running `command` in an
  ephemeral `docker run --rm` with the source **bind-mounted** and a `port` published. For
  HMR servers (Vite / Next / `uvicorn --reload`) and codegen-vs-live-service prebuild
  chains that a production `bake` doesn't model. `--no-prebuild` re-runs the server only.

## cmru in detail (outer loop)

cmru is the **release orchestrator** for independently-versioned products sharing one
GitHub Releases page. It owns generic source mechanics (isolated worktrees, generated
history, declared generated-source commits, optional tags) and executes each portable
project's explicit `prepare`, `run-tests`, selected `build_step`, and `push` commands.
`artifacts = [...]` is an output inventory, not a behavior profile. Its small command library
can be composed explicitly for wheel/tarball/normal OCI operations; it never replaces a
project's declared command. See [`cmru/docs/SPEC.md`](../cmru/docs/SPEC.md) *S-REL*.

- **`cmru build --project X`** creates a retained isolated `cmru/build/<id>` worktree and
  runs X's `prepare` (if declared), release gate, and explicit `build_step`—never `push`.
  It is a release-contract diagnostic, not a local dev image. Its logs and artifacts stay in
  that worktree; the console names the exact location.

## Worked examples

- **dstdns** — a consumer/deployment stack: builds controller / workers / webapp images **to
  run the stack**, never released. → **ciu only** (`ciu bake all-services` → `ciu up`;
  `ciu dev <stack>` for HMR). The React app's extra build step is a multi-stage step **inside**
  `applications/webapp-ui-react/Dockerfile`, driven by `ciu bake`. **No cmru** — until/unless
  one of those images is published to ghcr for outside consumers, at which point that product
  gets a portable `cmru.toml` with explicit image build/push steps.
- **modern-debian-tools-python-debug (mdt)** — the deliverable *is* the ghcr image(s). Its
  explicit CMRU contract prepares one digest-verified OCI layout, commits regenerated
  manifests, and pushes it. `git_tag = false`; image identity is project-owned.
- **ciu / cmru themselves** — Python wheels. Their project contracts explicitly compose the
  CMRU wheel build/publish commands, yielding SemVer tags, GitHub Releases, checksums, and
  `latest.json` without a private release implementation.

## Frequently confused

- **"Is there a `dev` verb for cmru?"** No — and there shouldn't be. There is no "dev
  release." `dev` lives in **ciu** (`ciu dev`, run something locally with reload). cmru's
  nearest analog is `cmru build --project X` (build the release artifact without publishing).
- **"`ciu bake` vs `cmru build`?"** Both invoke a build, but for different audiences:
  `ciu bake` → local image to **run**; `cmru build` → publishable artifact to **ship**.
- **"Does dstdns need cmru?"** No. It produces nothing for external consumption.

## See also

- [`cmru/README.md`](../cmru/README.md) — cmru's model, verbs, command library, and templates.
- [`docs/RELEASE-TOOLING.md`](RELEASE-TOOLING.md) — cmru file/verb overview.
- [`ciu/README.md`](../ciu/README.md), `ciu/docs/SPEC.md` (S5a `dev`) — ciu's surface.
- [`docs/plan-cmru-release-modes.md`](plan-cmru-release-modes.md) — the release-modes design.
