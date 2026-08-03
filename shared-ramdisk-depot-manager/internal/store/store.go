// Package store is srdm's persistent release store: the transactional path
// from a directory of freshly acquired content to an immutable,
// hash-verified release a channel can point at.
//
// The order is fixed by the master plan and by what a crash can do to it:
//
//	transaction -> classification -> per-file SHA-256 manifest -> probes ->
//	ownership normalization -> fsync'd COMPLETE written last ->
//	atomic channel symlink flip
//
// COMPLETE is written last and fsync'd because it is the only thing that
// makes a release believable. Everything before it can be interrupted and
// leaves no trace on any channel; everything after it is a rename.
package store

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/fsx"
	"srdm/internal/journal"
	"srdm/internal/profile"
)

// CompleteSchemaVersion is the COMPLETE document version.
const CompleteSchemaVersion = 1

// Phase names a boundary in the promotion pipeline. Every phase is
// journaled before it executes and after it settles, so a store recovered
// after a crash can say which phase it died in.
type Phase string

const (
	PhaseClassify  Phase = "classify"
	PhaseManifest  Phase = "manifest"
	PhaseProbes    Phase = "probes"
	PhaseOwnership Phase = "ownership"
	PhasePersist   Phase = "persist"
	PhaseComplete  Phase = "complete"
	PhasePublish   Phase = "publish"
	PhaseFlip      Phase = "flip"
)

// Operation kinds, as they appear in the journal.
const (
	KindPromote  = "promote"
	KindActivate = "activate"
	KindRecover  = "recover"
	KindVerify   = "verify"
)

// Provenance records where a release's content came from. `harvest` (P04)
// writes KindHarvested; a staged acquisition writes KindStaged.
type Provenance struct {
	Kind   string `json:"kind"`
	Source string `json:"source,omitempty"`
}

// Provenance kinds.
const (
	ProvenanceStaged    = "staged"
	ProvenanceHarvested = "harvested"
)

// Complete is the document written last. Its presence means the release
// beside it is whole and matches its manifest.
type Complete struct {
	SchemaVersion  int        `json:"schema_version"`
	ReleaseID      string     `json:"release_id"`
	Profile        string     `json:"profile"`
	OperationID    string     `json:"operation_id"`
	ContentDigest  string     `json:"content_digest"`
	ManifestSHA256 string     `json:"manifest_sha256"`
	Provenance     Provenance `json:"provenance"`
}

// Tx is an open transaction: a private directory the caller populates.
type Tx struct {
	// ID is the operation id; it names the transaction directory and every
	// journal record the promotion writes.
	ID string
	// Dir is the transaction directory.
	Dir string
	// Root is where content goes. Nothing else in Dir is part of the
	// release's content, which is how manifest.json and COMPLETE can sit
	// beside a tree without appearing in it.
	Root string
}

// Release is a promoted, verified, immutable release.
type Release struct {
	ID       string
	Dir      string
	Manifest *Manifest
	Complete *Complete
}

// Root is the release's content root.
func (r *Release) RootDir() string { return filepath.Join(r.Dir, RootDir) }

// Store owns the on-disk release store.
type Store struct {
	cfg   config.Config
	jnl   *journal.Journal
	chown chownFunc

	// hook fires at each phase boundary, after that phase's journal record
	// is durable. It exists for the kill-at-every-phase oracle, which stops
	// a real child process at a real boundary and kills it; nothing in
	// production sets it.
	hook func(Phase) error
}

// Option configures a Store.
type Option func(*Store)

// WithChown injects the chown syscall. Tests use it to record what the
// ownership walk would have done without needing to be root.
func WithChown(f chownFunc) Option {
	return func(s *Store) { s.chown = f }
}

// Open prepares the store layout under the configured state directory.
func Open(cfg config.Config, jnl *journal.Journal, opts ...Option) (*Store, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("store: a journal is required")
	}
	s := &Store{cfg: cfg, jnl: jnl, chown: os.Lchown}
	for _, o := range opts {
		o(s)
	}
	for _, dir := range []string{
		cfg.StoreDir(), cfg.ReleasesDir(), cfg.ChannelsDir(),
		cfg.TxDir(), cfg.QuarantineDir(),
	} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("store: create %s: %w", dir, err)
		}
	}
	return s, nil
}

// Config returns the store's configuration.
func (s *Store) Config() config.Config { return s.cfg }

