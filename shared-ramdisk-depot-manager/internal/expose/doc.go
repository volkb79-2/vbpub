// Package expose will own the exposure drivers — the one fork in srdm's
// pipeline, and the only place the two Wings integration modes differ:
//
//	store -> transaction -> verified immutable release
//	      -> publication (op tmpfs -> hold services -> verify -> RO bind)
//	      -> EXPOSURE DRIVER
//	           |- host-bind  (stock Wings)   bind into the volume path   v1
//	           |- provider   (L1 + L1b)      Docker mounts + leases      v2
//
// P06 shipped the interface and the host-bind driver. Everything upstream of
// the fork is shared, so this package is an interface and (eventually) two
// implementations, never two products.
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
// access: rw (P10, D-027/D-035) is an overlay: the sealed, published
// exposure as lowerdir, a per-server upper layer under the state dir
// absorbing every write. That retires the single-consumer restriction P06
// shipped — any number of servers may hold rw on the SAME generation at
// once, each seeing only its own writes, because there is no longer a
// second writer of the same pages to collide with. It also retires
// in-place unsealing (D-020's measured half, D-022's ownership model):
// nothing hands the generation to a declared uid anymore, because the
// generation is never written. What an overlay writes to is a directory
// srdm itself creates and owns.
//
// The one new hazard is D-028's: an overlay reports its OWN device, never
// the lower's, so the superblock matching every other holder check relies
// on is blind to it. internal/consumer gains a second recognizer for
// exactly this — an overlay whose lowerdir resolves under an srdm path is a
// holder, matched by path rather than device, because an overlay's device
// is uninformative and its lowerdir is chosen by the mounter.
package expose
