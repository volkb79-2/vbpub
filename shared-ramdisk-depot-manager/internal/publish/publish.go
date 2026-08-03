// Package publish turns a verified release into mounts a consumer can see.
//
// The sequence is fixed by the master plan and by what has to be true at
// every instant of it:
//
//  1. mkdir  /run/srdm/.op/<op>/<class>            0700, root
//  2. mount  tmpfs size=<class>,mode=0755,nodev,nosuid[,noexec]
//  3. populate <op>/<class>/root from the store, VERIFY every file against
//     the manifest, then chmod -R a-w
//  4. mount --bind <op>/<class>/root -> /run/srdm/<profile>/<gen>/<class>
//     then remount,ro,bind
//  5. fsync the published-state record; only now is the generation usable
//
// The invariant the order exists to hold: **the visible path appears only
// as a read-only bind of an already-verified tree.** Nothing is renamed
// into visibility, nothing visible was ever writable, and no consumer can
// observe a half-populated class. A publication interrupted anywhere leaves
// mounts under the operation-private root and nothing under the visible
// one.
//
// P03 populates inline. P04 relocates that into a per-class hold unit whose
// worker parks after populating, so the pages are charged to a cgroup that
// carries the class memory policy (D-011).
package publish

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"srdm/internal/config"
	"srdm/internal/fsx"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// RecordSchemaVersion is the published-state document version.
const RecordSchemaVersion = 1

// Phases, journaled before they execute and after they settle.
const (
	PhaseSize     = "size"
	PhaseMount    = "mount"
	PhasePopulate = "populate"
	PhaseVerify   = "verify"
	PhaseSeal     = "seal"
	PhaseExpose   = "expose"
	PhaseRecord   = "record"
	PhaseTeardown = "teardown"
)

// KindPublish and friends name operations in the journal.
const (
	KindPublish   = "publish"
	KindTeardown  = "teardown"
	KindReconcile = "reconcile"
)

// GenerationID is the short identity a generation carries into unit and
// path names: the first 8 hex of SHA-256 of the release id.
//
// Short because it goes into systemd unit names, where the full identity
// would be unreadable; the full release id lives in the record and the
// journal, which is where anyone debugging actually looks.
func GenerationID(releaseID string) string {
	sum := sha256.Sum256([]byte(releaseID))
	return hex.EncodeToString(sum[:])[:8]
}

// Sizing constants for a class tmpfs.
const (
	// SizeHeadroomPercent is applied to the measured content size. tmpfs is
	// a hard cap: too small and population dies with ENOSPC mid-write,
	// which is the 2026-07-29 corruption shape.
	SizeHeadroomPercent = 15
	// SizeGranularity rounds the result up. Unused tmpfs space costs
	// nothing — pages are allocated on write — so a generous cap is free
	// and a tight one is not.
	SizeGranularity = 64 << 20
)

// ClassSize returns the tmpfs size for a class holding contentBytes.
func ClassSize(contentBytes int64) int64 {
	if contentBytes < 0 {
		contentBytes = 0
	}
	withHeadroom := contentBytes + (contentBytes*SizeHeadroomPercent+99)/100
	rounded := (withHeadroom + SizeGranularity - 1) / SizeGranularity * SizeGranularity
	if rounded < SizeGranularity {
		return SizeGranularity
	}
	return rounded
}

// Mounter is the mount syscall surface, injected so the topology logic is
// exercisable without privilege. The real one is SyscallMounter.
type Mounter interface {
	Mount(source, target, fstype string, flags uintptr, data string) error
	Unmount(target string, flags int) error
}

// SyscallMounter performs real mounts.
type SyscallMounter struct{}

func (SyscallMounter) Mount(source, target, fstype string, flags uintptr, data string) error {
	return syscall.Mount(source, target, fstype, flags, data)
}

func (SyscallMounter) Unmount(target string, flags int) error {
	return syscall.Unmount(target, flags)
}

// Publisher owns the publication topology.
type Publisher struct {
	cfg           config.Config
	jnl           *journal.Journal
	mounter       Mounter
	mountInfoPath string
	sizer         func(class string, contentBytes int64) int64
}

// Option configures a Publisher.
type Option func(*Publisher)

