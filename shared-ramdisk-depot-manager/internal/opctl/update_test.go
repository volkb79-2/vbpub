package opctl

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"

	"srdm/internal/assign"
	"srdm/internal/power"
	"srdm/internal/publish"
)

// --- fakes -------------------------------------------------------------
//
// Real, like fakeDriver and fakePublisher: what this package contributes
// about power and readiness is an ORDER — every slave stops before main,
// the whole cohort switches together, main starts and proves ready before
// any slave does — not a Panel client or a Docker log reader, so both are
// faked and the ordering oracles read the rig's shared log exactly as they
// do for exposure.

type fakePower struct {
	rig *rig

	statusErr map[string]error
	stopErr   map[string]error
	// failFirstStart names servers whose FIRST Start call fails. A later
	// Start for the same server (rollback's own, onto the OLD generation)
	// succeeds: what failed was the new content, not the server itself.
	failFirstStart map[string]bool
	startCalls     map[string]int
}

func newFakePower(r *rig) *fakePower {
	return &fakePower{rig: r, startCalls: map[string]int{}}
}

func (f *fakePower) Name() string { return "fake-power" }

func (f *fakePower) Status(_ context.Context, serverID string) (power.State, error) {
	if err := f.statusErr[serverID]; err != nil {
		return power.StateUnknown, err
	}
	return power.StateRunning, nil
}

func (f *fakePower) Stop(_ context.Context, serverID string) error {
	f.rig.note("stop %s", serverID)
	return f.stopErr[serverID]
}

func (f *fakePower) Start(_ context.Context, serverID string) error {
	f.startCalls[serverID]++
	n := f.startCalls[serverID]
	f.rig.note("start %s (attempt %d)", serverID, n)
	if f.failFirstStart[serverID] && n == 1 {
		return errors.New("the server did not come up")
	}
	return nil
}

// fakeReadiness stands in for power.ReadinessChecker: it never touches
// Docker, only records that it was asked and, on request, fails on cue.
type fakeReadiness struct {
	rig *rig

	// failFirst names servers whose FIRST WaitReady call fails — main never
	// signalling ready on the NEW generation, the shape oracle 31/33's
	// rollback is about. A later call (rollback's own, against the OLD
	// generation) succeeds.
	failFirst map[string]bool
	// alwaysFail names servers whose WaitReady NEVER succeeds — the double
	// failure a strong safety test (no slave ever starts) needs.
	alwaysFail map[string]bool
	calls      map[string]int
}

func newFakeReadiness(r *rig) *fakeReadiness {
	return &fakeReadiness{rig: r, calls: map[string]int{}}
}

// Anchor never fails in the rig: nothing here exercises Anchor's own error
// path (that lives in internal/power's tests), only the ordering WaitReady
// gates.
func (f *fakeReadiness) Anchor(_ context.Context, _ string) (power.ReadinessAnchor, error) {
	return nil, nil
}

func (f *fakeReadiness) WaitReady(_ context.Context, serverID string, _ power.Readiness,
	_ power.ReadinessAnchor) error {

	f.calls[serverID]++
	n := f.calls[serverID]
	f.rig.note("wait-ready %s", serverID)
	if f.alwaysFail[serverID] {
		return errors.New("main never signalled ready")
	}
	if f.failFirst[serverID] && n == 1 {
		return errors.New("main never signalled ready")
	}
	return nil
}

// setMemAvailable overwrites the meminfo fixture Update's headroom check
// reads, in place — so a test can start from the rig's generous default and
// shrink it, or vice versa.
func (r *rig) setMemAvailable(bytes int64) {
	r.t.Helper()
	body := fmt.Sprintf("MemTotal:       %d kB\nMemAvailable:   %d kB\n", bytes/1024, bytes/1024)
	if err := os.WriteFile(r.memInfoPath, []byte(body), 0o644); err != nil {
		r.t.Fatal(err)
	}
}

func stringSlicesEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func countOf(log []string, s string) int {
	n := 0
	for _, l := range log {
		if l == s {
			n++
		}
	}
	return n
}

// --- oracle 29 (replaced) ---------------------------------------------------

// The stop order is every slave before main, and the start order is main
// before every slave, with no slave started before main signalled ready —
// asserted from the recorded operation order. A rolling, one-at-a-time
// shape would be exactly wrong here: slaves depend on main, so the correct
// safety property is a FULL cohort cycle, not "at most one down".
func TestUpdateCyclesEverySlaveBeforeMainThenMainBeforeSlaves(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1", "slave-2")
	r.release("rel-2")

	if _, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatalf("Update: %v", err)
	}

	gen1, gen2 := publish.GenerationID("rel-1"), publish.GenerationID("rel-2")
	want := []string{
		"publish rel-2",
		"stop slave-1",
		"stop slave-2",
		"stop main-1",
		"unexpose main-1 <- " + gen1,
		"expose main-1 -> " + gen2 + " (assignment=rel-1)",
		"unexpose slave-1 <- " + gen1,
		"expose slave-1 -> " + gen2 + " (assignment=rel-1)",
		"unexpose slave-2 <- " + gen1,
		"expose slave-2 -> " + gen2 + " (assignment=rel-1)",
		"start main-1 (attempt 1)",
		"wait-ready main-1",
		"start slave-1 (attempt 1)",
		"start slave-2 (attempt 1)",
		"teardown " + gen1 + " (assignment=rel-2)",
	}
	if !stringSlicesEqual(r.log, want) {
		t.Fatalf("the sequence was\n  %s\nwant\n  %s",
			strings.Join(r.log, "\n  "), strings.Join(want, "\n  "))
	}

	// The mechanical property, read off the log rather than assumed from
	// the code that produced it: main is running (its start is logged)
	// before either slave's start ever appears.
	startMain := indexOf(r.log, "start main-1 (attempt 1)")
	waitMain := indexOf(r.log, "wait-ready main-1")
	startSlave1 := indexOf(r.log, "start slave-1 (attempt 1)")
	startSlave2 := indexOf(r.log, "start slave-2 (attempt 1)")
	if !(startMain < waitMain && waitMain < startSlave1 && startSlave1 < startSlave2) {
		t.Errorf("wrong order: start main=%d wait main=%d start slave-1=%d start slave-2=%d",
			startMain, waitMain, startSlave1, startSlave2)
	}
	stopSlave1, stopSlave2 := indexOf(r.log, "stop slave-1"), indexOf(r.log, "stop slave-2")
	stopMain := indexOf(r.log, "stop main-1")
	if !(stopSlave1 < stopMain && stopSlave2 < stopMain) {
		t.Errorf("main was stopped before a slave: stop slave-1=%d stop slave-2=%d stop main=%d",
			stopSlave1, stopSlave2, stopMain)
	}
}

func indexOf(log []string, s string) int {
	for i, l := range log {
		if l == s {
			return i
		}
	}
	return -1
}

// --- oracle 30 ---------------------------------------------------------------

// The assignment is recorded once the whole cohort is confirmed on the new
// generation — main proved ready, every slave started — and before the old
// generation is torn down: the same crash window activate uses.
func TestUpdateRecordsTheAssignmentAfterTheCohortIsConfirmedAndBeforeTeardown(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")

	if _, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatal(err)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "expose") && !strings.Contains(step, "assignment=rel-1") {
			t.Errorf("the record was updated before the cohort finished moving onto the new "+
				"generation: %q", step)
		}
		if strings.HasPrefix(step, "teardown") && !strings.Contains(step, "assignment=rel-2") {
			t.Errorf("the old generation was torn down while the record still named the "+
				"old release: %q", step)
		}
	}
	if got := r.loadAssignment().Release; got != "rel-2" {
		t.Errorf("the assignment is %q after a successful update, want rel-2", got)
	}
}

// --- oracle 31 (ordered) and oracle 33 (readiness) ---------------------------

