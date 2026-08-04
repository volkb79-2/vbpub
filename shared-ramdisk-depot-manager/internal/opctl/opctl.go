// Package opctl is srdm's operations layer: the sequences an operator asks
// for, expressed once.
//
// Everything below it is a library that does one thing correctly —
// publication builds a topology, exposure binds it into a volume, harvest
// reads it back, the store promotes and verifies. None of them knows about
// the others, and that is why they are testable. What none of them has is an
// answer to "swap this server onto that release", which is three of them in
// an order, under a lock, with a refusal in the middle.
//
// The order is the whole content of this package, and it is not obvious:
//
//	publish the new generation -> expose every assigned server to it ->
//	record the assignment -> tear the old generation down
//
// The record is written after the exposures and before the teardown, which
// is the only position with no bad crash. Written first, a crash leaves an
// assignment naming a release nothing has mounted. Written last, a crash
// after the teardown leaves an assignment naming a generation that no longer
// exists while the servers are already on the new one. In between, every
// crash leaves the servers reading the release the record names, and at worst
// an old generation still holding memory — which reconciliation can see and
// which costs pages rather than correctness.
package opctl

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"

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

// Operation kinds, as they appear in the journal.
const (
	KindActivate  = "activate"
	KindRollback  = "rollback"
	KindAttach    = "attach"
	KindDetach    = "detach"
	KindRelease   = "release-generation"
	KindGC        = "gc"
	KindReconcile = "reconcile"
	KindRestore   = "restore"
)

// Phases.
const (
	PhasePlan     = "plan"
	PhasePublish  = "publish"
	PhaseExpose   = "expose"
	PhaseAssign   = "assign"
	PhaseTeardown = "teardown"
	PhaseCollect  = "collect"
	PhaseAdopt    = "adopt"
)

// Driver is the exposure surface. *expose.HostBind satisfies it.
type Driver interface {
	Name() string
	Expose(ctx context.Context, rec *publish.Record, prof *profile.Profile, req expose.Request) error
	Unexpose(ctx context.Context, rec *publish.Record, prof *profile.Profile, req expose.Request) error
}

// Publisher is the publication surface. *publish.Publisher satisfies it.
type Publisher interface {
	Publish(ctx context.Context, opID, profileID string, rel *store.Release,
		prof *profile.Profile) (*publish.Record, error)
	Teardown(ctx context.Context, opID, profileID, generation string) error
	LoadRecord(profileID, generation string) (*publish.Record, error)
	Holders(ctx context.Context, profileID, generation string) (*consumer.Report, error)

	// The reconciliation-and-repair surface, P08b. Reconcile and
	// TeardownOrphans already existed; IsComplete, RepairReadOnly,
	// ClearForRepublish and AdoptOrQuarantine are what turn the drift they
	// find into something acted on rather than only reported.
	Reconcile(ctx context.Context, opID string) (*publish.Reconciliation, error)
	TeardownOrphans(opID string, res *publish.Reconciliation) error
	IsComplete(ctx context.Context, rec *publish.Record) (bool, error)
	RepairReadOnly(opID, exposePath string) error
	ClearForRepublish(ctx context.Context, opID, profileID, generation string) (*publish.Record, error)
	AdoptOrQuarantine(ctx context.Context, opID string) (*publish.OpRecovery, error)
}

// Controller sequences operations.
type Controller struct {
	cfg config.Config
	jnl *journal.Journal
	st  *store.Store
	pub Publisher
	drv Driver
	asg *assign.Store
	hrv *harvest.Harvester
}

// Option configures a Controller.
type Option func(*Controller)

// WithHarvester injects the harvester. Optional: only Harvest needs it.
func WithHarvester(h *harvest.Harvester) Option {
	return func(c *Controller) { c.hrv = h }
}

// New returns a Controller.
func New(cfg config.Config, jnl *journal.Journal, st *store.Store, pub Publisher,
	drv Driver, opts ...Option) (*Controller, error) {

	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("opctl: a journal is required")
	}
	if st == nil || pub == nil || drv == nil {
		return nil, errors.New("opctl: a store, a publisher and an exposure driver are required")
	}
	asg, err := assign.New(cfg)
	if err != nil {
		return nil, err
	}
	c := &Controller{cfg: cfg, jnl: jnl, st: st, pub: pub, drv: drv, asg: asg}
	for _, o := range opts {
		o(c)
	}
	return c, nil
}

// Assignments exposes the declared state for readers that do not change it.
func (c *Controller) Assignments() *assign.Store { return c.asg }

// HeldError is an operation declining because a consumer still holds the
// generation it would change underneath them.
//
// activate, rollback and teardown all swap what a running server is reading;
// the master plan groups them for that reason (oracle 24) and so does this.
type HeldError struct {
	Op         string
	Generation string
	Holders    []consumer.Holder
}

