// Package publish will own the publication mount topology: the op-private
// tmpfs per class, the per-class populate-and-hold services that carry the
// class memory policy, verification against the manifest, and the read-only
// bind that finally exposes a generation.
//
// Empty until P02. The shape is fixed by the master plan (§Publication
// topology, §Generation slices and charging) and two properties of it are
// worth carrying here so P02 does not rediscover them:
//
//   - Populate and hold are the SAME transient unit (Type=oneshot,
//     RemainAfterExit=yes). The pages a worker faults are charged to the
//     cgroup it ran in, and they stay charged; keeping the unit active is
//     what stops the charge and the policy from ever separating. One cgroup
//     cannot hold two memory policies, so there is one hold service per
//     class, not one per generation.
//
//   - Teardown order is: drop the read-only bind exposure, unmount the op
//     tmpfs, stop the hold service, stop the generation slice. "Unmounting
//     frees the pages" holds only while nothing else holds a reference — a
//     consumer that still has the bind in its own rprivate namespace keeps
//     the superblock, and every page, alive across the host unmount. So
//     teardown must resolve holders and refuse, rather than proceed into a
//     leak it cannot see.
package publish
