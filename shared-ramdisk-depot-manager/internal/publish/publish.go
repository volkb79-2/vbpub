// Package publish turns a verified release into mounts a consumer can see.
//
// The sequence is fixed by the master plan and by what has to be true at
// every instant of it:
//
//  1. set the generation slice's floor, before anything runs in it
//  2. mkdir  /run/srdm/.op/<op>/<class>            0700, root
//  3. mount  tmpfs size=<class>,mode=0755,nodev,nosuid[,noexec]
//  4. start  srdm-hold-<g8>-<class>.service, whose worker populates the
//     class, VERIFIES every file against the manifest, seals it read-only
//     and then parks — signalling ready only when all of that is done
//  5. mount  --bind <op>/<class>/root -> /run/srdm/<profile>/<gen>/<class>
//     then remount,ro,bind
//  6. fsync the published-state record; only now is the generation usable
//
// The invariant the order exists to hold: **the visible path appears only
// as a read-only bind of an already-verified tree.** Nothing is renamed
// into visibility, nothing visible was ever writable, and no consumer can
// observe a half-populated class. A publication interrupted anywhere leaves
// mounts under the operation-private root and nothing under the visible
// one.
//
// The split between this package and internal/hold is the charging
// boundary, and it is not stylistic. Every page of a class is faulted by
// the hold unit's own worker, inside the cgroup that carries that class's
// memory policy, so the charge and the policy can never separate (D-011).
// This package mounts and binds; it never writes a class tree.
package publish

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/fsx"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// RecordSchemaVersion is the published-state document version.
//
// 2 since P04: a record now names the hold unit of each class and the
// generation slice they share, because teardown and reconciliation have to
// act on those and a record is the only durable thing that knows them.
const RecordSchemaVersion = 2

