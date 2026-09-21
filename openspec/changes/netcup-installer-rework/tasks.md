# Implementation plan

## Phase 1: shared boundaries

- [ ] Add the canonical shared Netcup API configuration and loader.
- [ ] Refactor `netcup_scp_client.py` to own API configuration/auth/client
      construction without per-frontend global setup duplication.
- [ ] Remove `[api]` from `install-host.toml`, `monitor-task.toml`, and the
      obsolete duplicate API config; update settings tests and help text.
- [ ] Extract the shared installation-plan/customScript builder from
      `install-host.py`; make normal install and `build-customscript` consume
      it.

## Phase 2: target and mode flow

- [ ] Validate mutually exclusive modes and reject ignored combinations.
- [ ] Remove installer `--poweroff` and its implementation/help/docs.
- [ ] Add interactive API-backed target selection when no target is configured.
- [ ] Add explicit target selection support for non-interactive use.
- [ ] Move target resolution, API authentication, and protected-server checks
      before every controller-key operation.
- [ ] Replace the `$SERVER_NAME` error with the real environment variable and
      an actionable interactive/non-interactive remedy.

## Phase 3: key roles and lifecycle

- [ ] Replace single-selection account-key prompting with zero-or-more
      selection and preserve repeated `--ssh-key-id` support.
- [ ] Remove the path that registers the temporary controller key as a Netcup
      account key.
- [ ] Implement host-targeted controller-key discovery and reuse, including
      compatibility with existing dated filenames.
- [ ] Make explicitly supplied missing identity paths fail clearly.
- [ ] Add host/local retention settings, validation, summary output, and the
      three valid outcome combinations.
- [ ] Add v2 bootstrap support for host retention and preserve failure-safe
      retention.
- [ ] Remove local controller key material only after observed successful
      stage2 completion; otherwise disclose why it remains.

## Phase 4: validation and tests

- [ ] Add tests proving missing credentials/target/protected target cause no
      key generation.
- [ ] Add tests for target picker and non-interactive target refusal.
- [ ] Add tests for mode conflicts and removed poweroff parsing.
- [ ] Add tests for zero/one/multiple persistent account-key selection.
- [ ] Add tests for host-key reuse, dated-key compatibility, explicit-path
      refusal, and retention matrix validation.
- [ ] Add tests for v2 host retention and failure behavior.
- [ ] Add payload tests for non-object JSON, bool-as-int, non-positive IDs,
      malformed key lists, and clean diagnostics.
- [ ] Run the Netcup and Debian-install-v2 gates with
      `CGROUP_PARENT_DEV_GATES` explicitly set.

## Phase 5: documentation and handoff

- [ ] Update `scripts/netcup/README.md` with the target-picker quickstart,
      account-key/controller-key distinction, reuse behavior, and retention
      matrix.
- [ ] Update `scripts/netcup/DESIGN-GUIDE.md` with API/config/customScript
      boundaries and failure ordering.
- [ ] Update `install-host.py --help`, `.env.example`, installer TOML comments,
      and v2 bootstrap configuration documentation.
- [ ] Verify all examples, closed retention values, and cross-document links.
