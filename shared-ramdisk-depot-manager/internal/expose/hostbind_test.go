package expose

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/wings"
)

// --- harness ---------------------------------------------------------------

type mountCall struct {
	Source, Target string
	Flags          uintptr
}

func (c mountCall) ReadOnly() bool { return c.Flags&syscall.MS_RDONLY != 0 }
func (c mountCall) Bind() bool     { return c.Flags&syscall.MS_BIND != 0 }
func (c mountCall) Remount() bool  { return c.Flags&syscall.MS_REMOUNT != 0 }

type fakeMounter struct {
	ops       []string
	calls     []mountCall
	mountErr  map[string]error
	unmounted []string
}

func (m *fakeMounter) Mount(source, target, _ string, flags uintptr, _ string) error {
	var f []string
	for _, spec := range []struct {
		bit  uintptr
		name string
	}{
		{syscall.MS_BIND, "bind"},
		{syscall.MS_REMOUNT, "remount"},
		{syscall.MS_RDONLY, "ro"},
	} {
		if flags&spec.bit != 0 {
			f = append(f, spec.name)
		}
	}
	op := "mount -o " + strings.Join(f, ",")
	if source != "" {
		op += " " + source
	}
	m.ops = append(m.ops, op+" -> "+target)
	m.calls = append(m.calls, mountCall{Source: source, Target: target, Flags: flags})
	return m.mountErr[target]
}

func (m *fakeMounter) Unmount(target string, _ int) error {
	m.ops = append(m.ops, "umount "+target)
	m.unmounted = append(m.unmounted, target)
	return nil
}

type fakeGuard struct {
	report consumer.Report
	err    error
}

func (g *fakeGuard) Resolve(context.Context, []string) (*consumer.Report, error) {
	if g.err != nil {
		return nil, g.err
	}
	rep := g.report
	return &rep, nil
}

type fakeMarker struct {
	marked []string
	err    error
}

func (m *fakeMarker) MarkDirtyCapable(_, profileID, generation string) error {
	if m.err != nil {
		return m.err
	}
	m.marked = append(m.marked, profileID+"/"+generation)
	return nil
}

type fakeInspector struct {
	propagation string
	name        string
	err         error
}

func (f fakeInspector) BindPropagation(context.Context, string) (string, string, error) {
	return f.propagation, f.name, f.err
}

// harness is a node that is set up correctly, which each case then breaks in
// exactly one way.
type harness struct {
	t         *testing.T
	dir       string
	cfg       config.Wings
	mounter   *fakeMounter
	guard     *fakeGuard
	marker    *fakeMarker
	inspector fakeInspector
	walk      wings.ChownWalk
	walkErr   error
	f1        bool
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	dir := t.TempDir()
	return &harness{
		t:   t,
		dir: dir,
		cfg: config.Wings{
			BindRoot:   filepath.Join(dir, "pterodactyl"),
			VolumeRoot: filepath.Join(dir, "pterodactyl", "volumes"),
			ConfigPath: filepath.Join(dir, "wings.yml"),
		},
		mounter:   &fakeMounter{mountErr: map[string]error{}},
		guard:     &fakeGuard{},
		marker:    &fakeMarker{},
		inspector: fakeInspector{propagation: "rslave", name: "wings"},
		walk:      wings.ChownWalk{Enabled: false, Known: true, Source: "the harness"},
	}
}

// mountInfo writes a table in which the bind root is carried by a shared /.
func (h *harness) mountInfo(rootPropagation string) string {
	h.t.Helper()
	path := filepath.Join(h.dir, "mountinfo")
	body := "24 1 0:20 / / rw,relatime " + rootPropagation + " - ext4 /dev/sda1 rw\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		h.t.Fatal(err)
	}
	return path
}

func (h *harness) driver(rootPropagation string) *HostBind {
	h.t.Helper()
	cfg := h.cfg
	cfg.ChownSkipPatch = h.f1
	d, err := NewHostBind(cfg, h.journal(),
		WithMounter(h.mounter), WithGuard(h.guard), WithMarker(h.marker),
		WithInspector(h.inspector), WithMountInfoPath(h.mountInfo(rootPropagation)),
		WithChownWalk(func() (wings.ChownWalk, error) { return h.walk, h.walkErr }))
	if err != nil {
		h.t.Fatal(err)
	}
	return d
}

