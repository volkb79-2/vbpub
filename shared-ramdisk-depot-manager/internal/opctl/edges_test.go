package opctl

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/harvest"
	"srdm/internal/publish"
)

// Every refusal in this file is an operation declining because the profile is
// not in a state where it means anything. They are grouped because they share
// a property worth asserting as one: each says what to do instead. An
// operator meets these at the moment they are learning the tool, and "no" on
// its own is where a tool stops being adoptable.

func TestOperationsOnAProfileWithNoActiveReleaseSayWhatToDoInstead(t *testing.T) {
	r := newRig(t)

	cases := map[string]func() error{
		"attach": func() error {
			return r.ctl.Attach(ctx(), "op-1", "server-a", "ro", assign.RoleSlave, r.prof)
		},
		"teardown": func() error {
			return r.ctl.ReleaseGeneration(ctx(), "op-1", r.prof)
		},
		"rollback": func() error {
			_, err := r.ctl.Rollback(ctx(), "op-1", r.prof)
			return err
		},
		"harvest": func() error {
			_, err := r.ctl.Harvest(ctx(), "op-1", "rel-new", r.prof)
			return err
		},
	}
	for name, run := range cases {
		err := run()
		if err == nil {
			t.Errorf("%s on an unassigned profile reported success", name)
			continue
		}
		if !strings.Contains(err.Error(), r.prof.ID) {
			t.Errorf("%s: the error does not name the profile: %v", name, err)
		}
	}
}

// A profile assigned a release whose generation is not published is the state
// every profile is in after a reboot. The operations that need mounts have to
// say that precisely, because the remedy — activate, which republishes — is
// not the remedy for anything else on this list.
func TestOperationsNeedingMountsSayWhenTheGenerationIsNotPublished(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	// Assigned, but nothing published: what a reboot leaves behind.
	delete(r.pub.records, publish.GenerationID("rel-1"))

	for name, run := range map[string]func() error{
		"attach": func() error {
			return r.ctl.Attach(ctx(), "op-1", "server-b", "ro", assign.RoleSlave, r.prof)
		},
		"teardown": func() error {
			return r.ctl.ReleaseGeneration(ctx(), "op-1", r.prof)
		},
		"harvest": func() error {
			_, err := r.ctl.Harvest(ctx(), "op-1", "rel-new", r.prof)
			return err
		},
	} {
		err := run()
		if err == nil {
			t.Errorf("%s reported success with nothing published", name)
			continue
		}
		if !strings.Contains(err.Error(), "not published") {
			t.Errorf("%s: the error does not say the generation is not published: %v", name, err)
		}
	}
}

// Detach still works with nothing published: removing a server from the
// assignment is how a consumer stops being one, and refusing it because the
// mounts are already gone would make the state unreachable.
func TestDetachWorksEvenWhenTheGenerationIsNotPublished(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	delete(r.pub.records, publish.GenerationID("rel-1"))

	if err := r.ctl.Detach(ctx(), "op-1", "server-a", r.prof); err != nil {
		t.Fatalf("Detach with nothing published: %v", err)
	}
	if _, ok := r.loadAssignment().Server("server-a"); ok {
		t.Error("detach did not remove the server")
	}
	if len(r.log) != 0 {
		t.Errorf("detach tried to unmount something: %v", r.log)
	}
}

// Re-activating the live release republishes and re-exposes rather than
// doing nothing — that is how a degraded generation is repaired — and it
// adopts the existing generation rather than publishing a second one under
// the same unit names.
func TestReActivatingTheLiveReleaseAdoptsRatherThanRepublishing(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-1", r.prof); err != nil {
		t.Fatalf("re-activating the live release: %v", err)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "publish") {
			t.Errorf("re-activating published a second generation over a live one: %v", r.log)
		}
		if strings.HasPrefix(step, "teardown") {
			t.Errorf("re-activating tore down the generation it was re-activating: %v", r.log)
		}
	}
	// The server was still moved, which is what makes it a repair.
	if len(r.log) == 0 || !strings.HasPrefix(r.log[len(r.log)-1], "expose server-a") {
		t.Errorf("re-activating did not re-expose the server: %v", r.log)
	}
	if a := r.loadAssignment(); a.Previous != "" {
		t.Errorf("re-activating invented a rollback target: %q", a.Previous)
	}
}

