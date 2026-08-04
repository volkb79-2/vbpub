package opctl

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"srdm/internal/consumer"
	"srdm/internal/publish"
)

// persistProfile writes the rig's profile to the durable copy D-026 keeps,
// so Restore (which has no --profile flag to fall back on) can find it by
// id alone — exactly as newOpEnv's persistProfile does for the real CLI.
func (r *rig) persistProfile() {
	r.t.Helper()
	body, err := json.Marshal(r.prof)
	if err != nil {
		r.t.Fatal(err)
	}
	if err := os.MkdirAll(r.cfg.ProfilesDir(), 0o755); err != nil {
		r.t.Fatal(err)
	}
	if err := os.WriteFile(r.cfg.ProfileDocument(r.prof.ID), body, 0o644); err != nil {
		r.t.Fatal(err)
	}
}

// Restore's whole point: an assignment that is already fully realized is
// still re-exposed, because the mount that is missing after a reboot is not
// the topology, it is the BIND into Wings' volume, which Reconcile never
// looks at (P06's driver owns that, not P03/P04's topology).
func TestRestoreReExposesEveryAssignedServerEvenWhenNothingNeedsRebuilding(t *testing.T) {
	r := newRig(t)
	r.persistProfile()
	r.assigned("rel-1", "server-a")
	r.log = nil

	rep, err := r.ctl.Restore(ctx(), "op-restore")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rep.Restored, []string{"testgame/rel-1"}) {
		t.Fatalf("Restored = %v, want [testgame/rel-1]", rep.Restored)
	}
	if len(rep.Failed) != 0 {
		t.Fatalf("Failed = %v", rep.Failed)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "publish") {
			t.Errorf("restore republished a generation that was already complete: %v", r.log)
		}
	}
	if r.log == nil || !strings.HasPrefix(r.log[len(r.log)-1], "expose server-a") {
		t.Errorf("restore did not re-expose the assigned server: %v", r.log)
	}
}

// The state every generation is actually in immediately after a reboot:
// the record survived (StateDir is persistent) but nothing the record names
// is mounted (RunDir is not). Restore has to rebuild it, not just adopt.
func TestRestoreRebuildsAGenerationThatDidNotSurviveTheReboot(t *testing.T) {
	r := newRig(t)
	r.persistProfile()
	r.assigned("rel-1", "server-a")
	gen := publish.GenerationID("rel-1")
	r.pub.incomplete = map[string]bool{gen: true}
	r.log = nil

	rep, err := r.ctl.Restore(ctx(), "op-restore")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rep.Restored, []string{"testgame/rel-1"}) {
		t.Fatalf("Restored = %v, want [testgame/rel-1]", rep.Restored)
	}
	var sawClear, sawPublish, sawExpose bool
	for _, step := range r.log {
		switch {
		case strings.HasPrefix(step, "clear"):
			sawClear = true
		case strings.HasPrefix(step, "publish"):
			sawPublish = true
		case strings.HasPrefix(step, "expose"):
			sawExpose = true
		}
	}
	if !sawClear || !sawPublish || !sawExpose {
		t.Fatalf("restore did not clear-then-rebuild-then-expose an incomplete generation: %v", r.log)
	}
}

// No durable profile document means restore cannot even classify the
// content it would republish — reported, never guessed past (D-026).
func TestRestoreFailsAProfileWithNoDurableDocument(t *testing.T) {
	r := newRig(t)
	// Deliberately no r.persistProfile().
	r.assigned("rel-1", "server-a")

	rep, err := r.ctl.Restore(ctx(), "op-restore")
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Restored) != 0 {
		t.Errorf("a profile with no durable document was restored anyway: %v", rep.Restored)
	}
	if _, ok := rep.Failed["testgame"]; !ok {
		t.Fatalf("Failed = %v, want an entry for testgame", rep.Failed)
	}
}