// healthy is the driver on a correctly set up node.
func (h *harness) healthy() *HostBind { return h.driver("shared:1") }

func (h *harness) journal() *journal.Journal {
	h.t.Helper()
	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	j, err := journal.New(filepath.Join(h.dir, "journal"),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		h.t.Fatal(err)
	}
	h.t.Cleanup(func() { _ = j.Close() })
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
				Paths: []string{"WS/Saved", "WS/Config"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

func testRecord() *publish.Record {
	return &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    "a1b2c3d4",
		ReleaseID:     "rel-1",
		Profile:       "testgame",
		OperationID:   "op-1",
		Slice:         "srdm-a1b2c3d4.slice",
		Classes: []publish.ClassRecord{
			{Name: "code", OpMount: "/run/srdm/.op/op-1/code",
				ExposePath: "/run/srdm/testgame/a1b2c3d4/code", HoldUnit: "srdm-hold-a1b2c3d4-code.service"},
			{Name: "pak", OpMount: "/run/srdm/.op/op-1/pak",
				ExposePath: "/run/srdm/testgame/a1b2c3d4/pak", HoldUnit: "srdm-hold-a1b2c3d4-pak.service"},
		},
	}
}

const serverID = "7f1c2e3d-0000-4000-8000-abcdefabcdef"

func request(access Access) Request {
	return Request{ServerID: serverID, Access: access, OperationID: "op-expose"}
}

func ctx() context.Context { return context.Background() }

// --- the plan: the invariant-14 waiver, bounded ---------------------------

// One binding per DECLARED class path, not one per class. `code` is Engine
// plus WS/Binaries, and each has to land at its own place in the volume;
// binding the class root instead would put content where the game does not
// look and shadow everything else under that ancestor.
func TestPlanBindsEveryDeclaredClassPathAndNothingElse(t *testing.T) {
	h := newHarness(t)
	bindings, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRO))
	if err != nil {
		t.Fatal(err)
	}

	volume := h.cfg.ServerVolume(serverID)
	want := []Binding{
		{Class: "code", Path: "Engine",
			Source: "/run/srdm/testgame/a1b2c3d4/code/Engine",
			Target: filepath.Join(volume, "Engine"), ReadOnly: true},
		{Class: "code", Path: "WS/Binaries",
			Source: "/run/srdm/testgame/a1b2c3d4/code/WS/Binaries",
			Target: filepath.Join(volume, "WS/Binaries"), ReadOnly: true},
		{Class: "pak", Path: "WS/Content/Paks",
			Source: "/run/srdm/testgame/a1b2c3d4/pak/WS/Content/Paks",
			Target: filepath.Join(volume, "WS/Content/Paks"), ReadOnly: true},
	}
	if !reflect.DeepEqual(bindings, want) {
		t.Fatalf("Plan =\n%+v\nwant\n%+v", bindings, want)
	}
}

// The absolute state rule: WS/Saved is never shared, so it is never bound —
// and nothing is bound over it either.
func TestPlanNeverTouchesPerInstanceState(t *testing.T) {
	h := newHarness(t)
	bindings, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRO))
	if err != nil {
		t.Fatal(err)
	}
	for _, b := range bindings {
		for _, excluded := range []string{"WS/Saved", "WS/Config"} {
			if b.Path == excluded || strings.HasPrefix(b.Path, excluded+"/") {
				t.Errorf("binding %+v lands on per-instance state %q", b, excluded)
			}
			if strings.HasPrefix(excluded, b.Path+"/") {
				t.Errorf("binding %+v shadows per-instance state %q", b, excluded)
			}
		}
	}
}

