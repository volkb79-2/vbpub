package expose

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/wings"
)

// --- harness ---------------------------------------------------------------

type mountCall struct {
	Source, Target, FSType, Data string
	Flags                        uintptr
}

func (c mountCall) ReadOnly() bool { return c.Flags&syscall.MS_RDONLY != 0 }
func (c mountCall) Bind() bool     { return c.Flags&syscall.MS_BIND != 0 }
func (c mountCall) Remount() bool  { return c.Flags&syscall.MS_REMOUNT != 0 }
func (c mountCall) Overlay() bool  { return c.FSType == "overlay" }

type fakeMounter struct {
	ops       []string
	calls     []mountCall
	mountErr  map[string]error
	unmounted []string
}

func (m *fakeMounter) Mount(source, target, fstype string, flags uintptr, data string) error {
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
	// Identical to the pre-P10 rendering when fstype is empty (every ro/bind
	// call), so every exact-string assertion written against a plain bind
	// keeps working unchanged; an overlay call (fstype "overlay") gets a
	// -t clause and its options appended, which nothing asserts on verbatim.
	op := "mount"
	if fstype != "" {
		op += " -t " + fstype
	}
	op += " -o " + strings.Join(f, ",")
	if source != "" {
		op += " " + source
	}
	if data != "" {
		op += " (" + data + ")"
	}
	m.ops = append(m.ops, op+" -> "+target)
	m.calls = append(m.calls, mountCall{Source: source, Target: target, FSType: fstype, Data: data, Flags: flags})
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
	inspector fakeInspector
	walk      wings.ChownWalk
	walkErr   error
	f1        bool
}

// gameOwner is the uid:gid a Wings node runs its server containers as. P10
// (D-029) retires the config that used to hand a class tree over to it —
// nothing reads config.Wings.WriteOwner anymore — but the harness still sets
// it, matching a real node's config carrying the now-vestigial field
// unremarked.
var gameOwner = config.WriteOwner{UID: 988, GID: 988}

func newHarness(t *testing.T) *harness {
	t.Helper()
	dir := t.TempDir()
	owner := gameOwner
	return &harness{
		t:   t,
		dir: dir,
		cfg: config.Wings{
			BindRoot:   filepath.Join(dir, "pterodactyl"),
			VolumeRoot: filepath.Join(dir, "pterodactyl", "volumes"),
			ConfigPath: filepath.Join(dir, "wings.yml"),
			WriteOwner: &owner,
		},
		mounter:   &fakeMounter{mountErr: map[string]error{}},
		guard:     &fakeGuard{},
		inspector: fakeInspector{propagation: "rslave", name: "wings"},
		walk:      wings.ChownWalk{Enabled: false, Known: true, Source: "the harness"},
	}
}

