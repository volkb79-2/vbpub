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
# 1. Register the project (records a path in the registry).
nyxloom project add <project-id> /path/to/repo

# 2. (Recommended) keep it inert until you've onboarded a spine.
nyxloom pause <project-id>
```

The daemon **caches the registry at startup**, so `project add` is invisible to
a running daemon until its next restart — there is no dispatch race. Register
and pause freely; the project only goes live (and only if unpaused) after a
controlled restart.

Then give the repo a trove (`nyxloom-trove/` with `nyxloom.toml`, `handoffs/`,
`reports/`, `archive/`) — `nyxloom init <repo>` scaffolds the skeleton from
bundled templates.

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
3. `nyxloom lint <project>` until **0 findings** (this enforces S1–S5:
   milestone features exist in the product-definition, `folds_into` resolves,
   ids unique).
4. Retire the now-migrated source docs (repoint or delete the old
   `roadmap.md`/`backlog.md`).

The **dstdns** and **topos** troves were onboarded exactly this way on
2026-07-23 (803-line roadmap → 13 milestones, all backlog IDs verbatim) — use
them as worked examples.

---

## 4. CLI reference

| Command | Purpose |
|---|---|
| `project add <id> <root>` | Register a project path in the registry. |
| `project list` | Print the registry table. |
| `lint [path…]` | Lint registered projects / specific handoff files (the quality gate). |
| `doctor [--project] [--rebuild [--write]]` | Integrity findings + dashboard URL. |
| `status [--project]` | Per-task state / since / route / cost / notes. |
| `resync <project> [--apply] [--apply-content-merges]` | Re-baseline state against ground truth (post manual-merge drift). |
| `render` | Render the read-only `www/` dashboard. |
| `migrate-store <project>` | Migrate the file-backend event store → SQLite. |
| `daemon [--foreground]` | Run the resident reconcile daemon. |
| `tick [--project]` | One reconcile pass (degraded/debug mode). |
| `decide <project> <D-id> --choose` | Resolve a `D-NNN` product decision. |
| `discuss <project> <D-id>` | Print the decision-chat command. |
| `intake <project> <intake_id> <msg>` | Advance a feature-intake chat turn. |
| `reject <project> <task> [--note]` | Merge-gate rejection. |
| `merge <project> <task> [--commit]` | Record a manual merge. |
| `pause` / `resume <project> [task]` | Set / clear the pause flag. |
| `leases` | Show mutex (flock) holders. |
| `digest <project> [--since]` | Notification digest. |
| `events <project> [--since --type --tail --json]` | Dump the event store as JSONL. |
| `init <project_folder>` | Scaffold a `nyxloom-trove/` from templates. |
| `onboard <project_folder> [--maturity --docs --mode --scan --questionnaire]` | Guided onboarding (see §3). |
| `free-models list \| refresh` | Discover currently-free models & refresh routes (see §5). |
| `version` | Print the version. |

Against the deployed daemon, run any verb through the container wrapper:
`python3 exec-nyxloom.py <verb> …` (it `docker exec`s into the running daemon;
add `-i` for stdin heredocs).

---

## 5. free-models — dynamic free-model discovery

nyxloom can discover currently-**free** model endpoints across multiple
providers and regenerate `routes.toml`'s `[tiers.free-high]` block, instead of
hand-curating it.

```bash
nyxloom free-models list [--source NAME]              # discover + print, no write
nyxloom free-models refresh --dry-run                 # compute the plan, write nothing
nyxloom free-models refresh [--source NAME]           # discover + write the managed block
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

## 6. Worked use cases

**Onboard a new microservice (code-only).** `project add svc /repos/svc` →
`pause svc` → `onboard /repos/svc --maturity mature --docs absent --mode
derive-from-code --scan --questionnaire` → **review the draft spine with the
team** → `lint svc` → resume when ready.

**Bring a well-documented project under nyxloom.** `project add app /repos/app`
→ scaffold the trove → **migrate** the existing roadmap/backlog/product docs
into the spine (preserve every entry, author the north-star with the owner) →
`lint app` to 0 findings → retire the old docs. (See dstdns/topos.)

**Add free models and preview a routes refresh.** `export GROQ_API_KEY=…` →
`nyxloom free-models list` to see what's discovered → `nyxloom free-models
refresh --dry-run` to preview the managed block → `refresh` to apply.