// The dangerous version of the same mistake: binding an ANCESTOR of the
// excluded path. The mount hides everything beneath it, so the saves are not
// overwritten — they become invisible, and the damage is found only when
// somebody goes looking for a world that is still on disk underneath.
func TestPlanRefusesAClassPathThatShadowsExcludedState(t *testing.T) {
	h := newHarness(t)
	bad := &profile.Profile{
		SchemaVersion: profile.SchemaVersion, ID: "testgame",
		Classes: []profile.Class{
			// "WS" covers WS/Saved.
			{Name: "code", Kind: profile.KindManaged, Paths: []string{"WS"}},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := bad.Validate(); err != nil {
		t.Fatal(err)
	}
	rec := testRecord()
	rec.Classes = []publish.ClassRecord{{Name: "code", ExposePath: "/run/srdm/x/code"}}

	_, err := h.healthy().Plan(rec, bad, request(AccessRO))
	if err == nil {
		t.Fatal("a class path covering per-instance state was planned")
	}
	if !IsRefusal(err) {
		t.Errorf("shadowing state was reported as a fault rather than a refusal: %v", err)
	}
	if !strings.Contains(err.Error(), "WS/Saved") {
		t.Errorf("the refusal does not name what would be hidden: %v", err)
	}
}

func TestPlanRefusesAnUnknownAccessOrMissingServer(t *testing.T) {
	h := newHarness(t)
	d, prof, rec := h.healthy(), testProfile(t), testRecord()

	if _, err := d.Plan(rec, prof, Request{ServerID: serverID, Access: "sideways"}); err == nil {
		t.Error("an unknown access mode was accepted")
	}
	if _, err := d.Plan(rec, prof, Request{Access: AccessRO}); err == nil {
		t.Error("a request with no server id was accepted")
	}
}

// A profile and a record that disagree about what was published is a
// programming error, not a mount to attempt.
func TestPlanRefusesAProfileTheRecordDoesNotMatch(t *testing.T) {
	h := newHarness(t)
	rec := testRecord()
	rec.Classes = rec.Classes[:1] // pak is published no more
	if _, err := h.healthy().Plan(rec, testProfile(t), request(AccessRO)); err == nil {
		t.Fatal("a record missing a managed class was planned against anyway")
	}
}

// --- precondition 1: propagation ------------------------------------------

func TestExposeRefusesWhenTheHostSideIsNotShared(t *testing.T) {
	h := newHarness(t)
	d := h.driver("") // private: no peer group at all

	err := d.Expose(ctx(), testRecord(), testProfile(t), request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	var r *RefusalError
	errors.As(err, &r)
	if r.Precondition != PreconditionPropagation {
		t.Errorf("Precondition = %q", r.Precondition)
	}
	if !strings.Contains(r.Fix, "make-rshared") {
		t.Errorf("the fix does not name the remedy: %s", r.Fix)
	}
	if len(h.mounter.ops) != 0 {
		t.Errorf("a refused exposure still mounted something: %v", h.mounter.ops)
	}
}

// Docker's default, and the 2026-07-31 outage: every mount srdm makes is
// invisible to Wings and every unmount leaves a ghost Wings still traverses.
func TestExposeRefusesAnRPrivateWingsContainer(t *testing.T) {
	h := newHarness(t)
	h.inspector = fakeInspector{propagation: "rprivate", name: "wings"}

	err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	if !strings.Contains(err.Error(), "rslave") {
		t.Errorf("the refusal does not name the fix: %v", err)
	}
	if !strings.Contains(err.Error(), "2026-07-31") {
		t.Errorf("the refusal does not say what this caused before: %v", err)
	}
	if len(h.mounter.ops) != 0 {
		t.Errorf("a refused exposure still mounted something: %v", h.mounter.ops)
	}
}

// Not knowing is not the same as being wrong, but it is still a refusal:
// proceeding on "I could not ask" reproduces the outage silently.
func TestExposeRefusesWhenTheContainerCannotBeInspected(t *testing.T) {
	h := newHarness(t)
	h.inspector = fakeInspector{err: errors.New("dial unix /var/run/docker.sock: no such file")}

	err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	if !strings.Contains(err.Error(), "could not read") {
		t.Errorf("the refusal does not distinguish not-knowing from being-wrong: %v", err)
	}
}

// --- precondition 2: the pre-boot chown walk ------------------------------

func TestExposeRefusesReadOnlyWhenTheChownWalkWouldRun(t *testing.T) {
	h := newHarness(t)
	h.walk = wings.ChownWalk{Enabled: true, Known: true, Source: h.cfg.ConfigPath}

	err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	var r *RefusalError
	errors.As(err, &r)
	if r.Precondition != PreconditionChownWalk {
		t.Errorf("Precondition = %q", r.Precondition)
	}
	// Both remedies, because a node can take either.
	if !strings.Contains(r.Fix, "F1") || !strings.Contains(r.Fix, "check_permissions_on_boot") {
		t.Errorf("the fix does not name both remedies: %s", r.Fix)
	}
	if !strings.Contains(r.Detail, "EROFS") {
		t.Errorf("the refusal does not say what would happen: %s", r.Detail)
	}
}

// A node running a patched build says so, and then the walk is irrelevant.
func TestF1WaivesTheChownWalkPrecondition(t *testing.T) {
	h := newHarness(t)
	h.f1 = true
	h.walk = wings.ChownWalk{Enabled: true, Known: true} // would refuse without F1

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatalf("a node asserting F1 was refused anyway: %v", err)
	}
}

// rw does not chown-walk into trouble, because nothing is read-only.
func TestTheChownWalkPreconditionOnlyAppliesToReadOnly(t *testing.T) {
	h := newHarness(t)
	h.walk = wings.ChownWalk{Enabled: true, Known: true}

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); err != nil {
		t.Fatalf("an rw exposure was refused over the chown walk: %v", err)
	}
}

// Not being able to read the answer is the same as the answer being bad.
// Guessing the other way produces the unstartable server this prevents.
func TestAnUnreadableChownWalkSettingRefuses(t *testing.T) {
	h := newHarness(t)
	h.walkErr = errors.New("config.yml is not readable")
	h.walk = wings.ChownWalk{Enabled: true}

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
}

// --- precondition 3, and the rw rule --------------------------------------

func TestExposeRWIsRefusedWhenAnotherConsumerHolds(t *testing.T) {
	h := newHarness(t)
	h.guard.report = consumer.Report{Holders: []consumer.Holder{{
		Kind: consumer.KindMount, MountPoint: "/home/container/paks",
		ContainerName: "soulmask-01",
	}}}

	err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	var r *RefusalError
	errors.As(err, &r)
	if r.Precondition != PreconditionSingleWrite {
		t.Errorf("Precondition = %q", r.Precondition)
	}
	if !strings.Contains(r.Detail, "soulmask-01") {
		t.Errorf("the refusal does not name the other consumer: %s", r.Detail)
	}
	if !strings.Contains(r.Detail, "2026-07-29") {
		t.Errorf("the refusal does not say what this caused before: %s", r.Detail)
	}
	if len(h.marker.marked) != 0 {
		t.Errorf("a refused rw exposure still marked the generation dirty: %v", h.marker.marked)
	}
}

// ro with other consumers is the normal case — that is the entire point of
// shared read-only content.
func TestExposeROIsFineWithOtherConsumers(t *testing.T) {
	h := newHarness(t)
	h.guard.report = consumer.Report{Holders: []consumer.Holder{{ContainerName: "soulmask-01"}}}

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatalf("a read-only exposure was refused because another server had it too: %v", err)
	}
}