func (e *HeldError) Error() string {
	var who []string
	for _, h := range e.Holders {
		who = append(who, h.String())
	}
	return fmt.Sprintf("opctl: %s would change what %d running consumer(s) are reading "+
		"underneath them: %s. The content they have open would be replaced by content "+
		"they did not open, which is not a restart away from working — it is a process "+
		"holding deleted inodes. Stop them first",
		e.Op, len(e.Holders), strings.Join(who, "; "))
}

// IsRefusal reports whether err is srdm declining by contract.
func IsRefusal(err error) bool {
	var held *HeldError
	var busy *BusyError
	if errors.As(err, &held) || errors.As(err, &busy) {
		return true
	}
	return expose.IsRefusal(err) || publish.IsRefusal(err) || harvest.IsRefusal(err) ||
		store.IsRefusal(err)
}

// Activate makes releaseID the profile's active generation and moves every
// assigned server onto it.
//
// Idempotent in the way that matters: activating the release that is already
// active republishes it and re-exposes the servers, which is how a degraded
// generation is repaired. It does not shift the rollback target, because
// repairing the present must not destroy the way back.
func (c *Controller) Activate(ctx context.Context, opID, releaseID string,
	prof *profile.Profile) (*publish.Record, error) {

	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()
	return c.activate(ctx, opID, KindActivate, releaseID, prof)
}

// Rollback activates the release the profile was on before the last change.
func (c *Controller) Rollback(ctx context.Context, opID string,
	prof *profile.Profile) (*publish.Record, error) {

	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return nil, err
	}
	if a.Previous == "" {
		return nil, fmt.Errorf("opctl: profile %q has no previous release to roll back to; "+
			"rollback undoes the last activation, and there has not been one — activate a "+
			"release by id instead", prof.ID)
	}
	return c.activate(ctx, opID, KindRollback, a.Previous, prof)
}

// activate assumes the operation lock is already held — every exported
// caller acquires it (Activate, Rollback, and Restore, which activates once
// per assigned profile under a single lock held for the whole boot pass
// rather than one per profile).
func (c *Controller) activate(ctx context.Context, opID, kind, releaseID string,
	prof *profile.Profile) (rec *publish.Record, err error) {

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
		"profile": prof.ID, "release_id": releaseID,
		"from_release": a.Release, "servers": itoa(len(a.Servers)),
	}

	var target *store.Release
	if err := c.phase(opID, kind, PhasePlan, fields, func() error {
		// The release has to exist and be whole before anything is torn down.
		// Loading it is the cheapest possible way to find out, and doing it
		// first is what keeps a typo from costing a running generation.
		target, err = c.st.Load(releaseID)
		if err != nil {
			return err
		}
		// And nothing may be holding what is about to be replaced. This is
		// oracle 24 for activate and rollback: the same hazard teardown
		// refuses, arriving by a different route.
		return c.refuseIfHeld(ctx, kind, prof.ID, a.Release)
	}); err != nil {
		return nil, err
	}

	oldGeneration := ""
	if a.Release != "" {
		oldGeneration = publish.GenerationID(a.Release)
	}
	newGeneration := publish.GenerationID(releaseID)

	// Publishing the new generation before touching the old one means a
	// failure here costs nothing: the servers are still reading what they
	// were, and the half-built generation tore itself down.
	if err := c.phase(opID, kind, PhasePublish, fields, func() error {
		rec, err = c.publishOrAdopt(ctx, opID, prof, target)
		return err
	}); err != nil {
		return nil, err
	}

	if err := c.phase(opID, kind, PhaseExpose, fields, func() error {
		return c.moveServers(ctx, opID, a, prof, rec, oldGeneration)
	}); err != nil {
		return nil, err
	}

	if err := c.phase(opID, kind, PhaseAssign, fields, func() error {
		a.SetRelease(releaseID)
		return c.asg.Save(a)
	}); err != nil {
		return nil, err
	}

	// Last, and allowed to fail loudly without undoing anything: the servers
	// are already on the new generation and the record says so. What is left
	// is memory, and a teardown that could not run is reconciliation's
	// problem rather than this operation's.
	if oldGeneration != "" && oldGeneration != newGeneration {
		if err := c.phase(opID, kind, PhaseTeardown, fields, func() error {
			return c.pub.Teardown(ctx, opID, prof.ID, oldGeneration)
		}); err != nil {
			return rec, err
		}
	}
	return rec, nil
}

