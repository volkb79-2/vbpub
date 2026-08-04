package opctl

import (
	"context"
	"errors"
	"fmt"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/expose"
	"srdm/internal/power"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// KindUpdate names the operation in the journal.
const KindUpdate = "update"

// Phases specific to Update. PhasePlan, PhasePublish, PhaseAssign and
// PhaseTeardown are shared with activate — Update reuses activate's own
// publish/assign/teardown internals rather than re-implementing them.
const (
	// PhaseHeadroom checks the host can hold two generations, before
	// anything is stopped (oracle 32).
	PhaseHeadroom = "headroom"
	// PhaseCycle is the ordered stop/switch/start cohort cycle, run once
	// forward (old generation -> new) and, on any failure inside it, once
	// more in the other direction (new -> old) to restore service.
	PhaseCycle = "cycle"
)

// Update moves a Soulmask-shaped cohort — one MAIN server every SLAVE
// depends on — onto releaseID through Wings' own node API rather than
// around it. The manual procedure this replaces stops the whole cohort by
// hand, runs srdm, and starts it by hand, offline for the entire window;
// srdm already publishes a new generation before touching the old one, and
// nothing before P14 used the window that opens.
//
// This is deliberately NOT a rolling, one-server-at-a-time update. Slaves
// connect to main, so "at most one server down at a time" is exactly the
// wrong shape here: it would run a slave against a main on a different
// content version, which for a game cluster is a version mismatch nobody
// asked for, arrived at by a design meant to prevent exactly that. The
// correct shape is an ORDERED FULL-COHORT CYCLE — every slave stops, then
// main stops, the release switches while nothing is running at all, main
// starts and PROVES it is actually ready (oracle 33 — a power status of
// "running" is not enough; Soulmask's main only accepts slave connections
// once its own world load finishes), and only then do slaves start.
//
// The cohort is fully offline for the length of the switch — there is no
// version-mismatch-free way to avoid that once a main/slave dependency
// exists — but the window is the switch itself, not a whole rolling pass
// over every server.
//
// Any failure in the cycle rolls the WHOLE cohort back onto the OLD
// generation, using the identical safe ordering (see cycleCohortTo): a
// split cohort is worse than a longer outage, so there is no partial
// result to leave standing.
func (c *Controller) Update(ctx context.Context, opID, releaseID string,
	prof *profile.Profile) (rec *publish.Record, err error) {

	if c.pwr == nil {
		return nil, errors.New("opctl: update needs a power driver, and none is configured; " +
			"set --wings-api-url")
	}

	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()

	for name, val := range map[string]string{"operation id": opID, "release id": releaseID} {
		if err := config.ValidName(name, val); err != nil {
			return nil, fmt.Errorf("opctl: %w", err)
		}
	}

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return nil, err
	}
	fields := map[string]string{
		"profile": prof.ID, "release_id": releaseID, "from_release": a.Release,
		"servers": itoa(len(a.Servers)),
	}

	var target *store.Release
	var main assign.Server
	if err := c.phase(opID, KindUpdate, PhasePlan, fields, func() error {
		if len(a.Servers) == 0 {
			return fmt.Errorf("opctl: profile %q has no servers assigned; there is nothing "+
				"to update — attach a server first", prof.ID)
		}
		main, err = a.Main()
		if err != nil {
			return err
		}
		target, err = c.st.Load(releaseID)
		if err != nil {
			return err
		}
		if err := target.Manifest.Verify(target.RootDir()); err != nil {
			return fmt.Errorf("opctl: release %s does not verify: %w", releaseID, err)
		}
		for _, srv := range a.Servers {
			if _, err := c.pwr.Status(ctx, srv.ID); err != nil {
				return fmt.Errorf("opctl: server %s did not answer a power status query, so "+
					"an update cannot know it is safe to stop it: %w", srv.ID, err)
			}
		}
		return nil
	}); err != nil {
		return nil, err
	}

	// The node has to hold two generations for the switch, and this is
	// checked before ANYTHING is stopped — oracle 32. The cohort is fully
	// offline during the switch itself, which frees the SERVERS' own
	// memory but not the tmpfs content either generation holds — that is
	// still charged to its own hold units regardless of who is or is not
	// running, so the two-generation sum is unchanged by the redesign; only
	// how long the overlap needs to last is shorter now (one switch, not a
	// whole rolling pass).
	if err := c.phase(opID, KindUpdate, PhaseHeadroom, fields, func() error {
		return c.checkHeadroom(target, a.Release, prof)
	}); err != nil {
		return nil, err
	}

	oldGeneration := ""
	if a.Release != "" {
		oldGeneration = publish.GenerationID(a.Release)
	}
	newGeneration := publish.GenerationID(releaseID)

	// Publish before anything is stopped. Old generation still live, every
	// server still running — a failure here costs nothing, exactly as it
	// does for activate.
	if err := c.phase(opID, KindUpdate, PhasePublish, fields, func() error {
		rec, err = c.publishOrAdopt(ctx, opID, prof, target)
		return err
	}); err != nil {
		return nil, err
	}

	var oldRec *publish.Record
	if oldGeneration != "" && oldGeneration != newGeneration {
		var lerr error
		if oldRec, lerr = c.pub.LoadRecord(prof.ID, oldGeneration); lerr != nil {
			return rec, fmt.Errorf("opctl: reading the record of the generation being "+
				"replaced (%s/%s): %w", prof.ID, oldGeneration, lerr)
		}
	}

	if err := c.phase(opID, KindUpdate, PhaseCycle, fields, func() error {
		return c.cycleCohortTo(ctx, opID, prof, main, a.Slaves(), oldRec, rec)
	}); err != nil {
		// Roll the whole cohort back onto the old generation, using the
		// identical safe ordering. Best effort: if THIS also fails, the
		// cohort could not be restored automatically and the operator is
		// told everything both attempts found, rather than only the first.
		rbErr := c.cycleCohortTo(ctx, opID, prof, main, a.Slaves(), rec, oldRec)
		return rec, joinRollback(fmt.Errorf("opctl: the update failed mid-cycle: %w", err), rbErr)
	}

	// Recorded once the whole cohort is confirmed on the new generation —
	// main proved ready, every slave started — and before the old
	// generation is torn down: oracle 30, the same crash window activate
	// uses, adapted to what "every server has moved" now means for a
	// cohort that depends on one of its own members coming up cleanly.
	if err := c.phase(opID, KindUpdate, PhaseAssign, fields, func() error {
		a.SetRelease(releaseID)
		return c.asg.Save(a)
	}); err != nil {
		return rec, err
	}

	// Last, and allowed to fail loudly without undoing anything — the
	// cohort is already fully on the new generation and the record says so.
	if oldGeneration != "" && oldGeneration != newGeneration {
		if err := c.phase(opID, KindUpdate, PhaseTeardown, fields, func() error {
			return c.pub.Teardown(ctx, opID, prof.ID, oldGeneration)
		}); err != nil {
			return rec, err
		}
	}
	return rec, nil
}

