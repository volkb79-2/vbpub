package publish

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"srdm/internal/journal"
	"srdm/internal/mountinfo"
)

// Teardown removes a published generation's mounts and holds, in the one
// order that actually returns the memory.
//
//  1. drop the read-only bind exposure
//  2. unmount the op tmpfs — THIS is what frees and uncharges the pages
//  3. stop the hold unit
//  4. stop the generation slice, and drop the floor it was given
//
// Dropping the exposure first matters because the bind is the reference a
// consumer could still be holding; unmounting the op tmpfs while a bind
// survives frees nothing at all — measured, D-012 — and the pages stay
// charged to a cgroup that is about to be removed.
//
// Stopping the unit before the slice, and the slice at all, is also measured
// rather than assumed: a slice stays active and keeps its cgroup after its
// last service exits, so a teardown that stops only the services leaves the
// generation's aggregate behind.
//
// Teardown does NOT decide whether it is safe to run. Resolving live
// consumers and refusing while one holds a bind is P05's job, and it runs
// before this.
func (p *Publisher) Teardown(ctx context.Context, opID, profileID, generation string) error {
	rec, err := p.LoadRecord(profileID, generation)
	if err != nil {
		return fmt.Errorf("publish: teardown %s/%s: %w", profileID, generation, err)
	}
	fields := recFields(rec)

	if err := p.phase(opID, KindTeardown, PhaseTeardown, fields, func() error {
		return p.releaseRecord(ctx, rec)
	}); err != nil {
		return err
	}

	// The record goes last: while it exists, reconciliation knows these
	// mounts were meant to be there and can republish them. Removing it
	// first would turn an interrupted teardown into an orphan.
	return p.phase(opID, KindTeardown, PhaseRecord, fields, func() error {
		if err := os.Remove(p.cfg.PublishedRecord(profileID, generation)); err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	})
}

// releaseRecord drops every mount and every hold a record names, in the
// order that uncharges.
func (p *Publisher) releaseRecord(ctx context.Context, rec *Record) error {
	var errs []error
	for _, c := range rec.Classes {
		if err := p.unmountIfMounted(c.ExposePath); err != nil {
			errs = append(errs, fmt.Errorf("exposure %s: %w", c.ExposePath, err))
		}
	}
	for _, c := range rec.Classes {
		if err := p.unmountIfMounted(c.OpMount); err != nil {
			errs = append(errs, fmt.Errorf("op tmpfs %s: %w", c.OpMount, err))
		}
	}
	// Only now: while a mount survived, stopping the unit would have left
	// the pages charged to a cgroup with nothing left to attribute them to.
	for _, c := range rec.Classes {
		if c.HoldUnit == "" {
			continue
		}
		if err := p.holder.Forget(ctx, c.HoldUnit); err != nil {
			errs = append(errs, fmt.Errorf("hold unit %s: %w", c.HoldUnit, err))
		}
	}
	if rec.Slice != "" {
		if err := p.holder.ReleaseSlice(ctx, rec.Slice); err != nil {
			errs = append(errs, fmt.Errorf("slice %s: %w", rec.Slice, err))
		}
	}
	if err := errors.Join(errs...); err != nil {
		return err
	}

	// Directories, once nothing is mounted on them.
	for _, c := range rec.Classes {
		_ = os.Remove(c.ExposePath)
		_ = os.Remove(filepath.Join(c.OpMount, "root"))
		_ = os.Remove(c.OpMount)
	}
	_ = os.Remove(p.cfg.GenerationDir(rec.Profile, rec.Generation))
	_ = os.Remove(p.cfg.OpDir(rec.OperationID))
	return nil
}

// unmountIfMounted unmounts a path, tolerating one that is already gone.
//
// EINVAL from umount means "not a mount point", which after a crash or a
// partial teardown is the normal case and not an error: the goal is that
// nothing is mounted there, and it already is not.
func (p *Publisher) unmountIfMounted(target string) error {
	err := p.mounter.Unmount(target, 0)
	if err == nil {
		return nil
	}
	if errors.Is(err, syscall.EINVAL) || errors.Is(err, syscall.ENOENT) {
		return nil
	}
	return err
}

// teardownOp cleans up after a failed publication.
//
// It works from the record the publication was BUILDING rather than from a
// completed one, because a publication that failed has no completed record —
// that is what failing means here. Every class is named in that record from
// the start precisely so this path can reach the one that was in flight.
func (p *Publisher) teardownOp(ctx context.Context, opID string, rec *Record) error {
	// Sweep the operation's own subtree for anything mounted that the record
	// does not name — a mount made between two named steps, or by a previous
	// attempt with the same operation id.
	entries, err := p.mountInfo()
	if err == nil {
		opDir := p.cfg.OpDir(opID)
		genDir := p.cfg.GenerationDir(rec.Profile, rec.Generation)
		var extra []string
		for _, e := range mountinfo.Under(entries, opDir) {
			extra = append(extra, e.MountPoint)
		}
		for _, e := range mountinfo.Under(entries, genDir) {
			extra = append(extra, e.MountPoint)
		}
		// Deepest first, so a nested mount never blocks its parent.
		sort.Sort(sort.Reverse(sort.StringSlice(extra)))
		for _, m := range extra {
			_ = p.unmountIfMounted(m)
		}
	}
	if uerr := p.releaseRecord(ctx, rec); uerr != nil {
		return uerr
	}
	_ = os.RemoveAll(p.cfg.OpDir(opID))
	return nil
}

