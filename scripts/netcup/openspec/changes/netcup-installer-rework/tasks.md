# Implementation plan

## Phase 1: shared boundaries

- [x] Add the canonical shared Netcup API configuration and loader.
- [x] Refactor `netcup_scp_client.py` to own API configuration/auth/client
      construction without per-frontend global setup duplication.
- [x] Remove `[api]` from `install-host.toml`, `monitor-task.toml`, and the
      obsolete duplicate API config; update settings tests and help text.
- [x] Extract the shared installation-plan/customScript builder from
      `install-host.py`; make normal install and `build-customscript` consume
      it.

## Phase 2: target and mode flow

- [x] Validate mutually exclusive modes and reject ignored combinations.
- [x] Remove installer `--poweroff` and its implementation/help/docs.
- [x] Add interactive API-backed target selection when no target is configured.
- [x] Add explicit target selection support for non-interactive use.
- [x] Move target resolution, API authentication, and protected-server checks
      before every controller-key operation.
- [x] Replace the `$SERVER_NAME` error with the real environment variable and
      an actionable interactive/non-interactive remedy.

## Phase 3: key roles and lifecycle

- [x] Replace single-selection account-key prompting with zero-or-more
      selection and preserve repeated `--ssh-key-id` support.
- [x] Remove the path that registers the temporary controller key as a Netcup
      account key.
- [x] Implement host-targeted controller-key discovery and reuse, including
      compatibility with existing dated filenames.
- [x] Make explicitly supplied missing identity paths fail clearly.
- [x] Add host/local retention settings, validation, summary output, and the
      three valid outcome combinations.
- [x] Add v2 bootstrap support for host retention and preserve failure-safe
      retention.
- [x] Remove local controller key material only after observed successful
      stage2 completion; otherwise disclose why it remains.

## Phase 4: validation and tests

- [x] Add tests proving missing credentials/target/protected target cause no
      key generation.
- [x] Add tests for target picker and non-interactive target refusal.
- [x] Add tests for mode conflicts and removed poweroff parsing.
- [x] Add tests for zero/one/multiple persistent account-key selection.
- [x] Add tests for host-key reuse, dated-key compatibility, explicit-path
      refusal, and retention matrix validation.
- [x] Add tests for v2 host retention and failure behavior.
- [x] Add payload tests for non-object JSON, bool-as-int, non-positive IDs,
      malformed key lists, and clean diagnostics.
- [x] Run the Netcup and Debian-install-v2 gates with
      `CGROUP_PARENT_DEV_GATES` explicitly set.

## Follow-up: explicit wizard and file-driven install boundary

- [x] Make a bare `install-host.py` invocation print usage without API or SSH
      side effects; expose the existing gather/install flow as `wizard`.
- [x] Make `configure` an alias for `wizard`, removing the server-name-only
      default-recipe path that failed before the shared target picker.
- [x] Add `install` with strict default `target-host.jsonc` loading and a
      `--config FILE` override; retain `--payload` as a compatibility alias.
- [x] Make `install` monitor its task by default with an explicit
      `--no-monitor` escape hatch.
- [x] Make Debian v2 `customScript` optional: wizard opt-out, authoritative
      file-driven payload, and no controller-key generation for plain image
      installs.
- [x] Update README, DESIGN-GUIDE, specification, help examples, and tests
      for the mode and bootstrap boundary.

## Phase 5: documentation and handoff

- [x] Update `scripts/netcup/README.md` with the target-picker quickstart,
      account-key/controller-key distinction, reuse behavior, and retention
      matrix.
- [x] Update `scripts/netcup/DESIGN-GUIDE.md` with API/config/customScript
      boundaries and failure ordering.
- [x] Update `install-host.py --help`, `.env.example`, installer TOML comments,
      and v2 bootstrap configuration documentation.
- [x] Verify all examples, closed retention values, and cross-document links.