// cycleCohortTo moves a cohort from whatever it is currently exposed to
// (from, which may be nil) onto to, in the one order that is safe
// regardless of direction — oracle 29:
//
//  1. stop every slave                    (main still up)
//  2. stop main                           (cohort now fully down)
//  3. switch every server's exposure      (nothing is holding either
//     generation right now, so this never races a consumer)
//  4. start main
//  5. wait for main's readiness signal    (oracle 33 — not just "the
//     power status says running")
//  6. start every slave, only now that main has proved ready
//
// It is the SAME function for the forward move (old generation -> new) and
// for rollback (new -> old, called with from/to swapped) rather than a
// hand-written mirror: reversing the actual STEP order — starting a server
// before stopping it, say — would not restore anything, but running the
// identical safe cycle again with the generations swapped does, exactly
// because main-last-stopped/main-first-started is what makes ANY move
// between two generations safe, in either direction. Decided and stated
// this way in decisions.md, because "walks the same order in reverse"
// reads as literal step-reversal and is not.
func (c *Controller) cycleCohortTo(ctx context.Context, opID string, prof *profile.Profile,
	main assign.Server, slaves []assign.Server, from, to *publish.Record) error {

	// Named steps, called in the fixed order below, rather than inlined in
	// that order — so the ORDER itself is two one-line calls to get right
	// (and, for a canary, to get wrong) rather than something implicit in
	// which block of code happens to come first in the file.
	stopSlaves := func() error {
		for _, s := range slaves {
			if err := c.pwr.Stop(ctx, s.ID); err != nil {
				return fmt.Errorf("stopping slave %s: %w", s.ID, err)
			}
		}
		return nil
	}
	stopMain := func() error {
		if err := c.pwr.Stop(ctx, main.ID); err != nil {
			return fmt.Errorf("stopping main %s: %w", main.ID, err)
		}
		return nil
	}
	// startMain also anchors and waits for readiness: the anchor MUST be
	// captured before Start is issued (main's log tail is polled, not
	// streamed with a server-side time filter, so the only way to tell a
	// fresh ready line from a stale one already sitting in the tail is to
	// have already seen the stale one — D-033), and the wait must complete
	// before this function reports main started at all.
	startMain := func() error {
		anchor, err := c.anchorMainReadiness(ctx, main.ID)
		if err != nil {
			return fmt.Errorf("anchoring main %s's readiness: %w", main.ID, err)
		}
		if err := c.pwr.Start(ctx, main.ID); err != nil {
			return fmt.Errorf("starting main %s: %w", main.ID, err)
		}
		return c.waitMainReady(ctx, main.ID, anchor)
	}
	startSlaves := func() error {
		for _, s := range slaves {
			if err := c.pwr.Start(ctx, s.ID); err != nil {
				return fmt.Errorf("starting slave %s: %w", s.ID, err)
			}
		}
		return nil
	}

	if err := stopSlaves(); err != nil {
		return err
	}
	if err := stopMain(); err != nil {
		return err
	}

	cohort := append([]assign.Server{main}, slaves...)
	for _, srv := range cohort {
		req := expose.Request{ServerID: srv.ID, Access: expose.Access(srv.Access), OperationID: opID}
		if from != nil {
			if err := c.drv.Unexpose(ctx, from, prof, req); err != nil {
				return err
			}
		}
		if to != nil {
			if err := c.drv.Expose(ctx, to, prof, req); err != nil {
				return err
			}
		}
	}

	if err := startMain(); err != nil {
		return err
	}
	if err := startSlaves(); err != nil {
		return err
	}
	return nil
}