// Phases, journaled before they execute and after they settle.
//
// Population, verification and sealing are no longer phases here: they
// happen inside the hold unit's worker, which is the point of P04. The
// daemon journals PhaseHold around them and systemd's journal carries the
// worker's own account under the unit name.
const (
	PhaseIsolate   = "isolate"
	PhaseSlice     = "slice"
	PhaseMount     = "mount"
	PhaseHold      = "hold"
	PhaseExpose    = "expose"
	PhaseRecord    = "record"
	PhaseConsumers = "consumers"
	PhaseTeardown  = "teardown"
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
// would be unreadable; the full release id lives in the record, the unit
// Description and the journal, which is where anyone debugging actually
// looks.
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

// Guard resolves who is still holding a generation.
//
// It is a field of the Publisher rather than a precondition the caller is
// trusted to run, and that is the whole point: a teardown that skips it does
// not fail, it silently leaks — the unmount succeeds, reports success, and
// frees nothing, because the consumer's own namespace still holds the
// superblock. A check that can be forgotten is one that will be. See D-018.
type Guard interface {
	Resolve(ctx context.Context, paths []string) (*consumer.Report, error)
}

// Holder is the hold-unit surface publication depends on, injected so the
// mount topology stays exercisable with no systemd present. The real one is
// *hold.Manager.
type Holder interface {
	EnsureSlice(ctx context.Context, slice string, memoryMin int64) error
	Start(ctx context.Context, spec hold.Spec) error
	Forget(ctx context.Context, unit string) error
	ReleaseSlice(ctx context.Context, slice string) error
	IsActive(ctx context.Context, unit string) (bool, error)
}

// Publisher owns the publication topology.
type Publisher struct {
	cfg           config.Config
	jnl           *journal.Journal
	mounter       Mounter
	holder        Holder
	guard         Guard
	mountInfoPath string
	sizer         func(class string, contentBytes int64) int64
}

// Option configures a Publisher.
type Option func(*Publisher)

// WithMounter injects the mount surface.
func WithMounter(m Mounter) Option { return func(p *Publisher) { p.mounter = m } }

// WithHolder injects the hold-unit surface.
func WithHolder(h Holder) Option { return func(p *Publisher) { p.holder = h } }

// WithGuard injects the consumer resolver.
func WithGuard(g Guard) Option { return func(p *Publisher) { p.guard = g } }

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
	if p.holder == nil {
		h, err := hold.New()
		if err != nil {
			return nil, err
		}
		p.holder = h
	}
	if p.guard == nil {
		p.guard = consumer.New()
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
	// HoldUnit is the transient unit whose cgroup the class's pages are
	// charged to and whose properties carry the class memory policy.
	HoldUnit string `json:"hold_unit"`
	// SizeBytes is the tmpfs hard cap.
	SizeBytes int64 `json:"size_bytes"`
	// ContentBytes is what the manifest said the class holds.
	ContentBytes int64 `json:"content_bytes"`
	// MemoryMin is the class floor the hold unit was given, in bytes.
	MemoryMin int64 `json:"memory_min"`
	NoExec    bool  `json:"noexec"`
}

// Record is the durable published-state document.
//
// It records what SHOULD be mounted and held. It is never evidence that
// anything IS: reconciliation compares it against the kernel's mount table
// and against systemd, because a record, a mount and a unit can each outlive
// the others.
type Record struct {
	SchemaVersion int    `json:"schema_version"`
	Generation    string `json:"generation"`
	ReleaseID     string `json:"release_id"`
	Profile       string `json:"profile"`
	OperationID   string `json:"operation_id"`
	ContentDigest string `json:"content_digest"`
	// Slice is the per-generation aggregate the hold units live in.
	Slice string `json:"slice"`
	// DirtyCapable records that this generation has been exposed writable,
	// so its content may no longer match the release it was published from.
	//
	// It is set when the exposure is made, not when a write happens: srdm
	// cannot see writes through a bind it does not mediate, so the only
	// honest moment is the one where writing became possible. A dirty
	// generation is not shared with a second consumer and is not used as a
	// source; the repair is to republish it, which is what makes a
	// generation ephemeral rather than a thing to be mended.
	DirtyCapable bool          `json:"dirty_capable,omitempty"`
	Classes      []ClassRecord `json:"classes"`
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

// IsRefusal reports whether err is srdm declining by contract rather than
// failing.
//
// Both members are refusals in the same sense — srdm looked, found the
// operation unsafe, and did not attempt it — and both need an operator to do
// something srdm cannot do for them: resize a class, or stop a consumer.
func IsRefusal(err error) bool {
	var enospc *ENOSPCError
	if errors.As(err, &enospc) {
		return true
	}
	var held *consumer.HeldError
	return errors.As(err, &held)
}

// Publish makes a verified release visible as read-only per-class binds.
//
// On any failure the operation's mounts are torn down, its hold units are
// stopped and forgotten, and nothing appears under the visible root: a
// caller sees either a whole generation or none of it.
func (p *Publisher) Publish(ctx context.Context, opID, profileID string,
	rel *store.Release, prof *profile.Profile) (rec *Record, err error) {

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
	slice, err := hold.SliceName(p.cfg.Slice, gen)
	if err != nil {
		return nil, err
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
		Slice:         slice,
	}
	// Every class is described BEFORE any of them is built, so cleanup knows
	// the names of the mounts and units of the class that was in flight when
	// a failure happened — which is exactly the one a record assembled as it
	// went would be missing.
	for _, class := range classes {
		cr, err := p.describeClass(opID, profileID, gen, rel, class)
		if err != nil {
			return nil, err
		}
		built.Classes = append(built.Classes, *cr)
	}

	// Anything already mounted, held or reserved for this operation is torn
	// down on failure, so a partial publication never survives the call that
	// made it.
	defer func() {
		if err != nil {
			_ = p.teardownOp(ctx, opID, built)
		}
	}()

	// Nothing else may receive these mounts by propagation. See isolateOpRoot.
	if err := p.phase(opID, KindPublish, PhaseIsolate,
		map[string]string{"op_root": p.cfg.OpRoot()}, p.isolateOpRoot); err != nil {
		return nil, err
	}

	// The aggregate's floor is set before anything runs inside it. A slice
	// created implicitly by the first service that names it starts at
	// memory.min=0, and a cgroup v2 floor is capped by every ancestor's, so
	// that window would be exactly the one in which the pages are faulted.
	if err := p.phase(opID, KindPublish, PhaseSlice, map[string]string{
		"generation": gen, "profile": profileID, "slice": slice,
		"memory_min": itoa(floorSum(classes)),
	}, func() error {
		return p.holder.EnsureSlice(ctx, slice, floorSum(classes))
	}); err != nil {
		return nil, err
	}

	for i := range built.Classes {
		if err := p.publishClass(ctx, opID, rel, classes[i], &built.Classes[i], slice); err != nil {
			return nil, err
		}
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

// isolateOpRoot makes the operation-private root a mount point of its own,
// with private propagation, so nothing srdm mounts beneath it is ever
// delivered to another mount namespace.
//
// Without this, publication sprays itself across the host. On a systemd host
// `/run` is shared, so a mount created under it propagates into every mount
// namespace that is a slave of the root — which is every service with
// PrivateTmp, ProtectSystem or its own `unshare`. Measured 2026-08-03: a
// pre-existing slave namespace received a new tmpfs immediately, and
// marking the mount private AFTERWARDS was too late, because the copy had
// already been delivered.
//
// Those copies are not harmless. They are holds: unmounting the host side
// does NOT remove them, and the content stays readable through them (D-019).
// So without isolation, every generation would acquire a holder per such
// service, teardown would be refused, and the refusal would be correct —
// the memory really would not come back.
//
// Idempotent, because it runs once per publication and there is no sensible
// place to run it exactly once: if the root is already a private mount
// point, there is nothing to do, and repeating the bind would stack mounts.
func (p *Publisher) isolateOpRoot() error {
	root := p.cfg.OpRoot()
	entries, err := p.mountInfo()
	if err != nil {
		return fmt.Errorf("publish: reading the mount table to isolate %s: %w", root, err)
	}
	if e, ok := mountinfo.At(entries, root); ok && e.Propagation() == mountinfo.PropagationPrivate {
		return nil
	}
	// A path can only carry propagation if it is a mount point, so it has to
	// become one: a bind of the directory onto itself.
	if err := p.mounter.Mount(root, root, "", syscall.MS_BIND, ""); err != nil {
		return fmt.Errorf("publish: binding %s onto itself: %w", root, err)
	}
	if err := p.mounter.Mount("", root, "", syscall.MS_PRIVATE, ""); err != nil {
		return fmt.Errorf("publish: making %s private: %w", root, err)
	}
	return nil
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

// floorSum is what the generation aggregate has to protect: the sum of the
// floors of the classes inside it.
//
// Not a maximum and not a guess. cgroup v2 distributes a parent's protection
// among its children, so an aggregate protecting less than the sum of its
// children's floors silently prorates every one of them.
func floorSum(classes []profile.Class) int64 {
	var total int64
	for _, c := range classes {
		if c.MemoryMin > 0 {
			total += c.MemoryMin
		}
	}
	return total
}

// describeClass names everything a class will occupy, before any of it
// exists.
func (p *Publisher) describeClass(opID, profileID, gen string, rel *store.Release,
	class profile.Class) (*ClassRecord, error) {

	unit, err := hold.UnitName(gen, class.Name)
	if err != nil {
		return nil, err
	}
	contentBytes := rel.Manifest.ClassBytes(class.Name)
	return &ClassRecord{
		Name:         class.Name,
		OpMount:      p.cfg.OpClassDir(opID, class.Name),
		ExposePath:   p.cfg.ExposeClassDir(profileID, gen, class.Name),
		HoldUnit:     unit,
		SizeBytes:    p.sizer(class.Name, contentBytes),
		ContentBytes: contentBytes,
		MemoryMin:    class.MemoryMin,
		NoExec:       class.NoExec,
	}, nil
}

func (p *Publisher) publishClass(ctx context.Context, opID string, rel *store.Release,
	class profile.Class, cr *ClassRecord, slice string) error {

	opRoot := p.cfg.OpClassRoot(opID, class.Name)
	fields := map[string]string{
		"generation": GenerationID(rel.ID), "class": class.Name,
		"release_id": rel.ID, "unit": cr.HoldUnit,
		"size_bytes": itoa(cr.SizeBytes), "content_bytes": itoa(cr.ContentBytes),
	}

	if err := p.phase(opID, KindPublish, PhaseMount, fields, func() error {
		// 0700: an in-flight class is nobody's business until it is bound.
		if err := os.MkdirAll(cr.OpMount, 0o700); err != nil {
			return err
		}
		flags := uintptr(syscall.MS_NODEV | syscall.MS_NOSUID)
		if class.NoExec {
			// Data-only classes carry no code, so nothing there should ever
			// be executable — pak content above all.
			flags |= syscall.MS_NOEXEC
		}
		data := fmt.Sprintf("size=%d,mode=0755", cr.SizeBytes)
		return p.mounter.Mount("tmpfs", cr.OpMount, "tmpfs", flags, data)
	}); err != nil {
		return err
	}

	// The hold unit populates, verifies and seals inside its own cgroup, and
	// reports ready only when all three are done. When Start returns, the
	// class tree is finished and read-only and the pages that make it up are
	// charged where the class policy applies.
	if err := p.phase(opID, KindPublish, PhaseHold, fields, func() error {
		err := p.holder.Start(ctx, hold.Spec{
			Unit:  cr.HoldUnit,
			Slice: slice,
			// The full release identity, since the unit name carries only
			// the 8 hex of the generation.
			Description: fmt.Sprintf("srdm hold: release %s class %s", rel.ID, class.Name),
			Policy: hold.Policy{
				MemoryMin:      class.MemoryMin,
				ZSwapMax:       class.ZSwapMax,
				ZSwapWriteback: class.ZSwapWriteback,
			},
			Worker: hold.WorkerArgs{
				ReleaseDir: rel.Dir,
				ReleaseID:  rel.ID,
				Class:      class.Name,
				Target:     opRoot,
			},
		})
		if err != nil && hold.ExitStatus(err) == hold.ExitNoSpace {
			// The worker's exit status is the refusal channel: this is the
			// sizing being wrong, not the machinery being broken, and the
			// generation is quarantined rather than half-published.
			return &ENOSPCError{Class: class.Name, SizeBytes: cr.SizeBytes,
				ContentBytes: cr.ContentBytes, Err: err}
		}
		return err
	}); err != nil {
		return err
	}

	if err := p.phase(opID, KindPublish, PhaseExpose, fields, func() error {
		if err := os.MkdirAll(cr.ExposePath, 0o755); err != nil {
			return err
		}
		if err := p.mounter.Mount(opRoot, cr.ExposePath, "", syscall.MS_BIND, ""); err != nil {
			return err
		}
		// A bind inherits the source's flags; read-only has to be applied as
		// a second, separate remount. Doing it in one call is a classic
		// mistake that silently leaves the bind writable.
		return p.mounter.Mount("", cr.ExposePath, "",
			syscall.MS_BIND|syscall.MS_REMOUNT|syscall.MS_RDONLY, "")
	}); err != nil {
		return err
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
	return LoadRecordAt(p.cfg, profileID, generation)
}

// LoadRecordAt reads a published-state record without a Publisher.
//
// A package function because doctor reads these too, and doctor has no
// business constructing something that creates directories and holds a
// journal in order to read one file.
func LoadRecordAt(cfg config.Config, profileID, generation string) (*Record, error) {
	body, err := os.ReadFile(cfg.PublishedRecord(profileID, generation))
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

// MarkDirtyCapable records that a generation has been exposed writable.
//
// Durable, and fsync'd, because the whole value of the flag is that it
// survives the crash during which somebody wrote through the exposure. A
// flag that only lived in memory would be cleared by exactly the event that
// makes it matter.
func (p *Publisher) MarkDirtyCapable(opID, profileID, generation string) error {
	rec, err := p.LoadRecord(profileID, generation)
	if err != nil {
		return fmt.Errorf("publish: marking %s/%s dirty-capable: %w", profileID, generation, err)
	}
	if rec.DirtyCapable {
		return nil
	}
	rec.DirtyCapable = true
	return p.phase(opID, KindPublish, PhaseRecord, recFields(rec), func() error {
		return p.writeRecord(rec)
	})
}

func recFields(rec *Record) map[string]string {
	return map[string]string{
		"generation": rec.Generation,
		"profile":    rec.Profile,
		"release_id": rec.ReleaseID,
		"slice":      rec.Slice,
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
