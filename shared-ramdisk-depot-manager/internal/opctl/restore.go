// P08b: the boot path and reconciliation acting on what it finds.
//
// Reconcile is the general repair pass — safe to run any time, and driven
// entirely by what the kernel and the durable records disagree about. It
// never needs an operator's --profile, because it never rebuilds content: it
// resolves crashed operations, remounts a bind read-only, removes orphan
// mounts, and clears generations that are drifting AND unassigned. Anything
// still needing content rebuilt is reported (NeedsActivate) rather than
// acted on here, because rebuilding needs the profile document and the
// re-exposure sequence only Activate carries end to end.
//
// Restore is Reconcile plus the part only the boot path needs: after a
// reboot RunDir is empty, so every assigned profile's own generation is
// always in that reported set, and Restore closes the loop by activating
// each one — the same idempotent repair path a degraded generation already
// uses while the system is up, run once per assignment with no operator
// watching.
package opctl

import (
	"context"
	"errors"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/profile"
	"srdm/internal/publish"
)

// RestoreReport is what a reconciliation-and-repair pass found and did.
type RestoreReport struct {
	// Adopted names operations a previous process died in the middle of,
	// found fully realized in the kernel and completed.
	Adopted []string
	// Quarantined names operations torn down as unrecoverable.
	Quarantined []string
	// ReadOnlyFixed names exposures remounted read-only in place.
	ReadOnlyFixed []string
	// OrphansRemoved names mounts nobody's record claimed.
	OrphansRemoved []string
	// Cleared names generations reconciliation tore down for good: drifting,
	// and not any profile's current assignment, so there is nothing to
	// rebuild them for.
	Cleared []string
	// NeedsActivate names an assigned profile's OWN generation, found
	// drifting. Reconcile does not rebuild it — that needs the profile
	// document and the re-exposure sequence Activate carries — but reports
	// it rather than silently clearing a generation somebody is assigned to.
	NeedsActivate []string
	// Restored names profile/release pairs Restore brought back by
	// activating them. Always empty from Reconcile, which never activates.
	Restored []string
	// Blocked names generations reconciliation would otherwise have acted
	// on, left alone because a consumer still holds them.
	Blocked []string
	// Failed maps a name (profile, profile/server, or mount) to why
	// reconciliation could not resolve it. Never silently dropped: a status
	// nobody can see is a status nobody can act on.
	Failed map[string]string
}

// Reconcile compares durable state against the kernel and repairs what is
// safe to repair without an operator present.
//
// Takes the operation lock for its own duration: every step it performs —
// resolving a crashed operation, remounting a bind, tearing a generation
// down — changes state exactly as an operator-driven verb does, and the same
// hazard applies (two of these, or one of these racing an activate, would
// each succeed at steps that contradict the other's).
func (c *Controller) Reconcile(ctx context.Context, opID string) (*RestoreReport, error) {
	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()
	return c.reconcileLocked(ctx, opID)
}

func (c *Controller) reconcileLocked(ctx context.Context, opID string) (*RestoreReport, error) {
	rep := &RestoreReport{Failed: map[string]string{}}

	// First: resolve every operation a previous process died inside of. This
	// runs before Reconcile reads the mount table, because an adopted
	// operation's record has to exist before reconciliation can see it as
	// healthy rather than as unclaimed mounts.
	if err := c.phase(opID, KindReconcile, PhaseAdopt, nil, func() error {
		ops, err := c.pub.AdoptOrQuarantine(ctx, opID)
		if err != nil {
			return err
		}
		rep.Adopted = ops.Adopted
		rep.Quarantined = ops.Quarantined
		return nil
	}); err != nil {
		return rep, err
	}

	res, err := c.pub.Reconcile(ctx, opID)
	if err != nil {
		return rep, err
	}

	// Always safe, whoever holds what: nothing is unmounted.
	for _, path := range res.NotReadOnly {
		if err := c.pub.RepairReadOnly(opID, path); err != nil {
			rep.Failed[path] = err.Error()
			continue
		}
		rep.ReadOnlyFixed = append(rep.ReadOnlyFixed, path)
	}

	if err := c.pub.TeardownOrphans(opID, res); err != nil {
		rep.Failed["orphans"] = err.Error()
	} else {
		rep.OrphansRemoved = res.Orphans
	}

	current, err := c.currentGenerations()
	if err != nil {
		return rep, err
	}

	for _, name := range res.NeedsRepublish {
		profileID, gen, ok := splitGenerationName(name)
		if !ok {
			rep.Failed[name] = "reconcile: malformed generation name"
			continue
		}
		if current[profileID] == gen {
			// Somebody is assigned to exactly this generation. Clearing it
			// here would only mean Activate has to rebuild it from nothing
			// a moment later; reporting it is enough, and cheaper.
			rep.NeedsActivate = append(rep.NeedsActivate, name)
			continue
		}
		if _, err := c.pub.ClearForRepublish(ctx, opID, profileID, gen); err != nil {
			if isHeld(err) {
				rep.Blocked = append(rep.Blocked, name)
			} else {
				rep.Failed[name] = err.Error()
			}
			continue
		}
		rep.Cleared = append(rep.Cleared, name)
	}

	sort.Strings(rep.Adopted)
	sort.Strings(rep.Quarantined)
	sort.Strings(rep.ReadOnlyFixed)
	sort.Strings(rep.Cleared)
	sort.Strings(rep.NeedsActivate)
	sort.Strings(rep.Blocked)
	return rep, nil
}