// Reconcile's ordering: crashed operations are resolved before the mount
// table is read, an assigned profile's own broken generation is reported
// rather than rebuilt (Activate owns rebuilding it), and anything else
// broken and unassigned is cleared for good.
func TestReconcileClearsUnassignedGenerationsAndReportsAssignedOnes(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	assignedGen := publish.GenerationID("rel-1")
	// A stale generation nothing is assigned to anymore — left over from a
	// release the profile has since moved off — keyed under its own
	// generation id rather than rel-old's real one, since only the map key
	// matters to the fake.
	staleGen := "deadbeef"
	r.pub.records[staleGen] = r.pub.publishRecord("rel-old")
	delete(r.pub.records, publish.GenerationID("rel-old"))

	r.pub.reconcileResult = &publish.Reconciliation{
		NeedsRepublish: []string{"testgame/" + assignedGen, "testgame/" + staleGen},
	}
	r.log = nil

	rep, err := r.ctl.Reconcile(ctx(), "op-reconcile")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rep.NeedsActivate, []string{"testgame/" + assignedGen}) {
		t.Fatalf("NeedsActivate = %v, want the assigned generation", rep.NeedsActivate)
	}
	if !reflect.DeepEqual(rep.Cleared, []string{"testgame/" + staleGen}) {
		t.Fatalf("Cleared = %v, want the unassigned generation", rep.Cleared)
	}
	if _, ok := r.pub.records[staleGen]; ok {
		t.Error("the stale, unassigned generation was not actually cleared")
	}
	if _, ok := r.pub.records[assignedGen]; !ok {
		t.Error("reconcile touched the assigned profile's own generation; that is Activate's job")
	}
	if len(r.log) == 0 || r.log[0] != "adopt-or-quarantine" {
		t.Fatalf("reconcile did not resolve crashed operations before reading the mount table: %v", r.log)
	}
}

// A generation still held by a live consumer is left alone even when it is
// unassigned and broken — an operator's call to schedule, not
// reconciliation's to force.
func TestReconcileLeavesAHeldGenerationAlone(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	staleGen := "deadbeef"
	r.pub.records[staleGen] = r.pub.publishRecord("rel-old")
	r.pub.clearErr = &consumer.HeldError{Generation: "testgame/" + staleGen}
	r.pub.reconcileResult = &publish.Reconciliation{NeedsRepublish: []string{"testgame/" + staleGen}}

	rep, err := r.ctl.Reconcile(ctx(), "op-reconcile")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rep.Blocked, []string{"testgame/" + staleGen}) {
		t.Fatalf("Blocked = %v, want the held generation", rep.Blocked)
	}
	if len(rep.Cleared) != 0 {
		t.Errorf("a held generation was cleared anyway: %v", rep.Cleared)
	}
}

func TestReconcileRepairsWritableExposuresAndRemovesOrphans(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.pub.reconcileResult = &publish.Reconciliation{
		NotReadOnly: []string{"/run/srdm/testgame/gen1/pak"},
		Orphans:     []string{"/run/srdm/.op/op-ancient/pak"},
	}

	rep, err := r.ctl.Reconcile(ctx(), "op-reconcile")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rep.ReadOnlyFixed, []string{"/run/srdm/testgame/gen1/pak"}) {
		t.Fatalf("ReadOnlyFixed = %v", rep.ReadOnlyFixed)
	}
	if !reflect.DeepEqual(rep.OrphansRemoved, []string{"/run/srdm/.op/op-ancient/pak"}) {
		t.Fatalf("OrphansRemoved = %v", rep.OrphansRemoved)
	}
}

func TestReconcileTakesTheLockAndRefusesASecondCaller(t *testing.T) {
	r := newRig(t)
	lock, err := Acquire(r.cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = lock.Release() }()

	if _, err := r.ctl.Reconcile(ctx(), "op-reconcile"); err == nil {
		t.Fatal("reconcile proceeded while another operation held the lock")
	} else if !IsRefusal(err) {
		t.Errorf("a busy lock was reported as a fault rather than a refusal: %v", err)
	}
}
