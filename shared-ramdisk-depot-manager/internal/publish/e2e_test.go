//go:build e2e

// The privileged half of publication: what the KERNEL and SYSTEMD do with
// the mounts and units the unit tests only assert the shape of.
//
// A read-only bind either produces EROFS or it does not, and no fake can
// tell you which. Neither can a fake tell you whether unmounting the op
// tmpfs actually returns the pages, whether a class floor reached the
// kernel, or whether the slice a generation lives in has an ancestor that
// quietly caps every floor beneath it at zero.
package publish

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/cgroupfs"
	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/hold"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/store"
	"srdm/internal/systemdx"
)

// workerEnv makes this test binary act as the hold worker.
//
// srdm's worker is the srdm binary invoked on itself; here the running
// process is a test binary, so the same trick is played on it. The
// alternative — building and installing a separate srdm into the container —
// would test a different binary from the one under test.
const workerEnv = "SRDM_E2E_WORKER"

// parentSliceFloor is what the harness gives the admin-owned parent slice.
//
// srdm VERIFIES this unit rather than writing it (D-003), so in production
// it is an operator's file. The oracles below need it to exist, because an
// ancestor with memory.min=0 caps every floor beneath it and the whole point
// of the chain assertion is to notice exactly that.
const parentSliceFloor = 512 << 20

// bigContent is a release whose pak class is large enough for a charge to be
// unmistakable — the default fixture is a few bytes and would fit in one
// page, so a charging oracle built on it would pass without measuring
// anything.
const bigPakBytes = 32 << 20

func bigContent() map[string]string {
	c := map[string]string{}
	for k, v := range content {
		c[k] = v
	}
	c["WS/Content/Paks/game.pak"] = strings.Repeat("p", bigPakBytes)
	return c
}

func TestMain(m *testing.M) {
	if os.Getenv(workerEnv) == "1" && len(os.Args) > 1 && os.Args[1] == hold.WorkerSubcommand {
		os.Exit(hold.RunWorker(os.Args[2:], os.Stderr))
	}
	installParentSlice()
	code := m.Run()
	removeParentSlice()
	os.Exit(code)
}

// installParentSlice writes the unit an admin would ship.
//
// Under /run, not /etc: it belongs to this container's boot and nothing
// more, and the e2e image is disposable either way.
func installParentSlice() {
	body := fmt.Sprintf(`[Unit]
Description=srdm managed content (e2e stand-in for the admin-owned unit)
[Slice]
MemoryMin=%d
`, parentSliceFloor)
	if err := os.MkdirAll("/run/systemd/system", 0o755); err != nil {
		panic(err)
	}
	if err := os.WriteFile("/run/systemd/system/srdm.slice", []byte(body), 0o644); err != nil {
		panic(err)
	}
	if out, err := exec.Command("systemctl", "daemon-reload").CombinedOutput(); err != nil {
		panic(fmt.Sprintf("daemon-reload: %v: %s", err, out))
	}
}

func removeParentSlice() {
	_ = exec.Command("systemctl", "stop", "srdm.slice").Run()
	_ = os.Remove("/run/systemd/system/srdm.slice")
	_ = exec.Command("systemctl", "daemon-reload").Run()
}

func requireRoot(t *testing.T) {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
}

// requireSystemd fails rather than skips. Building with -tags=e2e is an
// explicit claim that this IS the privileged harness, so a missing systemd
// is a failure here; the skip belongs at the outer level (tools/gate.sh),
// where it can be seen.
func requireSystemd(t *testing.T) {
	t.Helper()
	out, _ := exec.Command("systemctl", "is-system-running").CombinedOutput()
	state := strings.TrimSpace(string(out))
	// "degraded" is fine and expected: a container image carries units that
	// cannot start here, and none of them is one srdm depends on.
	if state != "running" && state != "degraded" {
		t.Fatalf("systemd is not running in this container (state=%q); "+
			"the e2e gate must boot it as PID 1", state)
	}
}

// realPublisher publishes for real: real mounts, real transient units, real
// cgroups — and guarantees both are gone at the end of the test, even on
// failure, since this suite runs as root in a shared container.
func realPublisher(t *testing.T, opts ...Option) (*Publisher, *store.Release, *profile.Profile) {
	t.Helper()
	return realPublisherWith(t, content, opts...)
}

