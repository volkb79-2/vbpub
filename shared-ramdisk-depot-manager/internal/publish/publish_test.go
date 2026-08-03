package publish

import (
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// --- harness ---------------------------------------------------------------

// fakeMounter records mount operations instead of performing them. A "tmpfs"
// is then just the directory that was already created, which is exactly what
// makes the topology logic — sizing, population, verification, sealing,
// ordering — testable without privilege. What the KERNEL does with those
// mounts is the e2e suite's business, not this one's.
type mountCall struct {
	Source, Target, FSType, Data string
	Flags                        uintptr
}

// ReadOnly inspects the FLAG, not the rendered string. An earlier version of
// this test matched the substring "ro" and duly found it inside "root".
func (c mountCall) ReadOnly() bool { return c.Flags&syscall.MS_RDONLY != 0 }
func (c mountCall) Bind() bool     { return c.Flags&syscall.MS_BIND != 0 }
func (c mountCall) Remount() bool  { return c.Flags&syscall.MS_REMOUNT != 0 }

type fakeMounter struct {
	ops       []string
	calls     []mountCall
	mountErr  map[string]error
	unmounted []string
}

func (m *fakeMounter) Mount(source, target, fstype string, flags uintptr, data string) error {
	m.ops = append(m.ops, describeMount(source, target, fstype, flags, data))
	m.calls = append(m.calls, mountCall{
		Source: source, Target: target, FSType: fstype, Flags: flags, Data: data})
	if err := m.mountErr[target]; err != nil {
		return err
	}
	return nil
}

func (m *fakeMounter) Unmount(target string, _ int) error {
	m.ops = append(m.ops, "umount "+target)
	m.unmounted = append(m.unmounted, target)
	return nil
}

func describeMount(source, target, fstype string, flags uintptr, data string) string {
	var f []string
	for _, spec := range []struct {
		bit  uintptr
		name string
	}{
		{syscall.MS_BIND, "bind"},
		{syscall.MS_REMOUNT, "remount"},
		{syscall.MS_RDONLY, "ro"},
		{syscall.MS_NOSUID, "nosuid"},
		{syscall.MS_NODEV, "nodev"},
		{syscall.MS_NOEXEC, "noexec"},
	} {
		if flags&spec.bit != 0 {
			f = append(f, spec.name)
		}
	}
	out := "mount"
	if fstype != "" {
		out += " -t " + fstype
	}
	if len(f) > 0 {
		out += " -o " + strings.Join(f, ",")
	}
	if data != "" {
		out += " [" + data + "]"
	}
	if source != "" {
		out += " " + source
	}
	return out + " -> " + target
}

func testConfig(t *testing.T) config.Config {
	t.Helper()
	dir := t.TempDir()
	// Publication seals its trees read-only, which is the point — and which
	// leaves TempDir unable to remove them. Cleanups run last-registered
	// first, so this one restores write access before TempDir's own runs.
	t.Cleanup(func() { restoreWritable(dir) })

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	cfg.Owner = config.Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
	return cfg
}

func restoreWritable(root string) {
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // best effort: this is cleanup, not an assertion
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		_ = os.Chmod(path, info.Mode().Perm()|0o200)
		return nil
	})
}

func testJournal(t *testing.T, cfg config.Config) *journal.Journal {
	t.Helper()
	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	j, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = j.Close() })
	return j
}

func testProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged,
				Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20, NoExec: true},
			{Name: "code", Kind: profile.KindManaged,
				Paths: []string{"Engine", "WS/Binaries"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded,
				Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

var content = map[string]string{
	"Engine/libengine.so":      "engine bytes",
	"WS/Binaries/server":       "server bytes",
	"WS/Content/Paks/game.pak": "pak bytes",
	"WS/Content/Paks/game.sig": "sig",
	"WS/Saved/world.db":        "never published",
}

// stagedRelease promotes a real release, so publication is tested against
// the actual store format rather than a hand-built stand-in.
func stagedRelease(t *testing.T, cfg config.Config, jnl *journal.Journal, p *profile.Profile) *store.Release {
	t.Helper()
	return stagedReleaseWith(t, cfg, jnl, p, content)
}

func stagedReleaseWith(t *testing.T, cfg config.Config, jnl *journal.Journal,
	p *profile.Profile, files map[string]string) *store.Release {
	t.Helper()
	st, err := store.Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := st.Begin("op-stage")
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range files {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	release, err := st.Promote(tx, p, store.PromoteOpts{
		ReleaseID:  "rel-1",
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return release
}

func newPublisher(t *testing.T, cfg config.Config, jnl *journal.Journal, opts ...Option) (*Publisher, *fakeMounter) {
	t.Helper()
	m := &fakeMounter{mountErr: map[string]error{}}
	p, err := New(cfg, jnl, append([]Option{WithMounter(m)}, opts...)...)
	if err != nil {
		t.Fatal(err)
	}
	return p, m
}

// --- sizing ----------------------------------------------------------------

func TestClassSize(t *testing.T) {
	const g = int64(SizeGranularity)
	cases := map[int64]int64{
		0:          g, // an empty class still needs a usable tmpfs
		1:          g,
		g:          2 * g,      // 64M + 15% rounds up past one granule
		100 << 20:  2 * g,      // 115M -> 128M
		1000 << 20: 1152 << 20, // 1000M + 15% = 1150M -> 1152M
		2400 << 20: 2816 << 20, // the Soulmask payload: 2760M -> 2816M
		-1:         g,          // nonsense in, usable out
	}
	for in, want := range cases {
		if got := ClassSize(in); got != want {
			t.Errorf("ClassSize(%d) = %d, want %d", in, got, want)
		}
	}
	// Never below the content, or the cap guarantees ENOSPC.
	for _, n := range []int64{1, 1 << 20, 100 << 20, 3 << 30} {
		if ClassSize(n) < n {
			t.Errorf("ClassSize(%d) = %d, which is smaller than the content", n, ClassSize(n))
		}
	}
	// Always a whole number of granules.
	for _, n := range []int64{0, 1, 100 << 20, 3 << 30} {
		if ClassSize(n)%SizeGranularity != 0 {
			t.Errorf("ClassSize(%d) = %d, not a multiple of the granularity", n, ClassSize(n))
		}
	}
}

func TestGenerationIDIsDeterministicAndShort(t *testing.T) {
	a, b := GenerationID("rel-1"), GenerationID("rel-1")
	if a != b {
		t.Fatalf("GenerationID is not deterministic: %q vs %q", a, b)
	}
	if len(a) != 8 {
		t.Fatalf("GenerationID = %q, want 8 hex chars (it goes into unit names)", a)
	}
	if GenerationID("rel-2") == a {
		t.Fatal("two release ids produced the same generation id")
	}
}

// --- the publication sequence ---------------------------------------------

func TestPublishPerformsTheExactMountSequence(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	gen := rec.Generation

	want := []string{
		// code first (classes are published in a stable, sorted order)
		"mount -t tmpfs -o nosuid,nodev [size=67108864,mode=0755] tmpfs -> " + cfg.OpClassDir("op-1", "code"),
		"mount -o bind " + cfg.OpClassRoot("op-1", "code") + " -> " + cfg.ExposeClassDir("testgame", gen, "code"),
		"mount -o bind,remount,ro -> " + cfg.ExposeClassDir("testgame", gen, "code"),
		// pak carries noexec: it is data, and nothing in it should execute
		"mount -t tmpfs -o nosuid,nodev,noexec [size=67108864,mode=0755] tmpfs -> " + cfg.OpClassDir("op-1", "pak"),
		"mount -o bind " + cfg.OpClassRoot("op-1", "pak") + " -> " + cfg.ExposeClassDir("testgame", gen, "pak"),
		"mount -o bind,remount,ro -> " + cfg.ExposeClassDir("testgame", gen, "pak"),
	}
	if !reflect.DeepEqual(m.ops, want) {
		t.Fatalf("mount sequence =\n  %s\nwant\n  %s",
			strings.Join(m.ops, "\n  "), strings.Join(want, "\n  "))
	}
}

// Read-only has to be a SECOND remount. A bind inherits its source's flags,
// so asking for MS_BIND|MS_RDONLY in one call silently leaves it writable.
func TestExposureIsMadeReadOnlyByASeparateRemount(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	if _, err := p.Publish("op-1", "testgame", rel, prof); err != nil {
		t.Fatal(err)
	}

	binds := 0
	for i, c := range m.calls {
		if !c.Bind() || c.Remount() {
			continue
		}
		binds++
		if c.ReadOnly() {
			t.Errorf("the initial bind of %s asked for read-only in one call; a bind "+
				"inherits its source's flags and silently stays writable", c.Target)
		}
		if i+1 >= len(m.calls) {
			t.Fatalf("the bind of %s is the last operation; nothing made it read-only", c.Target)
		}
		next := m.calls[i+1]
		if next.Target != c.Target || !next.Bind() || !next.Remount() || !next.ReadOnly() {
			t.Errorf("the bind of %s is not immediately followed by a read-only bind remount; "+
				"got %+v", c.Target, next)
		}
	}
	if binds != 2 {
		t.Fatalf("saw %d binds, want one per managed class", binds)
	}
}

// The excluded class is per-instance state. It must never be published, and
// the absolute state rule is that WS/Saved is never shared at all.
func TestExcludedClassesAreNeverPublished(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range rec.Classes {
		if c.Name == "state" {
			t.Fatal("an excluded class was published")
		}
	}
	for _, op := range m.ops {
		if strings.Contains(op, "state") {
			t.Errorf("an excluded class appears in the mount sequence: %s", op)
		}
	}
	for _, class := range []string{"pak", "code"} {
		root := cfg.OpClassRoot("op-1", class)
		if _, err := os.Stat(filepath.Join(root, "WS", "Saved")); !os.IsNotExist(err) {
			t.Errorf("class %q was populated with per-instance state: %v", class, err)
		}
	}
}

func TestPublishPopulatesEachClassWithOnlyItsOwnContent(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	if _, err := p.Publish("op-1", "testgame", rel, prof); err != nil {
		t.Fatal(err)
	}

	pak := cfg.OpClassRoot("op-1", "pak")
	body, err := os.ReadFile(filepath.Join(pak, "WS", "Content", "Paks", "game.pak"))
	if err != nil {
		t.Fatalf("pak content is missing: %v", err)
	}
	if string(body) != "pak bytes" {
		t.Errorf("pak content is %q", body)
	}
	// The structural ancestors exist, or the class path could not.
	for _, dir := range []string{"WS", "WS/Content", "WS/Content/Paks"} {
		if st, err := os.Stat(filepath.Join(pak, filepath.FromSlash(dir))); err != nil || !st.IsDir() {
			t.Errorf("structural directory %q is missing from the pak class: %v", dir, err)
		}
	}
	// And nothing from another class.
	if _, err := os.Stat(filepath.Join(pak, "Engine")); !os.IsNotExist(err) {
		t.Errorf("the pak class was populated with code content: %v", err)
	}

	code := cfg.OpClassRoot("op-1", "code")
	if _, err := os.Stat(filepath.Join(code, "Engine", "libengine.so")); err != nil {
		t.Errorf("code content is missing: %v", err)
	}
	if _, err := os.Stat(filepath.Join(code, "WS", "Content")); !os.IsNotExist(err) {
		t.Errorf("the code class was populated with pak content: %v", err)
	}
}

// Sealing is what makes "nothing visible was ever writable" true even for
// the source of the bind.
func TestPublishSealsTheTreeReadOnly(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	if _, err := p.Publish("op-1", "testgame", rel, prof); err != nil {
		t.Fatal(err)
	}

	err := filepath.WalkDir(cfg.OpClassRoot("op-1", "pak"), func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Mode()&fs.ModeSymlink != 0 {
			return nil
		}
		if info.Mode().Perm()&0o222 != 0 {
			t.Errorf("%s is still writable (%v) after sealing", path, info.Mode().Perm())
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

// --- failure leaves nothing behind ----------------------------------------

func TestPublishTearsDownEverythingWhenAClassFails(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	// Fail the second class's tmpfs mount, after the first has fully
	// published — the case where a partial generation could survive.
	m.mountErr[cfg.OpClassDir("op-1", "pak")] = syscall.EPERM

	if _, err := p.Publish("op-1", "testgame", rel, prof); err == nil {
		t.Fatal("Publish reported success despite a failed mount")
	}

	// Everything the first class mounted is unmounted again.
	gen := GenerationID(rel.ID)
	for _, want := range []string{
		cfg.ExposeClassDir("testgame", gen, "code"),
		cfg.OpClassDir("op-1", "code"),
	} {
		if !containsString(m.unmounted, want) {
			t.Errorf("%s was left mounted after a failed publication (unmounted: %v)", want, m.unmounted)
		}
	}
	// The exposure is dropped before the op tmpfs, always.
	assertExposureUnmountedFirst(t, m.unmounted,
		cfg.ExposeClassDir("testgame", gen, "code"), cfg.OpClassDir("op-1", "code"))

	// And no record claims a generation that does not exist.
	if _, err := p.LoadRecord("testgame", gen); !os.IsNotExist(err) {
		t.Errorf("a published record survived a failed publication: %v", err)
	}
}

// The record is written last, for the same reason COMPLETE is: it is the
// only thing that says the generation is whole.
func TestRecordIsWrittenLast(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	m.mountErr[cfg.ExposeClassDir("testgame", GenerationID(rel.ID), "code")] = syscall.EPERM
	if _, err := p.Publish("op-1", "testgame", rel, prof); err == nil {
		t.Fatal("Publish succeeded despite a failed bind")
	}
	entries, err := os.ReadDir(cfg.PublishedDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		sub, err := os.ReadDir(filepath.Join(cfg.PublishedDir(), e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if len(sub) != 0 {
			t.Fatalf("a record was written before the generation was whole: %v", sub)
		}
	}
}

func TestPublishRefusesAProfileWithNoManagedClasses(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	only := &profile.Profile{
		SchemaVersion: profile.SchemaVersion, ID: "testgame",
		Classes: []profile.Class{{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS"}}},
	}
	if err := only.Validate(); err != nil {
		t.Fatal(err)
	}
	if _, err := p.Publish("op-1", "testgame", rel, only); err == nil {
		t.Fatal("a profile with nothing to publish was accepted")
	}
}

func TestPublishRefusesUnsafeNames(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	if _, err := p.Publish("../escape", "testgame", rel, prof); err == nil {
		t.Error("a traversing operation id was accepted")
	}
	if _, err := p.Publish("op-1", "../escape", rel, prof); err == nil {
		t.Error("a traversing profile id was accepted")
	}
}

// --- teardown --------------------------------------------------------------

func TestTeardownDropsExposureBeforeTheOpTmpfs(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, m := newPublisher(t, cfg, jnl)

	rec, err := p.Publish("op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	m.unmounted = nil

	if err := p.Teardown("op-2", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}

	for _, c := range rec.Classes {
		// Unmounting the op tmpfs while a bind survives frees nothing at
		// all, and leaves the pages charged to a cgroup about to be removed.
		assertExposureUnmountedFirst(t, m.unmounted, c.ExposePath, c.OpMount)
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); !os.IsNotExist(err) {
		t.Errorf("the record survived teardown: %v", err)
	}
}

func TestTeardownOfAnUnknownGenerationFails(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	p, _ := newPublisher(t, cfg, jnl)
	if err := p.Teardown("op-1", "testgame", "deadbeef"); err == nil {
		t.Fatal("tearing down a generation with no record succeeded")
	}
}

func assertExposureUnmountedFirst(t *testing.T, order []string, exposePath, opMount string) {
	t.Helper()
	ei, oi := -1, -1
	for i, m := range order {
		if m == exposePath && ei < 0 {
			ei = i
		}
		if m == opMount && oi < 0 {
			oi = i
		}
	}
	if ei < 0 || oi < 0 {
		t.Fatalf("expected both %s and %s in the unmount order, got %v", exposePath, opMount, order)
	}
	if ei > oi {
		t.Fatalf("the op tmpfs %s was unmounted before the exposure %s; a bind that "+
			"survives its tmpfs frees nothing", opMount, exposePath)
	}
}
