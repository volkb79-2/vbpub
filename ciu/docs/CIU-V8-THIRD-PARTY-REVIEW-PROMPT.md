# Prompt: independent adversarial review of the CIU v8 design set (rev 3.0 / draft.3)

*Hand this file, unchanged, to the reviewer. Everything the reviewer needs is in it or in the files it names. The results come back to the author for a further revision, so the output format at the end is binding.*

---

## 1 Your role

You are an independent, adversarial reviewer of a configuration-and-deployment tool design. You have no stake in the design and no memory of the sessions that produced it. Your job is to find what is wrong, weak, over-complicated, under-specified, or simply worse than a known solution — and to say what would be better, with reasons. Praise is not useful; a finding that cannot be acted on is not useful; "consider …" without a concrete alternative is not useful.

You are explicitly invited to **propose alternative solutions**, including alternatives to decisions the authors consider settled, as long as you state (a) the alternative concretely enough to be adopted, (b) why it is better, and (c) what it costs. Where a well-known system already solves a problem the design solves by hand (Kubernetes, Nomad, Compose, systemd, Terraform, Pulumi, CUE/Pkl/Dhall/KCL, SOPS/age, Vault Agent, Docker configs/secrets, Tilt/Skaffold/Garden/Dagger, Nix, Bazel, GitHub Actions, and so on), name it and say exactly what it does better or worse — do not just name-drop.

## 2 What is being reviewed

**CIU** (`ciu`) is a Python tool that deploys a project — a set of Docker Compose stacks plus external and borrowed services — as isolated **instances** (one per git checkout or worktree), on one or several **hosts**, at a chosen **realness** level per capability (live / seeded / simulated / mock), and runs the project's **test gate**. It sits in a small estate of tools (`assay` — a coverage/mutation judge; `run-gate` — a standalone test-lane runner whose functionality v8 lifts into `ciu gate`; `cmru` — release tooling; `nyxloom` — an agent pipeline) whose rule is: **every tool must stay usable standalone by third parties with no hard dependency on the others.** The main consumer is a 35-stack project (`dstdns`); the design is also meant for small third-party projects (one stack, or a library with only a test gate).

v8 is a breaking redesign of CIU's configuration schema and deployment model. It replaces the shipped v7 (`SPEC.md` 5.0.0). The v8 design set has been through two internal review rounds; the current revision (rev 3.0 / draft.3, 2026-09-02) is the result of the second one. Nothing of v8 is implemented yet.

### 2.1 Files to read (repository `vbpub`, directory `ciu/docs/`; sizes given so you can plan)

Read in this order. If you only have file uploads and no repository access, the first four are mandatory and the demo files listed under 5 are the minimum sample.

1. **`SPEC-V8.md`** (128 KB) — the normative specification, 8.0.0-draft.3. Rules are numbered `S<section>.<rule>`; cite them that way. This is the primary object of review.
2. **`CIU-V8-TESTING-GATE-PROPOSAL.md`** (109 KB) — proposal rev 3.0. Part 1 (§4.1–§4.6, §4.11) is the model with its reasoning and the key tables; Part 2 (§4.2, §4.3, §4.7–§4.10) is the audit trail: inventory, interview decisions, contradictions X1–X56, the drop list, known gaps.
3. **`CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`** (52 KB) — the previous review round (findings R-01..R-78, interview record, change map). Read it so you do not re-find what it found; then look for what it missed and for places where its fix is wrong.
4. **`V8-REALIZATION-GRAPH.md`** (30 KB) — the design note behind the dependency graph, with the trace of a real five-wave bring-up and two real races. Its notation is older; the preface maps it.
5. **`v8-dstdns-demo/`** — the worked example: `README.md` (24 KB, every conversion decision), `ciu.toml` (33 KB, the whole global model), `ciu.site.toml`, `ciu.instance.toml`, `ciu.instance.generated.toml`, `ciu.hosts.toml`, `ciu.secrets.toml.example`, `assay.toml`, `examples/ciu.resolved.toml.example` (the derived tables), `examples/ciu.instance.joined.toml`, `examples/minimal/` (the smallest project), and the 27 stack directories (`ciu.stack.toml` + `ciu.compose.yml.j2`). Minimum sample if you cannot read all: `applications/controller`, `infra/db-core`, `infra/vault`, `tools/test-runner`, `infra-global/reverse-proxy`, `applications/worker-io`, `infra/db-core-seeded`.
6. Background, read as needed: `SPEC.md` (298 KB, the shipped v7 spec — the baseline the migration comes from; §S3 layering, §S4 secrets, §S5.3a config-file mounts, §S8 compose, §S9 hooks, §S13 provisioning, §S14 push, §S16 worktrees, §S17 provenance, §S19 init are the sections v8 replaces), `../../run-gate-project/SPEC.md` (56 KB, rules R-01..R-38 that `ciu gate` lifts), `../../AGENTS.md` (17 KB, the estate doctrine — read at least "defaults are hazards", "cockpit ≠ gate", "a check is only as strong as what it compares").

