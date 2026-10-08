# Using & adopting nyxloom

A practical guide to bringing a project under nyxloom and driving it day-to-day.
For *why* nyxloom is built the way it is, see [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`SPEC.md`](SPEC.md), and the [`README`](../README.md). This document is the
*how*.

nyxloom is a **files-first agent-orchestration daemon**: markdown handoffs with
YAML frontmatter are the single source of truth, a resident reconcile daemon
dispatches cheap implementer agents behind an independent review gate, and
`nyxloom lint` machine-checks quality so the cost model rests on real carve
discipline rather than trust.

The wheel installs three human commands by data boundary: `nyxloom` authors a
project's local trove, `nyxloom-harness` reads harness session stores, and
`nyxloomctl` performs local host operations. The first two do not require a
daemon or project registration. `nyxloomd` is the directly executable service
entrypoint inside the daemon container. See the
[CLI reference](CLI-REFERENCE.md#current-command-and-option-contract) for the
complete current grammar.

---

## 1. Concepts you need

| Concept | What it is |
|---|---|
| **Trove** | `nyxloom-trove/` in your repo — everything nyxloom durably manages (config, handoffs, reports, the direction spine, archive). Laid out per [`STANDARD.md`](../nyxloom-trove/STANDARD.md). |
| **Handoff** | A `<id>.md` work package under `handoffs/` with schema-validated frontmatter (tier, scope, oracles, gates, escalate-if). Lint-gated. The daemon dispatches these. |
| **Direction spine** | Four numbered docs that describe *where the project is going*: `1-north-star` → `2-product-definition` → `3-roadmap` → `4-backlog`. Optional but recommended. |
| **Gate** | The command that must pass before a change merges (e.g. a `test-runner`/`tester-unified` container run). Cockpit greens are never a ship signal. |
| **Pause/resume** | A project can be *registered but inert* (`pause`) — the daemon reconciles nothing until you `resume`. Unpausing is always an operator decision. |

### The spine cascade (this is the important part)

Each spine level **derives from the one above it**, getting more concrete and
more mutable as you go down:

- **`1-north-star`** — the *invariant mission*. Why the project exists; what
  never changes. Short prose, minimal frontmatter. You edit it **only when the
  mission itself shifts**.
- **`2-product-definition`** — the *versioned feature set* that realizes the
  north-star. `features[]` (F001…), each with acceptance criteria, a status
  (`planned`→`building`→`shipped`), and `non_goals[]`. You edit it **every time
  a feature ships**.
- **`3-roadmap`** — sequences product-definition features into `milestones[]`
  (M1…) with target versions and status.
- **`4-backlog`** — unscheduled `items[]` (ideas, sub-packages) that each
  `folds_into` a real feature or milestone.

> **north-star vs. product-definition, in one rule:** if you'd edit it when a
> feature ships, it's product-definition (2); if it only changes when the
> mission changes, it's north-star (1). (1) is the fixed star; (2) is the live,
> versioned map of what you're building toward it.

See a real, lint-clean spine in
[`netcup-api-filter/nyxloom-trove/`](../../netcup-api-filter/nyxloom-trove/) (the
reference) or the freshly-migrated
[`topos/nyxloom-trove/`](../../topos/nyxloom-trove/). The full schema + the
S1–S5 cross-consistency rules live in
[`spine-documents-spec.md`](spine-documents-spec.md).

---

## 2. Adopting nyxloom for a project

```bash
# 1. Register the project with this host's local operator state.
nyxloomctl project add <project-id> /path/to/repo

# 2. (Recommended) keep it inert until you've onboarded a spine.
nyxloomctl pause <project-id>
```

The daemon **caches the registry at startup**, so `project add` is invisible to
a running daemon until its next restart — there is no dispatch race. Register
and pause freely; the project only goes live (and only if unpaused) after a
controlled restart.

Then give the repo a trove (`nyxloom-trove/` with `nyxloom.toml`, `handoffs/`,
`reports/`, `archive/`) — from that checkout, `nyxloom init <repo>` scaffolds
the skeleton from bundled templates. Project-local lint and backlog authoring
do not require registration or a running daemon.

---

## 3. Onboarding: define the direction spine

**The guiding principle (read this first):** onboarding is **interview-driven
and content-preserving, at any project maturity.** nyxloom must never
autonomously draft a *thinner* canonical spine from a code scan — the canonical
north-star / product-definition are authored **with the user**, through an
extensive interview, and any **existing curated docs** (a roadmap, backlog,
product definition) are **migrated into the spine schema, not regenerated**.
The source docs are absorbed first, then retired once their content lives in the
spine. A project at *any* stage — empty, code-only, or richly documented — can
be onboarded this way.

> **Status note:** the `onboard` command below ships a deterministic scaffold
> (F2) + a read-only AI assessment (`--scan`, F3) + a one-shot AI spine *draft*
> (`--questionnaire`, F4b). The **interview-driven + migrate-existing-docs**
> workflow is the intended default and is tracked as backlog **B14** — until it
> lands, treat `--questionnaire` output strictly as a *draft to review with a
> human*, and for content-rich projects use the migration path in §3.3.

> **`--check-gate` (GA3 v1, docs/plan-gate-adoption.md §GA3):** an opt-in,
> deterministic, AI-free follow-on -- reports whether the project declares a
> usable `[gates.*]` and, if not, points to the next step: declare a project
> gate, then run its lane with `./run-gate.py <lane>`. It does not scaffold a
> gate (Dockerfile/`[gates.*]` authoring is a v2 follow-up); combine freely with
> `--scan`/`--questionnaire` in the same call.

### 3.1 Greenfield (empty repo)

```bash
nyxloom onboard /path/to/repo --maturity empty --mode greenfield-define-it
```

The wizard scaffolds the trove + a minimal-valid spine skeleton. Author the
north-star **with the user** (there's nothing to migrate yet).

### 3.2 Mature repo, no curated docs → derive-from-code (draft only)

```bash
nyxloom onboard /path/to/repo \
  --maturity mature --docs absent --mode derive-from-code \
  --scan --questionnaire
```

Stages, in order:

1. **F2 wizard** — deterministic: scaffolds any missing spine doc, wires the
   `nyxloom.toml` spine keys. Idempotent; never overwrites an existing doc.
2. **`--scan` (F3)** — dispatches a **read-only** assessment agent
   (`Read`/`Grep`/`Glob` only) that reads the repo and returns a structured
   `{maturity, existing_docs, existing_tests, intent_summary, gaps}`.
3. **`--questionnaire` (F4b)** — an AI agent drafts the *entire* spine
   north-star-first (features with acceptance criteria → milestones → backlog),
   write-then-self-lint with a byte-wise restore on any lint failure.

**Always review the draft with a human before it becomes canonical** — a code
scan captures *what the code does*, not *what the product is for*.

### 3.3 Mature repo WITH curated docs → content-preserving migration (preferred)

If the project already has a curated roadmap / backlog / product definition,
**do not regenerate** — migrate. A code-scan `--questionnaire` would produce a
thinner draft and lose hard-won detail. The migration flow:

1. Run the F2 wizard (scaffold + config wiring) — or hand-create the four spine
   files.
2. **Migrate** the existing curated docs into the spine schema: reformat the
   roadmap into `milestones[]`, the backlog into `items[]` (IDs preserved), the
   product doc into `features[]` — **keeping every entry**. Author the
   `1-north-star` from the product's mission, with the user.
3. From the project checkout, run `nyxloom lint` until **0 findings** (this
   enforces S1–S5:
   milestone features exist in the product-definition, `folds_into` resolves,
   ids unique).
4. Retire the now-migrated source docs (repoint or delete the old
   `roadmap.md`/`backlog.md`).

The **dstdns** and **topos** troves were onboarded exactly this way on
2026-07-23 (803-line roadmap → 13 milestones, all backlog IDs verbatim) — use
them as worked examples.

---

## 4. CLI reference

Nyxloom installs four commands from one wheel. Each human CLI is generated
from its own cli-extended registry; use `--help`, `help`, or `help VERB` to
inspect its current options. Bare invocation prints help and exits 0. The
short `-h` alias is not accepted.

| Program | Commands and target | Daemon required? |
|---|---|---|
| `nyxloom` | `init`, `onboard`, project-local `lint`, and `backlog *`; edits files in the current project checkout. | No |
| `nyxloom-harness` | `search`, `extract`, `extract-lossless`, `extract-debug`, `extract-report`, and `extract-sessions`; reads AI-harness session files/stores. | No |
| `nyxloomctl` | Local host operations: `project`, host-wide `lint`, `doctor`, `status`, `resync`, workflow/intake/finding actions, `auth`, `route`, model catalogs, `migrate-store`, and `daemon`. This is a local operator tool, not a remote HTTP client. | Only `daemon` starts it; other commands run directly. |
| `nyxloomd` | Direct service-manager entrypoint for the daemon lifecycle used by the existing container. | Starts the daemon |

Common tasks:

```bash
cd /path/to/project
nyxloom init .
nyxloom lint
nyxloom backlog new "describe the issue" --type bugfix
nyxloom backlog new --interactive
nyxloom backlog edit CIU-1

nyxloom-harness extract /path/to/session.jsonl --profile all

nyxloomctl project add project-id /path/to/project
nyxloomctl status --project-id project-id
nyxloomctl doctor --project-id project-id
nyxloomctl resync project-id                 # dry-run
nyxloomctl resync project-id --apply         # apply eligible transitions
```

`nyxloom lint` checks the current checkout or explicit handoff paths;
`nyxloomctl lint` checks all registered projects. See the
[canonical CLI contract](CLI-REFERENCE.md#current-command-and-option-contract)
for each verb's option semantics, defaults, safeguards, and migration path.
The container invokes the installed `nyxloomd` executable; the old Python
module command is not the service interface.

---

## 5. Session-log extraction

The `nyxloom-harness extract-*` family reads Claude Code, Codex, OpenCode, or
Reasonix session data. Use a path or a bare session ID when the adapter can
resolve exactly one match; pass `--opencode-session ID` when a SQLite store
contains several. Extraction does not need Nyxloom project registration or a
running daemon.

To locate sessions by remembered content, search the local stores. The best
matches are listed first, and only identifiers and metadata are printed:

```bash
nyxloom-harness search debian iso cloud qcow
nyxloom-harness search debian iso cloud qcow --term-match prefix --word-match all
nyxloom-harness search debian iso cloud qcow --client codex --source-root ~/.codex/sessions --term-match prefix
```

See the [search design](DESIGN-GUIDE.md#local-session-search) for matching
and ranking behavior and [the consumer recipe](CONSUMERS.md#search-local-session-history)
for source selection and progress controls. Exact word matching and any query
word are the defaults; `prefix` matches `qcow` in `qcow2`, and `all` requires
every query word.

| Command | Purpose |
|---|---|
| `nyxloom-harness search WORD...` | Search local Claude Code, Codex, and OpenCode sessions; choose word matching, client list, and source roots. |
| `nyxloom-harness extract SESSION_LOG` | Produce a compact, classified brief with operator text, Q&A pairs, and selected checkpoints. |
| `nyxloom-harness extract-lossless SESSION_LOG` | Dump every recoverable prose/thinking block, dropping only tool calls and harness bookkeeping. |
| `nyxloom-harness extract-debug SESSION_LOG` | Compare the lossless dump with what `extract` keeps, including drop reasons. |
| `nyxloom-harness extract-report SESSION_LOG` | Report tool calls, compactions, and session activity in condensed, detailed, or JSON form. |
| `nyxloom-harness extract-sessions FAMILY` | List the selected family and discovered subagent transcripts. |

`extract` and `extract-lossless` can be bounded with `--since MARKER` and
`--until MARKER`. Their text and JSON output embed a marker that can be read
back from a saved file:

```bash
nyxloom-harness extract /path/to/session.jsonl --until a1 > brief.txt
nyxloom-harness extract /path/to/session.jsonl --since-file brief.txt
```

The saved marker is checked against the explicit `--format`, or against the
adapter detected from the session path when `--format` is omitted. A marker
from a different adapter is refused with an error instead of being used as an
ambiguous resume boundary.

For a live log, add `--follow`; `extract` applies normal selection to new
records, while `extract-lossless` prints each new prose/thinking block. Use
`--render-markdown` for reading or `--highlight` to preserve markdown source;
`--on-attention`, `--bell`, and `--notify-project` deliver optional attention
notifications. See the [consumer recipes](CONSUMERS.md#extract-a-session-log)
for pasteable follow, redaction, and hook examples.

## 6. free-models — dynamic free-model discovery

`nyxloomctl` can discover currently-**free** model endpoints across multiple
providers and regenerate `routes.toml`'s `[tiers.free-high]` block, instead of
hand-curating it.

```bash
nyxloomctl free-models list [--source NAME]              # discover + print, no write
nyxloomctl free-models refresh --dry-run                 # compute the plan, write nothing
nyxloomctl free-models refresh [--source NAME]           # discover + write the managed block
```

`refresh` writes a delimited **managed block** (`# === nyxloom-free-models:
BEGIN/END ===`) — it regenerates `[tiers.free-high]` + `[routes.auto-*]` and
leaves **every other tier and every hand-authored route byte-identical**.
**Always `--dry-run` first.**

Each generated route carries the `free-endpoint` prompt-hint, so
`adapters.build_dispatch`'s no-secrets confidentiality guard fires — but note
that some free tiers **train on your prompts** (below). Keep sensitive work off
the `may-train` providers.

### Providers & keys

Export the env var for each provider you want active (a source with no key is
skipped silently; OpenRouter's *listing* needs no key).

| Provider | Privacy | Env var | Register |
|---|---|---|---|
| OpenRouter (self-describing, default-on) | may-train (downstream) | `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| Groq | 🟢 private | `GROQ_API_KEY` | https://console.groq.com/keys |
| Cerebras | 🟢 private | `CEREBRAS_API_KEY` | https://cloud.cerebras.ai/ |
| SambaNova | 🟢 private | `SAMBANOVA_API_KEY` | https://cloud.sambanova.ai/apis |
| Google Gemini | ⚠️ may-train (free tier) | `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| Mistral | ⚠️ may-train (Experiment tier) | `MISTRAL_API_KEY` | https://console.mistral.ai/ |

Only **OpenRouter** advertises free-ness in a machine-readable way (a public
`/api/v1/models` listing with per-model pricing); the Tier-2 providers above
expose an OpenAI-compatible `/v1/models` inventory whose free-ness is an
account-tier property carried as a per-provider constant.

### Adding a provider (the plugin model)

- **OpenAI-compatible + whole catalog free** → add one row to
  `[free_models.sources.<name>]` in `routes.toml` (`kind = "openai-compat"`,
  `base_url`, `key_env`, `privacy`, `all_free = true`). **Zero code.**
- **A genuinely different response shape** (e.g. OpenRouter's pricing dict) →
  write one small `FreeModelSource` subclass decorated `@register_kind("…")`.

> ⚠️ Tier-2 route addressing (`groq/<model>`, `cerebras/<model>`, …) generalizes
> OpenRouter's proven `openrouter/<vendor>/<model>:free` convention but is not
> yet validated against each provider — probe a route before real traffic
> (tracked as backlog **B15** / `route doctor`).

---

## 7. Worked use cases

**Onboard a new microservice (code-only).** `nyxloomctl project add svc
/repos/svc` → `nyxloomctl pause svc` → `cd /repos/svc && nyxloom onboard .
--maturity mature --docs absent --mode derive-from-code --scan --questionnaire`
→ **review the draft spine with the team** → `nyxloom lint` →
`nyxloomctl resume svc` when ready.

**Bring a well-documented project under nyxloom.** `nyxloomctl project add app
/repos/app` → `cd /repos/app && nyxloom init .` → **migrate** the existing roadmap/backlog/product docs
into the spine (preserve every entry, author the north-star with the owner) →
`nyxloom lint` to 0 findings → retire the old docs. (See dstdns/topos.)

**Add free models and preview a routes refresh.** `export GROQ_API_KEY=…` →
`nyxloomctl free-models list` to see what's discovered → `nyxloomctl free-models
refresh --dry-run` to preview the managed block → `nyxloomctl free-models
refresh` to apply.
