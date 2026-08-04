package doctor

import (
	"fmt"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/profile"
	"srdm/internal/publish"
)

// checkUpdateHeadroom reports whether the host could sustain a cohort
// update of the profile's CURRENT release — P14's own preflight
// (opctl.checkHeadroom) refuses the same shortfall against a specific
// target release, immediately before stopping anything; this is the same
// question asked on a quiet afternoon, against the only release doctor has
// to ask it about.
//
// The sizing arithmetic and the /proc/meminfo read both live in
// internal/publish (publish.GenerationBytes, publish.AvailableHostBytes),
// beside ClassSize, which already owns the rounding rule they depend on.
// This check used to carry its own copy of both; two copies of "would this
// fit" is exactly the kind of duplication that drifts silently — doctor
// reporting headroom fine while `update` refuses the same release, or the
// reverse.
func checkUpdateHeadroom(cfg config.Config, p *profile.Profile, opts Options) Check {
	c := Check{ID: "update-headroom", Title: "the host can hold two generations for a cohort update"}
	if p == nil {
		c.Status = StatusSkip
		c.Detail = "no profile given"
		return c
	}
	asg, err := assign.New(cfg)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot open the assignment store: %v", err)
		return c
	}
	a, err := asg.Load(p.ID)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read %s's assignment: %v", p.ID, err)
		return c
	}
	if a.Release == "" {
		c.Status = StatusPass
		c.Detail = "no active release yet; a cohort update has nothing to hold twice"
		return c
	}
	rel, err := loadRelease(cfg, a.Release)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot load the active release %s: %v", a.Release, err)
		return c
	}
	one := publish.GenerationBytes(rel, p)
	need := 2 * one
	have, err := publish.AvailableHostBytes(opts.memInfoPath())
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read host memory accounting: %v", err)
		return c
	}
	if have < need {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("a cohort update of the current %s generation needs about %s "+
			"resident at once; the host has %s available", a.Release, formatBytes(need), formatBytes(have))
		c.Fix = "free memory before the next `srdm update`, or reduce a managed class's content"
		return c
	}
	c.Status = StatusPass
	c.Detail = fmt.Sprintf("the host has %s available, room for roughly two %s generations",
		formatBytes(have), formatBytes(one))
	return c
}