// A generation somebody has already written through is not handed to a
// second consumer: its content no longer provably matches the release, so
// the newcomer would be reading something nobody verified.
func TestASharedGenerationIsRefusedOnceItIsDirty(t *testing.T) {
	h := newHarness(t)
	rec := testRecord()
	rec.DirtyCapable = true
	h.guard.report = consumer.Report{Holders: []consumer.Holder{{ContainerName: "soulmask-01"}}}

	err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	var r *RefusalError
	errors.As(err, &r)
	if r.Precondition != PreconditionDirty {
		t.Errorf("Precondition = %q", r.Precondition)
	}
	if !strings.Contains(r.Fix, "republish") {
		t.Errorf("the fix does not say how a dirty generation is repaired: %s", r.Fix)
	}
}

// A dirty generation with nobody on it is still usable by the one server
// that has it — refusing there would strand a running game.
func TestADirtyGenerationWithNoHoldersIsStillExposable(t *testing.T) {
	h := newHarness(t)
	rec := testRecord()
	rec.DirtyCapable = true

	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRO)); err != nil {
		t.Fatalf("a dirty generation with no other consumer was refused: %v", err)
	}
}

func TestExposeStopsWhenConsumersCannotBeResolved(t *testing.T) {
	h := newHarness(t)
	h.guard.err = errors.New("/proc is not readable")

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); err == nil {
		t.Fatal("an rw exposure proceeded without knowing whether anything else held it")
	}
	if len(h.mounter.ops) != 0 {
		t.Errorf("a failed check still mounted something: %v", h.mounter.ops)
	}
}

