package opctl

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/expose"
	"srdm/internal/harvest"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

func ctx() context.Context { return context.Background() }

// --- the rig --------------------------------------------------------------
//
// Publication and exposure are faked, because what this package contributes
// is an ORDER rather than a mount. The store and the assignment documents are
// real: they are the two durable things an operation moves between, and a
// fake of either would make the crash-window oracles meaningless.

type rig struct {
	t    *testing.T
	cfg  config.Config
	jnl  *journal.Journal
	st   *store.Store
	asg  *assign.Store
	pub  *fakePublisher
	drv  *fakeDriver
	ctl  *Controller
	prof *profile.Profile
	// log is every step the fakes took, in order. The oracles about
	// sequencing read this and nothing else.
	log []string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	dir := t.TempDir()
	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")

	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	st, err := store.Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}
	asg, err := assign.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	r := &rig{t: t, cfg: cfg, jnl: jnl, st: st, asg: asg, prof: testProfile(t)}
	r.pub = &fakePublisher{rig: r, records: map[string]*publish.Record{}}
	r.drv = &fakeDriver{rig: r}

	// A real harvester, because it is cheap and needs no privilege: wiring a
	// fake would only test that the fake was called, while the real one
	// refuses on its own contract (nothing is mounted) and that refusal is
	// what a caller actually meets.
	hrv, err := harvest.New(cfg, jnl, st)
	if err != nil {
		t.Fatal(err)
	}
	r.ctl, err = New(cfg, jnl, st, r.pub, r.drv, WithHarvester(hrv))
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func testProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged, Paths: []string{"WS/Content/Paks"}},
			{Name: "code", Kind: profile.KindManaged, Paths: []string{"Engine"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

// release promotes a real release into the real store.
func (r *rig) release(id string) *store.Release {
	r.t.Helper()
	tx, err := r.st.Begin("op-stage-" + id)
	if err != nil {
		r.t.Fatal(err)
	}
	for _, rel := range []string{"Engine/libengine.so", "WS/Content/Paks/game.pak"} {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			r.t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(id+" "+rel), 0o644); err != nil {
			r.t.Fatal(err)
		}
	}
	out, err := r.st.Promote(tx, r.prof, store.PromoteOpts{
		ReleaseID: id, Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		r.t.Fatal(err)
	}
	return out
}

// assigned puts the profile on a release with servers already attached, as if
// an earlier activation had done it.
func (r *rig) assigned(releaseID string, servers ...string) *assign.Assignment {
	r.t.Helper()
	r.release(releaseID)
	a, err := r.asg.Load(r.prof.ID)
	if err != nil {
		r.t.Fatal(err)
	}
	a.SetRelease(releaseID)
	for _, s := range servers {
		if err := a.AddServer(s, "ro"); err != nil {
			r.t.Fatal(err)
		}
	}
	if err := r.asg.Save(a); err != nil {
		r.t.Fatal(err)
	}
	r.pub.publishRecord(releaseID)
	return a
}

// loadAssignment reads what is durably recorded right now.
func (r *rig) loadAssignment() *assign.Assignment {
	r.t.Helper()
	a, err := r.asg.Load(r.prof.ID)
	if err != nil {
		r.t.Fatal(err)
	}
	return a
}

func (r *rig) note(format string, args ...any) {
	r.log = append(r.log, fmt.Sprintf(format, args...))
}

type fakePublisher struct {
	rig     *rig
	records map[string]*publish.Record
	holders map[string][]consumer.Holder

	publishErr  error
	teardownErr error
	holdersErr  error

	// incomplete names generations IsComplete should report as NOT fully
	// realized in the kernel, even though a record exists for them — the
	// state every generation is in immediately after a reboot. Complete by
	// default, which is what makes re-activation adopt rather than rebuild
	// in the rest of this file's tests.
	incomplete  map[string]bool
	completeErr error

	reconcileResult    *publish.Reconciliation
	reconcileErr       error
	teardownOrphansErr error
	repairReadOnlyErr  error
	clearErr           error
	adoptResult        *publish.OpRecovery
	adoptErr           error
}

func (f *fakePublisher) publishRecord(releaseID string) *publish.Record {
	gen := publish.GenerationID(releaseID)
	rec := &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    gen, ReleaseID: releaseID, Profile: "testgame",
		Classes: []publish.ClassRecord{
			{Name: "code", OpMount: "/run/srdm/.op/" + gen + "/code",
				ExposePath: "/run/srdm/testgame/" + gen + "/code"},
		},
	}
	f.records[gen] = rec
	return rec
}

func (f *fakePublisher) Publish(_ context.Context, opID, profileID string, rel *store.Release,
	_ *profile.Profile) (*publish.Record, error) {
	f.rig.note("publish %s", rel.ID)
	if f.publishErr != nil {
		return nil, f.publishErr
	}
	return f.publishRecord(rel.ID), nil
}

func (f *fakePublisher) Teardown(_ context.Context, opID, profileID, generation string) error {
	// Recorded WITH what the durable assignment says at this instant. The
	// crash-window contract is about ordering between a file write and a
	// teardown, so the only way to assert it is to look at the file from
	// inside the teardown.
	f.rig.note("teardown %s (assignment=%s)", generation, f.rig.loadAssignment().Release)
	if f.teardownErr != nil {
		return f.teardownErr
	}
	delete(f.records, generation)
	return nil
}

func (f *fakePublisher) LoadRecord(profileID, generation string) (*publish.Record, error) {
	rec, ok := f.records[generation]
	if !ok {
		return nil, fmt.Errorf("no record for %s/%s", profileID, generation)
	}
	return rec, nil
}

func (f *fakePublisher) Holders(_ context.Context, profileID, generation string) (*consumer.Report, error) {
	if f.holdersErr != nil {
		return nil, f.holdersErr
	}
	if _, ok := f.records[generation]; !ok {
		return nil, fmt.Errorf("no record for %s/%s", profileID, generation)
	}
	return &consumer.Report{Holders: f.holders[generation]}, nil
}

// IsComplete defaults to true: a fake record represents a live, healthy
// generation unless a test explicitly marks it otherwise, which is what lets
// re-activation adopt rather than rebuild.
func (f *fakePublisher) IsComplete(_ context.Context, rec *publish.Record) (bool, error) {
	if f.completeErr != nil {
		return false, f.completeErr
	}
	return !f.incomplete[rec.Generation], nil
}

func (f *fakePublisher) Reconcile(_ context.Context, opID string) (*publish.Reconciliation, error) {
	f.rig.note("reconcile")
	if f.reconcileErr != nil {
		return nil, f.reconcileErr
	}
	if f.reconcileResult != nil {
		return f.reconcileResult, nil
	}
	return &publish.Reconciliation{}, nil
}

func (f *fakePublisher) TeardownOrphans(opID string, res *publish.Reconciliation) error {
	f.rig.note("teardown-orphans %d", len(res.Orphans))
	return f.teardownOrphansErr
}

func (f *fakePublisher) RepairReadOnly(opID, exposePath string) error {
	f.rig.note("repair-ro %s", exposePath)
	return f.repairReadOnlyErr
}

func (f *fakePublisher) ClearForRepublish(_ context.Context, opID, profileID,
	generation string) (*publish.Record, error) {
	rec, ok := f.records[generation]
	if !ok {
		return nil, fmt.Errorf("no record for %s/%s", profileID, generation)
	}
	if f.clearErr != nil {
		return nil, f.clearErr
	}
	f.rig.note("clear %s/%s", profileID, generation)
	delete(f.records, generation)
	return rec, nil
}

func (f *fakePublisher) AdoptOrQuarantine(_ context.Context, opID string) (*publish.OpRecovery, error) {
	f.rig.note("adopt-or-quarantine")
	if f.adoptErr != nil {
		return nil, f.adoptErr
	}
	if f.adoptResult != nil {
		return f.adoptResult, nil
	}
	return &publish.OpRecovery{}, nil
}

type fakeDriver struct {
	rig       *rig
	exposeErr map[string]error
}

func (f *fakeDriver) Name() string { return "fake" }

func (f *fakeDriver) Expose(_ context.Context, rec *publish.Record, _ *profile.Profile,
	req expose.Request) error {
	f.rig.note("expose %s -> %s (assignment=%s)", req.ServerID, rec.Generation,
		f.rig.loadAssignment().Release)
	if err := f.exposeErr[req.ServerID]; err != nil {
		return err
	}
	return nil
}

func (f *fakeDriver) Unexpose(_ context.Context, rec *publish.Record, _ *profile.Profile,
	req expose.Request) error {
	f.rig.note("unexpose %s <- %s", req.ServerID, rec.Generation)
	return nil
}

func held(name string) []consumer.Holder {
	return []consumer.Holder{{
		Kind: consumer.KindMount, MountPoint: "/var/lib/pterodactyl/volumes/x/Engine",
		Device: consumer.Device{Major: 0, Minor: 42}, PID: 1234, ContainerName: name,
	}}
}

// --- the order ------------------------------------------------------------

// The whole content of this package. Publish the new generation, move every
// server onto it, record it, and only then take the old one down.
func TestActivatePublishesExposesRecordsThenTearsDown(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a", "server-b")
	r.release("rel-2")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatalf("Activate: %v", err)
	}

	gen1, gen2 := publish.GenerationID("rel-1"), publish.GenerationID("rel-2")
	want := []string{
		"publish rel-2",
		"unexpose server-a <- " + gen1,
		"expose server-a -> " + gen2 + " (assignment=rel-1)",
		"unexpose server-b <- " + gen1,
		"expose server-b -> " + gen2 + " (assignment=rel-1)",
		"teardown " + gen1 + " (assignment=rel-2)",
	}
	if !reflect.DeepEqual(r.log, want) {
		t.Fatalf("the sequence was\n  %s\nwant\n  %s",
			strings.Join(r.log, "\n  "), strings.Join(want, "\n  "))
	}
}

