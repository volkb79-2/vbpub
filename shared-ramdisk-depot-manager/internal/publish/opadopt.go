package publish

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"

	"srdm/internal/journal"
	"srdm/internal/mountinfo"
)

// OpRecovery is what AdoptOrQuarantine found and did.
type OpRecovery struct {
	// Adopted names operations whose plan was found fully realized in the
	// kernel — every class mounted, sealed read-only, and held — and were
	// completed by writing the published record they never got to write.
	Adopted []string
	// Quarantined names operations torn down because their plan was found
	// incomplete, naming the operation id.
	Quarantined []string
}

// KindAdopt names this pass in the journal.
const KindAdopt = "adopt"

// AdoptOrQuarantine resolves every operation plan a earlier process left
// behind without finishing.
//
// A hold unit outliving the process that started it is normal and by design
// (D-011/D-013): systemd-run returns once the worker signals ready, and the
// unit parks. This is about a different process dying — the srdm invocation
// itself, to a SIGKILL or an OOM kill, between writing the plan and writing
// the final published record — on a host that stays up through the crash, so
// what Publish started is still there to find.
//
// The master plan states the v2 daemon's version of this rule: "on restart
// the daemon adopts operations whose units are still active, quarantines
// operations whose units are gone without a result record." D-025 confirmed
// v1 has no daemon, so "on restart" becomes "the next time anything holds
// the operation lock" and "a result record" becomes the plan this package
// writes — but the rule itself does not change:
//
//   - every class the plan named is mounted, read-only, and held: the
//     operation finished in every way that matters, and only its last write
//     never happened. It is ADOPTED — completed by writing the record now —
//     rather than discarded, which would throw away verified, sealed
//     content for no safety gained.
//   - anything less is QUARANTINED: torn down whole, the same way a failed
//     Publish already tears itself down when the process survives to run its
//     own deferred cleanup. There is no partial-credit state to resume into
//     safely, and guessing which half of a class is trustworthy is exactly
//     the kind of guess this project measures instead of making.
//
// Must be called with the operation lock held — that is what makes it safe:
// a plan file existing at all, while this process holds the lock, means the
// process that wrote it cannot still be writing to it.
func (p *Publisher) AdoptOrQuarantine(ctx context.Context, opID string) (*OpRecovery, error) {
	report := &OpRecovery{}

	entries, err := os.ReadDir(p.cfg.OpRoot())
	if err != nil {
		if os.IsNotExist(err) {
			return report, nil
		}
		return nil, fmt.Errorf("publish: adopt/quarantine: %w", err)
	}

	mounts, err := p.mountInfo()
	if err != nil {
		return nil, fmt.Errorf("publish: adopt/quarantine: %w", err)
	}

	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		crashedOp := e.Name()
		planPath := p.cfg.OpPlanFile(crashedOp)
		body, err := os.ReadFile(planPath)
		if err != nil {
			if os.IsNotExist(err) {
				// No plan means either a live generation's own op directory
				// (committed, plan already removed on success) or, if
				// nothing is mounted here either, debris with nothing left
				// to say about it — reconciliation's Orphans handles that
				// case from the mount-table side, which is the only side a
				// plan-less directory can still be reasoned about from.
				continue
			}
			return nil, fmt.Errorf("publish: reading plan %s: %w", planPath, err)
		}

		var rec Record
		dec := json.NewDecoder(strings.NewReader(string(body)))
		dec.DisallowUnknownFields()
		if decErr := dec.Decode(&rec); decErr != nil {
			// A plan that does not even parse cannot be trusted for
			// anything, including its own class list — fall back to
			// sweeping whatever is mounted under this operation's directory
			// by path alone. This cannot stop the hold units: their names
			// live only in the plan this branch has just declared unusable.
			// A stopped-but-orphaned hold unit is stated as a residual gap,
			// not silently accepted — see backlog.
			if err := p.sweepOpDir(crashedOp); err != nil {
				return nil, err
			}
			report.Quarantined = append(report.Quarantined, crashedOp)
			continue
		}

		complete, _, _, err := p.classCompleteness(ctx, mounts, &rec)
		if err != nil {
			return nil, fmt.Errorf("publish: adopt/quarantine %s: %w", crashedOp, err)
		}

		if complete {
			if err := p.adoptOp(opID, &rec, report); err != nil {
				return nil, err
			}
			continue
		}
		if err := p.quarantineOp(ctx, &rec, report); err != nil {
			return nil, err
		}
	}

	sort.Strings(report.Adopted)
	sort.Strings(report.Quarantined)
	return report, nil
}