// Begin opens a transaction directory for operation opID.
func (s *Store) Begin(opID string) (*Tx, error) {
	if err := config.ValidName("operation id", opID); err != nil {
		return nil, fmt.Errorf("store: %w", err)
	}
	dir := filepath.Join(s.cfg.TxDir(), opID)
	if _, err := os.Lstat(dir); err == nil {
		return nil, fmt.Errorf("store: transaction %q already exists at %s", opID, dir)
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	root := filepath.Join(dir, RootDir)
	if err := os.MkdirAll(root, 0o755); err != nil {
		return nil, fmt.Errorf("store: create transaction %q: %w", opID, err)
	}
	if err := fsx.SyncDir(s.cfg.TxDir()); err != nil {
		return nil, err
	}
	return &Tx{ID: opID, Dir: dir, Root: root}, nil
}

// Discard removes an open transaction.
func (s *Store) Discard(tx *Tx) error {
	if err := os.RemoveAll(tx.Dir); err != nil {
		return err
	}
	return fsx.SyncDir(s.cfg.TxDir())
}

// PromoteOpts parameterizes a promotion.
type PromoteOpts struct {
	// ReleaseID names the release. It is supplied rather than generated so
	// promotion is deterministic and a caller can retry an interrupted
	// operation under the same identity.
	ReleaseID string
	// Provenance records where the content came from.
	Provenance Provenance
	// Channel, when set, is flipped to the new release once it is published.
	// The flip is a separate journaled phase; a crash before it leaves the
	// release published and the channel untouched.
	Channel string
}

// Promote turns a populated transaction into an immutable release.
//
// On success the transaction directory no longer exists: it *became* the
// release, by rename. On any failure the store is untouched — no channel
// moves, no release appears — and the transaction is left in place for
// Recover to quarantine.
func (s *Store) Promote(tx *Tx, p *profile.Profile, opts PromoteOpts) (*Release, error) {
	if err := config.ValidName("release id", opts.ReleaseID); err != nil {
		return nil, fmt.Errorf("store: %w", err)
	}
	if err := config.ValidName("profile id", p.ID); err != nil {
		return nil, fmt.Errorf("store: %w", err)
	}
	if opts.Channel != "" {
		if err := config.ValidName("channel", opts.Channel); err != nil {
			return nil, fmt.Errorf("store: %w", err)
		}
	}
	relDir := s.cfg.ReleaseDir(opts.ReleaseID)
	if _, err := os.Lstat(relDir); err == nil {
		return nil, fmt.Errorf("store: release %q already exists", opts.ReleaseID)
	} else if !os.IsNotExist(err) {
		return nil, err
	}

	base := map[string]string{
		"release_id": opts.ReleaseID,
		"profile":    p.ID,
		"provenance": opts.Provenance.Kind,
	}
	if opts.Provenance.Source != "" {
		base["provenance_source"] = opts.Provenance.Source
	}

	// Classification first, and on its own: it is the cheapest phase and the
	// one most likely to refuse, so an unclassified path costs the operator
	// a walk rather than a full hash of a multi-gigabyte tree.
	if err := s.phase(tx.ID, KindPromote, PhaseClassify, base, func() error {
		return classifyTree(tx.Root, p)
	}); err != nil {
		return nil, err
	}

	var manifest *Manifest
	if err := s.phase(tx.ID, KindPromote, PhaseManifest, base, func() error {
		m, err := BuildManifest(tx.Root, p)
		if err != nil {
			return err
		}
		manifest = m
		return nil
	}); err != nil {
		return nil, err
	}

	if err := s.phase(tx.ID, KindPromote, PhaseProbes, base, func() error {
		return runProbes(tx.Root, p)
	}); err != nil {
		return nil, err
	}

	if err := s.phase(tx.ID, KindPromote, PhaseOwnership, base, func() error {
		if _, err := normalizeOwnership(tx.Root, s.cfg.Owner, s.chown); err != nil {
			return err
		}
		// Normalization can strip setuid or setgid from a non-directory, so
		// the recorded modes are refreshed before the manifest is persisted.
		return manifest.RefreshModes(tx.Root)
	}); err != nil {
		return nil, err
	}

	var complete *Complete
	if err := s.phase(tx.ID, KindPromote, PhasePersist, base, func() error {
		body, err := manifest.Marshal()
		if err != nil {
			return err
		}
		if err := fsx.WriteFileSync(filepath.Join(tx.Dir, ManifestFile), body, 0o644); err != nil {
			return err
		}
		sum := sha256.Sum256(body)
		complete = &Complete{
			SchemaVersion:  CompleteSchemaVersion,
			ReleaseID:      opts.ReleaseID,
			Profile:        p.ID,
			OperationID:    tx.ID,
			ContentDigest:  manifest.ContentDigest,
			ManifestSHA256: hex.EncodeToString(sum[:]),
			Provenance:     opts.Provenance,
		}
		// Content must be durable before COMPLETE claims it is. Without
		// this, COMPLETE could survive a power cut that the file bytes did
		// not, and the store would hold a release that verifies clean only
		// until someone reads it.
		return syncTree(tx.Root)
	}); err != nil {
		return nil, err
	}

	if err := s.phase(tx.ID, KindPromote, PhaseComplete, base, func() error {
		body, err := json.MarshalIndent(complete, "", "  ")
		if err != nil {
			return err
		}
		if err := fsx.WriteFileSync(filepath.Join(tx.Dir, CompleteFile), append(body, '\n'), 0o644); err != nil {
			return err
		}
		return fsx.SyncDir(tx.Dir)
	}); err != nil {
		return nil, err
	}

	if err := s.phase(tx.ID, KindPromote, PhasePublish, base, func() error {
		if err := os.Rename(tx.Dir, relDir); err != nil {
			return err
		}
		if err := fsx.SyncDir(s.cfg.ReleasesDir()); err != nil {
			return err
		}
		return fsx.SyncDir(s.cfg.TxDir())
	}); err != nil {
		return nil, err
	}

	rel := &Release{ID: opts.ReleaseID, Dir: relDir, Manifest: manifest, Complete: complete}

	if opts.Channel != "" {
		fields := cloneFields(base)
		fields["channel"] = opts.Channel
		if err := s.phase(tx.ID, KindPromote, PhaseFlip, fields, func() error {
			return s.flip(p.ID, opts.Channel, opts.ReleaseID)
		}); err != nil {
			return rel, err
		}
	}
	return rel, nil
}

// phase journals a step before and after it runs, then fires the boundary
// hook. A refusal — srdm declining by contract — is journaled distinctly
// from a fault, so an operator can tell "srdm said no" from "srdm broke".
func (s *Store) phase(opID, kind string, ph Phase, fields map[string]string, fn func() error) error {
	if err := s.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: string(ph),
		Outcome: journal.OutcomeStarted, Fields: fields,
	}); err != nil {
		return err
	}
	if err := fn(); err != nil {
		outcome := journal.OutcomeFailed
		if IsRefusal(err) {
			outcome = journal.OutcomeRefused
		}
		_ = s.jnl.Emit(journal.Record{
			OperationID: opID, Kind: kind, Phase: string(ph),
			Outcome: outcome, Fields: fields, Error: err.Error(),
		})
		return err
	}
	if err := s.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: string(ph),
		Outcome: journal.OutcomeOK, Fields: fields,
	}); err != nil {
		return err
	}
	if s.hook != nil {
		return s.hook(ph)
	}
	return nil
}