### 2.2 Constraints the design must satisfy (judge against these, not against your preferences alone)

- **P1–P11** in the proposal §4.1.1: one source per fact; fail fast; explicit over magic (no built-ins a file could declare); mechanical checkability; full preflight; one identity derivation; minimal per-kind special-casing; declaration separate from resolution; config as data; nothing hidden; **declarations are data, only artifacts are templates**.
- **Standalone / no hard dependencies** between the estate's tools (§2 above). `ciu gate` must work for a project with zero stacks and no docker; `assay` must not need to know ciu's file layout; `run-gate` stays maintained in parallel.
- **Estate doctrine**: defaults are hazards (DERIVE > READ > FAIL; a policy default is allowed only when it shadows no fact); a check is only as strong as what it compares; visible, gitignored machine state, never hidden directories.
- **Both humans and machines** must be able to read and write the declaration files (editor schemas, external validators, round-trip writers).
- **Breaking changes are allowed** (v8 is a cutover; `project.revision = 8`); migration must be mechanical where possible and honest where not.

### 2.3 Decisions the operator has taken (interviews of 2026-08-30 and 2026-09-02)

These are settled *unless you show a better alternative with reasons* — then say so explicitly in a "challenges a settled decision" finding. Do not spend effort re-arguing them without a concrete alternative.

- Lift run-gate's functionality into `ciu gate`; keep run-gate maintained standalone; zero-instance gate mode.
- Identity derived as data (`{project}-{instance}-{realization}-{service}`, `_`→`-`, elision when service == realization), written to the rendered file.
- Generic `[realization.<n>] kind = ciu_stack | external | joined`; stack-file root `[ciu_stack.<svc>]`; explicit layouts; phases dropped, waves computed and written.
- Consumers declare **bindings** (`binds.<local>` with `to`, `wait`, `delivery`, `env_prefix`, `facts`; `requires` sugar), delivered like secrets; no `routes` in any file; the contract of a capability is derived from bindings.
- All declaration files plain TOML (`ciu.toml`, `ciu.site.toml`, `ciu.instance.toml`, `ciu.stack.toml`, rendered `ciu.resolved.toml`); Jinja only in compose and config-file templates.
- Structured secrets (`from = vault|generate|ask|file|host|ephemeral|hook`, `path` checked against `[vault.paths]`, `delivery` mandatory incl. `hook`); one store `ciu.secrets.toml`; push carries entries per source and reachability.
- Instance lock = `flock` on the checkout directory; rendered file atomic.
- Hooks: subprocess with JSON on stdin/stdout, plus a shipped `ciu.hookkit` helper package.
- cgroup-v2 resource key names shared by governance and gate lanes; the judge is an image-baked floor plus verdict provenance.
- Renames: `bundles`, `seeded`, `[project]`, top-level `[bundles]/[layouts]/[realness]`; no built-in localhost host or `local` layout (`ciu init` writes them explicitly).
- `sequence` lanes; `[testing] inherit` (environments only); v7 exit-code meanings kept; one JSON envelope.

### 2.4 Known open items (do not re-report; do go deeper if you can resolve them)