// anchorMainReadiness snapshots main's current log tail, or does nothing
// when no readiness check is configured. Must be called before main is
// started — see cycleCohortTo.
func (c *Controller) anchorMainReadiness(ctx context.Context, mainID string) (power.ReadinessAnchor, error) {
	if !c.readiness.Configured() {
		return nil, nil
	}
	if c.rdy == nil {
		return nil, fmt.Errorf("opctl: readiness is configured (%s) but no readiness checker "+
			"is wired in", c.readiness.Kind)
	}
	return c.rdy.Anchor(ctx, mainID)
}

// waitMainReady checks main's readiness signal, or does nothing when none
// is configured — trusting the power status alone is a real, documented
// choice (D-031), not a gap.
func (c *Controller) waitMainReady(ctx context.Context, mainID string, anchor power.ReadinessAnchor) error {
	if !c.readiness.Configured() {
		return nil
	}
	if c.rdy == nil {
		return fmt.Errorf("opctl: readiness is configured (%s) but no readiness checker is "+
			"wired in", c.readiness.Kind)
	}
	return c.rdy.WaitReady(ctx, mainID, c.readiness, anchor)
}

// joinRollback folds a rollback's own errors into the failure that
// triggered it, so a caller sees both: what broke, and whether the recovery
// srdm attempted on its behalf also broke.
func joinRollback(cause, rollback error) error {
	if rollback == nil {
		return cause
	}
	return fmt.Errorf("%w (rollback also reported: %v)", cause, rollback)
}