// Per server, not all-unexpose-then-all-expose. A failure halfway through the
// second loop would otherwise leave the earlier servers with nothing bound at
// all, which is worse than leaving them where they were.
func TestActivateMovesEachServerBeforeStartingTheNext(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a", "server-b")
	r.release("rel-2")
	r.drv.exposeErr = map[string]error{"server-b": errors.New("mount refused")}

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err == nil {
		t.Fatal("Activate reported success despite a failed exposure")
	}
	// server-a made it all the way over; server-b was unexposed and failed to
	// re-expose. What must NOT have happened is server-a being unexposed and
	// left that way while the operation worked on server-b.
	if !reflect.DeepEqual(r.log[1:4], []string{
		"unexpose server-a <- " + publish.GenerationID("rel-1"),
		"expose server-a -> " + publish.GenerationID("rel-2") + " (assignment=rel-1)",
		"unexpose server-b <- " + publish.GenerationID("rel-1"),
	}) {
		t.Errorf("servers were not moved one at a time: %v", r.log)
	}
	// And a failed activation records nothing and tears nothing down: the
	// assignment still names the release the surviving mounts came from.
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a failed activation recorded %q", got)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "teardown") {
			t.Errorf("a failed activation tore down the old generation: %v", r.log)
		}
	}
}

