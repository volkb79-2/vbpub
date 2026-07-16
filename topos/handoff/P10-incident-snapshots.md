# P10 — Incident snapshots

**Cut:** v1.5. **Depends:** P2, P5 merged. Parallel to P8 is fine.
Branch: `feat/topos-p10-snapshots`. Follow `topos/README.md` workflow protocol.

## Goal

One hotkey saves a self-contained incident bundle answering "what happened
just now around this entity" — shareable, replayable, reviewable offline.

## Spec references

§3.8a (incident snapshots), §3.8 (recording format reuse), §6.5 (bundles can
contain sensitive process/docker metadata — document, and honor a
privacy-reduced mode config flag that drops cmdlines and env-derived fields).

## Scope — in

1. `snapshot/bundle.py`: `create(entity_key, ring, frame, config) -> Path`
   writing `topos-incident-<ts>-<entity-slug>.tar.zst` when `zstandard` is
   importable, otherwise `topos-incident-<ts>-<entity-slug>.tar`, under
   `[snapshots].dir` (default $XDG_STATE_HOME/topos/incidents):
   - `frames.jsonl`: current frame + previous N (config, default 60) from the
     ring, serialized via P2's writer (header included);
   - `entity/cgroup/`: raw copies of the selected entity's cgroup files
     (memory.*, cpu.*, io.*, pids.*, cgroup.*) and its ancestor chain's
     protection files;
   - `entity/systemctl-show.txt`, `entity/docker-inspect.json` (summary
     fields only), `providers-status.json`;
   - `manifest.json`: topos version, schema version, host id, ts, entity,
     privacy mode, file list with sha256.
2. UI: hotkey in drill-down + table (P5 keys), toast with the bundle path;
   non-blocking (worker thread), failure surfaces as a notice not a crash.
3. `topos snapshot inspect FILE`: CLI that validates the manifest and prints
   the summary (no UI needed to triage a bundle).
4. Privacy-reduced mode: `[snapshots].redact = true` drops cmdlines,
   environment-derived docker labels, and file paths outside /sys/fs/cgroup.
5. Tests: bundle round-trip on fixtures (create → inspect → manifest hashes
   verify); redaction test; ring-shorter-than-N test.

## Scope — out

Automatic/triggered snapshots (rule-engine hook is v2), uploading anywhere,
bundle diffing.

## Acceptance

- Bundle created from replay fixtures validates and contains the previous N
  frames + entity files; `snapshot inspect` output matches manifest.
- pytest green; report per README protocol.