// WithMounter injects the mount surface.
func WithMounter(m Mounter) Option { return func(p *Publisher) { p.mounter = m } }

// WithMountInfoPath points reconciliation at a specific mount table.
func WithMountInfoPath(path string) Option { return func(p *Publisher) { p.mountInfoPath = path } }

// WithClassSizer overrides how a class tmpfs is sized.
//
// The default is ClassSize. An override exists because the ENOSPC path is
// only reachable by making a tmpfs genuinely too small, and a gate that
// cannot reach its own quarantine branch has not tested it.
func WithClassSizer(f func(class string, contentBytes int64) int64) Option {
	return func(p *Publisher) { p.sizer = f }
}

// New returns a Publisher.
func New(cfg config.Config, jnl *journal.Journal, opts ...Option) (*Publisher, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("publish: a journal is required")
	}
	p := &Publisher{
		cfg: cfg, jnl: jnl, mounter: SyscallMounter{},
		sizer: func(_ string, contentBytes int64) int64 { return ClassSize(contentBytes) },
	}
	for _, o := range opts {
		o(p)
	}
	for _, dir := range []string{cfg.OpRoot(), cfg.PublishedDir()} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("publish: create %s: %w", dir, err)
		}
	}
	return p, nil
}

// ClassRecord is one published class.
type ClassRecord struct {
	Name string `json:"name"`
	// OpMount is the operation-private tmpfs mount point.
	OpMount string `json:"op_mount"`
	// ExposePath is the read-only bind a consumer sees.
	ExposePath string `json:"expose_path"`
	// SizeBytes is the tmpfs hard cap.
	SizeBytes int64 `json:"size_bytes"`
	// ContentBytes is what the manifest said the class holds.
	ContentBytes int64 `json:"content_bytes"`
	NoExec       bool  `json:"noexec"`
}

// Record is the durable published-state document.
//
// It records what SHOULD be mounted. It is never evidence that anything IS:
// reconciliation compares it against the kernel's mount table, because a
// record and a mount can each outlive the other.
type Record struct {
	SchemaVersion int           `json:"schema_version"`
	Generation    string        `json:"generation"`
	ReleaseID     string        `json:"release_id"`
	Profile       string        `json:"profile"`
	OperationID   string        `json:"operation_id"`
	ContentDigest string        `json:"content_digest"`
	Classes       []ClassRecord `json:"classes"`
}

// ENOSPCError reports a class tmpfs that could not hold its content.
//
// A distinct type because the remedy is distinct: the sizing was wrong or
// the content grew, and no amount of retrying fixes either.
type ENOSPCError struct {
	Class        string
	SizeBytes    int64
	ContentBytes int64
	Err          error
}

func (e *ENOSPCError) Error() string {
	return fmt.Sprintf("publish: class %q ran out of space: its tmpfs is %d bytes and the "+
		"manifest says the content is %d; the generation is quarantined rather than "+
		"published half-written (%v)", e.Class, e.SizeBytes, e.ContentBytes, e.Err)
}

func (e *ENOSPCError) Unwrap() error { return e.Err }

// IsRefusal reports whether err is publication declining by contract.
func IsRefusal(err error) bool {
	var enospc *ENOSPCError
	return errors.As(err, &enospc)
}

// Publish makes a verified release visible as read-only per-class binds.
//
// On any failure the operation-private mounts are torn down and nothing
// appears under the visible root: a caller sees either a whole generation
// or none of it.
func (p *Publisher) Publish(opID, profileID string, rel *store.Release, prof *profile.Profile) (rec *Record, err error) {
	for kind, val := range map[string]string{"operation id": opID, "profile id": profileID} {
		if err := config.ValidName(kind, val); err != nil {
			return nil, fmt.Errorf("publish: %w", err)
		}
	}
	gen := GenerationID(rel.ID)

	classes := managedClasses(prof)
	if len(classes) == 0 {
		return nil, fmt.Errorf("publish: profile %q declares no managed classes", prof.ID)
	}

	// `built` is deliberately NOT the named return value. A deferred cleanup
	// that reads the named return sees nil the moment any path does
	// `return nil, err` — the classic Go trap, and here it would nil-deref
	// exactly when cleanup matters most.
	built := &Record{
		SchemaVersion: RecordSchemaVersion,
		Generation:    gen,
		ReleaseID:     rel.ID,
		Profile:       profileID,
		OperationID:   opID,
		ContentDigest: rel.Manifest.ContentDigest,
	}

	// Anything already mounted for this operation is torn down on failure,
	// so a partial publication never survives the call that made it.
	defer func() {
		if err != nil {
			_ = p.teardownOp(opID, built, profileID, gen)
		}
	}()

	for _, class := range classes {
		cr, cerr := p.publishClass(opID, profileID, gen, rel, prof, class)
		if cerr != nil {
			return nil, cerr
		}
		built.Classes = append(built.Classes, *cr)
	}

	// The record is written last and fsync'd, for the same reason COMPLETE
	// is: it is the only thing that says this generation is whole.
	if err := p.phase(opID, KindPublish, PhaseRecord, recFields(built), func() error {
		return p.writeRecord(built)
	}); err != nil {
		return nil, err
	}
	return built, nil
}