// The crash window, asserted from inside the teardown: by the time the old
// generation goes, the durable record already names the new release. Written
// any later, a crash would leave an assignment naming a generation that no
// longer exists while the servers are already on the new one.
func TestTheAssignmentIsRecordedBeforeTheOldGenerationGoes(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatal(err)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "teardown") && !strings.Contains(step, "assignment=rel-2") {
			t.Errorf("the old generation was torn down while the record still said the "+
				"old release: %q", step)
		}
		if strings.HasPrefix(step, "expose") && !strings.Contains(step, "assignment=rel-1") {
			t.Errorf("the record was updated before the servers were moved onto the new "+
				"generation: %q — a crash there leaves a release nothing has mounted", step)
		}
	}
}

// --- oracle 24 for activate and rollback ----------------------------------

// The same hazard teardown refuses, arriving by a different route: activate
// swaps what a running server is reading underneath it.
func TestActivateIsRefusedWhileAConsumerHoldsTheLiveGeneration(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	r.pub.holders = map[string][]consumer.Holder{
		publish.GenerationID("rel-1"): held("soulmask-1"),
	}

	_, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal while a consumer holds the live generation, got %v", err)
	}
	if !strings.Contains(err.Error(), "soulmask-1") {
		t.Errorf("the refusal does not name the holder: %v", err)
	}
	// Nothing was attempted. The refusal comes before the new generation is
	// published, so it costs nothing and changes nothing.
	if len(r.log) != 0 {
		t.Errorf("a refused activation did %v", r.log)
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a refused activation recorded %q", got)
	}
}