// A generation being replaced whose record cannot be read is a generation
// srdm cannot unexpose from. Binding the new one over it would stack mounts
// rather than replace them, so the operation stops instead.
func TestActivateStopsWhenTheReplacedGenerationsRecordIsUnreadable(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	delete(r.pub.records, publish.GenerationID("rel-1"))

	_, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("activating over an unreadable record reported success")
	}
	if !strings.Contains(err.Error(), "being replaced") {
		t.Errorf("the error does not say which record could not be read: %v", err)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "expose") {
			t.Errorf("a server was exposed to the new generation without being unexposed "+
				"from the old: %v", r.log)
		}
	}
}

// A publication that fails leaves the assignment and the servers exactly
// where they were: the new generation tore itself down, and nothing else was
// touched.
func TestAFailedPublicationChangesNothing(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	r.pub.publishErr = errors.New("class pak ran out of space")

	if _, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof); err == nil {
		t.Fatal("a failed publication reported success")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a failed publication recorded %q", got)
	}
	if len(r.log) != 1 {
		t.Errorf("a failed publication did %v", r.log)
	}
}

// A teardown that fails after the servers have moved is reported and does not
// undo anything. They are already on the new generation and the record says
// so; what is left is memory, which is reconciliation's problem.
func TestAFailedTeardownLeavesTheSwapDone(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.release("rel-2")
	r.pub.teardownErr = errors.New("systemd is unreachable")

	rec, err := r.ctl.Activate(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("a failed teardown was not reported")
	}
	if rec == nil || rec.ReleaseID != "rel-2" {
		t.Errorf("the new generation was not returned alongside the error: %+v", rec)
	}
	if got := r.loadAssignment().Release; got != "rel-2" {
		t.Errorf("the assignment says %q; the servers are on rel-2 and the record has to "+
			"agree with them", got)
	}
}

// --- construction ---------------------------------------------------------

func TestNewRefusesAnIncompleteController(t *testing.T) {
	r := newRig(t)
	for name, build := range map[string]func() error{
		"no store": func() error {
			_, err := New(r.cfg, r.jnl, nil, r.pub, r.drv)
			return err
		},
		"no publisher": func() error {
			_, err := New(r.cfg, r.jnl, r.st, nil, r.drv)
			return err
		},
		"no driver": func() error {
			_, err := New(r.cfg, r.jnl, r.st, r.pub, nil)
			return err
		},
		"no journal": func() error {
			_, err := New(r.cfg, nil, r.st, r.pub, r.drv)
			return err
		},
	} {
		if err := build(); err == nil {
			t.Errorf("a controller with %s was accepted", name)
		}
	}
}

// Harvest is optional wiring, so a controller without it has to say that
// rather than nil-dereference at the moment somebody asks.
func TestHarvestWithoutAHarvesterSaysSo(t *testing.T) {
	r := newRig(t)
	bare, err := New(r.cfg, r.jnl, r.st, r.pub, r.drv)
	if err != nil {
		t.Fatal(err)
	}
	r.assigned("rel-1")
	if _, err := bare.Harvest(ctx(), "op-1", "rel-new", r.prof); err == nil {
		t.Fatal("harvesting with no harvester reported success")
	} else if !strings.Contains(err.Error(), "harvester") {
		t.Errorf("the error does not say what is missing: %v", err)
	}
}

// With one wired, the call reaches it — and what comes back is the
// HARVESTER's refusal, because nothing is really mounted. Which refusal
// arrives matters: an operator meeting "no harvester" when the real problem
// is an unpublished generation would go looking in the wrong place.
func TestAControllerWithAHarvesterReachesIt(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1")

	_, err := r.ctl.Harvest(ctx(), "op-1", "rel-new", r.prof)
	if err == nil {
		t.Fatal("harvesting an unmounted generation reported success")
	}
	if !harvest.IsRefusal(err) {
		t.Errorf("want the harvester's own refusal, got %v", err)
	}
}