func realPublisherWith(t *testing.T, files map[string]string, opts ...Option) (
	*Publisher, *store.Release, *profile.Profile) {
	t.Helper()
	requireRoot(t)
	requireSystemd(t)

	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedReleaseWith(t, cfg, jnl, prof, files)

	p, err := New(cfg, jnl, append([]Option{WithHolder(realHolder(t))}, opts...)...)
	if err != nil {
		t.Fatal(err)
	}

	// Cleanups run last-registered-first, so the units go AFTER the mounts —
	// which is also the only order that uncharges.
	t.Cleanup(func() { cleanUpUnits(t, rel, prof, cfg.Slice) })
	t.Cleanup(func() { cleanUpMounts(t, cfg.RunDir) })
	return p, rel, prof
}

func realHolder(t *testing.T) *hold.Manager {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	h, err := hold.New(hold.WithExe(exe), hold.WithWorkerEnv(workerEnv+"=1"))
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func cleanUpUnits(t *testing.T, rel *store.Release, prof *profile.Profile, parent string) {
	t.Helper()
	c := systemdx.New()
	gen := GenerationID(rel.ID)
	for _, cl := range prof.Classes {
		unit, err := hold.UnitName(gen, cl.Name)
		if err != nil {
			continue
		}
		_ = c.Stop(context.Background(), unit)
		_ = c.ResetFailed(context.Background(), unit)
	}
	if slice, err := hold.SliceName(parent, gen); err == nil {
		_ = c.Stop(context.Background(), slice)
		_ = c.Revert(context.Background(), slice)
	}
}

func cleanUpMounts(t *testing.T, runDir string) {
	t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		return
	}
	var points []string
	for _, e := range mountinfo.Under(entries, runDir) {
		points = append(points, e.MountPoint)
	}
	sortedDesc(points)
	for _, m := range points {
		_ = syscall.Unmount(m, syscall.MNT_DETACH)
	}
}

// liveMounts is every mount under root that belongs to a GENERATION.
//
// srdm's own operation root is excluded: publication binds it onto itself
// and makes it private so nothing beneath it is delivered to another mount
// namespace (D-019). It outlives every individual generation on purpose, so
// counting it would make "teardown left nothing behind" impossible to state.
func liveMounts(t *testing.T, root string) []mountinfo.Entry {
	t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		t.Fatal(err)
	}
	var out []mountinfo.Entry
	for _, e := range mountinfo.Under(entries, root) {
		if e.MountPoint == filepath.Join(root, config.OpRootName) {
			continue
		}
		out = append(out, e)
	}
	return out
}

// controlGroup is where systemd actually put the unit.
func controlGroup(t *testing.T, unit string) string {
	t.Helper()
	cg, err := systemdx.New().Property(context.Background(), unit, "ControlGroup")
	if err != nil {
		t.Fatalf("reading ControlGroup of %s: %v", unit, err)
	}
	if cg == "" {
		t.Fatalf("%s reports an empty ControlGroup: its cgroup has been reaped, so the "+
			"class floor applies to nothing and the pages are charged elsewhere", unit)
	}
	return cg
}

func unitProperty(t *testing.T, unit, name string) string {
	t.Helper()
	v, err := systemdx.New().Property(context.Background(), unit, name)
	if err != nil {
		t.Fatalf("reading %s of %s: %v", name, unit, err)
	}
	return v
}

// --- the exposure is genuinely read-only ----------------------------------

func TestPublishedExposureRefusesWritesWithEROFS(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}

	for _, c := range rec.Classes {
		// The content is there and readable.
		var found bool
		err := filepath.WalkDir(c.ExposePath, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if !d.IsDir() {
				found = true
				if _, err := os.ReadFile(path); err != nil {
					return err
				}
			}
			return nil
		})
		if err != nil {
			t.Fatalf("class %q: reading the exposure: %v", c.Name, err)
		}
		if !found {
			t.Errorf("class %q: the exposure has no files", c.Name)
		}

		// And writing to it fails EROFS — not EACCES, which is what a mere
		// chmod would produce. The distinction matters: EROFS is the mount
		// refusing, and it holds even for root.
		err = os.WriteFile(filepath.Join(c.ExposePath, "smuggled"), []byte("x"), 0o644)
		if err == nil {
			t.Fatalf("class %q: writing into a read-only exposure succeeded", c.Name)
		}
		if !errorIs(err, syscall.EROFS) {
			t.Errorf("class %q: writing gave %v, want EROFS — a chmod-only seal would "+
				"give EACCES and would not hold against root", c.Name, err)
		}
	}
}

