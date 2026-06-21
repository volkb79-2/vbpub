# SPEC MDT-DEVCONTAINER — Ship a template devcontainer.json + canonical agent-state persistence

**Spec ID:** MDT-DEVCONTAINER
**Repo:** `vbpub` (modern-debian-tools-python-debug).
**Status:** ready. Small–Medium, offline (validation needs one devcontainer rebuild).
**Motivation:** mdt installs the AI CLI agents (claude, codex, aider, reasonix, openclaw, antigravity,
deepcode) + signing/auth tooling (minisign, gh, gnupg) but ships **no devcontainer template**, so each
consumer hand-rolls `devcontainer.json` with an incomplete, inconsistent mount list. Measured on the
dstdns devcontainer (2026-06-21): `~/.claude` (188M) IS persisted via a host mount, but `~/.codex`
(147M auth+history), `~/.minisign` (signing key), `~/.config` (gh auth), `~/.gnupg` are in the
**ephemeral container layer → lost on every rebuild**. Each rebuild forces re-login (codex, gh) and
**destroys the minisign signing key** (the exact gap flagged during the 2026-06-21 release).

> Self-contained. The image already exists (`…-vsc-devcontainer`); this adds the *consumption template*.

---

## 1. Goal
mdt becomes the single source of truth for **how a devcontainer built on it persists agent/tool state**.
Consumers adopt the template (or a Feature) and get correct, complete, DRY persistence — login once,
keys survive rebuilds, history kept — across all repos (dstdns, vbpro, netcup-api-filter, …).

## 2. The canonical agent-state set (what must survive a rebuild)
Persist these (login/keys/history — NOT rebuildable caches, which stay ephemeral by choice):
- `~/.claude` + `~/.claude.json` — Claude Code config, auth, projects/memory, todos.
- `~/.codex` — Codex CLI auth + history.
- `~/.config` (at least `~/.config/gh`) — gh auth; other tool configs.
- `~/.minisign` — Ed25519 signing key (release/bundle signing; see SPEC B / KI-01).
- `~/.gnupg` — GPG keys.
- shell history (`~/.bash_history` / `~/.local/share` as applicable).
- (optional, document as opt-in) `~/.cache`, `~/.npm` — speed only; fine to lose.

## 3. Design decisions
- **Mechanism — two deliverables:**
  1. **Template `templates/devcontainer.json`** in the mdt repo: a copy-and-adapt reference
     (`image` pinned to `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:<tag>`,
     `remoteUser: vscode`, the `mounts` block below, sensible `runArgs`/`containerEnv`). Consumers copy
     to `.devcontainer/devcontainer.json` and set only their `workspaceFolder`/features.
  2. **A reusable `mounts` fragment** (`templates/agent-state.mounts.json`) + a documented snippet so a
     consumer with an existing devcontainer.json can paste just the mounts. *(Stretch: package as a
     devcontainer **Feature** `mdt-agent-state` whose `devcontainer-feature.json` declares the mounts —
     the DRY-est option, since Features can contribute `mounts`; evaluate vs template complexity.)*
- **Volume type — named volumes by default, bind-mount variant documented.**
  - Default: **named volumes** (`source=agentstate-claude,target=/home/vscode/.claude,type=volume`, …)
    — portable, no host-path coupling, and **shared across all devcontainers using the same volume
    name → log in once, available everywhere**. `~/.claude/projects` is internally namespaced by
    workspace path, so sharing is safe.
  - Document a **host bind-mount variant** (what dstdns uses today) for users who want host-visible
    state — parameterized on a host base path, not hardcoded.
- **Ownership/UID:** ensure volumes are owned by `vscode` (uid 1000) — add a `postCreateCommand`/
  `initializeCommand` chown if a fresh named volume mounts as root.
- **Secrets hygiene:** `~/.minisign`/`~/.gnupg`/`~/.config/gh` hold secrets — the template MUST NOT bake
  any secret into the image; it only mounts the (host/volume) location. Note this explicitly.

## 4. Tasks
A. Add `modern-debian-tools-python-debug/templates/devcontainer.json` (full reference) +
   `templates/agent-state.mounts.json` (the mounts fragment) + a short `templates/README.md` explaining
   named-volume vs bind-mount, the persistence set, and the one-line adoption steps.
B. (Decide) Optionally add a `mdt-agent-state` devcontainer Feature contributing the mounts; pick template
   vs Feature and document the rationale.
C. Update the mdt top-level README / CONTAINER-DOCTRINE to point at the template as the supported way to
   consume the image.
D. **Migrate dstdns** (`.devcontainer/devcontainer.json`) to the template's mount set as the first
   consumer — adding the missing `~/.codex`, `~/.minisign`, `~/.config`, `~/.gnupg` mounts (keep its
   existing `~/.claude` mount). This is the validation case.

## 5. Live-stack / validation tier
Offline to author. **Validation requires one devcontainer rebuild** of dstdns: rebuild, confirm codex/gh
stay logged in, `~/.minisign/minisign.key` survives, and shell history persists. Schedule the rebuild
when it won't interrupt active work (it recreates the container the agent runs in).

## 6. Acceptance
1. mdt ships a documented template devcontainer.json + reusable mounts fragment (+ optional Feature).
2. The canonical agent-state set is persisted; caches remain intentionally ephemeral (documented).
3. dstdns migrated to the template's mounts; after a rebuild, codex/gh auth + minisign key + history
   all survive (no re-login, key intact).
4. No secret is baked into the image; secret dirs are mount-only.
5. mdt README points at the template as the supported consumption path.
