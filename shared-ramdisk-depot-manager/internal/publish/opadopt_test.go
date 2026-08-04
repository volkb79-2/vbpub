package publish

import (
	"os"
	"reflect"
	"testing"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// crashedPlanFixture builds a Record the same way Publish assembles one and
// writes it as an operation plan, WITHOUT publishing anything for real and
// WITHOUT ever writing the published record — exactly the state a process
// that died between writePlan and writeRecord leaves behind.
func crashedPlanFixture(t *testing.T) (cfg config.Config, jnl *journal.Journal,
	prof *profile.Profile, rel *store.Release, built *Record) {
	t.Helper()
	cfg = testConfig(t)
	jnl = testJournal(t, cfg)
	prof = testProfile(t)
	rel = stagedRelease(t, cfg, jnl, prof)

	p, _ := newPublisher(t, cfg, jnl)
	gen := GenerationID(rel.ID)
	const opID = "op-crashed"
	slice, err := hold.SliceName(cfg.Slice, gen)
	if err != nil {
		t.Fatal(err)
	}
	built = &Record{
		SchemaVersion: RecordSchemaVersion, Generation: gen, ReleaseID: rel.ID,
		Profile: prof.ID, OperationID: opID, ContentDigest: rel.Manifest.ContentDigest,
		Slice: slice,
	}
	for _, class := range managedClasses(prof) {
		cr, err := p.describeClass(opID, prof.ID, gen, rel, class)
		if err != nil {
			t.Fatal(err)
		}
		built.Classes = append(built.Classes, *cr)
	}
	if err := p.writePlan(built); err != nil {
		t.Fatal(err)
	}
	return cfg, jnl, prof, rel, built
}

// A plan whose every class is mounted, read-only and held is the operation
// finishing in every way that matters — only the record write never
// happened, and adoption is what performs it.
func TestAdoptOrQuarantineAdoptsAFullyRealizedPlan(t *testing.T) {
	cfg, jnl, prof, _, built := crashedPlanFixture(t)

	log := &opLog{}
	p2, err := New(cfg, jnl,
		WithMounter(&fakeMounter{log: log, mountErr: map[string]error{}}),
		WithHolder(newFakeHolder(log)),
		WithMountInfoPath(writeMountInfo(t, mountedLines(built)...)))
	if err != nil {
		t.Fatal(err)
	}

	report, err := p2.AdoptOrQuarantine(ctx(), "recovery-op")
	if err != nil {
		t.Fatal(err)
	}
	want := prof.ID + "/" + built.Generation
	if !reflect.DeepEqual(report.Adopted, []string{want}) {
		t.Fatalf("Adopted = %v, want %v", report.Adopted, []string{want})
	}
	if len(report.Quarantined) != 0 {
		t.Fatalf("a fully realized plan was quarantined: %v", report.Quarantined)
	}

	if _, err := os.Stat(cfg.OpPlanFile(built.OperationID)); !os.IsNotExist(err) {
		t.Errorf("the plan survived adoption: %v", err)
	}
	rec, err := LoadRecordAt(cfg, prof.ID, built.Generation)
	if err != nil {
		t.Fatalf("adoption did not write the published record: %v", err)
	}
	if rec.ReleaseID != built.ReleaseID || len(rec.Classes) != len(built.Classes) {
		t.Errorf("the adopted record does not match the plan: %+v", rec)
	}
}

// A plan missing even one class's completeness is not partial credit: the
// whole operation is torn down, the same way Publish's own crash cleanup
// tears itself down when the process survives to run it.
func TestAdoptOrQuarantineTearsDownAnIncompletePlan(t *testing.T) {
	cfg, jnl, prof, _, built := crashedPlanFixture(t)

	// Only the op tmpfs of each class is mounted — the bind to the visible
	// path never happened, exactly like a publication interrupted mid-flight.
	var lines []string
	id := 40
	for _, c := range built.Classes {
		lines = append(lines, mountLine(id, "/", c.OpMount, false))
		id++
	}

	log := &opLog{}
	h := newFakeHolder(log)
	m := &fakeMounter{log: log, mountErr: map[string]error{}}
	p2, err := New(cfg, jnl,
		WithMounter(m), WithHolder(h),
		WithMountInfoPath(writeMountInfo(t, lines...)))
	if err != nil {
		t.Fatal(err)
	}

	report, err := p2.AdoptOrQuarantine(ctx(), "recovery-op")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(report.Quarantined, []string{built.OperationID}) {
		t.Fatalf("Quarantined = %v, want %v", report.Quarantined, []string{built.OperationID})
	}
	if len(report.Adopted) != 0 {
		t.Fatalf("an incomplete plan was adopted: %v", report.Adopted)
	}

	for _, c := range built.Classes {
		if !containsString(h.forgotten, c.HoldUnit) {
			t.Errorf("quarantine did not forget hold unit %s (forgotten: %v)", c.HoldUnit, h.forgotten)
		}
	}
	if !containsString(h.released, built.Slice) {
		t.Errorf("quarantine did not release slice %s (released: %v)", built.Slice, h.released)
	}
	if _, err := os.Stat(cfg.OpDir(built.OperationID)); !os.IsNotExist(err) {
		t.Errorf("the operation directory survived quarantine: %v", err)
	}
	if _, err := LoadRecordAt(cfg, prof.ID, built.Generation); err == nil {
		t.Error("quarantine wrote a published record for an incomplete plan")
	}
}

// A plan that does not even parse cannot be trusted for its own class list,
// so it falls back to sweeping by path — but it must never widen that sweep
// to another operation's directory.
func TestAdoptOrQuarantineSweepsAnUnparsablePlanByPathAlone(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)

	garbageDir := cfg.OpDir("op-garbage")
	if err := os.MkdirAll(garbageDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.OpPlanFile("op-garbage"), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	garbageMount := cfg.OpClassDir("op-garbage", "pak")
	if err := os.MkdirAll(garbageMount, 0o700); err != nil {
		t.Fatal(err)
	}

	// A second, healthy operation directory sharing the same OpRoot — proof
	// the narrow sweep never touches it.
	healthyMount := cfg.OpClassDir("op-healthy", "pak")
	if err := os.MkdirAll(healthyMount, 0o700); err != nil {
		t.Fatal(err)
	}

	log := &opLog{}
	m := &fakeMounter{log: log, mountErr: map[string]error{}}
	p, err := New(cfg, jnl,
		WithMounter(m), WithHolder(newFakeHolder(log)),
		WithMountInfoPath(writeMountInfo(t,
			mountLine(40, "/", garbageMount, false),
			mountLine(41, "/", healthyMount, false))))
	if err != nil {
		t.Fatal(err)
	}

	report, err := p.AdoptOrQuarantine(ctx(), "recovery-op")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(report.Quarantined, []string{"op-garbage"}) {
		t.Fatalf("Quarantined = %v, want [op-garbage]", report.Quarantined)
	}
	if !containsString(m.unmounted, garbageMount) {
		t.Errorf("the garbage operation's own mount was not swept: %v", m.unmounted)
	}
	for _, u := range m.unmounted {
		if u == healthyMount {
			t.Fatalf("sweeping the garbage plan touched a different operation's mount: %v", m.unmounted)
		}
	}
	if _, err := os.Stat(healthyMount); err != nil {
		t.Errorf("the healthy operation's directory was removed: %v", err)
	}
}

func TestAdoptOrQuarantineOnAnEmptyHost(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	log := &opLog{}
	p, err := New(cfg, jnl,
		WithMounter(&fakeMounter{log: log, mountErr: map[string]error{}}),
		WithHolder(newFakeHolder(log)))
	if err != nil {
		t.Fatal(err)
	}
	report, err := p.AdoptOrQuarantine(ctx(), "recovery-op")
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Adopted)+len(report.Quarantined) != 0 {
		t.Fatalf("a host with no crashed operations produced work: %+v", report)
	}
}