// The op tmpfs stays writable until the exposure is made; only the bind is
// read-only. Both facts have to hold for the ordering to mean anything.
func TestOnlyTheExposureIsReadOnly(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	live := liveMounts(t, p.cfg.RunDir)

	for _, c := range rec.Classes {
		op, ok := mountinfo.At(live, c.OpMount)
		if !ok {
			t.Fatalf("class %q: the op tmpfs is not mounted", c.Name)
		}
		if op.ReadOnly() {
			t.Errorf("class %q: the op tmpfs is read-only; the seal is a chmod, not a remount", c.Name)
		}
		if op.FSType != "tmpfs" {
			t.Errorf("class %q: the op mount is %q, want tmpfs", c.Name, op.FSType)
		}

		exposed, ok := mountinfo.At(live, c.ExposePath)
		if !ok {
			t.Fatalf("class %q: the exposure is not mounted", c.Name)
		}
		if !exposed.ReadOnly() {
			t.Errorf("class %q: the exposure is not read-only", c.Name)
		}
		// A bind of a subdirectory records that subdirectory as its root,
		// which is how the op tmpfs's own mount point stays out of view.
		if !strings.HasSuffix(exposed.Root, "/root") {
			t.Errorf("class %q: the exposure binds %q, want the content root", c.Name, exposed.Root)
		}
	}
}

// noexec is not decoration: pak content is data, and nothing in it should
// ever be executable.
func TestDataClassesAreMountedNoExec(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	live := liveMounts(t, p.cfg.RunDir)

	for _, c := range rec.Classes {
		op, ok := mountinfo.At(live, c.OpMount)
		if !ok {
			t.Fatalf("class %q: not mounted", c.Name)
		}
		if got := op.HasOption("noexec"); got != c.NoExec {
			t.Errorf("class %q: noexec = %v, want %v", c.Name, got, c.NoExec)
		}
		// nosuid and nodev always: shared content is never a place to gain
		// privilege or reach a device.
		if !op.HasOption("nosuid") || !op.HasOption("nodev") {
			t.Errorf("class %q: options are %v, want nosuid and nodev", c.Name, op.Options)
		}
	}
}

// --- charging: the pages are where the policy is --------------------------

// The claim the whole design rests on. Population happens inside the hold
// unit, so the pages it faults are charged to the cgroup that carries the
// class's memory policy — not to the daemon that asked for them, and not to
// the parent slice.
func TestClassPagesAreChargedToTheirOwnHoldUnit(t *testing.T) {
	p, rel, prof := realPublisherWith(t, bigContent())
	r := cgroupfs.New()

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	pak := classByName(rec, "pak")
	cg := controlGroup(t, pak.HoldUnit)

	// shmem, not memory.current: tmpfs pages are shmem, so this isolates the
	// generation content from the kernel slab and page tables that
	// memory.current also counts.
	got, err := r.Shmem(cg)
	if err != nil {
		t.Fatalf("reading shmem of %s: %v", cg, err)
	}
	// No polling and no tolerance downward: the unit reported READY, which
	// means the worker finished writing before the daemon returned (D-013).
	// If the charge were not there by now, waiting could only make a real
	// failure pass.
	if got < pak.ContentBytes {
		t.Fatalf("%s holds shmem=%d but its class content is %d bytes — the pages were "+
			"faulted somewhere other than the hold unit, so the class floor applies to "+
			"nothing", pak.HoldUnit, got, pak.ContentBytes)
	}
	if slack := got - pak.ContentBytes; slack > 4<<20 {
		t.Errorf("%s holds shmem=%d against %d bytes of content (%d more than expected)",
			pak.HoldUnit, got, pak.ContentBytes, slack)
	}
	t.Logf("%s holds shmem=%d for %d bytes of content", pak.HoldUnit, got, pak.ContentBytes)

	// And the worker is PARKED, not exited. A unit whose last process exits
	// has its cgroup reaped and its charge reparented to the parent slice,
	// where the class floor does not apply (D-011).
	if sub := unitProperty(t, pak.HoldUnit, "SubState"); sub != "running" {
		t.Errorf("%s is in SubState=%s; a healthy hold unit is `running`, and anything "+
			"else means the cgroup holding the content is gone", pak.HoldUnit, sub)
	}
}