// A release that does not exist must be refused before anything is torn down.
// Loading it is the cheapest possible check, and doing it first is what keeps
// a typo from costing a running generation.
func TestActivateChecksTheReleaseExistsBeforeTouchingAnything(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-typo", r.prof); err == nil {
		t.Fatal("activating a release that does not exist reported success")
	}
	if len(r.log) != 0 {
		t.Errorf("activating a nonexistent release did %v", r.log)
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("the assignment moved to %q", got)
	}
}

// --- rollback -------------------------------------------------------------

func TestRollbackActivatesTheReleaseBeforeTheLastChange(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatal(err)
	}
	rec, err := r.ctl.Rollback(ctx(), "op-2", r.prof)
	if err != nil {
		t.Fatalf("Rollback: %v", err)
	}
	if rec.ReleaseID != "rel-1" {
		t.Errorf("rollback went to %q, want rel-1", rec.ReleaseID)
	}
	a := r.loadAssignment()
	if a.Release != "rel-1" || a.Previous != "rel-2" {
		t.Errorf("after rollback: release %q previous %q", a.Release, a.Previous)
	}
}

// A rollback with nowhere to go must say so rather than doing something
// plausible. There is no "the release before the first one".
func TestRollbackWithNoPreviousReleaseIsRefusedAndSaysWhy(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")

	_, err := r.ctl.Rollback(ctx(), "op-1", r.prof)
	if err == nil {
		t.Fatal("rollback with no previous release reported success")
	}
	if !strings.Contains(err.Error(), "activate a release by id") {
		t.Errorf("the error does not name what to do instead: %v", err)
	}
}

// --- attach and detach ----------------------------------------------------

// Attach ADDS a consumer, so the dangerous crash is a record claiming a
// server is attached when nothing is bound — a boot would then believe it had
// restored something it never mounted.
func TestAttachExposesBeforeItRecords(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1")

	if err := r.ctl.Attach(ctx(), "op-1", "server-a", "ro", r.prof); err != nil {
		t.Fatalf("Attach: %v", err)
	}
	if len(r.log) != 1 || !strings.HasPrefix(r.log[0], "expose server-a") {
		t.Fatalf("attach did %v", r.log)
	}
	if srv, ok := r.loadAssignment().Server("server-a"); !ok || srv.Access != "ro" {
		t.Errorf("attach did not record the server: %+v", r.loadAssignment())
	}
}

func TestAFailedAttachRecordsNothing(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1")
	r.drv.exposeErr = map[string]error{"server-a": errors.New("propagation refused")}

	if err := r.ctl.Attach(ctx(), "op-1", "server-a", "ro", r.prof); err == nil {
		t.Fatal("a failed exposure reported success")
	}
	if _, ok := r.loadAssignment().Server("server-a"); ok {
		t.Error("a server that was never exposed is recorded as attached; a boot would " +
			"believe it had restored something it never mounted")
	}
}

