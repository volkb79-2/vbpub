//go:build e2e

// The privileged half of publication: what the KERNEL does with the mounts
// the unit tests only assert the shape of.
//
// A read-only bind either produces EROFS or it does not, and no fake can
// tell you which. Neither can a fake tell you whether unmounting the op
// tmpfs actually returns the pages.
package publish

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/store"
)

func requireRoot(t *testing.T) {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
}

// realPublisher mounts for real, and guarantees teardown even on failure —
// this suite runs as root in a shared container, so a leaked mount is
// somebody else's confusing failure.
func realPublisher(t *testing.T, opts ...Option) (*Publisher, *store.Release, *profile.Profile) {
	t.Helper()
	requireRoot(t)

	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)

	p, err := New(cfg, jnl, opts...)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		// Whatever survived, unmount deepest-first.
		entries, err := mountinfo.Read("")
		if err != nil {
			return
		}
		var points []string
		for _, e := range mountinfo.Under(entries, cfg.RunDir) {
			points = append(points, e.MountPoint)
		}
		sortedDesc(points)
		for _, m := range points {
			_ = syscall.Unmount(m, syscall.MNT_DETACH)
		}
	})
	return p, rel, prof
}

func liveMounts(t *testing.T, root string) []mountinfo.Entry {
	t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		t.Fatal(err)
	}
	return mountinfo.Under(entries, root)
}

// --- the exposure is genuinely read-only ----------------------------------

func TestPublishedExposureRefusesWritesWithEROFS(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
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

	rec, err := p.Publish("op-1", "testgame", rel, prof)
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

	rec, err := p.Publish("op-1", "testgame", rel, prof)
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

// --- teardown --------------------------------------------------------------

func TestTeardownLeavesNoMountsBehind(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	if len(liveMounts(t, p.cfg.RunDir)) == 0 {
		t.Fatal("publication mounted nothing")
	}

	if err := p.Teardown("op-2", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	if left := liveMounts(t, p.cfg.RunDir); len(left) != 0 {
		var points []string
		for _, e := range left {
			points = append(points, e.MountPoint)
		}
		t.Fatalf("teardown left %v mounted", points)
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); !os.IsNotExist(err) {
		t.Errorf("the record survived teardown: %v", err)
	}
}

// --- ENOSPC quarantines rather than half-publishing -----------------------
//
// The 2026-07-29 corruption was a write that ran out of tmpfs space
// half-way. A class that cannot hold its content must refuse, not produce a
// partial generation — and the only way to test that is to make a tmpfs
// genuinely too small.
func TestClassTooSmallIsRefusedAndLeavesNothingMounted(t *testing.T) {
	requireRoot(t)
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)

	// A pak large enough that no rounding saves it. The default content is
	// a few bytes per file and would fit in a single page, so this test
	// would pass without ever reaching the branch it exists for.
	big := map[string]string{
		"Engine/libengine.so":      "engine bytes",
		"WS/Binaries/server":       "server bytes",
		"WS/Content/Paks/game.pak": strings.Repeat("p", 4<<20),
		"WS/Saved/world.db":        "never published",
	}
	rel := stagedReleaseWith(t, cfg, jnl, prof, big)

	p, err := New(cfg, jnl, WithClassSizer(func(class string, _ int64) int64 {
		if class == "pak" {
			return 64 << 10 // 64 KiB against 4 MiB of content
		}
		return ClassSize(1)
	}))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		entries, err := mountinfo.Read("")
		if err != nil {
			return
		}
		var points []string
		for _, e := range mountinfo.Under(entries, cfg.RunDir) {
			points = append(points, e.MountPoint)
		}
		sortedDesc(points)
		for _, m := range points {
			_ = syscall.Unmount(m, syscall.MNT_DETACH)
		}
	})

	_, err = p.Publish("op-1", "testgame", rel, prof)
	if err == nil {
		t.Fatal("a class that cannot hold its content was published")
	}
	if !IsRefusal(err) {
		t.Errorf("running out of space was reported as a fault rather than a refusal: %v", err)
	}
	var enospc *ENOSPCError
	if !errorAs(err, &enospc) {
		t.Fatalf("want an ENOSPCError, got %T: %v", err, err)
	}
	if enospc.Class != "pak" {
		t.Errorf("the error names class %q, want pak", enospc.Class)
	}
	if !strings.Contains(err.Error(), "quarantined") {
		t.Errorf("the error does not say what happened to the generation: %v", err)
	}

	// And nothing is left mounted or recorded.
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
}

// --- reconciliation against the real mount table --------------------------

func TestReconcileAgainstTheLiveMountTable(t *testing.T) {
	p, rel, prof := realPublisher(t)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}

	res, err := p.Reconcile("op-r")
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
	res, err = p.Reconcile("op-r2")
	if err != nil {
		t.Fatal(err)
	}
	if len(res.NeedsRepublish) != 1 || res.NeedsRepublish[0] != name {
		t.Fatalf("NeedsRepublish = %v after an exposure vanished", res.NeedsRepublish)
	}
}

func errorIs(err error, target syscall.Errno) bool {
	var errno syscall.Errno
	if errorAs(err, &errno) {
		return errno == target
	}
	return false
}

func errorAs(err error, target any) bool { return errors.As(err, target) }
