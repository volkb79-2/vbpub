# CIU Tests

This folder contains CIU contract and template checks. These tests are lightweight and do not require Docker.

## What is covered
- CIU template presence and section sanity checks.
- CLI argument parsing and dependency checks.
- Hostdir creation and reset behavior.
- Hook execution (including TOML persistence).
- Registry authentication checks.

## Integration tests (planned)
Once Vault and Consul hooks are fully migrated, add integration tests that:
1. Render configs via CIU in dry-run mode.
2. Start Vault + Consul stacks.
3. Execute hooks and validate resulting state/seeded KV.

These integration tests should run in the testing container to ensure consistent tooling.

## Test Repo Fixture

CIU includes a synthetic repository at [ciu/test-repo](../../test-repo) that
covers multiple configuration layouts:

- Standard multi-stack repo with vault + consul + apps
- App without vault
- App with vault hook
- Standalone project with its own ciu.global.defaults.toml.j2