func managedClasses(prof *profile.Profile) []profile.Class {
	var out []profile.Class
	for _, c := range prof.Classes {
		if c.Kind == profile.KindManaged {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func (p *Publisher) publishClass(opID, profileID, gen string, rel *store.Release,
	prof *profile.Profile, class profile.Class) (*ClassRecord, error) {

	contentBytes := rel.Manifest.ClassBytes(class.Name)
	size := p.sizer(class.Name, contentBytes)
	opMount := p.cfg.OpClassDir(opID, class.Name)
	opRoot := p.cfg.OpClassRoot(opID, class.Name)
	exposePath := p.cfg.ExposeClassDir(profileID, gen, class.Name)

	cr := &ClassRecord{
		Name:         class.Name,
		OpMount:      opMount,
		ExposePath:   exposePath,
		SizeBytes:    size,
		ContentBytes: contentBytes,
		NoExec:       class.NoExec,
	}
	fields := map[string]string{
		"generation": gen, "profile": profileID, "class": class.Name,
		"release_id": rel.ID,
		"size_bytes": itoa(size), "content_bytes": itoa(contentBytes),
	}

	if err := p.phase(opID, KindPublish, PhaseMount, fields, func() error {
		// 0700: an in-flight class is nobody's business until it is bound.
		if err := os.MkdirAll(opMount, 0o700); err != nil {
			return err
		}
		flags := uintptr(syscall.MS_NODEV | syscall.MS_NOSUID)
		if class.NoExec {
			// Data-only classes carry no code, so nothing there should ever
			// be executable — pak content above all.
			flags |= syscall.MS_NOEXEC
		}
		data := fmt.Sprintf("size=%d,mode=0755", size)
		return p.mounter.Mount("tmpfs", opMount, "tmpfs", flags, data)
	}); err != nil {
		return nil, err
	}

	if err := p.phase(opID, KindPublish, PhasePopulate, fields, func() error {
		if err := os.MkdirAll(opRoot, 0o755); err != nil {
			return err
		}
		if err := populateClass(rel.RootDir(), opRoot, rel.Manifest, class.Name); err != nil {
			if errors.Is(err, syscall.ENOSPC) {
				return &ENOSPCError{Class: class.Name, SizeBytes: size,
					ContentBytes: contentBytes, Err: err}
			}
			return err
		}
		return nil
	}); err != nil {
		return nil, err
	}

	// Verified BEFORE it is sealed and long before it is visible. A class
	// that does not match its manifest never becomes a bind.
	if err := p.phase(opID, KindPublish, PhaseVerify, fields, func() error {
		return rel.Manifest.VerifyClass(opRoot, class.Name)
	}); err != nil {
		return nil, err
	}

	if err := p.phase(opID, KindPublish, PhaseSeal, fields, func() error {
		return sealTree(opRoot)
	}); err != nil {
		return nil, err
	}

	if err := p.phase(opID, KindPublish, PhaseExpose, fields, func() error {
		if err := os.MkdirAll(exposePath, 0o755); err != nil {
			return err
		}
		if err := p.mounter.Mount(opRoot, exposePath, "", syscall.MS_BIND, ""); err != nil {
			return err
		}
		// A bind inherits the source's flags; read-only has to be applied as
		// a second, separate remount. Doing it in one call is a classic
		// mistake that silently leaves the bind writable.
		return p.mounter.Mount("", exposePath, "",
			syscall.MS_BIND|syscall.MS_REMOUNT|syscall.MS_RDONLY, "")
	}); err != nil {
		return nil, err
	}

	return cr, nil
}

// populateClass copies one class's entries out of the release into the op
// tree, in manifest order so parents exist before their children.
func populateClass(releaseRoot, opRoot string, m *store.Manifest, class string) error {
	entries := m.ClassEntries(class)
	if len(entries) == 0 {
		return fmt.Errorf("publish: manifest has no entries for class %q", class)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })

	for _, e := range entries {
		src := filepath.Join(releaseRoot, filepath.FromSlash(e.Path))
		dst := filepath.Join(opRoot, filepath.FromSlash(e.Path))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		switch e.Type {
		case store.EntryDir:
			if err := os.MkdirAll(dst, 0o755); err != nil {
				return err
			}
		case store.EntrySymlink:
			if err := os.Remove(dst); err != nil && !os.IsNotExist(err) {
				return err
			}
			if err := os.Symlink(e.Target, dst); err != nil {
				return err
			}
		case store.EntryFile:
			if err := copyRegular(src, dst); err != nil {
				return err
			}
		default:
			return fmt.Errorf("publish: manifest entry %q has unknown type %q", e.Path, e.Type)
		}
	}
	return nil
}

func copyRegular(src, dst string) (err error) {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	// 0644 now; sealTree removes the write bits once the tree verifies.
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	defer func() {
		if cerr := out.Close(); err == nil {
			err = cerr
		}
	}()
	_, err = io.Copy(out, in)
	return err
}

// sealTree removes every write bit, deepest entry first.
//
// Deepest-first because removing write from a directory is harmless to
// chmod but the ordering makes the walk independent of whether it is: no
// step depends on a permission an earlier step removed.
func sealTree(root string) error {
	var paths []string
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			// A symlink has no permission bits of its own, and chmod would
			// follow it to the target.
			return nil
		}
		paths = append(paths, p)
		return nil
	})
	if err != nil {
		return err
	}
	sort.Sort(sort.Reverse(sort.StringSlice(paths)))
	for _, p := range paths {
		info, err := os.Lstat(p)
		if err != nil {
			return err
		}
		if err := os.Chmod(p, info.Mode().Perm()&^0o222); err != nil {
			return err
		}
	}
	return nil
}