Review §7 and proposal §4.10: per-replica endpoints for stateful replicated providers; the `pki` hook contract for TLS networks; cgroup slice behaviour inside a devcontainer; `ciu.hookkit` argument shapes; binding-carried credentials (deferred idea); run-gate's exec-mode read of the renamed rendered file (filed); the migration tool's best-effort expansion of Jinja control flow in old declarations; the workers' one-secret-one-delivery choice in the demo.

## 3 What to do (method — do all of it, in this order, and say which steps you could not complete)

1. **Read the spec as an implementer.** For every section, ask: could I implement exactly this without asking a question? List every rule that is ambiguous, contradicts another rule, references a rule that does not exist, or leaves a case undefined. Cross-check every `S<n>.<m>` cross-reference.
2. **Read the spec as an attacker.** For every mechanism, construct the input or sequence of events that makes it lie, block, leak, or destroy state. Concrete probes you must run in your head (add your own):
   - two instances on one machine, both with the same `publish = "host"` port; a `[ciu.instance.host_ports]` override on one; then a third; then `git clean -x` in one while it is up;
   - a git worktree that is moved or renamed (instance id is a path hash); a checkout on NFS or in Docker Desktop on macOS/Windows (directory `flock`, physical paths);
   - a binding to a `per_host` capability; a binding whose target is selected `mock`; a binding with `env` delivery whose variables collide with a secret's `env_name`; two services of one stack binding the same target with different deliveries; a binding with `facts` whose provider is on another host (cross-host fact probing is reachability-only — is the contract then weaker than it looks?);
   - the derived contract when a capability has **no** consumers: is a `seeded` variant ever checked? Is that acceptable?
   - `from = "hook"` when no hook emits the key; a hook that emits a secret for a key declared with another source; hook `state` visibility ordering across two hooks of the same phase;
   - the realness record after a pin changes in a committed file that a teammate pulled; the same after `--realness` on a layout that has no record yet; a joined reference that is down, or that changed its level after the joiner recorded it;
   - push to a host that has no resolution to `vault` but whose stacks need vault-sourced values (sender pre-fetch): what if the sender cannot reach Vault either? What if two hosts need the same `ephemeral`?
   - the identity collision `db_core`+`postgres` vs `db`+`core_postgres` (the spec says checked, not structural — is the check complete for replicas, aliases, compose keys and network aliases?); a 63-byte overflow only for replica `-10`;
   - config-file directory mounts: two services targeting the same directory; a target that is a symlink in the image; the `/etc/nginx` case;
   - the zero-instance gate: an `ephemeral` lane with `binds` (refused) — is there a legitimate need it blocks? A `host` lane that needs an instance's address without an instance;
   - a `sequence` whose member is `NOT_RUN`; a member that is itself a sequence; `--worktree` propagation; admission of a sequence's members against a full slice;
   - `[testing] inherit` across a monorepo where the inherited file itself has `inherit` (forbidden) — is one level enough? What about an inherited environment that names `extra_mounts` with paths relative to the other project?
   - `ciu instance add --join` round-trip writer on a file with comments, arrays of tables, or a syntax error;
   - the secret-free scan (S2.4.1) — construct a false negative (a secret it misses) and a false positive (a legitimate value it refuses) beyond the ones already fixed;
   - the exit-code table vs the gate's table when `ciu gate` is a member of a shell pipeline in CI;
   - `schema_version = 2` on the rendered file, LaneResults and `--json`: who bumps it, when, and what does a v7 reader do.