// When main never signals ready on the new generation, the WHOLE cohort
// rolls back onto the OLD generation, using the identical safe cycle
// (every slave stopped, main stopped, exposure switched, main started and
// proved ready, every slave started) rather than a partial or reversed
// one — and the assignment is left naming the old release, because it was
// never touched.
func TestUpdateRollsBackTheWholeCohortWhenMainNeverBecomesReady(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1", "slave-2")
	r.release("rel-2")
	r.rdy.failFirst = map[string]bool{"main-1": true}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update reported success despite main never becoming ready")
	}
	if IsRefusal(err) {
		t.Error("a main that never becomes ready is an operational fault, not a refusal")
	}

	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "teardown") {
			t.Errorf("a rolled-back update tore down a generation: %v", r.log)
		}
	}
	// wait-ready was asked twice: once for the failed forward attempt, once
	// for rollback's successful restart on the old generation.
	if got := countOf(r.log, "wait-ready main-1"); got != 2 {
		t.Errorf("wait-ready main-1 was logged %d times, want 2 (forward + rollback)", got)
	}

	gen1, gen2 := publish.GenerationID("rel-1"), publish.GenerationID("rel-2")
	wantTail := []string{
		// rollback: same safe order, now targeting the OLD generation.
		"stop slave-1",
		"stop slave-2",
		"stop main-1",
		"unexpose main-1 <- " + gen2,
		"expose main-1 -> " + gen1 + " (assignment=rel-1)",
		"unexpose slave-1 <- " + gen2,
		"expose slave-1 -> " + gen1 + " (assignment=rel-1)",
		"unexpose slave-2 <- " + gen2,
		"expose slave-2 -> " + gen1 + " (assignment=rel-1)",
		"start main-1 (attempt 2)",
		"wait-ready main-1",
		"start slave-1 (attempt 1)",
		"start slave-2 (attempt 1)",
	}
	if len(r.log) < len(wantTail) || !stringSlicesEqual(r.log[len(r.log)-len(wantTail):], wantTail) {
		t.Fatalf("the rollback sequence was\n  %s\nwant it to end with\n  %s",
			strings.Join(r.log, "\n  "), strings.Join(wantTail, "\n  "))
	}
}

// The strongest safety property, under a double failure: even when
// rollback's OWN restart of main also never becomes ready, no slave is ever
// started against an unconfirmed main — on either generation.
func TestUpdateNeverStartsASlaveWhenMainNeverBecomesReadyEvenOnRollback(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.rdy.alwaysFail = map[string]bool{"main-1": true}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update reported success despite main never becoming ready on either generation")
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "start slave") {
			t.Errorf("a slave was started with main never confirmed ready: %v", r.log)
		}
	}
}

func TestUpdateRollsBackWhenMainFailsToStart(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.pwr.failFirstStart = map[string]bool{"main-1": true}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update reported success despite main failing to start")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
	// slave-1 is started exactly once — by rollback, once main is confirmed
	// running again on the OLD generation — never on the forward path,
	// where main's own Start never succeeded in the first place.
	startSlave := indexOf(r.log, "start slave-1 (attempt 1)")
	startMainRollback := indexOf(r.log, "start main-1 (attempt 2)")
	if startSlave < 0 || startMainRollback < 0 || startSlave < startMainRollback {
		t.Errorf("slave-1 was started before main's rollback restart succeeded: %v", r.log)
	}
}

// --- oracle 32 ----------------------------------------------------------------

