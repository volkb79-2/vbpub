//go:build e2e

// The privileged half of srdm's gate: the claims that are about the kernel
// and systemd rather than about Go, and that no unit test can reach.
//
// These run as root inside the srdm-gate:e2e container, with systemd as
// PID 1. Building with -tags=e2e is an explicit claim that this is that
// harness, so a missing systemd is a FAILURE here, not a skip — the skip
// belongs at the outer level (tools/gate.sh, when the daemon refuses
// privileged containers), where it can be seen.
package systemdx_test

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/cgroupfs"
	"srdm/internal/systemdx"
)

// blobSize is what each probe faults into its tmpfs. Big enough to dwarf a
// cgroup's own bookkeeping, small enough to be free.
const blobSize = 64 << 20

// chargeSettleBudget bounds the waits below. Like systemdx's own budget it
// is a HANG failsafe, never an oracle: expiry is reported as "the charge
// never settled", which is a true failure — memory that is not returned is
// not returned however long you wait — and freeing a 64 MiB tmpfs is a
// sub-millisecond operation, so this is five orders of magnitude of margin.
const chargeSettleBudget = 30 * time.Second

func requireSystemd(t *testing.T) *systemdx.Client {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
	out, _ := exec.Command("systemctl", "is-system-running").CombinedOutput()
	state := strings.TrimSpace(string(out))
	// "degraded" is fine and expected: a container image carries units that
	// cannot start here, and none of them is one srdm depends on. The exit
	// status is non-zero for "degraded", so the state string decides.
	if state != "running" && state != "degraded" {
		t.Fatalf("systemd is not running in this container (state=%q); "+
			"the e2e gate must boot it as PID 1", state)
	}
	return systemdx.New()
}

// mountTmpfs mounts a tmpfs and guarantees it is gone at the end of the
// test, even on failure — this suite runs as root in a shared container, so
// a leaked mount is somebody else's confusing failure.
func mountTmpfs(t *testing.T, size int64) string {
	t.Helper()
	dir := t.TempDir()
	opts := fmt.Sprintf("size=%d,mode=0755", size)
	if err := syscall.Mount("tmpfs", dir, "tmpfs", syscall.MS_NODEV|syscall.MS_NOSUID, opts); err != nil {
		t.Fatalf("mount tmpfs at %s: %v", dir, err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(dir, syscall.MNT_DETACH) })
	return dir
}

// populateArgv fills the tmpfs and then PARKS, keeping the unit's cgroup
// alive. The parking is the whole point — see systemdx.HoldBaseProperties.
// The shell string is fixed here and interpolates only a path this test
// created, so there is no quoting hazard; srdm's real worker parks itself
// and needs no shell at all.
func populateArgv(tmpfsDir string) []string {
	return []string{"/bin/sh", "-c", fmt.Sprintf(
		"dd if=/dev/zero of=%s/blob bs=1M count=%d status=none; exec sleep infinity",
		tmpfsDir, blobSize>>20)}
}

// startHoldUnit starts the shape srdm actually uses and returns its cgroup
// once the pages are charged.
func startHoldUnit(t *testing.T, c *systemdx.Client, name, tmpfsDir string, extra ...systemdx.Property) string {
	t.Helper()
	ctx := context.Background()

	unit := systemdx.TransientUnit{
		Name:        name,
		Description: "srdm e2e hold probe",
		Properties:  append(systemdx.HoldBaseProperties(), extra...),
		ExecStart:   populateArgv(tmpfsDir),
	}

	t.Cleanup(func() {
		_ = c.Stop(ctx, name)
		_ = c.ResetFailed(ctx, name)
	})

	if err := c.StartTransient(ctx, unit); err != nil {
		t.Fatalf("starting %s: %v", name, err)
	}
	if _, err := c.WaitForSubState(ctx, name, "running"); err != nil {
		t.Fatalf("waiting for %s: %v", name, err)
	}

	cg, err := c.Property(ctx, name, "ControlGroup")
	if err != nil {
		t.Fatalf("reading ControlGroup of %s: %v", name, err)
	}
	if cg == "" {
		t.Fatalf("%s reports an empty ControlGroup even with a parked worker; "+
			"the hold design has no remaining shape on this systemd", name)
	}
	return cg
}

