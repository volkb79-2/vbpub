package publish

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// --- harness ---------------------------------------------------------------

// opLog is the interleaved record of everything a publication does to the
// system: mounts, unmounts, and the hold units and slices it starts and
// stops. One log rather than several, because the ORDER between those is the
// contract — a bind before its content is populated, or a unit stopped
// before its mount is dropped, are both silent correctness failures that
// per-surface logs would not show.
type opLog struct{ ops []string }

func (l *opLog) add(format string, args ...any) {
	l.ops = append(l.ops, fmt.Sprintf(format, args...))
}

// fakeMounter records mount operations instead of performing them. A "tmpfs"
// is then just the directory that was already created, which is exactly what
// makes the topology logic — sizing, ordering, exposure — testable without
// privilege. What the KERNEL does with those mounts is the e2e suite's
// business, not this one's.
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
	log       *opLog
	calls     []mountCall
	mountErr  map[string]error
	unmounted []string
}

func (m *fakeMounter) Mount(source, target, fstype string, flags uintptr, data string) error {
	m.log.add("%s", describeMount(source, target, fstype, flags, data))
	m.calls = append(m.calls, mountCall{
		Source: source, Target: target, FSType: fstype, Flags: flags, Data: data})
	if err := m.mountErr[target]; err != nil {
		return err
	}
	return nil
}

func (m *fakeMounter) Unmount(target string, _ int) error {
	m.log.add("umount %s", target)
	m.unmounted = append(m.unmounted, target)
	return nil
}

// fakeHolder stands in for systemd, and deliberately does NOT populate
// anything.
//
// Publication no longer writes class trees — the hold unit's worker does,
// inside its own cgroup — so a fake that quietly populated would let a
// publication which never started a worker still produce content and look
// correct. What this package owes is the right unit, in the right slice,
// with the right policy, at the right point in the sequence. The content is
// internal/hold's contract and internal/hold's tests.
type fakeHolder struct {
	log      *opLog
	specs    []hold.Spec
	slices   map[string]int64
	sliceErr error
	// startErr fails one named unit, so a mid-flight failure is reachable.
	startErr map[string]error
	// inactive names units IsActive reports as not running. Absent means
	// running, which is the healthy case.
	inactive  map[string]bool
	activeErr error
	forgotten []string
	released  []string
}

func newFakeHolder(log *opLog) *fakeHolder {
	return &fakeHolder{
		log: log, slices: map[string]int64{},
		startErr: map[string]error{}, inactive: map[string]bool{},
	}
}

func (h *fakeHolder) EnsureSlice(_ context.Context, slice string, memoryMin int64) error {
	h.log.add("slice %s memory_min=%d", slice, memoryMin)
	h.slices[slice] = memoryMin
	return h.sliceErr
}

func (h *fakeHolder) Start(_ context.Context, spec hold.Spec) error {
	h.log.add("hold %s slice=%s", spec.Unit, spec.Slice)
	h.specs = append(h.specs, spec)
	return h.startErr[spec.Unit]
}

func (h *fakeHolder) Forget(_ context.Context, unit string) error {
	h.log.add("forget %s", unit)
	h.forgotten = append(h.forgotten, unit)
	return nil
}

func (h *fakeHolder) ReleaseSlice(_ context.Context, slice string) error {
	h.log.add("release %s", slice)
	h.released = append(h.released, slice)
	return nil
}

func (h *fakeHolder) IsActive(_ context.Context, unit string) (bool, error) {
	if h.activeErr != nil {
		return false, h.activeErr
	}
	return !h.inactive[unit], nil
}

func (h *fakeHolder) specFor(t *testing.T, unit string) hold.Spec {
	t.Helper()
	for _, s := range h.specs {
		if s.Unit == unit {
			return s
		}
	}
	t.Fatalf("no hold unit %q was started (started: %v)", unit, h.startedUnits())
	return hold.Spec{}
}