// The declared class policy has to reach the kernel, not just systemd.
// Reading the cgroup rather than `systemctl show` is the stronger assertion:
// show reports what was requested; the cgroup file reports what was applied,
// and a floor that was silently dropped reads exactly like one that was not.
func TestClassPolicyReachesTheKernel(t *testing.T) {
	p, rel, prof := realPublisher(t)
	r := cgroupfs.New()

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}

	pak := classByName(rec, "pak")
	cg := controlGroup(t, pak.HoldUnit)
	if min, err := r.ReadInt(cg, "memory.min"); err != nil {
		t.Fatalf("reading memory.min of %s: %v", cg, err)
	} else if min != 150<<20 {
		t.Errorf("%s memory.min = %d, want %d — the class floor did not reach the kernel",
			pak.HoldUnit, min, 150<<20)
	}
	// pak bypasses zswap entirely: its content is zstd-incompressible, so
	// compressing it burns CPU to not shrink.
	if z, err := r.ReadInt(cg, "memory.zswap.max"); err != nil {
		t.Fatalf("reading memory.zswap.max of %s: %v", cg, err)
	} else if z != 0 {
		t.Errorf("%s memory.zswap.max = %d, want 0 — pak pages would be compressed "+
			"pointlessly", pak.HoldUnit, z)
	}
	if w, err := r.ReadInt(cg, "memory.zswap.writeback"); err != nil {
		t.Fatalf("reading memory.zswap.writeback of %s: %v", cg, err)
	} else if w != 1 {
		t.Errorf("%s memory.zswap.writeback = %d, want 1", pak.HoldUnit, w)
	}

	// The code class declares no zswap policy, and unset must stay unset —
	// otherwise every class silently inherits pak's.
	code := classByName(rec, "code")
	ccg := controlGroup(t, code.HoldUnit)
	if min, err := r.ReadInt(ccg, "memory.min"); err != nil {
		t.Fatal(err)
	} else if min != 200<<20 {
		t.Errorf("%s memory.min = %d, want %d", code.HoldUnit, min, 200<<20)
	}
	if z, err := r.ReadInt(ccg, "memory.zswap.max"); err != nil {
		t.Fatal(err)
	} else if z != cgroupfs.Unlimited {
		t.Errorf("%s memory.zswap.max = %d, want the default (max) — a class that declared "+
			"no zswap policy was given one", code.HoldUnit, z)
	}
}

// The floor that matters is the EFFECTIVE one, and cgroup v2 caps a cgroup's
// protection by every ancestor's. A single zero anywhere between the hold
// unit and the admin-owned parent makes every floor below it arithmetically
// dead — and an implicitly created slice starts at exactly zero.
//
// This is the oracle that rejects the master plan's `srdm-gen-<g8>.slice`
// naming, which interposes an auto-created `srdm-gen.slice` nobody owns
// (D-015). Measured: with that name the chain reads 0 -> 0 -> 367001600.
func TestNoAncestorSliceCapsTheClassFloorsAtZero(t *testing.T) {
	p, rel, prof := realPublisher(t)
	r := cgroupfs.New()

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	pak := classByName(rec, "pak")
	cg := controlGroup(t, pak.HoldUnit)

	// Everything from the unit's cgroup up to and including the configured
	// parent slice is srdm's responsibility. Above that is the host's, and
	// doctor's parent-slice check.
	const generationFloor = int64(350 << 20) // pak 150M + code 200M
	chain := ancestorsUpTo(t, cg, p.cfg.Slice)
	if len(chain) != 2 {
		t.Fatalf("the hold unit sits at %s, whose ancestry up to %s is %v; want exactly "+
			"the generation slice and the parent — anything else is a slice srdm does not "+
			"own sitting between them", cg, p.cfg.Slice, chain)
	}
	for _, ancestor := range chain {
		min, err := r.ReadInt(ancestor, "memory.min")
		if err != nil {
			t.Fatalf("reading memory.min of %s: %v", ancestor, err)
		}
		if min < generationFloor {
			t.Errorf("ancestor %s has memory.min=%d, below the %d of class floors beneath "+
				"it; cgroup v2 caps a floor by its ancestors', so every class floor under "+
				"this is prorated away", ancestor, min, generationFloor)
		}
		t.Logf("%s memory.min=%d", ancestor, min)
	}
}

