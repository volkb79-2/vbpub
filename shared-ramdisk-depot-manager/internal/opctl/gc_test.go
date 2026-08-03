package opctl

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"srdm/internal/publish"
)

// releases promotes several, newest id last.
func (r *rig) releases(ids ...string) {
	r.t.Helper()
	for _, id := range ids {
		r.release(id)
	}
}

func (r *rig) storeHas(id string) bool {
	r.t.Helper()
	_, err := os.Stat(r.cfg.ReleaseDir(id))
	return err == nil
}

// Retention is a floor on what is kept, not a cap on what exists. With five
// unpinned releases and a retention of three, the two oldest go.
func TestGCKeepsTheConfiguredNumberOfUnpinnedReleases(t *testing.T) {
	r := newRig(t)
	r.cfg.Retention = 3
	r.ctl.cfg.Retention = 3
	r.releases("rel-1", "rel-2", "rel-3", "rel-4", "rel-5")

	res, err := r.ctl.GC("op-gc", false)
	if err != nil {
		t.Fatalf("GC: %v", err)
	}
	if !reflect.DeepEqual(res.Removed, []string{"rel-1", "rel-2"}) {
		t.Fatalf("removed %v, want the two oldest", res.Removed)
	}
	for _, id := range []string{"rel-3", "rel-4", "rel-5"} {
		if !r.storeHas(id) {
			t.Errorf("%s was collected but should have been retained", id)
		}
		if res.Kept[id] != KeptRetention {
			t.Errorf("%s was kept for %q, want retention", id, res.Kept[id])
		}
	}
	for _, id := range res.Removed {
		if r.storeHas(id) {
			t.Errorf("%s is reported as removed but is still on disk", id)
		}
	}
}

// The three pins, each for a different reason, and each stronger than
// retention: what an operator declared, what they can roll back to, and what
// is live right now whatever anybody declared.
func TestGCNeverCollectsWhatIsAssignedRollableOrPublished(t *testing.T) {
	r := newRig(t)
	r.cfg.Retention = 1
	r.ctl.cfg.Retention = 1
	r.releases("rel-1", "rel-2", "rel-3", "rel-4", "rel-5")

	// rel-2 is the rollback target, rel-3 is assigned, and rel-1 has a live
	// generation nobody assigned — an activation that was rolled back but
	// whose generation has not been torn down yet.
	a := r.loadAssignment()
	a.SetRelease("rel-2")
	a.SetRelease("rel-3")
	if err := r.asg.Save(a); err != nil {
		t.Fatal(err)
	}
	r.pub.publishRecord("rel-1")
	writeRecord(t, r, "rel-1")

	res, err := r.ctl.GC("op-gc", false)
	if err != nil {
		t.Fatalf("GC: %v", err)
	}

	for id, want := range map[string]string{
		"rel-3": KeptAssigned,
		"rel-2": KeptRollback,
		"rel-1": KeptPublished,
	} {
		if res.Kept[id] != want {
			t.Errorf("%s was kept for %q, want %q", id, res.Kept[id], want)
		}
		if !r.storeHas(id) {
			t.Errorf("%s was collected", id)
		}
	}
	// Retention keeps one more beyond the pins, so only the oldest unpinned
	// release goes.
	if !reflect.DeepEqual(res.Removed, []string{"rel-4"}) {
		t.Errorf("removed %v, want only rel-4", res.Removed)
	}
}

