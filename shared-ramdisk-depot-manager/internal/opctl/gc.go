package opctl

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/publish"
)

// Collection is what `gc` would do, or did.
type Collection struct {
	// Removed names releases collected.
	Removed []string
	// Kept names releases that survived, each with the reason.
	Kept map[string]string
}

// Reasons a release is kept.
//
// A release can be pinned several times over — the active one usually is both
// assigned and published — and only one reason is reported. The order below
// is the order they are tried, and it is most-declarative-first on purpose:
// the useful answer to "why is this kept" is the one the operator can change.
// `published` is a consequence of `assigned` for the active release, so
// reporting it there would name the symptom; reporting it for a release
// nobody assigned, which is the rolled-back-but-not-yet-torn-down case, names
// exactly the thing that is surprising.
const (
	KeptAssigned  = "assigned: the profile's active release"
	KeptRollback  = "rollback target: the release the profile was on before"
	KeptPublished = "published: a live generation was built from it"
	KeptChannel   = "channel target"
	KeptRetention = "retention"
)

// GC removes releases nothing points at, keeping the configured number of
// most recent ones.
//
// The master plan's rule is "removable when not assigned, no live lease, no
// labeled container in any state". Two thirds of that is v2: leases and
// Wings-constructed labels arrive with the provider protocol, and in the v1
// host-bind shape a container never references a RELEASE at all — it has a
// bind of a generation's tmpfs, whose superblock is what teardown refuses
// over. So the v1 rule is the same rule with the two absent terms dropped,
// and the term that replaces them is `published`: a release with a live
// generation is one somebody is reading right now, whatever the assignment
// says.
//
// dryRun reports without removing. It is not a convenience — a garbage
// collector whose decisions cannot be inspected before it acts is one nobody
// will run on a node that matters.
func (c *Controller) GC(opID string, dryRun bool) (*Collection, error) {
	lock, err := Acquire(c.cfg)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Release() }()

	res := &Collection{Kept: map[string]string{}}
	fields := map[string]string{
		"retention": itoa(c.cfg.Retention),
		"dry_run":   fmt.Sprintf("%t", dryRun),
	}

	if err := c.phase(opID, KindGC, PhasePlan, fields, func() error {
		return c.planCollection(res)
	}); err != nil {
		return nil, err
	}
	if dryRun || len(res.Removed) == 0 {
		return res, nil
	}

	fields["removing"] = strings.Join(res.Removed, ",")
	if err := c.phase(opID, KindGC, PhaseCollect, fields, func() error {
		for _, id := range res.Removed {
			if err := os.RemoveAll(c.cfg.ReleaseDir(id)); err != nil {
				return fmt.Errorf("opctl: removing release %s: %w", id, err)
			}
		}
		return nil
	}); err != nil {
		return nil, err
	}
	return res, nil
}

// planCollection decides what may go, and records why everything else stays.
func (c *Controller) planCollection(res *Collection) error {
	ids, err := c.st.List()
	if err != nil {
		return err
	}

	// Pin 1 and 2: what an operator declared. Every profile's active release
	// and its rollback target.
	assignments, err := c.asg.LoadAll()
	if err != nil {
		return err
	}
	for _, a := range assignments {
		if a.Release != "" {
			keepIfUnpinned(res.Kept, a.Release, KeptAssigned)
		}
		if a.Previous != "" {
			keepIfUnpinned(res.Kept, a.Previous, KeptRollback)
		}
	}

	// Pin 3: what is live. A published record is srdm's own statement that a
	// generation was built from this release and may still be mounted, and it
	// outranks every declaration — the pages exist whatever anybody meant.
	records, err := publish.ListRecordsAt(c.cfg)
	if err != nil {
		return err
	}
	for _, rec := range records {
		keepIfUnpinned(res.Kept, rec.ReleaseID, KeptPublished)
	}

	// Pin 4: channel targets. A channel is a name somebody else may resolve,
	// and collecting what it points at turns a working reference into a
	// dangling one.
	channels, err := c.channelTargets()
	if err != nil {
		return err
	}
	for _, id := range channels {
		keepIfUnpinned(res.Kept, id, KeptChannel)
	}

	// Everything left is a candidate, newest first. "Newest" is the store's
	// own order — release ids sort lexically and are the operator's to
	// choose — rather than a timestamp: a release has no mtime that means
	// anything after a restore, and inventing one would make retention depend
	// on how the store was last copied.
	var candidates []string
	for _, id := range ids {
		if _, pinned := res.Kept[id]; !pinned {
			candidates = append(candidates, id)
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(candidates)))

	for i, id := range candidates {
		if i < c.cfg.Retention {
			res.Kept[id] = KeptRetention
			continue
		}
		res.Removed = append(res.Removed, id)
	}
	sort.Strings(res.Removed)
	return nil
}

