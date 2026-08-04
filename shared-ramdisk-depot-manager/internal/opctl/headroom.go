package opctl

import (
	"fmt"

	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// HeadroomError is Update declining before anything is stopped, because the
// host cannot hold two generations at once.
//
// Publish always happens before the old generation is torn down — the same
// window Activate opens, and Update's own cohort cycle opens it for the
// same reason: a failed publish must leave something to restart the cohort
// on. Whether the SERVERS are up or down does not change this — a
// generation's tmpfs is charged to its own hold units regardless of who is
// consuming it — so the byte math a per-server rolling design would have
// needed is unchanged by the redesign to an ordered cohort cycle; only the
// DURATION of the overlap is shorter (one switch, not a whole pass over
// every server). Refusing up front (oracle 32) replaces "the cohort goes
// down and the publish then fails on ENOSPC or an OOM, leaving every
// server offline with nothing to come back to" with a refusal that costs
// nothing and changes nothing.
type HeadroomError struct {
	NeedBytes, HaveBytes, ShortfallBytes int64
}

func (e *HeadroomError) Error() string {
	return fmt.Sprintf("opctl: update needs room for two generations at once (%d bytes) but "+
		"the host has %d bytes available — short by %d bytes; free memory, or reduce a "+
		"class's content",
		e.NeedBytes, e.HaveBytes, e.ShortfallBytes)
}

// checkHeadroom refuses an update before anything is stopped when the host
// cannot hold both the live generation and the target one at once.
//
// The sizing arithmetic (publish.GenerationBytes) and the host fact
// (publish.AvailableHostBytes) both live in internal/publish, beside
// ClassSize, which already owns the rounding rule they depend on —
// deliberately the ONE place this arithmetic exists. It used to be copied
// here and, separately, into internal/doctor's own update-headroom check;
// two copies of the same "would this fit" question is exactly the kind of
// duplication that drifts silently, one side answering yes while the other
// answers no about the identical release.
//
// liveReleaseID is a.Release, which may be empty — a profile updating from
// nothing has no old generation to add, so the sum is just the target's.
func (c *Controller) checkHeadroom(target *store.Release, liveReleaseID string,
	prof *profile.Profile) error {

	need := publish.GenerationBytes(target, prof)
	if liveReleaseID != "" && liveReleaseID != target.ID {
		if live, err := c.st.Load(liveReleaseID); err == nil {
			need += publish.GenerationBytes(live, prof)
		}
		// A live release id srdm cannot load is a different failure this
		// operation meets elsewhere (LoadRecord, in moveServers); the
		// headroom check does not repeat that refusal here, it just cannot
		// add a number it does not have.
	}
	have, err := publish.AvailableHostBytes(c.memInfoPath)
	if err != nil {
		return err
	}
	if have < need {
		return &HeadroomError{NeedBytes: need, HaveBytes: have, ShortfallBytes: need - have}
	}
	return nil
}