// A published release outranks every declaration. The pages exist whatever
// anybody meant, and collecting the release they were built from is how a
// republish after a reboot finds nothing to republish from.
func TestGCKeepsAPublishedReleaseNobodyAssigned(t *testing.T) {
	r := newRig(t)
	r.cfg.Retention = 1
	r.ctl.cfg.Retention = 1
	r.releases("rel-1", "rel-2", "rel-3")
	writeRecord(t, r, "rel-1")

	res, err := r.ctl.GC("op-gc", false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Kept["rel-1"] != KeptPublished {
		t.Errorf("a published release was kept for %q", res.Kept["rel-1"])
	}
	if !r.storeHas("rel-1") {
		t.Error("a release with a live generation was collected")
	}
}

// A channel is a name somebody else may resolve. Collecting what it points at
// turns a working reference into a dangling one.
func TestGCKeepsAChannelTarget(t *testing.T) {
	r := newRig(t)
	r.cfg.Retention = 1
	r.ctl.cfg.Retention = 1
	r.releases("rel-1", "rel-2", "rel-3")
	if err := r.st.Activate("op-flip", r.prof.ID, "stable", "rel-1"); err != nil {
		t.Fatal(err)
	}

	res, err := r.ctl.GC("op-gc", false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Kept["rel-1"] != KeptChannel {
		t.Errorf("a channel target was kept for %q", res.Kept["rel-1"])
	}
	if !r.storeHas("rel-1") {
		t.Error("a channel target was collected, leaving the channel dangling")
	}
}

// A collector whose decisions cannot be inspected before it acts is one
// nobody will run on a node that matters.
func TestGCDryRunReportsAndRemovesNothing(t *testing.T) {
	r := newRig(t)
	r.cfg.Retention = 1
	r.ctl.cfg.Retention = 1
	r.releases("rel-1", "rel-2", "rel-3")

	res, err := r.ctl.GC("op-gc", true)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.Removed, []string{"rel-1", "rel-2"}) {
		t.Fatalf("a dry run reported %v", res.Removed)
	}
	for _, id := range res.Removed {
		if !r.storeHas(id) {
			t.Errorf("a dry run removed %s", id)
		}
	}
}

// Retention below one would mean "keep nothing beyond what is pinned", which
// is a coherent policy nobody wants by accident. Refusing it keeps gc from
// ever being destructive because a field was left unset.
func TestAZeroRetentionIsRefusedByConfiguration(t *testing.T) {
	r := newRig(t)
	cfg := r.cfg
	cfg.Retention = 0
	if err := cfg.Validate(); err == nil {
		t.Fatal("a retention of zero was accepted")
	} else if !strings.Contains(err.Error(), "at least one") {
		t.Errorf("the error does not say what the minimum is: %v", err)
	}
}

// --- status ---------------------------------------------------------------

// Declared intent beside measured reality. The pair is the point: a status
// that reported only one of them would be a status that cannot show a
// disagreement, which is the only thing anybody looks at it for.
func TestStatusReportsIntentBesideReality(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatal(err)
	}

	all, err := r.ctl.Status(ctx())
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 1 {
		t.Fatalf("status returned %d profiles", len(all))
	}
	s := all[0]
	if s.Release != "rel-2" || s.Previous != "rel-1" {
		t.Errorf("status says release %q previous %q", s.Release, s.Previous)
	}
	if s.Generation != publish.GenerationID("rel-2") || !s.Published {
		t.Errorf("status says generation %q published=%v", s.Generation, s.Published)
	}
	if !reflect.DeepEqual(s.Servers, []string{"server-a"}) {
		t.Errorf("status says servers %v", s.Servers)
	}
}

// A profile whose generation has been torn down is assigned and not
// published, and saying so is the whole job — that is exactly the state a
// boot has to notice and repair.
func TestStatusDistinguishesAssignedFromPublished(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	if err := r.ctl.ReleaseGeneration(ctx(), "op-1", r.prof); err != nil {
		t.Fatal(err)
	}

	all, err := r.ctl.Status(ctx())
	if err != nil {
		t.Fatal(err)
	}
	if all[0].Release != "rel-1" {
		t.Errorf("status forgot the assignment: %+v", all[0])
	}
	if all[0].Published {
		t.Error("status reports a torn-down generation as published")
	}
}

// writeRecord puts a published-state record on disk, which is what
// publish.ListRecordsAt reads and therefore what gc's published pin sees.
func writeRecord(t *testing.T, r *rig, releaseID string) {
	t.Helper()
	rec := r.pub.publishRecord(releaseID)
	body := `{"schema_version":` + itoa(publish.RecordSchemaVersion) +
		`,"generation":"` + rec.Generation + `","release_id":"` + releaseID +
		`","profile":"testgame","operation_id":"op","content_digest":"",` +
		`"slice":"srdm-` + rec.Generation + `.slice","classes":[]}`
	path := r.cfg.PublishedRecord("testgame", rec.Generation)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}