func (p *Publisher) writeRecord(rec *Record) error {
	body, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	path := p.cfg.PublishedRecord(rec.Profile, rec.Generation)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return fsx.WriteFileSync(path, append(body, '\n'), 0o644)
}

// LoadRecord reads a published-state record.
func (p *Publisher) LoadRecord(profileID, generation string) (*Record, error) {
	body, err := os.ReadFile(p.cfg.PublishedRecord(profileID, generation))
	if err != nil {
		return nil, err
	}
	var rec Record
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&rec); err != nil {
		return nil, fmt.Errorf("publish: decode record %s/%s: %w", profileID, generation, err)
	}
	if rec.SchemaVersion != RecordSchemaVersion {
		return nil, fmt.Errorf("publish: record %s/%s has schema_version %d (want %d)",
			profileID, generation, rec.SchemaVersion, RecordSchemaVersion)
	}
	return &rec, nil
}

func recFields(rec *Record) map[string]string {
	return map[string]string{
		"generation": rec.Generation,
		"profile":    rec.Profile,
		"release_id": rec.ReleaseID,
	}
}

func itoa(n int64) string { return fmt.Sprintf("%d", n) }

// phase journals a step before and after it runs.
func (p *Publisher) phase(opID, kind, ph string, fields map[string]string, fn func() error) error {
	if err := p.jnl.Emit(journal.Record{
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
		_ = p.jnl.Emit(journal.Record{
			OperationID: opID, Kind: kind, Phase: ph,
			Outcome: outcome, Fields: fields, Error: err.Error(),
		})
		return err
	}
	return p.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: ph,
		Outcome: journal.OutcomeOK, Fields: fields,
	})
}

// mountInfo reads the kernel's mount table.
func (p *Publisher) mountInfo() ([]mountinfo.Entry, error) {
	return mountinfo.Read(p.mountInfoPath)
}
