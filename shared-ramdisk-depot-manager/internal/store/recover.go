package store

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"srdm/internal/fsx"
	"srdm/internal/journal"
)

// RecoveryReport is what Recover found and did.
type RecoveryReport struct {
	// Adopted lists releases recovered from transactions that had reached
	// COMPLETE but had not yet been renamed into place.
	Adopted []string
	// Quarantined lists transaction and release directories moved aside.
	Quarantined []string
	// Channels maps "<profile>/<channel>" to the release id it resolves to.
	// Oracle O1's "the journal names which": this is that answer.
	Channels map[string]string
	// DanglingChannels maps "<profile>/<channel>" to the reason it does not
	// resolve to a usable release.
	DanglingChannels map[string]string
}

// QuarantineDirName is where unusable directories are moved.
const QuarantineDirName = "quarantine"

// Recover brings the store to a consistent state after a crash.
//
// The rule COMPLETE encodes is applied literally, in both directions:
//
//   - a transaction that reached COMPLETE and still verifies is *adopted* —
//     it is by definition whole and durable, so discarding it would throw
//     away finished work for no safety gain;
//   - anything else is quarantined rather than deleted, because a store
//     that silently erases a half-written transaction erases the evidence
//     of why it was half-written.
//
// A channel is never repointed here. Recovery decides what exists; an
// operator decides what is active.
func (s *Store) Recover(opID string) (*RecoveryReport, error) {
	report := &RecoveryReport{
		Channels:         map[string]string{},
		DanglingChannels: map[string]string{},
	}

	if err := s.phase(opID, KindRecover, PhasePublish, nil, func() error {
		return s.recoverTransactions(opID, report)
	}); err != nil {
		return report, err
	}

	if err := s.phase(opID, KindRecover, PhaseComplete, nil, func() error {
		return s.recoverReleases(opID, report)
	}); err != nil {
		return report, err
	}

	if err := s.phase(opID, KindRecover, PhaseFlip, nil, func() error {
		return s.recoverChannels(opID, report)
	}); err != nil {
		return report, err
	}

	sort.Strings(report.Adopted)
	sort.Strings(report.Quarantined)
	return report, nil
}

func (s *Store) recoverTransactions(opID string, report *RecoveryReport) error {
	entries, err := os.ReadDir(s.cfg.TxDir())
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		txID := e.Name()
		dir := filepath.Join(s.cfg.TxDir(), txID)

		rel, loadErr := LoadReleaseDir(txID, dir)
		if loadErr != nil {
			if err := s.quarantine(opID, dir, txID, "transaction", loadErr.Error(), report); err != nil {
				return err
			}
			continue
		}
		if err := rel.Manifest.Verify(filepath.Join(dir, RootDir)); err != nil {
			if err := s.quarantine(opID, dir, txID, "transaction",
				"reached "+CompleteFile+" but does not verify: "+err.Error(), report); err != nil {
				return err
			}
			continue
		}

		target := s.cfg.ReleaseDir(rel.Complete.ReleaseID)
		if _, err := os.Lstat(target); err == nil {
			if err := s.quarantine(opID, dir, txID, "transaction",
				"release "+rel.Complete.ReleaseID+" already exists", report); err != nil {
				return err
			}
			continue
		} else if !os.IsNotExist(err) {
			return err
		}

		if err := os.Rename(dir, target); err != nil {
			return err
		}
		if err := fsx.SyncDir(s.cfg.ReleasesDir()); err != nil {
			return err
		}
		if err := fsx.SyncDir(s.cfg.TxDir()); err != nil {
			return err
		}
		report.Adopted = append(report.Adopted, rel.Complete.ReleaseID)
		if err := s.jnl.Emit(journal.Record{
			OperationID: opID, Kind: KindRecover, Phase: string(PhasePublish),
			Outcome: journal.OutcomeOK,
			Message: "adopted a transaction that had reached " + CompleteFile,
			Fields: map[string]string{
				"transaction": txID,
				"release_id":  rel.Complete.ReleaseID,
			},
		}); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) recoverReleases(opID string, report *RecoveryReport) error {
	entries, err := os.ReadDir(s.cfg.ReleasesDir())
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		id := e.Name()
		if _, err := LoadReleaseDir(id, s.cfg.ReleaseDir(id)); err != nil {
			if err := s.quarantine(opID, s.cfg.ReleaseDir(id), id, "release", err.Error(), report); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *Store) recoverChannels(opID string, report *RecoveryReport) error {
	profiles, err := os.ReadDir(s.cfg.ChannelsDir())
	if err != nil {
		return err
	}
	for _, pe := range profiles {
		if !pe.IsDir() {
			continue
		}
		links, err := os.ReadDir(filepath.Join(s.cfg.ChannelsDir(), pe.Name()))
		if err != nil {
			return err
		}
		for _, le := range links {
			name := pe.Name() + "/" + le.Name()
			releaseID, err := s.Resolve(pe.Name(), le.Name())
			if err != nil {
				report.DanglingChannels[name] = err.Error()
				continue
			}
			if _, err := s.Load(releaseID); err != nil {
				report.DanglingChannels[name] = err.Error()
				if jerr := s.jnl.Emit(journal.Record{
					OperationID: opID, Kind: KindRecover, Phase: string(PhaseFlip),
					Outcome: journal.OutcomeRefused,
					Message: "channel points at a release that is not usable",
					Fields:  map[string]string{"channel": name, "release_id": releaseID},
					Error:   err.Error(),
				}); jerr != nil {
					return jerr
				}
				continue
			}
			report.Channels[name] = releaseID
			if err := s.jnl.Emit(journal.Record{
				OperationID: opID, Kind: KindRecover, Phase: string(PhaseFlip),
				Outcome: journal.OutcomeOK,
				Message: "channel resolves to a verified release",
				Fields:  map[string]string{"channel": name, "release_id": releaseID},
			}); err != nil {
				return err
			}
		}
	}
	return nil
}

// quarantine moves a directory aside and journals why.
func (s *Store) quarantine(opID, dir, name, what, reason string, report *RecoveryReport) error {
	target, err := s.quarantineTarget(name)
	if err != nil {
		return err
	}
	if err := os.Rename(dir, target); err != nil {
		return err
	}
	if err := fsx.SyncDir(s.cfg.QuarantineDir()); err != nil {
		return err
	}
	if err := fsx.SyncDir(filepath.Dir(dir)); err != nil {
		return err
	}
	report.Quarantined = append(report.Quarantined, name)
	return s.jnl.Emit(journal.Record{
		OperationID: opID, Kind: KindRecover, Phase: string(PhaseComplete),
		Outcome: journal.OutcomeRefused,
		Message: "quarantined an unusable " + what,
		Fields: map[string]string{
			"name":            name,
			"quarantine_path": target,
		},
		Error: reason,
	})
}

// quarantineTarget picks a free name under the quarantine directory.
// Suffixes are a deterministic counter rather than a timestamp, so the same
// recovery over the same store always produces the same layout.
func (s *Store) quarantineTarget(name string) (string, error) {
	base := filepath.Join(s.cfg.QuarantineDir(), name)
	if _, err := os.Lstat(base); os.IsNotExist(err) {
		return base, nil
	} else if err != nil {
		return "", err
	}
	for i := 1; i < 10000; i++ {
		candidate := fmt.Sprintf("%s.%d", base, i)
		if _, err := os.Lstat(candidate); os.IsNotExist(err) {
			return candidate, nil
		} else if err != nil {
			return "", err
		}
	}
	return "", fmt.Errorf("store: cannot find a free quarantine name for %q", name)
}