// The update is refused, before anything is stopped, when the node cannot
// hold two generations at once; the refusal names the shortfall in bytes.
func TestUpdateRefusesWhenTheHostCannotHoldTwoGenerations(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1")
	r.release("rel-2")
	r.setMemAvailable(1 << 20) // 1 MiB: nowhere near two generations' tmpfs

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update reported success on a host with 1 MiB available")
	}
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	var headroom *HeadroomError
	if !errors.As(err, &headroom) {
		t.Fatalf("want a *HeadroomError, got %v (%T)", err, err)
	}
	if headroom.ShortfallBytes <= 0 {
		t.Errorf("HeadroomError does not name a positive shortfall: %+v", headroom)
	}
	if !strings.Contains(err.Error(), fmt.Sprint(headroom.ShortfallBytes)) {
		t.Errorf("the refusal does not name the shortfall in bytes: %v", err)
	}
	if len(r.log) != 0 {
		t.Errorf("a refused update did %v", r.log)
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a refused update recorded %q", got)
	}
}

func TestUpdateSucceedsWhenTheHostHasEnoughHeadroom(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1")
	r.release("rel-2")
	r.setMemAvailable(2 << 30) // generous — a control on the refusal above

	if _, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof); err != nil {
		t.Fatalf("Update: %v", err)
	}
}

// --- preflight -----------------------------------------------------------------

func TestUpdateRefusesWithNoPowerDriverConfigured(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1")
	r.release("rel-2")

	ctl, err := New(r.cfg, r.jnl, r.st, r.pub, r.drv, WithMemInfoPath(r.memInfoPath))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ctl.Update(ctx(), "op-1", "rel-2", r.prof); err == nil {
		t.Fatal("Update succeeded with no power driver configured")
	}
}

func TestUpdateRefusesAProfileWithNoServersAssigned(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1") // no servers
	r.release("rel-2")

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded with no servers assigned")
	}
	if len(r.log) != 0 {
		t.Errorf("an update with nothing to roll did %v", r.log)
	}
}

func TestUpdateRefusesACohortWithNoMainDeclared(t *testing.T) {
	r := newRig(t)
	a := r.assigned("rel-1")
	// Attach directly rather than through r.assigned, so nobody is main.
	if err := a.AddServer("server-a", "ro", "slave"); err != nil {
		t.Fatal(err)
	}
	if err := r.asg.Save(a); err != nil {
		t.Fatal(err)
	}
	r.release("rel-2")

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil || !strings.Contains(err.Error(), "no server declared main") {
		t.Fatalf("want a refusal naming the missing main, got %v", err)
	}
	if len(r.log) != 0 {
		t.Errorf("an update with no main did %v", r.log)
	}
}

func TestUpdateChecksEveryServerAnswersStatusBeforePublishing(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.pwr.statusErr = map[string]error{"slave-1": errors.New("the Panel is unreachable")}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite slave-1 not answering a power status query")
	}
	if len(r.log) != 0 {
		t.Errorf("an update that could not confirm every server's power state did %v", r.log)
	}
}

func TestUpdateRefusesANonexistentRelease(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1")
	// rel-typo was never promoted into the store.

	_, err := r.ctl.Update(ctx(), "op-1", "rel-typo", r.prof)
	if err == nil {
		t.Fatal("Update succeeded against a release that was never promoted")
	}
	if len(r.log) != 0 {
		t.Errorf("updating to a nonexistent release did %v", r.log)
	}
}

// The assignment names an old release whose published record cannot be
// read — corrupted or vanished between the headroom check and here.
// Update must report that plainly rather than binding a swap to a
// generation it cannot describe.
func TestUpdateFailsWhenTheOldGenerationsRecordCannotBeRead(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1")
	r.release("rel-2")
	delete(r.pub.records, publish.GenerationID("rel-1"))

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite the old generation's record being unreadable")
	}
	// The new generation was already published before this failure — that
	// publish is not rolled back (nothing was ever exposed to it), but the
	// assignment must still name the untouched old release.
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("assignment is %q after a failed update, want rel-1", got)
	}
}

func TestUpdateRefusesACohortWithTwoMainsDeclared(t *testing.T) {
	r := newRig(t)
	a := r.assigned("rel-1")
	if err := a.AddServer("server-a", "ro", assign.RoleMain); err != nil {
		t.Fatal(err)
	}
	if err := a.AddServer("server-b", "ro", assign.RoleMain); err != nil {
		t.Fatal(err)
	}
	if err := r.asg.Save(a); err != nil {
		t.Fatal(err)
	}
	r.release("rel-2")

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil || !strings.Contains(err.Error(), "2 servers declared main") {
		t.Fatalf("want a refusal naming the extra main, got %v", err)
	}
	if len(r.log) != 0 {
		t.Errorf("an update with two mains did %v", r.log)
	}
}