// waitForCharge polls until the cgroup's shmem satisfies want. See
// chargeSettleBudget: the budget is a hang failsafe, and its expiry is a
// real failure rather than a tuning knob.
func waitForCharge(t *testing.T, r cgroupfs.Reader, cg string, want func(int64) bool, describe string) int64 {
	t.Helper()
	deadline := time.Now().Add(chargeSettleBudget)
	var last int64
	for {
		v, err := r.Shmem(cg)
		if err != nil {
			if os.IsNotExist(err) {
				return 0 // the cgroup is gone: nothing is charged here
			}
			t.Fatalf("reading shmem of %s: %v", cg, err)
		}
		last = v
		if want(v) {
			return v
		}
		if time.Now().After(deadline) {
			t.Fatalf("the charge never settled to %s: shmem is %d after %s", describe, last, chargeSettleBudget)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func approximately(target int64) func(int64) bool {
	lo, hi := target-target/10, target+target/10
	return func(v int64) bool { return v >= lo && v <= hi }
}

// --- O6a: the shape the master plan sketched does NOT work here -----------
//
// This pins a measured negative. The plan specified Type=oneshot with
// RemainAfterExit=yes and left the fallback explicitly open; on systemd 257
// the unit stays active but its cgroup is reaped the moment the last
// process exits, so the charge reparents to system.slice where the class
// floor does not apply.
//
// If this test ever FAILS, that is good news, not a regression: a systemd
// that keeps the cgroup would let srdm drop the parked worker. Read the
// failure as "re-open D-011", not as "fix the test".
func TestOneshotRemainAfterExitDoesNotKeepItsCgroup(t *testing.T) {
	c := requireSystemd(t)
	r := cgroupfs.New()
	tmpfs := mountTmpfs(t, blobSize*2)
	ctx := context.Background()

	const unit = "srdm-e2e-oneshot.service"
	t.Cleanup(func() {
		_ = c.Stop(ctx, unit)
		_ = c.ResetFailed(ctx, unit)
	})

	err := c.StartTransient(ctx, systemdx.TransientUnit{
		Name: unit,
		Properties: []systemdx.Property{
			{Name: "Type", Value: "oneshot"},
			{Name: "RemainAfterExit", Value: "yes"},
			{Name: "MemoryMin", Value: "32M"},
		},
		ExecStart: []string{"/bin/dd", "if=/dev/zero",
			"of=" + filepath.Join(tmpfs, "blob"), "bs=1M",
			fmt.Sprintf("count=%d", blobSize>>20), "status=none"},
	})
	if err != nil {
		t.Fatalf("starting %s: %v", unit, err)
	}
	if _, err := c.WaitForSubState(ctx, unit, "exited"); err != nil {
		t.Fatalf("waiting for %s: %v", unit, err)
	}

	props, err := c.Show(ctx, unit, "ActiveState", "SubState", "ControlGroup")
	if err != nil {
		t.Fatal(err)
	}
	// RemainAfterExit does what it says about the UNIT.
	if props["ActiveState"] != "active" || props["SubState"] != "exited" {
		t.Fatalf("unit is %s/%s, want active/exited", props["ActiveState"], props["SubState"])
	}
	// But the cgroup is gone, which is the part that matters.
	if cg := props["ControlGroup"]; cg != "" && r.Exists(cg) {
		t.Fatalf("this systemd KEPT the cgroup %s of an active-but-empty unit. "+
			"That is better than what was measured on systemd 257, and it means the "+
			"parked-worker fallback (D-011) can be dropped — re-open the decision "+
			"rather than editing this test", cg)
	}
	t.Logf("confirmed: systemd reaps an active-but-empty unit's cgroup "+
		"(ControlGroup=%q), so populate-and-hold needs a parked worker", props["ControlGroup"])
}

// --- O6b: the parked-worker shape holds the charge ------------------------

func TestHoldUnitKeepsChargeWhileTheWorkerParks(t *testing.T) {
	c := requireSystemd(t)
	r := cgroupfs.New()
	tmpfs := mountTmpfs(t, blobSize*2)

	cg := startHoldUnit(t, c, "srdm-e2e-hold-charge.service", tmpfs)

	if !r.Exists(cg) {
		t.Fatalf("cgroup %s does not exist while the worker is parked", cg)
	}
	// The pages the worker faulted are charged THERE, not to whoever
	// started it and not to the parent slice.
	got := waitForCharge(t, r, cg, approximately(blobSize), fmt.Sprintf("~%d bytes", blobSize))
	t.Logf("hold unit cgroup %s holds shmem=%d (wrote %d)", cg, got, blobSize)
}

// --- O9: declared class policy is actually applied ------------------------

func TestClassMemoryPolicyReachesTheCgroup(t *testing.T) {
	c := requireSystemd(t)
	r := cgroupfs.New()
	tmpfs := mountTmpfs(t, blobSize*2)

	cg := startHoldUnit(t, c, "srdm-e2e-hold-props.service", tmpfs,
		systemdx.Property{Name: "MemoryMin", Value: "32M"},
		// The pak class bypasses zswap entirely: its content is
		// zstd-incompressible, so compressing it burns CPU to not shrink.
		systemdx.Property{Name: "MemoryZSwapMax", Value: "0"},
	)

	// Reading the CGROUP, not `systemctl show`, is the stronger assertion:
	// show reports what was requested, the cgroup file reports what the
	// kernel applied. A silently dropped property is the failure that
	// matters — a class floor nobody applied reads exactly like one that was.
	min, err := r.ReadInt(cg, "memory.min")
	if err != nil {
		t.Fatalf("reading memory.min of %s: %v", cg, err)
	}
	if min != 32<<20 {
		t.Errorf("memory.min = %d, want %d — the class floor did not reach the kernel", min, 32<<20)
	}
	zswap, err := r.ReadInt(cg, "memory.zswap.max")
	if err != nil {
		t.Fatalf("reading memory.zswap.max of %s: %v", cg, err)
	}
	if zswap != 0 {
		t.Errorf("memory.zswap.max = %d, want 0 — pak pages would be compressed pointlessly", zswap)
	}
}

// --- O7: unmounting frees and uncharges ------------------------------------

func TestUnmountingTheTmpfsDropsTheChargeAndLeaksNothing(t *testing.T) {
	c := requireSystemd(t)
	r := cgroupfs.New()
	ctx := context.Background()

	dyingBefore, err := r.DyingDescendants("/")
	if err != nil {
		t.Fatalf("reading root cgroup.stat: %v", err)
	}

	// Repeated cycles, because a single one cannot tell a leak from the
	// kernel's ordinary bookkeeping. Removing a cgroup that still carries
	// ANY charge — the parked worker's own anon pages and page tables, not
	// just content — creates a transient dying cgroup that drains shortly
	// after. Measured: one teardown reliably bumps nr_dying_descendants by
	// one. "Stable" in the acceptance oracle therefore means DOES NOT
	// ACCUMULATE, not "never increments": a real leak grows the count once
	// per cycle and never gives it back.
	const cycles = 3
	for i := range cycles {
		unit := fmt.Sprintf("srdm-e2e-teardown-%d.service", i)
		tmpfs := mountTmpfs(t, blobSize*2)

		cg := startHoldUnit(t, c, unit, tmpfs)
		waitForCharge(t, r, cg, approximately(blobSize), "the written size")

		// The design's teardown order: drop the mount FIRST, while the hold
		// unit is still alive to be uncharged.
		if err := syscall.Unmount(tmpfs, 0); err != nil {
			t.Fatalf("cycle %d: unmount %s: %v", i, tmpfs, err)
		}
		got := waitForCharge(t, r, cg, func(v int64) bool { return v < blobSize/10 }, "~0")
		t.Logf("cycle %d: after unmount, shmem=%d (was ~%d)", i, got, blobSize)

		if err := c.Stop(ctx, unit); err != nil {
			t.Fatalf("cycle %d: stopping the hold unit: %v", i, err)
		}
		_ = c.ResetFailed(ctx, unit)
	}

	// Give the transient dying cgroups time to drain. The budget is a hang
	// failsafe: a count that is going to come back down does so in
	// milliseconds, and one that never does is a leak however long we wait.
	deadline := time.Now().Add(chargeSettleBudget)
	var dyingAfter int64
	for {
		dyingAfter, err = r.DyingDescendants("/")
		if err != nil {
			t.Fatal(err)
		}
		if dyingAfter <= dyingBefore {
			t.Logf("nr_dying_descendants returned to %d after %d teardowns", dyingAfter, cycles)
			return
		}
		if time.Now().After(deadline) {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}

	// It did not drain. Distinguish "slow" from "leaking" by the shape: a
	// per-cycle leak grows the count once per cycle.
	if dyingAfter-dyingBefore >= cycles {
		t.Fatalf("nr_dying_descendants grew %d -> %d over %d teardowns — one per cycle, "+
			"which is pages still charged below every removed cgroup", dyingBefore, dyingAfter, cycles)
	}
	t.Fatalf("nr_dying_descendants was %d before %d teardowns and %d after, and did not "+
		"drain within %s", dyingBefore, cycles, dyingAfter, chargeSettleBudget)
}

// --- O8: teardown does NOT free while a consumer's bind survives ----------
//
// This is the property that makes teardown safety a correctness gate rather
// than politeness. A game container that still has the bind in its own
// rprivate namespace keeps the superblock — and every page — alive across
// the host unmount, so the memory is never returned and the hold cgroup
// does not drain. srdm must therefore resolve holders and REFUSE, not
// proceed into a leak it cannot see.
//
// A second bind of the same tmpfs models that exactly: two mounts, one
// superblock. Dropping the host-side mount leaves the consumer's alive.
//
// Note what this test does NOT use, because the first attempt did and was
// wrong: an open file descriptor. An open file makes the mount BUSY, so the
// unmount fails outright with EBUSY rather than succeeding and leaving a
// ghost. That is a useful finding in its own right — it is why holder
// resolution reads /proc/*/mountinfo rather than scanning for open fds, and
// why the production symptom was a ghost mount rather than a failed umount.
func TestUnmountDoesNotFreeWhileAConsumerBindSurvives(t *testing.T) {
	c := requireSystemd(t)
	r := cgroupfs.New()
	tmpfs := mountTmpfs(t, blobSize*2)

	cg := startHoldUnit(t, c, "srdm-e2e-held.service", tmpfs)
	waitForCharge(t, r, cg, approximately(blobSize), "the written size")

	// The consumer's own bind of the same superblock.
	consumer := t.TempDir()
	if err := syscall.Mount(tmpfs, consumer, "", syscall.MS_BIND, ""); err != nil {
		t.Fatalf("bind %s -> %s: %v", tmpfs, consumer, err)
	}
	unbound := false
	defer func() {
		if !unbound {
			_ = syscall.Unmount(consumer, syscall.MNT_DETACH)
		}
	}()

	// The host-side mount goes away, as srdm's teardown would do it.
	if err := syscall.Unmount(tmpfs, 0); err != nil {
		t.Fatalf("unmount %s: %v", tmpfs, err)
	}

	// Checked immediately and without waiting: the strict reading. If the
	// charge were going to drop, waiting could only help this assertion
	// pass spuriously.
	stillCharged, err := r.Shmem(cg)
	if err != nil {
		t.Fatalf("reading shmem after the host-side unmount: %v", err)
	}
	if !approximately(blobSize)(stillCharged) {
		t.Fatalf("shmem dropped to %d after unmounting the host side while a consumer "+
			"bind survived; if teardown really did free here, srdm's "+
			"refusal-on-live-consumer rule would be unnecessary — verify that before "+
			"relaxing it", stillCharged)
	}
	t.Logf("with a consumer bind alive, shmem stayed at %d across the host unmount", stillCharged)

	// Release the last reference: only now may the pages go.
	if err := syscall.Unmount(consumer, 0); err != nil {
		t.Fatalf("unmount the consumer bind: %v", err)
	}
	unbound = true

	got := waitForCharge(t, r, cg, func(v int64) bool { return v < blobSize/10 }, "~0")
	t.Logf("after the consumer bind went away, shmem=%d", got)
}