// publishOrAdopt publishes the target release, or adopts the generation if
// it is already published AND still whole in the kernel.
//
// Republishing over a live generation would mean two publications of the same
// content racing for the same unit names, and the second would fail on the
// first's units — so an existing, complete record is the answer when there
// is one. A record alone is not enough to conclude that, though: it is
// exactly what every generation on the node still has after a reboot, with
// nothing mounted and no unit holding it (Reconcile's own words). Trusting
// LoadRecord without asking the kernel would hand `moveServers` a record
// naming mounts that do not exist, and bind exposures to it that fail or,
// worse, resolve to nothing — which is what made this the wrong idempotent
// path for `Restore` to reuse until it was fixed here (P08b).
func (c *Controller) publishOrAdopt(ctx context.Context, opID string, prof *profile.Profile,
	rel *store.Release) (*publish.Record, error) {

	gen := publish.GenerationID(rel.ID)
	if rec, err := c.pub.LoadRecord(prof.ID, gen); err == nil {
		complete, err := c.pub.IsComplete(ctx, rec)
		if err != nil {
			return nil, err
		}
		if complete {
			return rec, nil
		}
		// Clear whatever remains of the broken generation before rebuilding
		// it — Publish itself would otherwise collide with leftover hold
		// units carrying the same, generation-derived names.
		if _, err := c.pub.ClearForRepublish(ctx, opID, prof.ID, gen); err != nil {
			return nil, err
		}
	}
	return c.pub.Publish(ctx, opID, prof.ID, rel, prof)
}

// moveServers unexposes each assigned server from the old generation and
// exposes it to the new one.
//
// Per server rather than all-unexpose-then-all-expose, because a failure
// halfway through the second loop would leave the earlier servers with
// nothing bound at all, which is worse than leaving them where they were.
func (c *Controller) moveServers(ctx context.Context, opID string, a *assign.Assignment,
	prof *profile.Profile, rec *publish.Record, oldGeneration string) error {

	var oldRec *publish.Record
	if oldGeneration != "" && oldGeneration != rec.Generation {
		var err error
		if oldRec, err = c.pub.LoadRecord(prof.ID, oldGeneration); err != nil {
			// A record srdm cannot read is a generation it cannot unexpose
			// from, and binding the new one over the old would stack mounts
			// rather than replace them.
			return fmt.Errorf("opctl: reading the record of the generation being "+
				"replaced (%s/%s): %w", prof.ID, oldGeneration, err)
		}
	}
	for _, srv := range a.Servers {
		req := expose.Request{ServerID: srv.ID, Access: expose.Access(srv.Access), OperationID: opID}
		if oldRec != nil {
			if err := c.drv.Unexpose(ctx, oldRec, prof, req); err != nil {
				return err
			}
		}
		if err := c.drv.Expose(ctx, rec, prof, req); err != nil {
			return err
		}
	}
	return nil
}

// Attach adds a server to a profile's assignment and exposes it.
func (c *Controller) Attach(ctx context.Context, opID, serverID, access string,
	prof *profile.Profile) error {

	lock, err := Acquire(c.cfg)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Release() }()

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return err
	}
	if a.Release == "" {
		return fmt.Errorf("opctl: profile %q has no active release, so there is nothing to "+
			"attach server %s to; activate a release first", prof.ID, serverID)
	}
	rec, err := c.pub.LoadRecord(prof.ID, publish.GenerationID(a.Release))
	if err != nil {
		return fmt.Errorf("opctl: profile %q is assigned release %s but its generation is "+
			"not published: %w", prof.ID, a.Release, err)
	}
	fields := map[string]string{
		"profile": prof.ID, "server": serverID, "access": access,
		"release_id": a.Release, "generation": rec.Generation,
	}

	// Exposed before recorded, and this is the opposite order from activate's
	// on purpose. Attach ADDS a consumer, so the dangerous crash is a record
	// claiming a server is attached when nothing is bound — a boot would then
	// believe it had restored something it never mounted. Activate MOVES
	// consumers that are already recorded, where the dangerous crash is the
	// other way round.
	if err := c.phase(opID, KindAttach, PhaseExpose, fields, func() error {
		return c.drv.Expose(ctx, rec, prof, expose.Request{
			ServerID: serverID, Access: expose.Access(access), OperationID: opID,
		})
	}); err != nil {
		return err
	}
	return c.phase(opID, KindAttach, PhaseAssign, fields, func() error {
		if err := a.AddServer(serverID, access); err != nil {
			return err
		}
		return c.asg.Save(a)
	})
}

