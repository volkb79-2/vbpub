# cowtree backlog (COW-BL)

Open work. Each item names what it needs and what closes it. Consumers: `ciu worktree` (CIU-159) and run-gate R2 clones.

* **COW-BL-01 declaration schema for CoW-managed gitignored paths.** Cowtree defines and validates the schema; the consumers read it and stay agnostic of what the paths hold (`artifacts/vm-lab`, `downloads` and the like are the project's business, never a ciu or run-gate constant).
  * **Where it lives.** The project declares it in a tracked file (one table per path). The consumer passes the parsed declaration to cowtree; cowtree refuses an unknown key, a duplicate path, an absolute path, a `..` segment and a path that is tracked content other than its committed placeholder.
  * **Keys per path.**
    * `path`: repo-relative, gitignored.
    * `mode`: `clone-from-main` is the only value today. A worktree gets a writable snapshot of main's content after checkout.
    * `compression`: a btrfs compression name (for example `zstd`) or `none`. Applied to the new subvolume; ignored by the reflink and copy backends.
    * `fallback`: what a plain host does. The value is `empty` (an empty folder, plus exactly one INFO line naming the path and the reason). Any other value is refused.
  * **Placeholder contract.** Every declared path is a committed placeholder directory: a `README.md` and a `.gitignore` (`*`, `!.gitignore`, `!README.md`), force-added. The validator checks both files exist in the tree and that the `.gitignore` has exactly that shape.
  * **No overrides.** No `MDT_*` or other environment variable relocates a declared path, and there is no mount-over: the declared path is a subvolume of the checkout itself.
  * **Closes when.** The schema has a SPEC section with oracle ids, a `validate_declaration()` API and a CLI verb with a stable exit code, and tests cover each refusal above; CIU-159 and the run-gate R2 clone code read the declaration only through it.

* **COW-BL-02 provision operation (main) and snapshot operations (worktree).** Primary model: the checkout, with `.worktrees/` inside it, lives on the CoW filesystem. Worktrees still come from git. Only declared paths become CoW snapshots of main's.
  * **Provision in main (`cowtree provision`).**
    1. Move the committed placeholder contents aside (a subvolume create needs a non-existing path).
    2. Create the subvolume at the declared path with the declared compression.
    3. Move the placeholder files back, so `git status` stays clean.
    * Idempotent: a path that is already a subvolume is left alone. A plain non-empty directory with content beyond the placeholder is refused, with its size and top-level entries; a `--migrate` flag copies with reflink, verifies, then replaces. `--migrate` refuses while a process holds files there.
  * **Snapshot for a worktree (`cowtree snapshot-declared`).** After `git worktree add`, replace each declared path with a snapshot of main's subvolume, then restore the tracked placeholder files from the worktree's own branch (so a branch that changed the placeholder keeps its version and `git status` stays clean).
  * **Remove for a worktree (`cowtree remove-declared`).** Delete each declared subvolume with the subvolume-delete ioctl (not `rm -rf` into it), then report remaining protected state as before. Refuse when a foreign mount sits under the worktree (see the worktree library item WT-OW-01).
  * **Persistence.** Anything a worktree keeps is copied (reflink where possible) to main's declared path by an explicit operation; there is no automatic merge back.
  * **Fallback.** On a plain host (no btrfs, or a checkout not on the CoW area) every operation degrades to the declared `fallback`: an empty folder, one INFO line, exit 0.
  * **Closes when.** The three operations exist on the btrfs backend with FakeBackend coverage for the ordering (aside, create, back; snapshot, restore; delete), a live btrfs test on the tester host proves a clean `git status` after each, and the plain-host fallback prints exactly one INFO line per path.

* **COW-BL-03 ZFS dataset backend (design note only; no ZFS host to test).**
  * **Today.** Cowtree has btrfs, reflink and copy backends. ZFS is reachable only through block-cloning reflink on a ZFS version that supports it; there is no snapshot or subvolume semantics there.
  * **Design.** A persistent `dataset` operation set (`create`, `mount`, `destroy`, plus `snapshot_clone` for the worktree case) would be the new backend interface, beside the existing per-run `clone` interface. Provision and the worktree operations of COW-BL-02 call it instead of calling btrfs directly, so a ZFS backend adds one class.
  * **Not decided.** Dataset naming under the pool, mount point management when the checkout is not under the dataset's mount point, and the destroy ordering (clones before origin).
  * **Closes when.** Written into DESIGN-GUIDE as an interface sketch the btrfs backend already satisfies, implemented only when a ZFS host exists to run the live test. Until then nothing is built.