// Reconciliation is what the mount table, the durable records and systemd
// agree and disagree about.
type Reconciliation struct {
	// Healthy names generations whose record, mounts and holds all exist.
	Healthy []string
	// NeedsRepublish names generations whose topology is incomplete in any
	// way. A record alone proves nothing — after a reboot, every record is
	// in this state.
	NeedsRepublish []string
	// Orphans are mount points under the srdm roots that no record claims.
	// They are torn down: a mount nobody recorded is one nobody can reason
	// about, and it holds memory.
	Orphans []string
	// NotReadOnly names exposure binds that exist but are writable — a
	// publication interrupted between the bind and the remount.
	NotReadOnly []string
	// Unheld names classes whose content is mounted but whose hold unit is
	// gone. The pages are still there and still charged — to a cgroup that
	// has been removed and carries no policy at all, so the class floor
	// protects nothing and the memory is attributable to nobody.
	Unheld []string
}

// Reconcile compares the durable records against the kernel's mount table
// and against systemd.
//
// Neither source is trusted alone, and the master plan is explicit about
// why: "a COMPLETE or published-state file alone proves nothing about mount
// topology — a published record without its mounts triggers republish;
// mounts without a record are torn down as orphans." P04 adds the third
// source, because a class can be fully mounted and still not be held.
func (p *Publisher) Reconcile(ctx context.Context, opID string) (*Reconciliation, error) {
	res := &Reconciliation{}

	entries, err := p.mountInfo()
	if err != nil {
		return nil, fmt.Errorf("publish: reconcile: %w", err)
	}
	claimed := make(map[string]bool)

	records, err := p.listRecords()
	if err != nil {
		return nil, err
	}
	for _, rec := range records {
		name := rec.Profile + "/" + rec.Generation
		complete := true
		for _, c := range rec.Classes {
			claimed[c.OpMount] = true
			claimed[c.ExposePath] = true

			_, opOK := mountinfo.At(entries, c.OpMount)
			exposeMnt, exposeOK := mountinfo.At(entries, c.ExposePath)
			if !opOK || !exposeOK {
				complete = false
				continue
			}
			if !exposeMnt.ReadOnly() {
				res.NotReadOnly = append(res.NotReadOnly, c.ExposePath)
				complete = false
			}
			if c.HoldUnit == "" {
				continue
			}
			// An error here is systemd being unreachable, not a unit being
			// absent — `systemctl show` reports a unit it has never heard of
			// as inactive, with a zero exit. So this cannot conclude
			// anything and must not guess: guessing "unheld" would schedule
			// a republish of a healthy generation.
			active, err := p.holder.IsActive(ctx, c.HoldUnit)
			if err != nil {
				return nil, fmt.Errorf("publish: reconcile %s: %w", name, err)
			}
			if !active {
				res.Unheld = append(res.Unheld, c.HoldUnit)
				complete = false
			}
		}
		if complete {
			res.Healthy = append(res.Healthy, name)
		} else {
			res.NeedsRepublish = append(res.NeedsRepublish, name)
		}
	}

	// Anything mounted under an srdm root that no record claims.
	for _, root := range []string{p.cfg.OpRoot(), p.cfg.RunDir} {
		for _, e := range mountinfo.Under(entries, root) {
			if claimed[e.MountPoint] || e.MountPoint == p.cfg.RunDir {
				continue
			}
			if !containsString(res.Orphans, e.MountPoint) {
				res.Orphans = append(res.Orphans, e.MountPoint)
			}
		}
	}

	sort.Strings(res.Healthy)
	sort.Strings(res.NeedsRepublish)
	sort.Strings(res.NotReadOnly)
	sort.Strings(res.Unheld)
	// Deepest first, so tearing them down in order never blocks on a child.
	sort.Sort(sort.Reverse(sort.StringSlice(res.Orphans)))

	return res, p.jnl.Emit(journal.Record{
		OperationID: opID, Kind: KindReconcile, Phase: PhaseRecord,
		Outcome: journal.OutcomeOK,
		Message: "compared published records against the mount table and systemd",
		Fields: map[string]string{
			"healthy":         strings.Join(res.Healthy, ","),
			"needs_republish": strings.Join(res.NeedsRepublish, ","),
			"unheld":          strings.Join(res.Unheld, ","),
			"orphan_count":    itoa(int64(len(res.Orphans))),
		},
	})
}

// TeardownOrphans unmounts every mount reconciliation could not attribute.
func (p *Publisher) TeardownOrphans(opID string, rec *Reconciliation) error {
	var errs []error
	for _, m := range rec.Orphans {
		fields := map[string]string{"mount": m}
		err := p.phase(opID, KindReconcile, PhaseTeardown, fields, func() error {
			return p.unmountIfMounted(m)
		})
		if err != nil {
			errs = append(errs, err)
			continue
		}
		_ = os.Remove(m)
	}
	return errors.Join(errs...)
}

func (p *Publisher) listRecords() ([]*Record, error) {
	var out []*Record
	profiles, err := os.ReadDir(p.cfg.PublishedDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	for _, pe := range profiles {
		if !pe.IsDir() {
			continue
		}
		files, err := os.ReadDir(filepath.Join(p.cfg.PublishedDir(), pe.Name()))
		if err != nil {
			return nil, err
		}
		for _, fe := range files {
			name := fe.Name()
			if fe.IsDir() || filepath.Ext(name) != ".json" {
				continue
			}
			rec, err := p.LoadRecord(pe.Name(), strings.TrimSuffix(name, ".json"))
			if err != nil {
				return nil, err
			}
			out = append(out, rec)
		}
	}
	return out, nil
}

func containsString(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}