// keepIfUnpinned records a reason without overwriting a stronger one, so a
// release that is both published and assigned reports the fact that matters.
func keepIfUnpinned(kept map[string]string, id, reason string) {
	if _, ok := kept[id]; !ok {
		kept[id] = reason
	}
}

// channelTargets is every release a channel symlink resolves to.
func (c *Controller) channelTargets() ([]string, error) {
	profiles, err := os.ReadDir(c.cfg.ChannelsDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []string
	for _, pe := range profiles {
		if !pe.IsDir() {
			continue
		}
		links, err := os.ReadDir(filepath.Join(c.cfg.ChannelsDir(), pe.Name()))
		if err != nil {
			return nil, err
		}
		for _, le := range links {
			target, err := c.st.Resolve(pe.Name(), le.Name())
			if err != nil {
				continue
			}
			out = append(out, target)
		}
	}
	return out, nil
}

// ProfileStatus is what one profile looks like right now: what was asked for,
// what is actually mounted, and who is holding it.
type ProfileStatus struct {
	Profile  string   `json:"profile"`
	Release  string   `json:"release,omitempty"`
	Previous string   `json:"previous,omitempty"`
	Servers  []string `json:"servers,omitempty"`
	// Generation is the id the assigned release would publish as, whether or
	// not it is published.
	Generation string `json:"generation,omitempty"`
	// Published is whether a record exists for it.
	Published bool `json:"published"`
	// Holders names what is holding the generation's content.
	Holders []string `json:"holders,omitempty"`
	// Degraded names checks that could not be run. Never silently empty: a
	// status that could not ask has to say so, or "nothing is holding this"
	// and "I could not tell" read identically.
	Degraded []string `json:"degraded,omitempty"`
	// DirtyCapable is the published record's flag: this generation has been
	// exposed writable, so its content no longer provably matches Release.
	DirtyCapable bool `json:"dirty_capable,omitempty"`
}

// Status reports declared intent beside measured reality, for every profile
// with an assignment.
//
// It takes no lock. A status that could be blocked by the operation somebody
// is trying to understand is a status nobody can use at the moment they need
// it most — and it only reads, so the worst it can do is describe a system
// mid-change, which is exactly what it was asked to do.
func (c *Controller) Status(ctx context.Context) ([]ProfileStatus, error) {
	assignments, err := c.asg.LoadAll()
	if err != nil {
		return nil, err
	}
	out := make([]ProfileStatus, 0, len(assignments))
	for _, a := range assignments {
		st := ProfileStatus{
			Profile: a.Profile, Release: a.Release, Previous: a.Previous,
			Servers: a.ServerIDs(),
		}
		if a.Release != "" {
			st.Generation = publish.GenerationID(a.Release)
			if rec, err := c.pub.LoadRecord(a.Profile, st.Generation); err == nil {
				st.Published = true
				st.DirtyCapable = rec.DirtyCapable
				rep, err := c.pub.Holders(ctx, a.Profile, st.Generation)
				switch {
				case err != nil:
					st.Degraded = append(st.Degraded, "holders: "+err.Error())
				default:
					for _, h := range rep.Holders {
						st.Holders = append(st.Holders, h.String())
					}
					st.Degraded = append(st.Degraded, rep.Degraded...)
				}
			}
		}
		out = append(out, st)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Profile < out[j].Profile })
	return out, nil
}

// Releases lists the store, so `status` can show what is available to
// activate without a second command.
func (c *Controller) Releases() ([]string, error) {
	ids, err := c.st.List()
	if err != nil {
		return nil, err
	}
	return sorted(ids), nil
}
