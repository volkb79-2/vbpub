# hostobs M0 workspace decisions

R-1 establishes only the Cargo package boundaries and gate contract. The
directory names follow hostobs plan v4. The legacy sampler remains the
behavioral source for later imports; M0 does not move or rewrite its runtime
code, select the unresolved TUI, or claim hostobs features.

Dependencies remain outside Git. The sampler's measured dependency floors are
plain caret constraints in the workspace manifest, while `Cargo.lock` pins
the resolved graph and the sibling tester image caches its `.crate` archives.
Every lane is offline and locked, with a precheck tying the mounted lock bytes
and each registry archive to the image build.

The workspace denies unsafe Rust by default. Only `kernel` and `act` declare
the planned unsafe-code allowance. Cargo rejects combining a workspace-lint
inheritance stanza with a package-level override, so those two manifests
declare their allowance directly while the other seven inherit the workspace
lint. The current scaffold contains no unsafe code.

The bootstrap coverage tests verify crate identities so the workspace can
exercise the 90% line and 85% region floor mechanics before hostobs behavior
is imported. Later functional imports must replace these checks with tests of
observable behavior; these bootstrap tests are not a product-coverage claim.

`cargo deny` keeps duplicate-version and external wildcard checks strict. Its
single `winnow@0.7.15` skip is tied to the imported sampler lock: `toml 0.9.8`
and its `toml_parser 1.1.5` dependency resolve `winnow` 0.7.15 and 1.0.4.
The skip names only the older exact version, so it becomes an unused-entry
warning when that lock edge disappears. Workspace packages are explicitly
unpublished in M0; their license metadata is deferred until a publication
decision, while registry dependencies remain license-checked. The wildcard
path allowance covers only local workspace edges; registry wildcards remain
denied.
The lint lane invokes cargo-deny with `--locked --offline`, matching the
workspace's cache contract and preventing dependency resolution from becoming
an implicit gate side effect.