// --- the mounts ------------------------------------------------------------

func TestExposeReadOnlyMakesEachBindReadOnlyBySeparateRemount(t *testing.T) {
	h := newHarness(t)
	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatal(err)
	}

	volume := h.cfg.ServerVolume(serverID)
	want := []string{
		"mount -o bind /run/srdm/testgame/a1b2c3d4/code/Engine -> " + filepath.Join(volume, "Engine"),
		"mount -o bind,remount,ro -> " + filepath.Join(volume, "Engine"),
		"mount -o bind /run/srdm/testgame/a1b2c3d4/code/WS/Binaries -> " + filepath.Join(volume, "WS/Binaries"),
		"mount -o bind,remount,ro -> " + filepath.Join(volume, "WS/Binaries"),
		"mount -o bind /run/srdm/testgame/a1b2c3d4/pak/WS/Content/Paks -> " + filepath.Join(volume, "WS/Content/Paks"),
		"mount -o bind,remount,ro -> " + filepath.Join(volume, "WS/Content/Paks"),
	}
	if !reflect.DeepEqual(h.mounter.ops, want) {
		t.Fatalf("mount sequence =\n  %s\nwant\n  %s",
			strings.Join(h.mounter.ops, "\n  "), strings.Join(want, "\n  "))
	}

	// The initial bind must never ask for read-only in the same call: a bind
	// inherits its source's flags, so that silently leaves it writable — and
	// here that means a game server writing into shared content.
	for i, c := range h.mounter.calls {
		if !c.Bind() || c.Remount() {
			continue
		}
		if c.ReadOnly() {
			t.Errorf("the initial bind of %s asked for read-only in one call", c.Target)
		}
		next := h.mounter.calls[i+1]
		if next.Target != c.Target || !next.ReadOnly() || !next.Remount() {
			t.Errorf("the bind of %s is not immediately followed by a read-only remount", c.Target)
		}
	}
}

func TestExposeReadWriteLeavesTheBindsWritable(t *testing.T) {
	h := newHarness(t)
	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); err != nil {
		t.Fatal(err)
	}
	for _, c := range h.mounter.calls {
		if c.ReadOnly() {
			t.Errorf("an rw exposure made %s read-only", c.Target)
		}
		// And it binds the WRITABLE side of the generation. Binding the
		// published exposure — which is itself a read-only bind — would
		// inherit that flag and give EROFS on the first write, whatever this
		// asked for. Measured.
		if !strings.Contains(c.Source, "/.op/") {
			t.Errorf("an rw exposure binds %s, which is the read-only published path; "+
				"a bind inherits its source's flags", c.Source)
		}
	}
	if len(h.mounter.calls) != 3 {
		t.Errorf("saw %d mounts, want one per declared class path", len(h.mounter.calls))
	}
}