// IsRefusal reports whether err is srdm declining by contract rather than
// failing.
func IsRefusal(err error) bool {
	var unclassified *profile.UnclassifiedError
	var probe *ProbeFailure
	return errors.As(err, &unclassified) || errors.As(err, &probe)
}

// classifyTree walks root and classifies every path, returning the first
// path no rule matches.
func classifyTree(root string, p *profile.Profile) error {
	return filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		class, err := p.Classify(filepath.ToSlash(rel))
		if err != nil {
			return err
		}
		return checkStructural(filepath.ToSlash(rel), class, d.IsDir())
	})
}

// checkStructural rejects a non-directory that only classified because it
// sits on the path to a declared class.
//
// The ancestor rule exists so a profile need not declare every intermediate
// directory. Letting a *file* through it would be a silent hole in O3: the
// profile never named that path, and content would land with no class and
// no memory policy.
func checkStructural(rel string, class *profile.Class, isDir bool) error {
	if class.Kind != profile.KindStructure || isDir {
		return nil
	}
	return fmt.Errorf("store: %q is not a directory, but it is only classified as the "+
		"ancestor of a declared class path; declare it in the profile or remove it", rel)
}

// Activate points a channel at an already-published release.
func (s *Store) Activate(opID, profileID, channel, releaseID string) error {
	for kind, val := range map[string]string{
		"operation id": opID, "profile id": profileID,
		"channel": channel, "release id": releaseID,
	} {
		if err := config.ValidName(kind, val); err != nil {
			return fmt.Errorf("store: %w", err)
		}
	}
	fields := map[string]string{
		"profile": profileID, "channel": channel, "release_id": releaseID,
	}
	return s.phase(opID, KindActivate, PhaseFlip, fields, func() error {
		// A channel may only ever point at a release that verifies. This is
		// the check that stops a corrupted or half-written release from
		// becoming the one servers boot from.
		if _, err := s.Load(releaseID); err != nil {
			return err
		}
		return s.flip(profileID, channel, releaseID)
	})
}