3. **Walk the demo against the spec.** Find demo files that violate the spec (a key not in the closed set, a binding that cannot resolve on some layout, a `publish` that leaks a port, an `allow_from` that admits the wrong host, a healthcheck missing where the spec requires one, a hook `provides` fact nobody consumes and one that somebody needs but nobody provides). Compute — by hand — the resolution of at least three bindings on `prod3` and on `local`, the waves, and the publication table for `prod3`, and compare with `examples/ciu.resolved.toml.example`. Report every discrepancy.
4. **Write the minimal project from the spec alone** (do not copy `examples/minimal/`), then diff it against `examples/minimal/`. Every place the spec did not tell you what to write is a finding.
5. **Evaluate the schema for humans and machines.** How many concepts must a newcomer hold to deploy one stack? Where do names mislead (e.g. the several senses of "service")? Can an editor validate a stack file from `ciu schema --json` alone, and what can it not validate? Is `ciu.resolved.toml` a good machine interface (stable paths, versioning, size, what a reader must parse to find one container name)?
6. **Compare with known solutions.** For each of the following, say whether a known system solves it better and whether adopting that would be net positive for this estate (with the costs): the binding/delivery model (Kubernetes Service Binding spec, 12-factor env, service meshes/DNS aliases, Compose `depends_on` conditions); the declaration format (plain TOML without references vs CUE, Pkl, Dhall, KCL, Nickel, Jsonnet — typed references and validation vs "any tool can read it"); secrets (SOPS/age files in git, Vault Agent templates, Docker secrets, systemd credentials); the instance lock and registry (lock files, lease files, `flock` semantics on network filesystems); the hook model (in-process plugins, subprocess JSON, OCI hooks, Ansible-style modules); waves and gates (Compose healthchecks + `depends_on`, systemd ordering, Nomad/Kubernetes readiness); the test gate (GitHub Actions/GitLab CI job graphs, Bazel test targets, Dagger); push and activation (OCI artifact bundles, Nix closures, rsync); identity and labels; the migration story.
7. **Propose better designs** where you have them: a complete alternative for any part (not just a tweak), with what it replaces, why it is better against §2.2, what it costs, and what in the current design would have to change. Rank them by expected value.
8. **Check the review round itself.** Where the previous review (R-01..R-78) accepted something on weak evidence, or where its resolution introduced a new problem, say so with the R-id.

## 4 Severity scale

- **BLOCKER** — a contradiction or an unimplementable rule; the spec cannot be implemented as written.
- **MAJOR** — a design defect with a concrete failure (data loss, wrong deploy, security regression, silent misconfiguration, a hard dependency the estate forbids) or a demo/spec mismatch that changes behaviour.
- **MINOR** — a real defect with a workaround or limited blast radius; a missing rule; an inconsistency.
- **NOTE** — usability, naming, documentation, or an alternative worth considering without a defect behind it.

## 5 Output format (binding — the results are fed back into the next revision)

Produce one Markdown document with exactly these sections:

1. **Verdict** (≤ 1 page): is the model sound; the three to five most important findings; the alternatives you would adopt first.
2. **Findings**, numbered `T-01`, `T-02`, … in severity order, each with:
   - `Severity`; `Where` (rule ids `S<n>.<m>`, proposal §, demo file:line, or review R-id); `Claim` (one sentence); `Evidence` (quote or reconstruct exactly what the text says); `Failure scenario` (concrete input/state → concrete wrong outcome) or, for a NOTE, `Why it matters`; `Proposed fix` (concrete: the rule text you would write, or the key you would add/remove); `Alternative design` (optional: a different approach with why-better and cost); `Challenges a settled decision` (yes/no, and which).
3. **Alternative designs** (the §3.7 proposals), each with: what it replaces; how it works; why it is better against P1–P11 and the standalone constraint; costs and migration impact; what would have to change in the spec.
4. **Demo walk results**: the three resolutions, the wave list and the `prod3` publication table you computed, with every discrepancy against `examples/ciu.resolved.toml.example` and the demo files.
5. **Minimal project from the spec alone**: your files, and every gap you hit.
6. **Not verified**: what you could not check and why (missing file, ambiguity, time).
7. **Machine summary**: a fenced JSON block, an array of objects `{ "id": "T-01", "severity": "MAJOR", "where": ["S7.4.1"], "claim": "...", "challenges_settled": false, "has_alternative": true }` — one per finding, same order as section 2.

Rules for the document: cite rule ids for every claim about the spec; quote rather than paraphrase when the wording matters; never restate what the documents already say; no finding without a failure scenario or a concrete alternative; if two findings share a root cause, merge them and say so; do not soften — a wrong design is wrong. Length: as long as the findings need, no longer.

## 6 If you have questions the documents do not answer

Do not stop. State your assumption inline (`ASSUMPTION: …`), proceed, and list every assumption in section 6. The author will answer them in the next round.