// ancestorsUpTo lists the cgroups between a unit's cgroup and the named
// parent slice, innermost first, including the parent.
func ancestorsUpTo(t *testing.T, controlGroup, parent string) []string {
	t.Helper()
	var out []string
	cur := controlGroup
	for {
		i := strings.LastIndex(cur, "/")
		if i <= 0 {
			t.Fatalf("walked past the cgroup root without reaching %s from %s",
				parent, controlGroup)
		}
		cur = cur[:i]
		out = append(out, cur)
		if strings.HasSuffix(cur, "/"+parent) {
			return out
		}
	}
}

// --- teardown --------------------------------------------------------------

func TestTeardownLeavesNoMountsAndNoUnitsBehind(t *testing.T) {
	p, rel, prof := realPublisherWith(t, bigContent())

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	if len(liveMounts(t, p.cfg.RunDir)) == 0 {
		t.Fatal("publication mounted nothing")
	}

	if err := p.Teardown(ctx(), "op-2", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	if left := liveMounts(t, p.cfg.RunDir); len(left) != 0 {
		var points []string
		for _, e := range left {
			points = append(points, e.MountPoint)
		}
		t.Fatalf("teardown left %v mounted", points)
	}
	for _, c := range rec.Classes {
		if state := unitProperty(t, c.HoldUnit, "ActiveState"); state != "inactive" {
			t.Errorf("%s is %s after teardown; its cgroup and the pages charged to it "+
				"are still there", c.HoldUnit, state)
		}
	}
	// The aggregate too: measured, a slice stays active and keeps its cgroup
	// after its last service exits, so stopping the services is not enough.
	if state := unitProperty(t, rec.Slice, "ActiveState"); state != "inactive" {
		t.Errorf("%s is %s after teardown, so the generation's cgroup survives it",
			rec.Slice, state)
	}
	if _, err := os.Stat(cgroupfs.New().Path("/" + rec.Slice)); !os.IsNotExist(err) {
		t.Errorf("the cgroup of %s outlived teardown: %v", rec.Slice, err)
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); !os.IsNotExist(err) {
		t.Errorf("the record survived teardown: %v", err)
	}
}

// Master-plan oracle 12's last clause. A teardown that leaks shows up as
// nr_dying_descendants climbing and not coming back down — cgroups removed
// while pages are still charged below them.
//
// Repeated cycles, because a single one cannot tell a leak from the kernel's
// ordinary bookkeeping: removing a cgroup that still carries ANY charge
// creates a transient dying cgroup that drains shortly after. "Stable" means
// DOES NOT ACCUMULATE, not "never increments".
func TestRepeatedPublishAndTeardownDoNotAccumulateDyingCgroups(t *testing.T) {
	p, rel, prof := realPublisherWith(t, bigContent())
	r := cgroupfs.New()

	before, err := r.DyingDescendants("/")
	if err != nil {
		t.Fatalf("reading root cgroup.stat: %v", err)
	}

	const cycles = 3
	for i := range cycles {
		op := fmt.Sprintf("op-%d", i)
		rec, err := p.Publish(ctx(), op, "testgame", rel, prof)
		if err != nil {
			t.Fatalf("cycle %d: Publish: %v", i, err)
		}
		// The same unit names every cycle, deliberately: if teardown did not
		// really clear them, systemd refuses the next start by name and this
		// fails on cycle 1.
		if err := p.Teardown(ctx(), op+"-down", "testgame", rec.Generation); err != nil {
			t.Fatalf("cycle %d: Teardown: %v", i, err)
		}
	}

	// A budget, not an oracle: a count that is going to come back down does
	// so in milliseconds, and one that never does is a leak however long you
	// wait.
	deadline := time.Now().Add(30 * time.Second)
	var after int64
	for {
		after, err = r.DyingDescendants("/")
		if err != nil {
			t.Fatal(err)
		}
		if after <= before {
			t.Logf("nr_dying_descendants returned to %d after %d publish/teardown cycles",
				after, cycles)
			return
		}
		if time.Now().After(deadline) {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	// Distinguish "slow" from "leaking" by the shape: a per-cycle leak grows
	// the count once per cycle.
	if after-before >= cycles {
		t.Fatalf("nr_dying_descendants grew %d -> %d over %d cycles — one per cycle, "+
			"which is pages still charged below every removed cgroup", before, after, cycles)
	}
	t.Fatalf("nr_dying_descendants was %d before %d cycles and %d after, and did not drain",
		before, cycles, after)
}

// --- ENOSPC quarantines rather than half-publishing -----------------------
//
// The 2026-07-29 corruption was a write that ran out of tmpfs space
// half-way. A class that cannot hold its content must refuse, not produce a
// partial generation — and the only way to test that is to make a tmpfs
// genuinely too small. It also exercises the refusal channel end to end: the
// worker is a separate process, so the only way it can say "out of space"
// rather than "broken" is its exit status.
func TestClassTooSmallIsRefusedAndLeavesNothingMounted(t *testing.T) {
	big := bigContent()
	big["WS/Content/Paks/game.pak"] = strings.Repeat("p", 4<<20)

	p, rel, prof := realPublisherWith(t, big, WithClassSizer(func(class string, _ int64) int64 {
		if class == "pak" {
			return 64 << 10 // 64 KiB against 4 MiB of content
		}
		return ClassSize(1)
	}))

	_, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err == nil {
		t.Fatal("a class that cannot hold its content was published")
	}
	if !IsRefusal(err) {
		t.Errorf("running out of space was reported as a fault rather than a refusal: %v", err)
	}
	var enospc *ENOSPCError
	if !errors.As(err, &enospc) {
		t.Fatalf("want an ENOSPCError, got %T: %v", err, err)
	}
	if enospc.Class != "pak" {
		t.Errorf("the error names class %q, want pak", enospc.Class)
	}
	if !strings.Contains(err.Error(), "quarantined") {
		t.Errorf("the error does not say what happened to the generation: %v", err)
	}

	// Nothing left mounted, nothing recorded.
	if left := liveMounts(t, p.cfg.RunDir); len(left) != 0 {
		var points []string
		for _, e := range left {
			points = append(points, e.MountPoint)
		}
		t.Errorf("a refused publication left %v mounted", points)
	}
	if _, err := p.LoadRecord("testgame", GenerationID(rel.ID)); !os.IsNotExist(err) {
		t.Errorf("a refused publication left a record: %v", err)
	}

	// And no unit left in a failed state. A transient unit that failed
	// lingers in systemd until it is reset, and the next publication of the
	// same generation is then refused by name — which reads as a flaky
	// daemon rather than as leftover state.
	unit, err := hold.UnitName(GenerationID(rel.ID), "pak")
	if err != nil {
		t.Fatal(err)
	}
	if state := unitProperty(t, unit, "LoadState"); state == "loaded" {
		t.Errorf("%s is still loaded (ActiveState=%s) after a refusal; the next attempt "+
			"at this generation would fail by name", unit, unitProperty(t, unit, "ActiveState"))
	}
}

// --- teardown safety: master-plan oracle 24 --------------------------------

// startConsumer runs a process in its own mount namespace holding a bind of
// path — which is what a game container is, as far as the superblock is
// concerned.
//
// `--propagation slave`, not `private`: a private namespace would hold an
// independent copy of every mount it inherited and pin every superblock on
// the host, which would make this pass for the wrong reason. A slave
// namespace receives srdm's mounts and loses them again when srdm unmounts;
// its own bind is the one thing that survives, which is exactly the hold
// Docker gives a game container and exactly what teardown must refuse over.
//
// The returned stop waits for the process to be reaped, which is a real
// synchronization point and not a delay: after it, the pid and its mount
// namespace are gone from /proc.
func startConsumer(t *testing.T, path string) (stop func()) {
	t.Helper()
	script := fmt.Sprintf(
		"mkdir -p /tmp/held.$$ && mount --bind %q /tmp/held.$$ && echo ready && exec sleep 600", path)
	cmd := exec.Command("unshare", "--mount", "--propagation", "slave", "/bin/sh", "-c", script)
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the consumer: %v", err)
	}
	stopped := false
	stop = func() {
		if stopped {
			return
		}
		stopped = true
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}
	t.Cleanup(stop)

	sc := bufio.NewScanner(out)
	if !sc.Scan() || strings.TrimSpace(sc.Text()) != "ready" {
		stop()
		t.Fatalf("the consumer never reported ready (%v)", sc.Err())
	}
	return stop
}

// Oracle 24, both directions, with no protocol to help: with a consumer
// still running teardown is refused and the holder named; after a clean stop
// it proceeds and the memory actually comes back.
//
// The refusal direction is the one that cannot be checked any other way.
// Every step of an unguarded teardown SUCCEEDS here — the unmounts return 0,
// the units stop, the record is removed — and not one page is returned,
// because the consumer's namespace still holds the superblock. There is no
// error afterwards to detect. Refusing beforehand is the only place the
// difference exists.
func TestTeardownRefusesWhileAConsumerHoldsAndFreesAfterItStops(t *testing.T) {
	p, rel, prof := realPublisherWith(t, bigContent())
	r := cgroupfs.New()

	dyingBefore, err := r.DyingDescendants("/")
	if err != nil {
		t.Fatal(err)
	}

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	pak := classByName(rec, "pak")
	cg := controlGroup(t, pak.HoldUnit)
	charged, err := r.Shmem(cg)
	if err != nil {
		t.Fatal(err)
	}
	if charged < pak.ContentBytes {
		t.Fatalf("the class is not charged before the test begins: shmem=%d", charged)
	}

	stop := startConsumer(t, pak.ExposePath)

	// --- with a consumer running: refused, and nothing attempted ---
	err = p.Teardown(ctx(), "op-2", "testgame", rec.Generation)
	if err == nil {
		t.Fatal("teardown proceeded while a consumer still held the generation, which " +
			"would have reported success and freed nothing")
	}
	if !IsRefusal(err) {
		t.Errorf("a live consumer was reported as a fault rather than a refusal: %v", err)
	}
	var held *consumer.HeldError
	if !errors.As(err, &held) {
		t.Fatalf("want a HeldError, got %T: %v", err, err)
	}
	// The consumer's own bind has to be among them. The count is not pinned:
	// `unshare` copies the whole mount table into the new namespace, so this
	// stand-in holds more than a Docker container would — every one of those
	// copies is a real hold (D-019), so reporting them is right, but only
	// this one models what a game container does.
	var named bool
	for _, h := range held.Holders {
		if strings.HasPrefix(h.MountPoint, "/tmp/held.") {
			named = true
			t.Logf("refused, naming: %s", h)
		}
	}
	if !named {
		t.Errorf("the refusal does not name the consumer's own bind: %v", held.Holders)
	}

	// The generation is untouched: still mounted, still charged, still
	// recorded, still held by its unit.
	if len(liveMounts(t, p.cfg.RunDir)) == 0 {
		t.Fatal("a refused teardown unmounted the generation anyway")
	}
	if still, err := r.Shmem(cg); err != nil || still != charged {
		t.Errorf("shmem is %d (was %d, err %v) after a refused teardown", still, charged, err)
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); err != nil {
		t.Errorf("a refused teardown removed the record: %v", err)
	}
	if state := unitProperty(t, pak.HoldUnit, "ActiveState"); state != "active" {
		t.Errorf("%s is %s after a refused teardown", pak.HoldUnit, state)
	}

	// --- after a clean stop: it proceeds, and the memory comes back ---
	stop()

	if err := p.Teardown(ctx(), "op-3", "testgame", rec.Generation); err != nil {
		t.Fatalf("teardown was refused after the consumer stopped: %v", err)
	}
	if left := liveMounts(t, p.cfg.RunDir); len(left) != 0 {
		t.Errorf("teardown left %d mount(s) behind", len(left))
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); !os.IsNotExist(err) {
		t.Errorf("the record survived teardown: %v", err)
	}

	// "The memory came back" has to be stated in a way that survives the
	// cgroup being removed, which teardown does last. A cgroup removed while
	// pages are still charged below it becomes a DYING cgroup and the count
	// stays up; one whose pages were freed by the unmount drains. That is
	// the same number oracle 12 watches, and here it is the difference the
	// consumer made.
	deadline := time.Now().Add(30 * time.Second)
	for {
		dyingAfter, err := r.DyingDescendants("/")
		if err != nil {
			t.Fatal(err)
		}
		if dyingAfter <= dyingBefore {
			t.Logf("nr_dying_descendants returned to %d; %d bytes of shmem were returned",
				dyingAfter, charged)
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("nr_dying_descendants was %d before and %d after a teardown that "+
				"should have freed %d bytes — the pages are still charged below a cgroup "+
				"that no longer exists", dyingBefore, dyingAfter, charged)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// The check must not fire on srdm's own mounts, nor on any namespace that
// merely receives them by propagation. This is the failure that would not
// look like a bug: teardown simply refusing forever, on every generation,
// with a plausible-looking holder named.
func TestAFreshlyPublishedGenerationHasNoHolders(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	rep, err := p.Holders(ctx(), "testgame", rec.Generation)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Held() {
		t.Fatalf("srdm reported holders of a generation nothing has consumed: %+v", rep.Holders)
	}
}

// --- reconciliation against the real mount table and systemd --------------

func TestReconcileAgainstTheLiveSystem(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	name := "testgame/" + rec.Generation
	if len(res.Healthy) != 1 || res.Healthy[0] != name {
		t.Fatalf("Healthy = %v, want [%s]", res.Healthy, name)
	}
	if len(res.Orphans) != 0 {
		t.Errorf("a freshly published generation produced orphans: %v", res.Orphans)
	}

	// Drop one exposure behind srdm's back — the shape a crash between the
	// bind and the record leaves.
	if err := syscall.Unmount(rec.Classes[0].ExposePath, 0); err != nil {
		t.Fatal(err)
	}
	res, err = p.Reconcile(ctx(), "op-r2")
	if err != nil {
		t.Fatal(err)
	}
	if len(res.NeedsRepublish) != 1 || res.NeedsRepublish[0] != name {
		t.Fatalf("NeedsRepublish = %v after an exposure vanished", res.NeedsRepublish)
	}
}

// The state the mount table alone cannot see: every mount intact, and the
// unit that pays for the pages gone. The content is still readable and still
// resident, charged to a cgroup that has been removed — so the class floor
// protects nothing, and no amount of looking at mounts would show it.
func TestReconcileSeesAStoppedHoldUnitWithEveryMountIntact(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	pak := classByName(rec, "pak")
	if err := systemdx.New().Stop(context.Background(), pak.HoldUnit); err != nil {
		t.Fatalf("stopping %s: %v", pak.HoldUnit, err)
	}

	// The mounts are all still there — this is the point.
	live := liveMounts(t, p.cfg.RunDir)
	for _, c := range rec.Classes {
		if _, ok := mountinfo.At(live, c.OpMount); !ok {
			t.Fatalf("class %q: stopping the hold unit also dropped the op tmpfs, so this "+
				"test is not measuring what it claims", c.Name)
		}
	}

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Unheld) != 1 || res.Unheld[0] != pak.HoldUnit {
		t.Fatalf("Unheld = %v, want [%s]", res.Unheld, pak.HoldUnit)
	}
	if len(res.Healthy) != 0 {
		t.Errorf("a generation whose content is charged to a dead cgroup was called "+
			"healthy: %v", res.Healthy)
	}
	if len(res.NeedsRepublish) != 1 {
		t.Errorf("an unheld class did not schedule a republish: %v", res.NeedsRepublish)
	}
}

func errorIs(err error, target syscall.Errno) bool {
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return errno == target
	}
	return false
}