// --- names and status -----------------------------------------------------

func TestOperationsRefuseNamesThatWouldEscapeTheStoreLayout(t *testing.T) {
	r := newRig(t)
	if _, err := r.ctl.Activate(ctx(), "op-1", "../escape", r.prof); err == nil {
		t.Error("a release id containing a separator was accepted")
	}
	if _, err := r.ctl.Activate(ctx(), "", "rel-1", r.prof); err == nil {
		t.Error("an empty operation id was accepted")
	}
}

func TestStatusOfANodeWithNoAssignmentsIsEmptyRatherThanAnError(t *testing.T) {
	r := newRig(t)
	all, err := r.ctl.Status(ctx())
	if err != nil {
		t.Fatalf("status on a fresh node: %v", err)
	}
	if len(all) != 0 {
		t.Errorf("status = %+v, want nothing", all)
	}
	ids, err := r.ctl.Releases()
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 0 {
		t.Errorf("releases = %v", ids)
	}
}

// "Nothing is holding this" and "I could not tell" are different answers, and
// only one of them is safe to act on. A status that could not ask has to say
// so rather than reporting an empty list.
func TestStatusReportsAHolderCheckItCouldNotRun(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "server-a")
	r.pub.holdersErr = errors.New("/proc is not readable")

	all, err := r.ctl.Status(ctx())
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 1 || len(all[0].Degraded) == 0 {
		t.Fatalf("status did not report the failed check: %+v", all)
	}
	if !strings.Contains(all[0].Degraded[0], "/proc") {
		t.Errorf("the degraded note does not say what failed: %v", all[0].Degraded)
	}
}

// --- the lock -------------------------------------------------------------

func TestReleasingALockTwiceIsHarmless(t *testing.T) {
	r := newRig(t)
	lock, err := Acquire(r.cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := lock.Release(); err != nil {
		t.Fatal(err)
	}
	if err := lock.Release(); err != nil {
		t.Errorf("releasing an already-released lock: %v", err)
	}
	var nilLock *Lock
	if err := nilLock.Release(); err != nil {
		t.Errorf("releasing a nil lock: %v", err)
	}
}

// The lock lives under RunDir, so it goes away with the reboot. A lock that
// survived would be one held by a process that no longer exists, and the
// first boot after a crash is when srdm most needs to be able to act.
func TestTheLockIsUnderTheVolatileRoot(t *testing.T) {
	r := newRig(t)
	lock, err := Acquire(r.cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = lock.Release() }()

	if _, err := os.Stat(r.cfg.LockPath()); err != nil {
		t.Fatalf("the lock file is not where it says: %v", err)
	}
	if !strings.HasPrefix(r.cfg.LockPath(), r.cfg.RunDir+string(filepath.Separator)) {
		t.Errorf("the lock is at %s, which is not under the volatile root %s",
			r.cfg.LockPath(), r.cfg.RunDir)
	}
}

func TestABusyLockNamesTheFileAndTheReason(t *testing.T) {
	e := &BusyError{Path: "/run/srdm/srdm.lock"}
	if !strings.Contains(e.Error(), "/run/srdm/srdm.lock") {
		t.Errorf("the error does not name the lock: %v", e)
	}
	if !strings.Contains(e.Error(), "in progress") {
		t.Errorf("the error does not say what is happening: %v", e)
	}
}

// A run directory that cannot be created is a fault, not a refusal: srdm
// needs somewhere volatile to put a lock and there is nothing an operator can
// retry.
func TestAcquireReportsARunDirectoryItCannotUse(t *testing.T) {
	cfg := config.Default()
	cfg.StateDir = filepath.Join(t.TempDir(), "state")
	blocked := filepath.Join(t.TempDir(), "blocked")
	if err := os.WriteFile(blocked, []byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg.RunDir = filepath.Join(blocked, "run")

	if _, err := Acquire(cfg); err == nil {
		t.Fatal("acquiring a lock under an unusable run directory reported success")
	}
}