// Restore brings every profile's assignment back after a reboot.
//
// srdm-restore.service calls this, `After=local-fs.target` and with no
// dependency of its own on Docker: Docker is only ever reached through
// internal/consumer's holder resolution, and a generation nothing has
// mounted yet — which is every generation, the instant after a reboot — has
// nothing to hold it, so there is nothing to resolve.
//
// It runs Reconcile's repairs first, under the SAME lock (reconcileLocked,
// not Reconcile, which would try to take the lock a second time and refuse
// itself as busy), then activates every profile that still has an
// assignment. Activating a release that is already the assignment's own is
// exactly the idempotent repair path a degraded generation already uses
// while the system stays up (see Activate's doc) — Restore's whole
// contribution is running it once per assignment with nobody watching, and
// publishOrAdopt's completeness check (P08b) is what makes that safe to do
// unconditionally rather than only when something looks wrong: every
// generation IS wrong, in the reboot-specific way of simply not existing
// yet, and that check is what tells Activate to rebuild rather than trust a
// record naming mounts a reboot removed.
func (c *Controller) Restore(ctx context.Context, opID string) (*RestoreReport, error) {
	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()

	rep, err := c.reconcileLocked(ctx, opID)
	if err != nil {
		return rep, err
	}

	assignments, err := c.asg.LoadAll()
	if err != nil {
		return rep, err
	}
	for _, a := range assignments {
		if a.Release == "" {
			continue
		}
		prof, err := loadDurableProfile(c.cfg, a.Profile)
		if err != nil {
			rep.Failed[a.Profile] = "no durable profile document to restore with: " + err.Error()
			continue
		}
		restoreOp := opID + "-" + a.Profile
		if _, err := c.activate(ctx, restoreOp, KindRestore, a.Release, prof); err != nil {
			if isHeld(err) {
				rep.Blocked = append(rep.Blocked, a.Profile+"/"+a.Release)
			} else {
				rep.Failed[a.Profile] = err.Error()
			}
			continue
		}
		rep.Restored = append(rep.Restored, a.Profile+"/"+a.Release)
	}
	sort.Strings(rep.Restored)
	sort.Strings(rep.Blocked)
	return rep, nil
}

// currentGenerations maps a profile to the generation id its assignment's
// release resolves to, for every profile that has one.
func (c *Controller) currentGenerations() (map[string]string, error) {
	assignments, err := c.asg.LoadAll()
	if err != nil {
		return nil, err
	}
	out := make(map[string]string, len(assignments))
	for _, a := range assignments {
		if a.Release != "" {
			out[a.Profile] = publish.GenerationID(a.Release)
		}
	}
	return out, nil
}

// loadDurableProfile reads a profile document by id from the durable copy
// D-026 keeps, for a caller (Restore) that has an id and nobody to hand it a
// --profile path.
func loadDurableProfile(cfg config.Config, profileID string) (*profile.Profile, error) {
	return profile.Load(cfg.ProfileDocument(profileID))
}

// splitGenerationName reverses the "<profile>/<generation>" names Reconcile
// builds. Profile ids cannot themselves contain "/" (config.ValidName), so
// the first component is always the profile.
func splitGenerationName(name string) (profileID, generation string, ok bool) {
	i := strings.IndexByte(name, '/')
	if i < 0 {
		return "", "", false
	}
	return name[:i], name[i+1:], true
}

// isHeld reports whether err is publish's refusal because a consumer still
// holds the generation reconciliation was about to touch.
func isHeld(err error) bool {
	var held *consumer.HeldError
	return errors.As(err, &held)
}