// Detach REMOVES a consumer, where the dangerous crash is the mirror: a
// record saying the server is gone while its mounts are still there, because
// nothing would ever go looking for them again.
func TestDetachUnexposesBeforeItRecords(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")

	if err := r.ctl.Detach(ctx(), "op-1", "server-a", r.prof); err != nil {
		t.Fatalf("Detach: %v", err)
	}
	if len(r.log) != 1 || !strings.HasPrefix(r.log[0], "unexpose server-a") {
		t.Fatalf("detach did %v", r.log)
	}
	if _, ok := r.loadAssignment().Server("server-a"); ok {
		t.Error("detach did not remove the server from the assignment")
	}
}

func TestDetachingAServerThatIsNotAttachedSaysSo(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1")
	if err := r.ctl.Detach(ctx(), "op-1", "server-a", r.prof); err == nil {
		t.Fatal("detaching a server that was never attached reported success")
	}
}

// --- releasing the generation ---------------------------------------------

// Tearing the generation down is not a reconfiguration. The assignment keeps
// its servers and its release, because "assigned but not published" is the
// state every profile is in after a reboot and before the boot path runs.
func TestReleaseGenerationKeepsTheAssignment(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a", "server-b")

	if err := r.ctl.ReleaseGeneration(ctx(), "op-1", r.prof); err != nil {
		t.Fatalf("ReleaseGeneration: %v", err)
	}
	gen := publish.GenerationID("rel-1")
	want := []string{
		"unexpose server-a <- " + gen,
		"unexpose server-b <- " + gen,
		"teardown " + gen + " (assignment=rel-1)",
	}
	if !reflect.DeepEqual(r.log, want) {
		t.Fatalf("the sequence was %v, want %v", r.log, want)
	}
	a := r.loadAssignment()
	if a.Release != "rel-1" || len(a.Servers) != 2 {
		t.Errorf("teardown changed the assignment to %+v; it records intent, and nothing "+
			"about the intent changed", a)
	}
}

// --- the lock -------------------------------------------------------------

// Two operations interleaving would each succeed at steps that contradict the
// other's: two generations published, a server exposed to one and the other
// torn down, an assignment naming a release nothing has mounted.
func TestASecondOperationIsRefusedWhileOneHoldsTheLock(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")

	lock, err := Acquire(r.cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = lock.Release() }()

	_, err = r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof)
	var busy *BusyError
	if !errors.As(err, &busy) {
		t.Fatalf("want a *BusyError while the lock is held, got %v", err)
	}
	if !IsRefusal(err) {
		t.Error("a busy lock is a refusal, not a fault: nothing is broken and retrying works")
	}
	if len(r.log) != 0 {
		t.Errorf("an operation that could not take the lock did %v", r.log)
	}

	// And it lifts. A lock that never let go would be indistinguishable from
	// one nobody could take.
	if err := lock.Release(); err != nil {
		t.Fatal(err)
	}
	if _, err := r.ctl.Activate(ctx(), "op-2", "rel-2", r.prof); err != nil {
		t.Fatalf("the operation was still refused after the lock was released: %v", err)
	}
}

// --- the journal ----------------------------------------------------------

func TestARefusedOperationIsJournaledAsARefusal(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	r.pub.holders = map[string][]consumer.Holder{
		publish.GenerationID("rel-1"): held("soulmask-1"),
	}

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err == nil {
		t.Fatal("want a refusal")
	}
	op, err := journal.LoadOperation(r.cfg.JournalDir(), "op-1")
	if err != nil {
		t.Fatal(err)
	}
	var refused bool
	for _, e := range op.Events {
		if e.Outcome == journal.OutcomeRefused && e.Phase == PhasePlan {
			refused = true
		}
		if e.Outcome == journal.OutcomeFailed {
			t.Errorf("a refusal was journaled as a fault: %+v", e)
		}
	}
	if !refused {
		t.Error("the refusal is not in the journal")
	}
}