// The two access modes bind different mount points of the same superblock,
// and the read-only one must keep using the published exposure — that is
// what makes "nothing visible was ever writable" true of it.
func TestReadOnlyBindsThePublishedExposureAndReadWriteBindsTheOpTmpfs(t *testing.T) {
	h := newHarness(t)
	ro, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRO))
	if err != nil {
		t.Fatal(err)
	}
	rw, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	for i := range ro {
		if !strings.HasPrefix(ro[i].Source, "/run/srdm/testgame/a1b2c3d4/") {
			t.Errorf("ro binds %s, want the published exposure", ro[i].Source)
		}
		if !strings.HasPrefix(rw[i].Source, "/run/srdm/.op/op-1/") {
			t.Errorf("rw binds %s, want the writable op tmpfs", rw[i].Source)
		}
		// Same class path, same destination: only the side differs.
		if ro[i].Path != rw[i].Path || ro[i].Target != rw[i].Target {
			t.Errorf("the two modes disagree about where content goes: %+v vs %+v",
				ro[i], rw[i])
		}
	}
}

// The flag goes down BEFORE writing becomes possible. Marked-but-not-mounted
// costs a republish nobody needed; mounted-but-not-marked lets a second
// consumer join a generation somebody is already writing through.
func TestExposeRWMarksTheGenerationBeforeMountingIt(t *testing.T) {
	h := newHarness(t)
	h.mounter.mountErr[filepath.Join(h.cfg.ServerVolume(serverID), "Engine")] = syscall.EPERM

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); err == nil {
		t.Fatal("Expose reported success despite a failed mount")
	}
	if !reflect.DeepEqual(h.marker.marked, []string{"testgame/a1b2c3d4"}) {
		t.Fatalf("marked = %v; the generation must be marked before writing becomes "+
			"possible, so a crash between the two cannot leave it unmarked", h.marker.marked)
	}
}

// Read-only exposures never mark: nothing can be written through them.
func TestExposeRODoesNotMarkTheGeneration(t *testing.T) {
	h := newHarness(t)
	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatal(err)
	}
	if len(h.marker.marked) != 0 {
		t.Errorf("a read-only exposure marked the generation dirty: %v", h.marker.marked)
	}
}

// Without somewhere to record it, an rw exposure is refused: nothing
// downstream would ever know the generation had been written through.
func TestExposeRWWithNoMarkerIsRefused(t *testing.T) {
	h := newHarness(t)
	d, err := NewHostBind(h.cfg, h.journal(),
		WithMounter(h.mounter), WithGuard(h.guard), WithInspector(h.inspector),
		WithMountInfoPath(h.mountInfo("shared:1")),
		WithChownWalk(func() (wings.ChownWalk, error) { return h.walk, nil }))
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	if len(h.mounter.ops) != 0 {
		t.Errorf("a refused exposure still mounted something: %v", h.mounter.ops)
	}
}

// A half-exposed server is worse than an unexposed one: the game starts,
// finds some of its content, and fails somewhere further in.
func TestAFailedBindUnwindsEverythingItHadMounted(t *testing.T) {
	h := newHarness(t)
	volume := h.cfg.ServerVolume(serverID)
	h.mounter.mountErr[filepath.Join(volume, "WS/Content/Paks")] = syscall.EPERM

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err == nil {
		t.Fatal("Expose reported success despite a failed mount")
	}
	// Only what was actually mounted, deepest first so a nested mount never
	// blocks its parent. The bind that FAILED is not unwound, because it
	// never happened — unmounting it would either error or, worse, remove
	// something that was already there.
	want := []string{
		filepath.Join(volume, "WS/Binaries"),
		filepath.Join(volume, "Engine"),
	}
	if !reflect.DeepEqual(h.mounter.unmounted, want) {
		t.Fatalf("unwound %v, want %v", h.mounter.unmounted, want)
	}
}

// --- unexpose --------------------------------------------------------------

func TestUnexposeRemovesEveryBindDeepestFirst(t *testing.T) {
	h := newHarness(t)
	d := h.healthy()
	if err := d.Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatal(err)
	}
	h.mounter.unmounted = nil

	if err := d.Unexpose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatal(err)
	}
	volume := h.cfg.ServerVolume(serverID)
	want := []string{
		filepath.Join(volume, "WS/Content/Paks"),
		filepath.Join(volume, "WS/Binaries"),
		filepath.Join(volume, "Engine"),
	}
	if !reflect.DeepEqual(h.mounter.unmounted, want) {
		t.Fatalf("unmounted %v, want %v", h.mounter.unmounted, want)
	}
}

