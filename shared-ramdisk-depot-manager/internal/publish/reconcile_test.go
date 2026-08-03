package publish

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// mountLine renders one mountinfo entry for a synthetic table.
func mountLine(id int, root, point string, ro bool) string {
	opts := "rw,nosuid,nodev"
	if ro {
		opts = "ro,nosuid,nodev"
	}
	return fmt.Sprintf("%d 30 0:%d %s %s %s - tmpfs tmpfs rw,size=67108864,mode=755",
		id, id+100, root, point, opts)
}

// writeMountInfo materializes a synthetic mount table.
func writeMountInfo(t *testing.T, lines ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "mountinfo")
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// publishedFixture produces a real record, then lets the caller describe
// whatever mount table and unit state they want to reconcile it against.
func publishedFixture(t *testing.T) (*Record, func(...string) (*Publisher, *fakeHolder)) {
	t.Helper()
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	prof := testProfile(t)
	rel := stagedRelease(t, cfg, jnl, prof)
	p, _ := newPublisher(t, cfg, jnl)

	rec, err := p.Publish(ctx(), "op-1", "testgame", rel, prof)
	if err != nil {
		t.Fatal(err)
	}

	withTable := func(lines ...string) (*Publisher, *fakeHolder) {
		log := &opLog{}
		h := newFakeHolder(log)
		np, err := New(cfg, jnl,
			WithMounter(&fakeMounter{log: log, mountErr: map[string]error{}}),
			WithHolder(h),
			WithMountInfoPath(writeMountInfo(t, lines...)))
		if err != nil {
			t.Fatal(err)
		}
		return np, h
	}
	return rec, withTable
}

// mountedLines is the table a healthy generation produces.
func mountedLines(rec *Record) []string {
	var lines []string
	id := 40
	for _, c := range rec.Classes {
		lines = append(lines,
			mountLine(id, "/", c.OpMount, false),
			mountLine(id+1, "/root", c.ExposePath, true))
		id += 2
	}
	return lines
}

func classByName(rec *Record, name string) ClassRecord {
	for _, c := range rec.Classes {
		if c.Name == name {
			return c
		}
	}
	return ClassRecord{}
}

// Record, mounts and holds all present: nothing to do.
func TestReconcileHealthyWhenEverySourceAgrees(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, _ := withTable(mountedLines(rec)...)

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.Healthy, []string{"testgame/" + rec.Generation}) {
		t.Fatalf("Healthy = %v", res.Healthy)
	}
	if len(res.NeedsRepublish)+len(res.Orphans)+len(res.NotReadOnly)+len(res.Unheld) != 0 {
		t.Fatalf("a healthy generation produced work: %+v", res)
	}
}

// After a reboot every record is in this state: the durable record survived
// and every mount is gone. A record alone proves nothing about topology.
func TestReconcileNeedsRepublishWhenMountsAreGone(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, _ := withTable(mountLine(40, "/", "/unrelated", false))

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.NeedsRepublish, []string{"testgame/" + rec.Generation}) {
		t.Fatalf("NeedsRepublish = %v, want the generation whose mounts vanished", res.NeedsRepublish)
	}
	if len(res.Healthy) != 0 {
		t.Errorf("a generation with no mounts was called healthy: %v", res.Healthy)
	}
}

// The op tmpfs is there but the bind never happened: publication was
// interrupted between the two.
func TestReconcileNeedsRepublishWhenOnlyTheOpMountSurvives(t *testing.T) {
	rec, withTable := publishedFixture(t)

	var lines []string
	id := 40
	for _, c := range rec.Classes {
		lines = append(lines, mountLine(id, "/", c.OpMount, false))
		id++
	}
	p, _ := withTable(lines...)

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if len(res.NeedsRepublish) != 1 {
		t.Fatalf("NeedsRepublish = %v", res.NeedsRepublish)
	}
}

// A bind that exists but is writable means publication died between the
// bind and the remount — the one window in which a consumer could write to
// what is meant to be immutable content.
func TestReconcileFlagsAWritableExposure(t *testing.T) {
	rec, withTable := publishedFixture(t)
	pak := classByName(rec, "pak")
	code := classByName(rec, "code")

	p, _ := withTable(
		mountLine(40, "/", pak.OpMount, false),
		mountLine(41, "/root", pak.ExposePath, false), // NOT read-only
		mountLine(42, "/", code.OpMount, false),
		mountLine(43, "/root", code.ExposePath, true),
	)

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.NotReadOnly, []string{pak.ExposePath}) {
		t.Fatalf("NotReadOnly = %v, want the writable bind", res.NotReadOnly)
	}
	if len(res.Healthy) != 0 {
		t.Error("a generation with a writable exposure was called healthy")
	}
	if len(res.NeedsRepublish) != 1 {
		t.Errorf("a writable exposure did not schedule a republish: %v", res.NeedsRepublish)
	}
}