// liveRecord is testRecord with its class trees actually on disk, at
// ExposePath, sealed as publication leaves them. The rw path needs it: an
// overlay's lowerdir has to exist for mirrorDirTree to walk, and a record
// naming paths that do not exist would prove nothing about what mounting
// rw over them does.
func (h *harness) liveRecord() *publish.Record {
	h.t.Helper()
	rec := testRecord()
	contents := map[string][]string{
		"code": {"Engine/libengine.so", "WS/Binaries/server"},
		"pak":  {"WS/Content/Paks/game.pak"},
	}
	for i := range rec.Classes {
		c := &rec.Classes[i]
		c.ExposePath = filepath.Join(h.dir, "expose", c.Name)
		for _, rel := range contents[c.Name] {
			full := filepath.Join(c.ExposePath, filepath.FromSlash(rel))
			if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
				h.t.Fatal(err)
			}
			if err := os.WriteFile(full, []byte(rel), 0o444); err != nil {
				h.t.Fatal(err)
			}
		}
	}
	return rec
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
		WithMounter(h.mounter), WithGuard(h.guard), WithStateDir(h.dir),
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

	if err := h.healthy().Expose(ctx(), h.liveRecord(), testProfile(t), request(AccessRW)); err != nil {
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

// D-029 retires the single-consumer rule: an overlay's writes land in a
// per-server upper, never in the generation itself, so a second rw request
// no longer shares anything with the first to collide over. Oracle 26 is
// the privileged version of this — that the two really do stay isolated —
// gated in e2e_test.go; this is the unit-level half, that Expose no longer
// even asks the question.
func TestExposeRWNoLongerRefusesASecondConsumer(t *testing.T) {
	h := newHarness(t)
	h.guard.report = consumer.Report{Holders: []consumer.Holder{{
		Kind: consumer.KindOverlay, MountPoint: "/home/container/paks",
		ContainerName: "soulmask-01",
	}}}

	if err := h.healthy().Expose(ctx(), h.liveRecord(), testProfile(t), request(AccessRW)); err != nil {
		t.Fatalf("access rw was refused because another server already held it: %v", err)
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

// access: rw mounts an OVERLAY now (D-027, D-029), not a plain writable
// bind: lowerdir is the sealed, published exposure — the SAME Source ro
// uses — and upperdir/workdir are per-server layers under the state dir.
func TestExposeReadWriteMountsAnOverlayOfTheSealedExposure(t *testing.T) {
	h := newHarness(t)
	rec := h.liveRecord()
	bindings, err := h.healthy().Plan(rec, testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRW)); err != nil {
		t.Fatal(err)
	}
	if len(h.mounter.calls) != 3 {
		t.Fatalf("saw %d mounts, want one per declared class path: %v",
			len(h.mounter.calls), h.mounter.ops)
	}
	for i, c := range h.mounter.calls {
		if !c.Overlay() {
			t.Errorf("an rw exposure mounted %s as fstype %q, want overlay", c.Target, c.FSType)
		}
		if c.ReadOnly() {
			t.Errorf("an rw exposure made %s read-only", c.Target)
		}
		b := bindings[i]
		// The lowerdir has to be the SEALED, PUBLISHED exposure specifically
		// — oracle 25's whole claim (byte-identical to the release
		// afterwards) is false if this is anything else, most dangerously if
		// it were ever the writable upper.
		want := fmt.Sprintf("lowerdir=%s,upperdir=%s,workdir=%s", b.Source, b.Upper, b.Work)
		if c.Data != want {
			t.Errorf("overlay options for %s are %q, want %q", c.Target, c.Data, want)
		}
	}
}

// Both access modes read from the SAME Source — the sealed, published
// exposure — which is the property that makes ro's "nothing visible was
// ever writable" still true once rw exists beside it: ro is a plain bind of
// that path, and nothing about mounting an overlay elsewhere touches it.
// Only rw carries Upper/Work, and each declared class PATH gets its own —
// two paths in one class (`code` is Engine plus WS/Binaries) cannot share
// an upper, because overlayfs owns upperdir/workdir exclusively for as
// long as a mount using them is live.
func TestBothAccessModesReadFromTheSameSourceOnlyRWCarriesAnOverlayLayer(t *testing.T) {
	h := newHarness(t)
	ro, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRO))
	if err != nil {
		t.Fatal(err)
	}
	rw, err := h.healthy().Plan(testRecord(), testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	seenUpper := map[string]bool{}
	for i := range ro {
		if ro[i].Source != rw[i].Source {
			t.Errorf("ro and rw disagree about Source: %q vs %q", ro[i].Source, rw[i].Source)
		}
		if !strings.HasPrefix(ro[i].Source, "/run/srdm/testgame/a1b2c3d4/") {
			t.Errorf("Source = %s, want the published exposure", ro[i].Source)
		}
		if ro[i].Upper != "" || ro[i].Work != "" {
			t.Errorf("a ro binding carries an overlay layer: %+v", ro[i])
		}
		if rw[i].Upper == "" || rw[i].Work == "" {
			t.Errorf("an rw binding has no overlay layer: %+v", rw[i])
		}
		if rw[i].Upper == rw[i].Work {
			t.Errorf("Upper and Work are the same directory: %s", rw[i].Upper)
		}
		if seenUpper[rw[i].Upper] {
			t.Errorf("two rw bindings share Upper %s", rw[i].Upper)
		}
		seenUpper[rw[i].Upper] = true
		// Same class path, same destination: only the side differs.
		if ro[i].Path != rw[i].Path || ro[i].Target != rw[i].Target {
			t.Errorf("the two modes disagree about where content goes: %+v vs %+v",
				ro[i], rw[i])
		}
	}
}

// Oracle 26's precondition, at the unit level: two servers holding rw on the
// same generation get DIFFERENT overlay layers. Sharing one would mean one
// server's writes land in the other's upper — the isolation the privileged
// oracle actually measures against a real kernel.
func TestPlanGivesDifferentServersDifferentOverlayLayers(t *testing.T) {
	h := newHarness(t)
	reqA := request(AccessRW)
	reqB := Request{ServerID: "8f1c2e3d-0000-4000-8000-abcdefabcd02", Access: AccessRW}

	a, err := h.healthy().Plan(testRecord(), testProfile(t), reqA)
	if err != nil {
		t.Fatal(err)
	}
	b, err := h.healthy().Plan(testRecord(), testProfile(t), reqB)
	if err != nil {
		t.Fatal(err)
	}
	for i := range a {
		if a[i].Upper == b[i].Upper {
			t.Errorf("server A and server B share Upper %s for %s; one server's writes "+
				"would land in the other's", a[i].Upper, a[i].Path)
		}
		if a[i].Work == b[i].Work {
			t.Errorf("server A and server B share Work %s for %s", a[i].Work, a[i].Path)
		}
		// Same generation, same class path: everything but the server-keyed
		// tail of Upper/Work should agree.
		if a[i].Source != b[i].Source || a[i].Path != b[i].Path {
			t.Errorf("server A and server B disagree about Source/Path for the same "+
				"generation: %+v vs %+v", a[i], b[i])
		}
	}
}

// --- the overlay's writable layer (D-029) ----------------------------------

// Nothing marks or hands a tree to a declared owner anymore: an overlay
// writes to a directory srdm itself creates and owns, so there is nothing
// to hand over. What srdm DOES do is mirror the lower's directory tree into
// the upper, permissively, which is what a merged view needs to be
// writable by whoever Wings runs the game container as — see mountOverlay's
// own comment for the measurement this rests on.
func TestExposeRWMirrorsTheLowerDirectoryTreeIntoTheUpperLayer(t *testing.T) {
	h := newHarness(t)
	rec := h.liveRecord()

	bindings, err := h.healthy().Plan(rec, testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRW)); err != nil {
		t.Fatal(err)
	}

	for _, b := range bindings {
		// "WS/Content/Paks" is the pak binding's lower — a directory holding
		// game.pak — and its own directory has to be mirrored too, not just
		// something beneath it.
		err := filepath.WalkDir(b.Source, func(p string, d fs.DirEntry, err error) error {
			if err != nil || !d.IsDir() {
				return err
			}
			rel, err := filepath.Rel(b.Source, p)
			if err != nil {
				return err
			}
			mirrored := filepath.Join(b.Upper, rel)
			info, err := os.Stat(mirrored)
			if err != nil {
				t.Errorf("%s (mirroring %s) was never created: %v", mirrored, p, err)
				return nil
			}
			if info.Mode().Perm() != 0o777 {
				t.Errorf("%s is %v, want 0777 — the mode that makes it writable by a uid "+
					"srdm never declared", mirrored, info.Mode().Perm())
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
}

// A read-only exposure must not create any overlay state: its guarantee is
// the mount, and creating a writable layer nobody asked for would be state
// with no purpose and no owner.
func TestExposeRODoesNotCreateAnOverlayLayer(t *testing.T) {
	h := newHarness(t)
	rec := h.liveRecord()
	h.f1 = true

	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRO)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(h.dir, "overlay")); !os.IsNotExist(err) {
		t.Errorf("a read-only exposure created overlay state: %v", err)
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

// Unexpose discards the overlay upper/work layers, once the mount over them
// is gone (D-029's "retained or discarded" decision — see the LOG). Safe
// rather than lossy: harvest is what reads a per-server merged view, and
// every documented rw flow reads it before this ever runs.
func TestUnexposeDiscardsTheOverlayUpperAndWorkLayers(t *testing.T) {
	h := newHarness(t)
	d := h.healthy()
	rec := h.liveRecord()

	bindings, err := d.Plan(rec, testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Expose(ctx(), rec, testProfile(t), request(AccessRW)); err != nil {
		t.Fatal(err)
	}
	for _, b := range bindings {
		if _, err := os.Stat(b.Upper); err != nil {
			t.Fatalf("Expose never created %s: %v", b.Upper, err)
		}
	}

	if err := d.Unexpose(ctx(), rec, testProfile(t), request(AccessRW)); err != nil {
		t.Fatal(err)
	}
	for _, b := range bindings {
		if _, err := os.Stat(filepath.Dir(b.Upper)); !os.IsNotExist(err) {
			t.Errorf("unexpose left overlay state behind at %s: %v", filepath.Dir(b.Upper), err)
		}
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
	dirty := func(h *harness) {
		h.guard.report = consumer.Report{Holders: []consumer.Holder{{}}}
	}
	cases := map[string]func(*harness){
		"host not shared":    func(h *harness) { h.inspector = fakeInspector{propagation: "rslave"} },
		"container rprivate": func(h *harness) { h.inspector = fakeInspector{propagation: "rprivate"} },
		"container unknown":  func(h *harness) { h.inspector = fakeInspector{err: errors.New("no socket")} },
		"chown walk enabled": func(h *harness) { h.walk = wings.ChownWalk{Enabled: true, Known: true} },
		"dirty and held":     dirty,
	}
	for name, breakIt := range cases {
		h := newHarness(t)
		breakIt(h)
		rec := testRecord()
		if name == "dirty and held" {
			rec.DirtyCapable = true
		}
		root := "shared:1"
		if name == "host not shared" {
			root = ""
		}
		err := h.driver(root).Expose(ctx(), rec, testProfile(t), request(AccessRO))
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

// --- mountOverlay / mirrorDirTree failure paths -----------------------------

// mirrorDirTree fails when the lowerdir it is asked to mirror does not
// exist, and Expose surfaces that rather than mounting a half-built overlay.
func TestExposeRWFailsWhenTheSealedExposureDoesNotExist(t *testing.T) {
	h := newHarness(t)
	// testRecord's ExposePath names are fake — nothing is on disk there,
	// unlike liveRecord — so mirrorDirTree's initial WalkDir fails.
	if err := h.healthy().Expose(ctx(), testRecord(), testProfile(t), request(AccessRW)); err == nil {
		t.Fatal("Expose rw succeeded although the sealed exposure it mirrors does not exist")
	}
}

// A failed overlay mount call is surfaced too, and unwinds anything already
// mounted — the same contract a failed plain bind has.
func TestExposeRWFailsWhenTheOverlayMountItselfFails(t *testing.T) {
	h := newHarness(t)
	rec := h.liveRecord()
	volume := h.cfg.ServerVolume(serverID)
	h.mounter.mountErr[filepath.Join(volume, "Engine")] = syscall.EPERM

	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRW)); err == nil {
		t.Fatal("Expose rw succeeded despite a failed overlay mount")
	}
}

func TestMirrorDirTreeFailsWhenTheLowerDoesNotExist(t *testing.T) {
	if err := mirrorDirTree(filepath.Join(t.TempDir(), "nonexistent"), t.TempDir()); err == nil {
		t.Fatal("mirroring a nonexistent lower tree succeeded")
	}
}

func TestMirrorDirTreeFailsWhenTheUpperCannotBeCreated(t *testing.T) {
	dir := t.TempDir()
	lower := filepath.Join(dir, "lower")
	if err := os.MkdirAll(lower, 0o755); err != nil {
		t.Fatal(err)
	}
	// A FILE where mirrorDirTree needs a directory component: MkdirAll fails
	// ENOTDIR trying to create anything beneath it.
	blocker := filepath.Join(dir, "blocker")
	if err := os.WriteFile(blocker, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := mirrorDirTree(lower, filepath.Join(blocker, "upper")); err == nil {
		t.Fatal("mirroring into an upper whose parent is a plain file succeeded")
	}
}

// The upper mirrors fine (it is a fresh path), but overlayfs's own scratch
// directory cannot be created — here because something already occupies
// that exact path as a plain file, not a directory.
func TestExposeRWFailsWhenTheWorkDirCannotBeCreated(t *testing.T) {
	h := newHarness(t)
	rec := h.liveRecord()
	bindings, err := h.healthy().Plan(rec, testProfile(t), request(AccessRW))
	if err != nil {
		t.Fatal(err)
	}
	blocked := bindings[0].Work
	if err := os.MkdirAll(filepath.Dir(blocked), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(blocked, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	if err := h.healthy().Expose(ctx(), rec, testProfile(t), request(AccessRW)); err == nil {
		t.Fatalf("Expose rw succeeded although %s already exists as a plain file", blocked)
	}
}

// --- RWServers (D-029): what harvest asks to find who holds rw ------------

// overlayEntry renders one overlay mountinfo line, the shape an rw exposure
// produces: srdm's own device (uninformative, D-028), a lowerdir option
// naming the generation, and a target under the volume root.
func overlayEntry(id int, point, lowerdirs string) string {
	return fmt.Sprintf(
		"%d 30 0:99 / %s rw,relatime - overlay overlay "+
			"rw,lowerdir=%s,upperdir=/state/x/upper,workdir=/state/x/work",
		id, point, lowerdirs)
}

// otherDevice is a superblock unrelated to anything srdm published, used to
// prove a plain tmpfs mount is never mistaken for an overlay.
const otherDevice = "0:77"

// mountLine renders a plain tmpfs mountinfo line — the shape RWServers must
// NOT mistake for an overlay, however its target path looks.
func mountLine(id int, device, point string) string {
	return fmt.Sprintf("%d 30 %s / %s rw,nosuid,nodev - tmpfs tmpfs rw,size=67108864,mode=755",
		id, device, point)
}

func (h *harness) rwServersTable(entries ...string) string {
	h.t.Helper()
	path := filepath.Join(h.dir, "mountinfo-rwservers")
	if err := os.WriteFile(path, []byte(strings.Join(entries, "\n")+"\n"), 0o644); err != nil {
		h.t.Fatal(err)
	}
	return path
}

func TestRWServersFindsEveryServerHoldingAnOverlayOfTheGeneration(t *testing.T) {
	h := newHarness(t)
	rec := testRecord()
	volume := h.cfg.VolumeRoot
	serverB := "8f1c2e3d-0000-4000-8000-abcdefabcd02"

	table := h.rwServersTable(
		overlayEntry(90, filepath.Join(volume, serverID, "Engine"),
			rec.Classes[0].ExposePath+"/Engine"),
		// A second declared path of the SAME class, same server: must not
		// be double-counted.
		overlayEntry(91, filepath.Join(volume, serverID, "WS/Binaries"),
			rec.Classes[0].ExposePath+"/WS/Binaries"),
		overlayEntry(92, filepath.Join(volume, serverB, "WS/Content/Paks"),
			rec.Classes[1].ExposePath+"/WS/Content/Paks"),
		// Noise: an overlay of something else entirely, one under the volume
		// root but of a generation this record does not name, and a plain
		// tmpfs at a path that only LOOKS like an rw binding's target.
		overlayEntry(93, "/var/lib/docker/overlay2/abc/merged", "/var/lib/docker/overlay2/abc/lower"),
		overlayEntry(95, filepath.Join(volume, serverB, "WS/Binaries"),
			"/run/srdm/othergame/deadbeef/code/WS/Binaries"),
		mountLine(94, otherDevice, filepath.Join(volume, serverB, "Engine")),
	)
	d, err := NewHostBind(h.cfg, h.journal(), WithMountInfoPath(table))
	if err != nil {
		t.Fatal(err)
	}

	servers, err := d.RWServers(rec)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{serverB, serverID}
	sort.Strings(want)
	if !reflect.DeepEqual(servers, want) {
		t.Fatalf("RWServers = %v, want %v", servers, want)
	}
}

func TestRWServersFindsNobodyWhenNothingHoldsRW(t *testing.T) {
	h := newHarness(t)
	rec := testRecord()
	table := h.rwServersTable(mountLine(90, otherDevice, "/somewhere/else"))
	d, err := NewHostBind(h.cfg, h.journal(), WithMountInfoPath(table))
	if err != nil {
		t.Fatal(err)
	}
	servers, err := d.RWServers(rec)
	if err != nil {
		t.Fatal(err)
	}
	if len(servers) != 0 {
		t.Errorf("RWServers = %v, want none", servers)
	}
}

func TestRWServersFailsWhenTheMountTableCannotBeRead(t *testing.T) {
	h := newHarness(t)
	d, err := NewHostBind(h.cfg, h.journal(),
		WithMountInfoPath(filepath.Join(h.dir, "nonexistent")))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.RWServers(testRecord()); err == nil {
		t.Fatal("RWServers succeeded reading an unreadable mount table")
	}
}

// --- RWServers's small helpers, tested directly -----------------------------

// An overlay mount with no lowerdir= option at all should not happen in
// practice — the kernel refuses to mount one without it — but the parser
// must not panic or misread the absence as an empty match.
func TestOverlayLowerDirsReturnsNilWithNoLowerdirOption(t *testing.T) {
	e, err := mountinfo.ParseLine(
		"90 30 0:99 / /t/merged rw,relatime - overlay overlay rw,index=off")
	if err != nil {
		t.Fatal(err)
	}
	if got := overlayLowerDirs(e); got != nil {
		t.Errorf("overlayLowerDirs = %v, want nil", got)
	}
}

func TestAnyLowerUnderIsFalseWhenNothingMatches(t *testing.T) {
	if anyLowerUnder([]string{"/a/b", "/c/d"}, []string{"/x", "/y"}) {
		t.Error("anyLowerUnder matched roots that share nothing with the lowerdirs")
	}
}

// A target equal to root names no server segment at all — the mount point
// would have to be the volume root itself, which no rw binding ever is.
func TestFirstPathComponentRejectsTheRootItself(t *testing.T) {
	if _, ok := firstPathComponent("/a/b", "/a/b"); ok {
		t.Error("firstPathComponent accepted a target equal to root")
	}
}

func TestFirstPathComponentRejectsATargetOutsideRoot(t *testing.T) {
	if _, ok := firstPathComponent("/a/b", "/somewhere/else"); ok {
		t.Error("firstPathComponent accepted a target outside root")
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