// A published operation removes its own plan once the record is written, so
// a normal, successful Publish leaves nothing for adoption to find.
func TestASuccessfulPublishLeavesNoPlanBehind(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, _ := withTable(mountedLines(rec)...)

	if _, err := os.Stat(p.cfg.OpPlanFile(rec.OperationID)); !os.IsNotExist(err) {
		t.Errorf("a completed publish left its plan behind: %v", err)
	}
}

func TestIsCompleteAgreesWithReconcile(t *testing.T) {
	rec, withTable := publishedFixture(t)

	healthy, _ := withTable(mountedLines(rec)...)
	ok, err := healthy.IsComplete(ctx(), rec)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Error("IsComplete reported false for a fully mounted, held generation")
	}

	broken, _ := withTable(mountLine(90, "/", "/unrelated", false))
	ok, err = broken.IsComplete(ctx(), rec)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Error("IsComplete reported true for a generation with nothing mounted")
	}
}

func TestRepairReadOnlyRemountsInPlace(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	log := &opLog{}
	m := &fakeMounter{log: log, mountErr: map[string]error{}}
	p, err := New(cfg, jnl, WithMounter(m), WithHolder(newFakeHolder(log)))
	if err != nil {
		t.Fatal(err)
	}

	target := "/run/srdm/testgame/deadbeef/pak"
	if err := p.RepairReadOnly("op-repair", target); err != nil {
		t.Fatal(err)
	}
	if len(m.calls) != 1 {
		t.Fatalf("RepairReadOnly issued %d mount calls, want 1: %v", len(m.calls), m.calls)
	}
	call := m.calls[0]
	if call.Target != target || !call.Bind() || !call.Remount() || !call.ReadOnly() {
		t.Errorf("RepairReadOnly did not remount %s bind+remount+ro: %+v", target, call)
	}
}

func TestClearForRepublishRefusesWhileHeld(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, f := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}
	f.guard.report = consumer.Report{Holders: []consumer.Holder{{
		Kind: consumer.KindMount, MountPoint: "/home/container/paks",
		Device: consumer.Device{Major: 0, Minor: 50}, PID: 4242,
		ContainerName: "soulmask-01",
	}}}

	if _, err := p.ClearForRepublish(ctx(), "op-clear", rec.Profile, rec.Generation); err == nil {
		t.Fatal("ClearForRepublish proceeded while a consumer held the generation")
	}
	if _, err := LoadRecordAt(p.cfg, rec.Profile, rec.Generation); err != nil {
		t.Errorf("a refused ClearForRepublish removed the record anyway: %v", err)
	}
}

func TestClearForRepublishTearsDownAndRemovesTheRecord(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, h := withTable(mountedLines(rec)...)

	cleared, err := p.ClearForRepublish(ctx(), "op-clear", rec.Profile, rec.Generation)
	if err != nil {
		t.Fatal(err)
	}
	if cleared.ReleaseID != rec.ReleaseID {
		t.Errorf("ClearForRepublish returned release %q, want %q", cleared.ReleaseID, rec.ReleaseID)
	}
	if _, err := LoadRecordAt(p.cfg, rec.Profile, rec.Generation); err == nil {
		t.Error("the published record survived ClearForRepublish")
	}
	for _, c := range rec.Classes {
		if !containsString(h.forgotten, c.HoldUnit) {
			t.Errorf("ClearForRepublish did not forget hold unit %s", c.HoldUnit)
		}
	}
}
