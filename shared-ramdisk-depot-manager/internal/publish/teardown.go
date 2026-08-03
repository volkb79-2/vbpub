package publish

import (
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

// Teardown removes a published generation's mounts, in the one order that
// actually returns the memory.
//
//  1. drop the read-only bind exposure
//  2. unmount the op tmpfs — THIS is what frees and uncharges the pages
//  3. (P04) stop the hold unit, then the generation slice
//
// Dropping the exposure first matters because the bind is the reference a
// consumer could still be holding; unmounting the op tmpfs while a bind
// survives frees nothing at all — measured, D-012 — and the pages stay
// charged to a cgroup that is about to be removed.
//
// Teardown does NOT decide whether it is safe to run. Resolving live
// consumers and refusing while one holds a bind is P05's job, and it runs
// before this.
func (p *Publisher) Teardown(opID, profileID, generation string) error {
	rec, err := p.LoadRecord(profileID, generation)
	if err != nil {
		return fmt.Errorf("publish: teardown %s/%s: %w", profileID, generation, err)
	}
	fields := recFields(rec)

	if err := p.phase(opID, KindTeardown, PhaseTeardown, fields, func() error {
		return p.unmountRecord(rec)
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

// unmountRecord drops every mount a record names, exposure before op tmpfs.
func (p *Publisher) unmountRecord(rec *Record) error {
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
// It works from what was actually mounted so far rather than from a
// completed record, because a publication that failed has no completed
// record — that is what failing means here.
func (p *Publisher) teardownOp(opID string, rec *Record, profileID, gen string) error {
	partial := &Record{
		Generation:  gen,
		Profile:     profileID,
		OperationID: opID,
		Classes:     rec.Classes,
	}
	// The class that was mid-flight when it failed is not in rec.Classes
	// yet, so sweep the operation's own subtree for anything mounted.
	entries, err := p.mountInfo()
	if err == nil {
		opDir := p.cfg.OpDir(opID)
		genDir := p.cfg.GenerationDir(profileID, gen)
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
	if uerr := p.unmountRecord(partial); uerr != nil {
		return uerr
	}
	_ = os.RemoveAll(p.cfg.OpDir(opID))
	return nil
}

// Reconciliation is what the mount table and the durable records agree and
// disagree about.
type Reconciliation struct {
	// Healthy names generations whose records and mounts both exist.
	Healthy []string
	// NeedsRepublish names generations with a record but missing mounts. A
	// record alone proves nothing about mount topology — after a reboot,
	// every record is in this state.
	NeedsRepublish []string
	// Orphans are mount points under the srdm roots that no record claims.
	// They are torn down: a mount nobody recorded is one nobody can reason
	// about, and it holds memory.
	Orphans []string
	// NotReadOnly names exposure binds that exist but are writable — a
	// publication interrupted between the bind and the remount.
	NotReadOnly []string
}

// Reconcile compares the durable records against the kernel's mount table.
//
// Neither source is trusted alone, and the master plan is explicit about
// why: "a COMPLETE or published-state file alone proves nothing about mount
// topology — a published record without its mounts triggers republish;
// mounts without a record are torn down as orphans."
func (p *Publisher) Reconcile(opID string) (*Reconciliation, error) {
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

			opMnt, opOK := mountinfo.At(entries, c.OpMount)
			exposeMnt, exposeOK := mountinfo.At(entries, c.ExposePath)
			if !opOK || !exposeOK {
				complete = false
				continue
			}
			_ = opMnt
			if !exposeMnt.ReadOnly() {
				res.NotReadOnly = append(res.NotReadOnly, c.ExposePath)
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
	// Deepest first, so tearing them down in order never blocks on a child.
	sort.Sort(sort.Reverse(sort.StringSlice(res.Orphans)))

	return res, p.jnl.Emit(journal.Record{
		OperationID: opID, Kind: KindReconcile, Phase: PhaseRecord,
		Outcome: journal.OutcomeOK,
		Message: "compared published records against the mount table",
		Fields: map[string]string{
			"healthy":         strings.Join(res.Healthy, ","),
			"needs_republish": strings.Join(res.NeedsRepublish, ","),
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