// Fully mounted and NOT held. This is the state that the mount table alone
// cannot see: the content is all there and readable, and every page of it is
// charged to a cgroup that has been removed — so the class floor protects
// nothing and the memory is attributable to nobody.
func TestReconcileFlagsAClassWhoseHoldUnitIsGone(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, h := withTable(mountedLines(rec)...)

	pak := classByName(rec, "pak")
	h.inactive[pak.HoldUnit] = true

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.Unheld, []string{pak.HoldUnit}) {
		t.Fatalf("Unheld = %v, want the class whose hold unit stopped", res.Unheld)
	}
	if len(res.Healthy) != 0 {
		t.Error("a generation whose content is charged to a dead cgroup was called healthy")
	}
	if len(res.NeedsRepublish) != 1 {
		t.Errorf("an unheld class did not schedule a republish: %v", res.NeedsRepublish)
	}
	// And it is NOT reported as a missing mount, because it is not one — the
	// repair is a republish, but the diagnosis has to be right or the next
	// person debugging looks at the mount table and finds nothing wrong.
	if len(res.NotReadOnly) != 0 {
		t.Errorf("an unheld class was also reported as a writable exposure: %v", res.NotReadOnly)
	}
}

// systemd being unreachable is not evidence that anything is wrong.
// Concluding "unheld" from it would schedule the teardown and republish of a
// perfectly healthy generation — including the unmount that actually frees
// the memory.
func TestReconcileRefusesToGuessWhenSystemdIsUnreachable(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, h := withTable(mountedLines(rec)...)
	h.activeErr = errors.New("failed to connect to bus")

	if _, err := p.Reconcile(ctx(), "op-r"); err == nil {
		t.Fatal("reconciliation reached a verdict with systemd unreachable")
	}
}

// A mount nobody recorded is one nobody can reason about, and it is holding
// memory. Tear it down.
func TestReconcileFindsOrphanMounts(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	log := &opLog{}
	m := &fakeMounter{log: log, mountErr: map[string]error{}}

	orphanOp := cfg.OpClassDir("op-ancient", "pak")
	orphanExpose := cfg.ExposeClassDir("testgame", "deadbeef", "pak")
	path := writeMountInfo(t,
		mountLine(40, "/", orphanOp, false),
		mountLine(41, "/root", orphanExpose, true),
		mountLine(42, "/", "/elsewhere", false),
	)
	p, err := New(cfg, jnl, WithMounter(m), WithHolder(newFakeHolder(log)), WithMountInfoPath(path))
	if err != nil {
		t.Fatal(err)
	}

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	// Deepest first, so tearing them down never blocks on a child.
	want := []string{orphanOp, orphanExpose}
	sortedDesc(want)
	if !reflect.DeepEqual(res.Orphans, want) {
		t.Fatalf("Orphans = %v, want %v", res.Orphans, want)
	}
	// A mount outside the srdm roots is somebody else's business.
	for _, o := range res.Orphans {
		if o == "/elsewhere" {
			t.Error("reconciliation claimed a mount outside the srdm roots")
		}
	}

	if err := p.TeardownOrphans("op-r", res); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(m.unmounted, want) {
		t.Fatalf("unmounted %v, want %v in that order", m.unmounted, want)
	}
}

func TestReconcileOnAnEmptyHost(t *testing.T) {
	cfg := testConfig(t)
	jnl := testJournal(t, cfg)
	log := &opLog{}
	p, err := New(cfg, jnl,
		WithMounter(&fakeMounter{log: log, mountErr: map[string]error{}}),
		WithHolder(newFakeHolder(log)),
		WithMountInfoPath(writeMountInfo(t, mountLine(40, "/", "/elsewhere", false))))
	if err != nil {
		t.Fatal(err)
	}

	res, err := p.Reconcile(ctx(), "op-r")
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Healthy)+len(res.NeedsRepublish)+len(res.Orphans)+len(res.NotReadOnly) != 0 {
		t.Fatalf("a host with no srdm state produced work: %+v", res)
	}
}

// A record srdm cannot parse must stop reconciliation rather than being
// skipped: silently ignoring it would classify its live mounts as orphans
// and tear down a healthy generation.
func TestReconcileRefusesAnUnreadableRecord(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, _ := withTable(mountLine(40, "/", "/elsewhere", false))

	path := p.cfg.PublishedRecord("testgame", rec.Generation)
	if err := os.WriteFile(path, []byte("{not json}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := p.Reconcile(ctx(), "op-r"); err == nil {
		t.Fatal("reconciliation ignored a record it could not read")
	}
}

// The record is the only durable thing that knows which unit holds a class,
// so a record from an older schema must be refused rather than read with the
// unit names missing — which would look exactly like a generation with no
// holds and schedule a republish of a healthy one.
func TestReconcileRefusesAnOlderRecordSchema(t *testing.T) {
	rec, withTable := publishedFixture(t)
	p, _ := withTable(mountedLines(rec)...)

	path := p.cfg.PublishedRecord("testgame", rec.Generation)
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	older := strings.Replace(string(body),
		fmt.Sprintf(`"schema_version": %d`, RecordSchemaVersion), `"schema_version": 1`, 1)
	if older == string(body) {
		t.Fatal("the record does not carry a schema version where this test expects one")
	}
	if err := os.WriteFile(path, []byte(older), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := p.Reconcile(ctx(), "op-r"); err == nil {
		t.Fatal("reconciliation accepted a record written by an older schema")
	}
}

func sortedDesc(s []string) {
	for i := range s {
		for j := i + 1; j < len(s); j++ {
			if s[j] > s[i] {
				s[i], s[j] = s[j], s[i]
			}
		}
	}
}