func (h *fakeHolder) startedUnits() []string {
	var out []string
	for _, s := range h.specs {
		out = append(out, s.Unit)
	}
	return out
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
	zero := int64(0)
	yes := true
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			// pak bypasses zswap entirely and may write back: its content is
			// zstd-incompressible, so compressing it burns CPU to not shrink.
			{Name: "pak", Kind: profile.KindManaged,
				Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20,
				ZSwapMax: &zero, ZSwapWriteback: &yes, NoExec: true},
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
	// The release id is derived from the test's own name because the
	// generation id — and therefore every systemd unit name — is derived
	// from it. Unit names are process-global in a way temp directories are
	// not, so a fixed id would have every e2e case colliding on one unit.
	release, err := st.Promote(tx, p, store.PromoteOpts{
		ReleaseID:  "rel-" + strings.ReplaceAll(t.Name(), "/", "-"),
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return release
}

// fakes bundles the two injected surfaces and the log they share.
type fakes struct {
	log     *opLog
	mounter *fakeMounter
	holder  *fakeHolder
}

func newPublisher(t *testing.T, cfg config.Config, jnl *journal.Journal, opts ...Option) (*Publisher, *fakes) {
	t.Helper()
	log := &opLog{}
	f := &fakes{
		log:     log,
		mounter: &fakeMounter{log: log, mountErr: map[string]error{}},
		holder:  newFakeHolder(log),
	}
	p, err := New(cfg, jnl, append([]Option{WithMounter(f.mounter), WithHolder(f.holder)}, opts...)...)
	if err != nil {
		t.Fatal(err)
	}
	return p, f
}

func ctx() context.Context { return context.Background() }

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

func TestPublishPerformsTheExactSequence(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	gen := rec.Generation

	want := []string{
		// The aggregate's floor is in place before anything runs inside it:
		// 150M + 200M of class floors. A slice created implicitly by its
		// first service starts at memory.min=0, and a cgroup v2 floor is
		// capped by every ancestor's.
		"slice srdm-" + gen + ".slice memory_min=367001600",
		// code first (classes are published in a stable, sorted order)
		"mount -t tmpfs -o nosuid,nodev [size=67108864,mode=0755] tmpfs -> " + cfg.OpClassDir("op-1", "code"),
		// The hold unit populates, verifies and seals INSIDE its own cgroup,
		// between the tmpfs it fills and the bind that exposes it.
		"hold srdm-hold-" + gen + "-code.service slice=srdm-" + gen + ".slice",
		"mount -o bind " + cfg.OpClassRoot("op-1", "code") + " -> " + cfg.ExposeClassDir("testgame", gen, "code"),
		"mount -o bind,remount,ro -> " + cfg.ExposeClassDir("testgame", gen, "code"),
		// pak carries noexec: it is data, and nothing in it should execute
		"mount -t tmpfs -o nosuid,nodev,noexec [size=67108864,mode=0755] tmpfs -> " + cfg.OpClassDir("op-1", "pak"),
		"hold srdm-hold-" + gen + "-pak.service slice=srdm-" + gen + ".slice",
		"mount -o bind " + cfg.OpClassRoot("op-1", "pak") + " -> " + cfg.ExposeClassDir("testgame", gen, "pak"),
		"mount -o bind,remount,ro -> " + cfg.ExposeClassDir("testgame", gen, "pak"),
	}
	if !reflect.DeepEqual(f.log.ops, want) {
		t.Fatalf("publication sequence =\n  %s\nwant\n  %s",
			strings.Join(f.log.ops, "\n  "), strings.Join(want, "\n  "))
	}
}

// The exposure is bound only after the hold unit has reported ready, and
// "ready" means populated, verified and sealed (D-013). Binding earlier
// would publish a half-written tree.
func TestEachClassIsHeldBeforeItIsExposed(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range rec.Classes {
		hi := indexOf(f.log.ops, "hold "+c.HoldUnit+" ")
		bi := indexOf(f.log.ops, "mount -o bind "+p.cfg.OpClassRoot("op-1", c.Name)+" ")
		mi := indexOf(f.log.ops, " -> "+c.OpMount)
		if mi < 0 || hi < 0 || bi < 0 {
			t.Fatalf("class %q: missing a step in %v", c.Name, f.log.ops)
		}
		if !(mi < hi && hi < bi) {
			t.Errorf("class %q: the order is tmpfs=%d hold=%d bind=%d; it must be "+
				"mount, then hold, then bind — a bind before the worker has finished "+
				"exposes a half-populated tree", c.Name, mi, hi, bi)
		}
	}
}

// Read-only has to be a SECOND remount. A bind inherits its source's flags,
// so asking for MS_BIND|MS_RDONLY in one call silently leaves it writable.
func TestExposureIsMadeReadOnlyByASeparateRemount(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	if _, err := p.Publish(ctx(), "op-1", "testgame", rel, prof); err != nil {
		t.Fatal(err)
	}

	binds := 0
	for i, c := range f.mounter.calls {
		if !c.Bind() || c.Remount() {
			continue
		}
		binds++
		if c.ReadOnly() {
			t.Errorf("the initial bind of %s asked for read-only in one call; a bind "+
				"inherits its source's flags and silently stays writable", c.Target)
		}
		if i+1 >= len(f.mounter.calls) {
			t.Fatalf("the bind of %s is the last operation; nothing made it read-only", c.Target)
		}
		next := f.mounter.calls[i+1]
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
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range rec.Classes {
		if c.Name == "state" {
			t.Fatal("an excluded class was published")
		}
	}
	for _, op := range f.log.ops {
		if strings.Contains(op, "state") {
			t.Errorf("an excluded class appears in the publication sequence: %s", op)
		}
	}
	// Nor is a worker ever pointed at it — which is where the content would
	// actually come from.
	for _, s := range f.holder.specs {
		if s.Worker.Class == "state" {
			t.Errorf("a hold unit was told to populate the excluded class: %+v", s.Worker)
		}
	}
}

// --- the hold units carry the class policy --------------------------------

func TestEachClassGetsItsOwnHoldUnitCarryingItsPolicy(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	if len(f.holder.specs) != 2 {
		t.Fatalf("started %v, want one hold unit per managed class", f.holder.startedUnits())
	}

	pak := f.holder.specFor(t, "srdm-hold-"+rec.Generation+"-pak.service")
	if pak.Policy.MemoryMin != 150<<20 {
		t.Errorf("pak MemoryMin = %d, want the class floor 150M", pak.Policy.MemoryMin)
	}
	if pak.Policy.ZSwapMax == nil || *pak.Policy.ZSwapMax != 0 {
		t.Errorf("pak ZSwapMax = %v, want 0 — pak content is incompressible, so zswap "+
			"would burn CPU without shrinking anything", pak.Policy.ZSwapMax)
	}
	if pak.Policy.ZSwapWriteback == nil || !*pak.Policy.ZSwapWriteback {
		t.Errorf("pak ZSwapWriteback = %v, want yes", pak.Policy.ZSwapWriteback)
	}

	code := f.holder.specFor(t, "srdm-hold-"+rec.Generation+"-code.service")
	if code.Policy.MemoryMin != 200<<20 {
		t.Errorf("code MemoryMin = %d, want 200M", code.Policy.MemoryMin)
	}
	// The code class declares no zswap policy, and unset must stay unset:
	// writing a zero would be indistinguishable from a class that asked for
	// zswap to be bypassed.
	if code.Policy.ZSwapMax != nil {
		t.Errorf("code ZSwapMax = %v, want unset", *code.Policy.ZSwapMax)
	}
	if code.Policy.ZSwapWriteback != nil {
		t.Errorf("code ZSwapWriteback = %v, want unset", *code.Policy.ZSwapWriteback)
	}

	// The unit name carries only the short generation, so the Description is
	// the only place the full release identity is visible in systemd.
	for _, s := range f.holder.specs {
		if !strings.Contains(s.Description, rel.ID) {
			t.Errorf("unit %s has Description %q, which does not name release %q",
				s.Unit, s.Description, rel.ID)
		}
	}
}

// The worker is pointed at the release it is publishing and at the content
// root inside its OWN class's tmpfs. Pointing it anywhere else would either
// populate the wrong class or fault the pages outside the mount that is
// meant to hold them.
func TestTheWorkerIsPointedAtItsOwnClassAndTmpfs(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range rec.Classes {
		s := f.holder.specFor(t, c.HoldUnit)
		if s.Worker.Class != c.Name {
			t.Errorf("unit %s populates class %q, want %q", c.Name, s.Worker.Class, c.Name)
		}
		if want := cfg.OpClassRoot("op-1", c.Name); s.Worker.Target != want {
			t.Errorf("class %q: worker target = %q, want %q (inside the class tmpfs)",
				c.Name, s.Worker.Target, want)
		}
		if s.Worker.ReleaseDir != rel.Dir || s.Worker.ReleaseID != rel.ID {
			t.Errorf("class %q: worker points at release %q in %q, want %q in %q",
				c.Name, s.Worker.ReleaseID, s.Worker.ReleaseDir, rel.ID, rel.Dir)
		}
		if s.Slice != rec.Slice {
			t.Errorf("class %q: unit is in slice %q, want the generation aggregate %q",
				c.Name, s.Slice, rec.Slice)
		}
	}
}

// The aggregate must protect at least the SUM of the floors beneath it.
// cgroup v2 prorates a parent's protection among its children, so an
// aggregate protecting less silently shrinks every class floor inside it.
func TestTheGenerationSliceIsGivenTheSumOfTheClassFloors(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	got, ok := f.holder.slices[rec.Slice]
	if !ok {
		t.Fatalf("no floor was set on %s (slices: %v)", rec.Slice, f.holder.slices)
	}
	if want := int64(350 << 20); got != want {
		t.Errorf("%s MemoryMin = %d, want %d — the sum of the pak and code floors",
			rec.Slice, got, want)
	}
	// And the aggregate nests DIRECTLY under the configured parent: an
	// intermediate slice nobody owns would carry memory.min=0 and kill every
	// floor beneath it.
	if want := "srdm-" + rec.Generation + ".slice"; rec.Slice != want {
		t.Errorf("generation slice = %q, want %q", rec.Slice, want)
	}
}

// --- failure leaves nothing behind ----------------------------------------

func TestPublishTearsDownEverythingWhenAClassFails(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	// Fail the second class's tmpfs mount, after the first has fully
	// published — the case where a partial generation could survive.
	f.mounter.mountErr[cfg.OpClassDir("op-1", "pak")] = syscall.EPERM

	if _, err := p.Publish(ctx(), "op-1", "testgame", rel, prof); err == nil {
		t.Fatal("Publish reported success despite a failed mount")
	}

	gen := GenerationID(rel.ID)
	for _, want := range []string{
		cfg.ExposeClassDir("testgame", gen, "code"),
		cfg.OpClassDir("op-1", "code"),
	} {
		if !containsString(f.mounter.unmounted, want) {
			t.Errorf("%s was left mounted after a failed publication (unmounted: %v)",
				want, f.mounter.unmounted)
		}
	}
	assertExposureUnmountedFirst(t, f.mounter.unmounted,
		cfg.ExposeClassDir("testgame", gen, "code"), cfg.OpClassDir("op-1", "code"))

	// The hold unit of the class that DID publish is stopped, and so is the
	// unit of the class that never got as far as starting one — a transient
	// unit left in a failed state makes the next publication of the same
	// generation fail by name, which reads as a flaky daemon.
	for _, unit := range []string{
		"srdm-hold-" + gen + "-code.service",
		"srdm-hold-" + gen + "-pak.service",
	} {
		if !containsString(f.holder.forgotten, unit) {
			t.Errorf("%s was not stopped after a failed publication (forgotten: %v)",
				unit, f.holder.forgotten)
		}
	}
	if !containsString(f.holder.released, "srdm-"+gen+".slice") {
		t.Errorf("the generation slice survived a failed publication: %v", f.holder.released)
	}

	// And no record claims a generation that does not exist.
	if _, err := p.LoadRecord("testgame", gen); !os.IsNotExist(err) {
		t.Errorf("a published record survived a failed publication: %v", err)
	}
}

// A worker that cannot fit its class into its tmpfs is a REFUSAL: the sizing
// was wrong or the content grew, and the generation is quarantined rather
// than published half-written. That is the 2026-07-29 corruption shape, and
// the exit status is how the worker says so across the process boundary.
func TestAWorkerOutOfSpaceIsARefusal(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	gen := GenerationID(rel.ID)
	unit := "srdm-hold-" + gen + "-code.service"
	f.holder.startErr[unit] = &hold.WorkerError{
		Unit: unit, Status: hold.ExitNoSpace, Err: errors.New("unit failed")}

	_, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err == nil {
		t.Fatal("a class that ran out of space was published")
	}
	if !IsRefusal(err) {
		t.Errorf("running out of space was reported as a fault rather than a refusal: %v", err)
	}
	var enospc *ENOSPCError
	if !errors.As(err, &enospc) {
		t.Fatalf("want an ENOSPCError, got %T: %v", err, err)
	}
	if enospc.Class != "code" {
		t.Errorf("the error names class %q, want code", enospc.Class)
	}
	if !strings.Contains(err.Error(), "quarantined") {
		t.Errorf("the error does not say what happened to the generation: %v", err)
	}
	if _, err := p.LoadRecord("testgame", gen); !os.IsNotExist(err) {
		t.Errorf("a refused publication left a record: %v", err)
	}
}

// Every other worker failure is a fault, not a refusal. Treating them alike
// would quarantine a generation for a bug in srdm, and — worse — would let a
// genuine sizing failure hide among them.
func TestAWorkerFailingForAnyOtherReasonIsAFault(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	unit := "srdm-hold-" + GenerationID(rel.ID) + "-code.service"
	f.holder.startErr[unit] = &hold.WorkerError{
		Unit: unit, Status: hold.ExitVerify, Err: errors.New("unit failed")}

	_, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err == nil {
		t.Fatal("a class whose content did not verify was published")
	}
	if IsRefusal(err) {
		t.Errorf("content that does not match the manifest was reported as a refusal: %v", err)
	}
}

// The record is written last, for the same reason COMPLETE is: it is the
// only thing that says the generation is whole.
func TestRecordIsWrittenLast(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	f.mounter.mountErr[cfg.ExposeClassDir("testgame", GenerationID(rel.ID), "code")] = syscall.EPERM
	if _, err := p.Publish(ctx(), "op-1", "testgame", rel, prof); err == nil {
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
	if _, err := p.Publish(ctx(), "op-1", "testgame", rel, only); err == nil {
		t.Fatal("a profile with nothing to publish was accepted")
	}
}

func TestPublishRefusesUnsafeNames(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	if _, err := p.Publish(ctx(), "../escape", "testgame", rel, prof); err == nil {
		t.Error("a traversing operation id was accepted")
	}
	if _, err := p.Publish(ctx(), "op-1", "../escape", rel, prof); err == nil {
		t.Error("a traversing profile id was accepted")
	}
}

// A class name that cannot be a systemd unit name is refused BEFORE anything
// is mounted. Everything here ends up in an argv and in a cgroup path.
func TestPublishRefusesAClassNameThatCannotBeAUnit(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	bad := &profile.Profile{
		SchemaVersion: profile.SchemaVersion, ID: "testgame",
		Classes: []profile.Class{
			{Name: "pak/../..", Kind: profile.KindManaged, Paths: []string{"WS/Content/Paks"}},
		},
	}
	if err := bad.Validate(); err != nil {
		t.Fatal(err)
	}
	if _, err := p.Publish(ctx(), "op-1", "testgame", rel, bad); err == nil {
		t.Fatal("a class whose name cannot be a unit name was accepted")
	}
	if len(f.log.ops) != 0 {
		t.Errorf("the refusal came after work had started: %v", f.log.ops)
	}
}

// --- teardown --------------------------------------------------------------

// The order is the whole content of teardown: exposure, then op tmpfs, then
// the hold unit, then the slice. Anything else either frees nothing or
// removes the cgroup the pages are still charged to.
func TestTeardownReleasesInTheOrderThatUnchages(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	f.log.ops = nil
	f.mounter.unmounted = nil

	if err := p.Teardown(ctx(), "op-2", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}

	gen := rec.Generation
	want := []string{
		"umount " + cfg.ExposeClassDir("testgame", gen, "code"),
		"umount " + cfg.ExposeClassDir("testgame", gen, "pak"),
		"umount " + cfg.OpClassDir("op-1", "code"),
		"umount " + cfg.OpClassDir("op-1", "pak"),
		"forget srdm-hold-" + gen + "-code.service",
		"forget srdm-hold-" + gen + "-pak.service",
		"release srdm-" + gen + ".slice",
	}
	if !reflect.DeepEqual(f.log.ops, want) {
		t.Fatalf("teardown sequence =\n  %s\nwant\n  %s",
			strings.Join(f.log.ops, "\n  "), strings.Join(want, "\n  "))
	}
	if _, err := p.LoadRecord("testgame", rec.Generation); !os.IsNotExist(err) {
		t.Errorf("the record survived teardown: %v", err)
	}
}

func TestTeardownOfAnUnknownGenerationFails(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	p, _ := newPublisher(t, cfg, jnl)
	if err := p.Teardown(ctx(), "op-1", "testgame", "deadbeef"); err == nil {
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

func indexOf(ops []string, substr string) int {
	for i, op := range ops {
		if strings.Contains(op, substr) {
			return i
		}
	}
	return -1
}