// Unexpose is how a consumer STOPS holding a generation, so it must not
// consult the consumer guard — refusing while one holds would make the state
// unreachable. The refusal belongs in publication's teardown, which runs
// after this.
func TestUnexposeDoesNotRefuseWhileAConsumerHolds(t *testing.T) {
	h := newHarness(t)
	h.guard.report = consumer.Report{Holders: []consumer.Holder{{ContainerName: "soulmask-01"}}}

	if err := h.healthy().Unexpose(ctx(), testRecord(), testProfile(t), request(AccessRO)); err != nil {
		t.Fatalf("unexpose was refused while a consumer held the generation, which is the "+
			"state it exists to leave: %v", err)
	}
}

// --- the journal -----------------------------------------------------------

func TestARefusalIsJournaledAsARefusalNotAFailure(t *testing.T) {
	h := newHarness(t)
	h.inspector = fakeInspector{propagation: "rprivate", name: "wings"}

	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRO)); !IsRefusal(err) {
		t.Fatal("expected a refusal")
	}
	op, err := journal.LoadOperation(filepath.Join(h.dir, "journal"), "op-expose")
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, r := range op.Events {
		if r.Phase != PhasePreflight || r.Outcome == journal.OutcomeStarted {
			continue
		}
		found = true
		if r.Outcome != journal.OutcomeRefused {
			t.Errorf("the preflight outcome is %q, want %q — a refusal is srdm declining "+
				"by contract, not srdm breaking", r.Outcome, journal.OutcomeRefused)
		}
	}
	if !found {
		t.Fatal("the preflight left no record of having run")
	}
}

func TestRefusalsAlwaysCarryAFix(t *testing.T) {
	cases := map[string]func(*harness){
		"host not shared":    func(h *harness) { h.inspector = fakeInspector{propagation: "rslave"} },
		"container rprivate": func(h *harness) { h.inspector = fakeInspector{propagation: "rprivate"} },
		"container unknown":  func(h *harness) { h.inspector = fakeInspector{err: errors.New("no socket")} },
		"chown walk enabled": func(h *harness) { h.walk = wings.ChownWalk{Enabled: true, Known: true} },
		"second rw consumer": func(h *harness) { h.guard.report = consumer.Report{Holders: []consumer.Holder{{}}} },
	}
	for name, breakIt := range cases {
		h := newHarness(t)
		breakIt(h)
		access := AccessRO
		if name == "second rw consumer" {
			access = AccessRW
		}
		root := "shared:1"
		if name == "host not shared" {
			root = ""
		}
		err := h.driver(root).Expose(ctx(), testRecord(), testProfile(t), request(access))
		if !IsRefusal(err) {
			t.Errorf("%s: want a refusal, got %v", name, err)
			continue
		}
		var r *RefusalError
		errors.As(err, &r)
		if r.Fix == "" {
			t.Errorf("%s: the refusal carries no remedy, which is an outage with extra "+
				"steps: %s", name, r.Detail)
		}
	}
}

func TestNewHostBindRefusesAMisconfiguredNode(t *testing.T) {
	h := newHarness(t)
	bad := h.cfg
	bad.VolumeRoot = "/somewhere/else"
	if _, err := NewHostBind(bad, h.journal()); err == nil {
		t.Fatal("a volume root outside the bind root was accepted; the propagation srdm " +
			"checks would not be the propagation its mounts travel over")
	}
	if _, err := NewHostBind(h.cfg, nil); err == nil {
		t.Fatal("a driver with no journal was accepted")
	}
}

func TestDriverName(t *testing.T) {
	h := newHarness(t)
	if got := h.healthy().Name(); got != "host-bind" {
		t.Fatalf("Name = %q", got)
	}
	// The interface is the fork; the driver has to satisfy it.
	var _ Driver = h.healthy()
	_ = fmt.Sprint(AccessRO, AccessRW)
}
