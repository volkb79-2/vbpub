// Package expose will own the exposure drivers — the one fork in srdm's
// pipeline, and the only place the two Wings integration modes differ:
//
//	store -> transaction -> verified immutable release
//	      -> publication (op tmpfs -> hold services -> verify -> RO bind)
//	      -> EXPOSURE DRIVER
//	           |- host-bind  (stock Wings)   bind into the volume path   v1
//	           |- provider   (L1 + L1b)      Docker mounts + leases      v2
//
// Empty until P03 (host-bind). Everything upstream of the fork is shared,
// so this package is an interface and two implementations, never two
// products.
//
// host-bind has three hard preconditions. They are refusals, not warnings,
// and each is a property srdm cannot repair for the operator:
//
//  1. The Wings container must carry propagation rslave on
//     /var/lib/pterodactyl, and the host peer group must be shared. Under
//     Docker's default rprivate, every mount srdm performs is invisible to
//     Wings and every unmount leaves a ghost Wings still traverses. That
//     combination caused a production outage on 2026-07-31.
//
//  2. For access: ro, the running Wings must either carry the F1 patch or
//     have system.check_permissions_on_boot set false. Wings' pre-boot chown
//     walk calls Lchownat on every entry unconditionally, and a chown on a
//     read-only mount returns EROFS even when the owner already matches — so
//     a read-only bind anywhere under a server's volume makes that server
//     unstartable. Detect it up front rather than let an operator meet it as
//     an unexplained start failure.
//
//  3. Activate, rollback and teardown require the affected consumers
//     stopped. There is no disposal callback in this mode, so srdm resolves
//     holders itself — running containers by volume path, plus
//     /proc/*/mountinfo — and refuses while any hold remains.
//
// access: rw is single-consumer, and that is a refusal too. With two
// consumers a write is the 2026-07-29 corruption by construction: the peer
// holds the deleted old .pak open, the tmpfs exhausts mid-write, and the
// generation ends with a new .sig and no .pak — survivable for the process
// that already mmap'd the old inode, fatal for the next start.
package expose