// --- cycleCohortTo's own branches, not just Update's outer preflight -------

func TestUpdateRollsBackWhenASlaveFailsToStop(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.pwr.stopErr = map[string]error{"slave-1": errors.New("the server is wedged")}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite a slave refusing to stop")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
	// Nothing was ever switched: the failure is in the very first step, so
	// there is no exposure to roll back — only the (harmless, idempotent)
	// restart attempts rollback always makes.
	for _, step := range r.log {
		if strings.HasPrefix(step, "unexpose") || strings.HasPrefix(step, "expose") {
			t.Errorf("an exposure changed despite the cohort never fully stopping: %v", r.log)
		}
	}
}

func TestUpdateRollsBackWhenAnExposeFails(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.drv.exposeErr = map[string]error{"slave-1": errors.New("mount refused")}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite a server failing to expose to the new generation")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
	for _, step := range r.log {
		if strings.HasPrefix(step, "teardown") {
			t.Errorf("a rolled-back update tore down a generation: %v", r.log)
		}
	}
}

func TestUpdateRollsBackWhenAnUnexposeFails(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.drv.unexposeErr = map[string]error{"main-1": errors.New("unmount refused")}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite a server failing to unexpose the old generation")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
}

func TestUpdateRollsBackWhenASlaveFailsToStart(t *testing.T) {
	r := newRig(t)
	r.assigned("rel-1", "main-1", "slave-1")
	r.release("rel-2")
	r.pwr.failFirstStart = map[string]bool{"slave-1": true}

	_, err := r.ctl.Update(ctx(), "op-1", "rel-2", r.prof)
	if err == nil {
		t.Fatal("Update succeeded despite a slave failing to start on the new generation")
	}
	if got := r.loadAssignment().Release; got != "rel-1" {
		t.Errorf("a rolled-back update recorded %q", got)
	}
	// main made it all the way up on the new generation before slave-1
	// failed; rollback must still bring main back down and restore it on
	// the old generation rather than leaving it stranded on the new one.
	if countOf(r.log, "stop main-1") < 2 {
		t.Errorf("main was not stopped again during rollback: %v", r.log)
	}
}

// --- readiness wiring: configured but nothing to check with ----------------

// Update itself never constructs this state (WithReadiness always sets the
// checker and the config together) — this exercises anchorMainReadiness
// and waitMainReady directly, against a Controller mutated in-package to
// the shape a caller could still reach by calling New with WithReadiness's
// checker argument nil.
func TestReadinessConfiguredWithNoCheckerWiredIsAnError(t *testing.T) {
	r := newRig(t)
	r.ctl.rdy = nil

	if _, err := r.ctl.anchorMainReadiness(ctx(), "main-1"); err == nil {
		t.Fatal("anchorMainReadiness succeeded with readiness configured and no checker")
	}
	if err := r.ctl.waitMainReady(ctx(), "main-1", nil); err == nil {
		t.Fatal("waitMainReady succeeded with readiness configured and no checker")
	}
}

func TestReadinessUnconfiguredSkipsAnchorAndWait(t *testing.T) {
	r := newRig(t)
	r.ctl.readiness.Kind = ""

	anchor, err := r.ctl.anchorMainReadiness(ctx(), "main-1")
	if err != nil || anchor != nil {
		t.Fatalf("anchorMainReadiness() = %v, %v; want nil, nil when unconfigured", anchor, err)
	}
	if err := r.ctl.waitMainReady(ctx(), "main-1", nil); err != nil {
		t.Fatalf("waitMainReady() = %v; want nil when unconfigured", err)
	}
}