// Detach unexposes a server and removes it from the assignment.
func (c *Controller) Detach(ctx context.Context, opID, serverID string,
	prof *profile.Profile) error {

	lock, err := Acquire(c.cfg)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Release() }()

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return err
	}
	if _, ok := a.Server(serverID); !ok {
		return fmt.Errorf("opctl: server %s is not attached to profile %q", serverID, prof.ID)
	}
	fields := map[string]string{"profile": prof.ID, "server": serverID, "release_id": a.Release}

	// Unexposed first, then recorded — the mirror of attach, and for the
	// mirror reason: the dangerous crash for a REMOVAL is a record saying the
	// server is gone while its mounts are still there, because nothing would
	// ever go looking for them again.
	if a.Release != "" {
		rec, err := c.pub.LoadRecord(prof.ID, publish.GenerationID(a.Release))
		if err == nil {
			srv, _ := a.Server(serverID)
			if err := c.phase(opID, KindDetach, PhaseExpose, fields, func() error {
				return c.drv.Unexpose(ctx, rec, prof, expose.Request{
					ServerID: serverID, Access: expose.Access(srv.Access), OperationID: opID,
				})
			}); err != nil {
				return err
			}
		}
	}
	return c.phase(opID, KindDetach, PhaseAssign, fields, func() error {
		a.RemoveServer(serverID)
		return c.asg.Save(a)
	})
}

// ReleaseGeneration takes a profile's active generation down entirely:
// every server detached from it, then the topology torn down.
//
// The assignment keeps its servers and its release. A generation that is not
// published is a normal state — it is what every profile is in after a
// reboot and before the boot path runs — and forgetting who consumes it would
// turn a teardown into a reconfiguration.
func (c *Controller) ReleaseGeneration(ctx context.Context, opID string,
	prof *profile.Profile) error {

	lock, err := Acquire(c.cfg)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Release() }()

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return err
	}
	if a.Release == "" {
		return fmt.Errorf("opctl: profile %q has no active release", prof.ID)
	}
	gen := publish.GenerationID(a.Release)
	rec, err := c.pub.LoadRecord(prof.ID, gen)
	if err != nil {
		return fmt.Errorf("opctl: profile %q is assigned release %s but its generation is "+
			"not published: %w", prof.ID, a.Release, err)
	}
	fields := map[string]string{
		"profile": prof.ID, "release_id": a.Release, "generation": gen,
		"servers": itoa(len(a.Servers)),
	}

	if err := c.phase(opID, KindRelease, PhaseExpose, fields, func() error {
		for _, srv := range a.Servers {
			if err := c.drv.Unexpose(ctx, rec, prof, expose.Request{
				ServerID: srv.ID, Access: expose.Access(srv.Access), OperationID: opID,
			}); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		return err
	}
	// Teardown consults the guard itself (D-018), so a consumer still holding
	// after its exposure was removed is refused here rather than here having
	// to ask again and race its own answer.
	return c.phase(opID, KindRelease, PhaseTeardown, fields, func() error {
		return c.pub.Teardown(ctx, opID, prof.ID, gen)
	})
}

// Harvest promotes the profile's active generation into a new release.
func (c *Controller) Harvest(ctx context.Context, opID, releaseID string,
	prof *profile.Profile) (*store.Release, error) {

	if c.hrv == nil {
		return nil, errors.New("opctl: this controller has no harvester")
	}
	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()

	a, err := c.asg.Load(prof.ID)
	if err != nil {
		return nil, err
	}
	if a.Release == "" {
		return nil, fmt.Errorf("opctl: profile %q has no active release to harvest", prof.ID)
	}
	rec, err := c.pub.LoadRecord(prof.ID, publish.GenerationID(a.Release))
	if err != nil {
		return nil, fmt.Errorf("opctl: profile %q is assigned release %s but its generation "+
			"is not published: %w", prof.ID, a.Release, err)
	}
	return c.hrv.Harvest(ctx, rec, prof, harvest.Opts{OperationID: opID, ReleaseID: releaseID})
}

// refuseIfHeld declines when anything still holds a generation.
func (c *Controller) refuseIfHeld(ctx context.Context, op, profileID, releaseID string) error {
	if releaseID == "" {
		return nil
	}
	gen := publish.GenerationID(releaseID)
	rep, err := c.pub.Holders(ctx, profileID, gen)
	if err != nil {
		// A generation that is not published has no record and no holders,
		// which is the normal state after a reboot. That is not a refusal and
		// it is not an error — there is nothing to be held.
		return nil
	}
	if rep.Held() {
		return &HeldError{Op: op, Generation: profileID + "/" + gen, Holders: rep.Holders}
	}
	return nil
}

// phase journals a step before and after it runs.
func (c *Controller) phase(opID, kind, ph string, fields map[string]string, fn func() error) error {
	if err := c.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: ph,
		Outcome: journal.OutcomeStarted, Fields: fields,
	}); err != nil {
		return err
	}
	if err := fn(); err != nil {
		outcome := journal.OutcomeFailed
		if IsRefusal(err) {
			outcome = journal.OutcomeRefused
		}
		_ = c.jnl.Emit(journal.Record{
			OperationID: opID, Kind: kind, Phase: ph,
			Outcome: outcome, Fields: fields, Error: err.Error(),
		})
		return err
	}
	return c.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: ph,
		Outcome: journal.OutcomeOK, Fields: fields,
	})
}

func itoa(n int) string { return fmt.Sprintf("%d", n) }

func sorted(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}