// flip atomically repoints a channel symlink.
//
// The link is relative so the whole store stays relocatable, and it is
// installed by rename over a temporary name: there is no instant at which
// the channel is missing or points at nothing.
func (s *Store) flip(profileID, channel, releaseID string) error {
	dir := filepath.Join(s.cfg.ChannelsDir(), profileID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	target := filepath.Join("..", "..", "releases", releaseID)
	tmp := filepath.Join(dir, "."+channel+".tmp")
	if err := os.Remove(tmp); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.Symlink(target, tmp); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(dir, channel)); err != nil {
		os.Remove(tmp)
		return err
	}
	return fsx.SyncDir(dir)
}

// Resolve returns the release id a channel points at.
func (s *Store) Resolve(profileID, channel string) (string, error) {
	target, err := os.Readlink(s.cfg.ChannelLink(profileID, channel))
	if err != nil {
		return "", err
	}
	return filepath.Base(target), nil
}

// Load reads a release's manifest and COMPLETE, and checks they agree.
//
// It does not re-hash the tree; Verify does that. A release without
// COMPLETE is not a release, and saying so precisely is the whole point of
// writing COMPLETE last.
func (s *Store) Load(releaseID string) (*Release, error) {
	if err := config.ValidName("release id", releaseID); err != nil {
		return nil, fmt.Errorf("store: %w", err)
	}
	dir := s.cfg.ReleaseDir(releaseID)
	return LoadReleaseDir(releaseID, dir)
}

// LoadReleaseDir reads a release from an explicit directory, so a caller
// can inspect a transaction that has reached COMPLETE but not yet been
// renamed into the store, and so doctor can read a store read-only.
func LoadReleaseDir(releaseID, dir string) (*Release, error) {
	completeBody, err := os.ReadFile(filepath.Join(dir, CompleteFile))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("store: release %q has no %s; it was never fully promoted",
				releaseID, CompleteFile)
		}
		return nil, err
	}
	var c Complete
	dec := json.NewDecoder(strings.NewReader(string(completeBody)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("store: decode %s of release %q: %w", CompleteFile, releaseID, err)
	}
	if c.SchemaVersion != CompleteSchemaVersion {
		return nil, fmt.Errorf("store: release %q has %s schema_version %d (want %d)",
			releaseID, CompleteFile, c.SchemaVersion, CompleteSchemaVersion)
	}

	manifestBody, err := os.ReadFile(filepath.Join(dir, ManifestFile))
	if err != nil {
		return nil, fmt.Errorf("store: release %q: read manifest: %w", releaseID, err)
	}
	sum := sha256.Sum256(manifestBody)
	if got := hex.EncodeToString(sum[:]); got != c.ManifestSHA256 {
		return nil, fmt.Errorf("store: release %q: %s pins manifest %s but the manifest on disk is %s",
			releaseID, CompleteFile, c.ManifestSHA256, got)
	}
	m, err := ParseManifest(manifestBody)
	if err != nil {
		return nil, fmt.Errorf("store: release %q: %w", releaseID, err)
	}
	if m.ContentDigest != c.ContentDigest {
		return nil, fmt.Errorf("store: release %q: %s records content digest %s, manifest says %s",
			releaseID, CompleteFile, c.ContentDigest, m.ContentDigest)
	}
	return &Release{ID: releaseID, Dir: dir, Manifest: m, Complete: &c}, nil
}

// Verify re-hashes a release's whole tree against its manifest.
//
// Oracle O2: a store where COMPLETE exists always verifies clean.
func (s *Store) Verify(releaseID string) error {
	rel, err := s.Load(releaseID)
	if err != nil {
		return err
	}
	return rel.Manifest.Verify(rel.RootDir())
}

// List returns every release id present in the store, sorted.
func (s *Store) List() ([]string, error) {
	entries, err := os.ReadDir(s.cfg.ReleasesDir())
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out, nil
}

func cloneFields(in map[string]string) map[string]string {
	out := make(map[string]string, len(in)+1)
	for k, v := range in {
		out[k] = v
	}
	return out
}

// syncTree fsyncs every directory and regular file beneath root.
//
// A symlink is skipped: it carries no data of its own, and its parent
// directory's fsync makes the link entry itself durable.
func syncTree(root string) error {
	return filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		syncErr := f.Sync()
		closeErr := f.Close()
		if syncErr != nil {
			return syncErr
		}
		return closeErr
	})
}