// adoptOp completes an operation whose plan is fully realized in the kernel:
// it writes the record the crashed process never got to write.
func (p *Publisher) adoptOp(opID string, rec *Record, report *OpRecovery) error {
	name := rec.Profile + "/" + rec.Generation
	if err := p.phase(opID, KindAdopt, PhaseRecord, map[string]string{
		"generation": rec.Generation, "profile": rec.Profile,
		"release_id": rec.ReleaseID, "adopted_op": rec.OperationID,
	}, func() error {
		return p.writeRecord(rec)
	}); err != nil {
		return err
	}
	_ = os.Remove(p.cfg.OpPlanFile(rec.OperationID))
	report.Adopted = append(report.Adopted, name)
	return nil
}

// quarantineOp tears down an operation whose plan is not fully realized,
// using the same mount-and-unit release path Publish's own crash cleanup
// uses — the plan names exactly what that cleanup needs.
func (p *Publisher) quarantineOp(ctx context.Context, rec *Record, report *OpRecovery) error {
	if err := p.teardownOp(ctx, rec.OperationID, rec); err != nil {
		return fmt.Errorf("publish: quarantining operation %s: %w", rec.OperationID, err)
	}
	report.Quarantined = append(report.Quarantined, rec.OperationID)
	return p.jnl.Emit(journal.Record{
		OperationID: rec.OperationID, Kind: KindAdopt, Phase: PhaseTeardown,
		Outcome: journal.OutcomeRefused,
		Message: "quarantined an operation plan that was not fully realized in the kernel",
		Fields:  map[string]string{"profile": rec.Profile, "generation": rec.Generation},
	})
}

// sweepOpDir tears down an operation directory whose plan could not even be
// parsed, by mount path alone rather than by a record it cannot trust.
//
// Deliberately narrower than teardownOp: it never derives a generation
// directory from the plan, because the plan is exactly what did not parse.
// teardownOp's generation-scoped sweep falls back to RunDir itself when a
// record's Profile and Generation are both empty (filepath.Join drops empty
// components), which would sweep every OTHER operation's live mounts too —
// so this walks only the one directory the crashed operation id names.
func (p *Publisher) sweepOpDir(crashedOp string) error {
	opDir := p.cfg.OpDir(crashedOp)
	entries, err := p.mountInfo()
	if err != nil {
		return fmt.Errorf("publish: sweeping unreadable operation %s: %w", crashedOp, err)
	}
	var mounts []string
	for _, e := range mountinfo.Under(entries, opDir) {
		mounts = append(mounts, e.MountPoint)
	}
	// Deepest first, so a nested mount never blocks its parent.
	sort.Sort(sort.Reverse(sort.StringSlice(mounts)))
	var errs []error
	for _, m := range mounts {
		if err := p.unmountIfMounted(m); err != nil {
			errs = append(errs, err)
		}
	}
	if err := errors.Join(errs...); err != nil {
		return fmt.Errorf("publish: sweeping unreadable operation %s: %w", crashedOp, err)
	}
	_ = os.RemoveAll(opDir)
	return p.jnl.Emit(journal.Record{
		OperationID: crashedOp, Kind: KindAdopt, Phase: PhaseTeardown,
		Outcome: journal.OutcomeRefused,
		Message: "quarantined an operation whose plan did not parse; its mounts were removed " +
			"but a hold unit named only in that plan could not be stopped",
		Fields: map[string]string{"operation": crashedOp},
	})
}
