package expose

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"syscall"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/wings"
)

// Phases, journaled before they execute and after they settle.
const (
	PhasePreflight = "preflight"
	PhaseUnseal    = "unseal"
	PhaseBind      = "bind"
	PhaseUnbind    = "unbind"
)

// KindExpose and friends name operations in the journal.
const (
	KindExpose   = "expose"
	KindUnexpose = "unexpose"
)

// Mounter is the mount syscall surface, injected so the binding logic is
// exercisable without privilege.
type Mounter interface {
	Mount(source, target, fstype string, flags uintptr, data string) error
	Unmount(target string, flags int) error
}

// Guard resolves who is holding a generation. *consumer.Resolver satisfies
// it; it is the same surface publication's teardown uses.
type Guard interface {
	Resolve(ctx context.Context, paths []string) (*consumer.Report, error)
}

// Marker durably records that a generation has been exposed writable.
// *publish.Publisher satisfies it.
type Marker interface {
	MarkDirtyCapable(opID, profileID, generation string) error
}

// HostBind is the v1 exposure driver: it mounts a generation's per-class
// read-only binds onto the matching paths under a server's volume.
//
// This is what the legacy soulmask_tmpfs does. srdm does it with verified
// immutable generations, atomic activate and rollback, a journal, retention
// and a doctor behind it — and, above all, with the three preconditions
// checked rather than discovered.
//
// It waives invariant 14 deliberately (master-plan decision 10): managed
// content DOES appear under the server's host volume path, so Wings' own
// filesystem operations — disk accounting, backups, SFTP, archive
// extraction, and the pre-boot chown walk — all walk over it. That is the
// price of needing no Wings patch, it is paid knowingly, and oracle 19 is
// what keeps the waiver bounded: content appears at exactly the declared
// class paths and nowhere else.
type HostBind struct {
	cfg     config.Wings
	jnl     *journal.Journal
	mounter Mounter
	guard   Guard
	marker  Marker

	mountInfoPath string
	inspector     wings.ContainerInspector
	// chownWalk is injected in tests; nil means read the node config.
	chownWalk func() (wings.ChownWalk, error)
	// chown is the syscall unsealing uses, injected so the ownership model
	// is exercisable in a gate that runs unprivileged.
	chown func(name string, uid, gid int) error
}

// Option configures a HostBind.
type Option func(*HostBind)

// WithMounter injects the mount surface.
func WithMounter(m Mounter) Option { return func(h *HostBind) { h.mounter = m } }

// WithGuard injects consumer resolution.
func WithGuard(g Guard) Option { return func(h *HostBind) { h.guard = g } }

// WithMarker injects the dirty-capable recorder. Required for access: rw.
func WithMarker(m Marker) Option { return func(h *HostBind) { h.marker = m } }

// WithMountInfoPath points the propagation check at a specific mount table.
func WithMountInfoPath(p string) Option { return func(h *HostBind) { h.mountInfoPath = p } }

// WithInspector injects the container inspector. *consumer.Docker satisfies it.
func WithInspector(i wings.ContainerInspector) Option {
	return func(h *HostBind) { h.inspector = i }
}

// WithChownWalk injects the pre-boot chown walk answer.
func WithChownWalk(f func() (wings.ChownWalk, error)) Option {
	return func(h *HostBind) { h.chownWalk = f }
}

// WithChown injects the chown syscall unsealing uses. Tests record what the
// ownership walk would have done without needing to be root.
func WithChown(f func(name string, uid, gid int) error) Option {
	return func(h *HostBind) { h.chown = f }
}

// NewHostBind returns the host-bind driver.
func NewHostBind(cfg config.Wings, jnl *journal.Journal, opts ...Option) (*HostBind, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("expose: a journal is required")
	}
	h := &HostBind{cfg: cfg, jnl: jnl, mounter: syscallMounter{}, chown: os.Lchown}
	for _, o := range opts {
		o(h)
	}
	if h.guard == nil {
		h.guard = consumer.New()
	}
	if h.chownWalk == nil {
		h.chownWalk = func() (wings.ChownWalk, error) { return wings.ReadChownWalk(cfg.ConfigPath) }
	}
	return h, nil
}

type syscallMounter struct{}

func (syscallMounter) Mount(source, target, fstype string, flags uintptr, data string) error {
	return syscall.Mount(source, target, fstype, flags, data)
}

func (syscallMounter) Unmount(target string, flags int) error {
	return syscall.Unmount(target, flags)
}

// Name identifies the driver.
func (h *HostBind) Name() string { return "host-bind" }

// Plan returns the mounts Expose would make.
func (h *HostBind) Plan(rec *publish.Record, prof *profile.Profile, req Request) ([]Binding, error) {
	bindings, err := plan(rec, prof, req, h.cfg.ServerVolume(req.ServerID))
	if err != nil {
		return nil, err
	}
	// The waiver is bounded here rather than checked afterwards: a plan that
	// would cover per-instance state never becomes mounts.
	excluded := excludedPaths(prof)
	for _, b := range bindings {
		if ex, bad := shadows(b.Path, excluded); bad {
			return nil, &RefusalError{
				Precondition: PreconditionBoundedWaiver,
				Detail: fmt.Sprintf("class %q declares path %q, which shadows the excluded "+
					"path %q; per-instance state would be hidden rather than overwritten, "+
					"so the damage would only be found when somebody went looking for a "+
					"world that is still on disk underneath", b.Class, b.Path, ex),
				Fix: "narrow the class path in the profile so it does not cover " + ex,
			}
		}
	}
	return bindings, nil
}

// Expose makes a published generation visible to one server, or refuses.
//
// The preconditions run first and all of them refuse rather than warn. Each
// is a property srdm cannot repair, and each one unrefused surfaces later as
// something worse: a mount Wings cannot see, a server that will not start,
// or a generation with two writers.
func (h *HostBind) Expose(ctx context.Context, rec *publish.Record, prof *profile.Profile,
	req Request) error {

	bindings, err := h.Plan(rec, prof, req)
	if err != nil {
		return err
	}
	access := req.Access
	if access == "" {
		access = AccessRO
	}
	fields := map[string]string{
		"generation": rec.Generation, "profile": rec.Profile,
		"server": req.ServerID, "access": string(access), "driver": h.Name(),
	}

	if err := h.phase(req.OperationID, KindExpose, PhasePreflight, fields, func() error {
		return h.preflight(ctx, rec, req, access, fields)
	}); err != nil {
		return err
	}

	// The flag goes down BEFORE writing becomes possible, not after. It is a
	// restriction, so the failure that matters is a crash between the two:
	// marked-but-not-mounted costs a republish nobody needed, while
	// mounted-but-not-marked lets a second consumer join a generation
	// somebody is already writing through.
	if access == AccessRW {
		if h.marker == nil {
			return &RefusalError{
				Precondition: PreconditionSingleWrite,
				Detail: "access rw was requested but this driver has no way to record the " +
					"generation as dirty-capable, so nothing downstream would know it had " +
					"been written through",
				Fix: "configure the driver with a marker (the publisher), or expose ro",
			}
		}
		if err := h.marker.MarkDirtyCapable(req.OperationID, rec.Profile, rec.Generation); err != nil {
			return err
		}
		// Unsealing comes after the mark and before the mounts, for the same
		// reason the mark does: it is the step that makes writing possible,
		// and everything that restricts a writable generation has to be
		// durable before it is.
		if h.cfg.WriteOwner != nil {
			fields["write_owner"] = h.cfg.WriteOwner.String()
		}
		if err := h.phase(req.OperationID, KindExpose, PhaseUnseal, fields, func() error {
			return h.unseal(rec, bindings, h.cfg.WriteOwner)
		}); err != nil {
			return err
		}
	}

	return h.phase(req.OperationID, KindExpose, PhaseBind, fields, func() error {
		return h.bind(bindings)
	})
}

// unseal hands each bound class tree to the declared owner and gives it back
// its owner write bit.
//
// This is the half of D-020 P06 left open, and it is not reversed on
// unexpose. Re-sealing would restore the MODES of a tree whose CONTENT is no
// longer the release's — the appearance of a sealed generation without the
// property, which is worse than an obviously dirty one. A generation exposed
// writable is repaired by republishing it, which is what makes generations
// ephemeral rather than things to be mended (D-022).
func (h *HostBind) unseal(rec *publish.Record, bindings []Binding, owner *config.WriteOwner) error {
	if owner == nil {
		// Unreachable: preflight refuses rw with no declared owner. Stated as
		// an error rather than assumed, because the alternative is unsealing
		// to nobody or skipping silently, and a silent skip would turn the
		// removal of that refusal into an exposure that merely does not work.
		return fmt.Errorf("expose: rw reached the unseal step for %s/%s with no write owner; "+
			"the preflight refusal is the gate and it did not run",
			rec.Profile, rec.Generation)
	}
	for _, root := range writableRoots(rec, bindings) {
		if err := hold.Unseal(root, h.chown, owner.UID, owner.GID); err != nil {
			return fmt.Errorf("expose: unsealing %s for %s: %w", root, owner, err)
		}
	}
	return nil
}

// preflight is the three hard preconditions, plus the rw rule.
func (h *HostBind) preflight(ctx context.Context, rec *publish.Record, req Request,
	access Access, fields map[string]string) error {

	// 1. Propagation. Without it every mount below is either invisible to
	//    Wings or leaves a ghost Wings still traverses.
	prop, err := wings.ReadPropagation(ctx, h.cfg, h.mountInfoPath, h.inspector)
	if err != nil {
		return err
	}
	if !prop.HostShared() {
		return &RefusalError{
			Precondition: PreconditionPropagation,
			Detail: fmt.Sprintf("the host mount carrying %s (%s) is %s, not shared, so there "+
				"is nothing for the Wings container's rslave to be a slave of",
				h.cfg.BindRoot, prop.HostMountPoint, prop.HostPeerGroup),
			Fix: "mount --make-rshared " + prop.HostMountPoint,
		}
	}
	fields["host_propagation"] = prop.HostPeerGroup
	switch {
	case prop.Degraded != "":
		// srdm could not ask Docker. It does not guess: rprivate and "I
		// could not ask" call for different things from an operator, and
		// proceeding on the second would reproduce the 2026-07-31 outage
		// silently.
		return &RefusalError{
			Precondition: PreconditionPropagation,
			Detail: fmt.Sprintf("srdm could not read how the Wings container binds %s (%s), "+
				"so it cannot tell whether anything it mounts would be visible there",
				h.cfg.BindRoot, prop.Degraded),
			Fix: "make the Docker socket readable by srdm, or fix the container so exactly " +
				"one running container binds " + h.cfg.BindRoot,
		}
	case !prop.ContainerReceives():
		return &RefusalError{
			Precondition: PreconditionPropagation,
			Detail: fmt.Sprintf("the Wings container %q binds %s with propagation %q; under "+
				"that, every mount srdm makes is invisible to Wings and every unmount "+
				"leaves a ghost Wings still traverses — the 2026-07-31 outage",
				prop.ContainerName, h.cfg.BindRoot, prop.Container),
			Fix: fmt.Sprintf("set the Wings container's bind of %s to rslave "+
				"(compose: `- %s:%s:rslave`) and recreate it",
				h.cfg.BindRoot, h.cfg.BindRoot, h.cfg.BindRoot),
		}
	}
	fields["container_propagation"] = prop.Container

	// 2. The pre-boot chown walk, for read-only exposures only. A chown on a
	//    read-only mount returns EROFS even when the owner already matches,
	//    so the walk makes the server unstartable.
	if access == AccessRO && !h.cfg.ChownSkipPatch {
		walk, err := h.chownWalk()
		if err != nil || walk.Enabled {
			detail := fmt.Sprintf("Wings will run its pre-boot chown walk (%s) and this "+
				"build is not asserted to carry F1; the walk chowns every entry "+
				"unconditionally and fails EROFS on a read-only mount even when the owner "+
				"already matches, so the server would not start", walk.Source)
			if err != nil {
				detail = fmt.Sprintf("srdm could not confirm Wings will skip its pre-boot "+
					"chown walk (%v), and this build is not asserted to carry F1", err)
			}
			return &RefusalError{
				Precondition: PreconditionChownWalk,
				Detail:       detail,
				Fix: "either run a Wings build carrying F1 and set wings.chown_skip_patch, " +
					"or set system.check_permissions_on_boot: false in " + h.cfg.ConfigPath,
			}
		}
		fields["chown_walk"] = "disabled"
	}

	// 3. Consumers stopped, and the rw single-consumer rule. Both are the
	//    same question asked of the same resolver.
	rep, err := h.guard.Resolve(ctx, recordPaths(rec))
	if err != nil {
		return fmt.Errorf("expose: resolving consumers of %s/%s: %w",
			rec.Profile, rec.Generation, err)
	}
	fields["holders"] = fmt.Sprintf("%d", len(rep.Holders))
	if len(rep.Degraded) > 0 {
		fields["degraded"] = strings.Join(rep.Degraded, "; ")
	}
	// A generation that has already been written through is not shared. Its
	// content no longer provably matches the release it came from, so a
	// second consumer would be reading something nobody verified — and the
	// first consumer's next write would be doing it underneath them.
	if rec.DirtyCapable && rep.Held() {
		return &RefusalError{
			Precondition: PreconditionDirty,
			Detail: fmt.Sprintf("%s/%s has been exposed writable, so its content no longer "+
				"provably matches release %s, and it already has %d consumer(s)",
				rec.Profile, rec.Generation, rec.ReleaseID, len(rep.Holders)),
			Fix: "republish the generation from the store — a generation is ephemeral, and " +
				"republishing is how a written-through one is repaired",
		}
	}
	if access == AccessRW && rep.Held() {
		var who []string
		for _, holder := range rep.Holders {
			who = append(who, holder.String())
		}
		return &RefusalError{
			Precondition: PreconditionSingleWrite,
			Detail: fmt.Sprintf("access rw was requested for %s/%s, which already has %d "+
				"consumer(s): %s. With two, a write is the 2026-07-29 corruption by "+
				"construction: the peer holds the deleted old .pak open, the tmpfs "+
				"exhausts mid-write, and the generation ends with a new .sig and no .pak",
				rec.Profile, rec.Generation, len(rep.Holders), strings.Join(who, "; ")),
			Fix: "stop the other consumers, or expose this generation ro",
		}
	}

	// 4. Somebody to hand the unsealed tree to. Publication seals a class
	//    tree a-w, so rw without an owner is a mount that permits writes
	//    nobody but root can make — and the game's own updater, which is the
	//    entire reason rw exists, runs as something else. Refused rather than
	//    exposed, because that failure arrives as an update that reported
	//    success and changed nothing.
	if access == AccessRW && h.cfg.WriteOwner == nil {
		return &RefusalError{
			Precondition: PreconditionWriteOwner,
			Detail: "access rw was requested but no write owner is declared. Publication " +
				"seals a class tree read-only for everyone, so the exposure would permit " +
				"writes at the mount level that the modes still refuse to every uid except " +
				"root — an updater running in the game container would report success and " +
				"write nothing",
			Fix: "set wings.write_owner to the uid:gid Wings runs its server containers as " +
				"(system.user.uid and system.user.gid in " + h.cfg.ConfigPath + "), or " +
				"expose this generation ro",
		}
	}
	return nil
}

// bind performs the mounts, and unwinds them if any one fails.
//
// A half-exposed server is worse than an unexposed one: the game starts,
// finds some of its content, and fails somewhere further in.
func (h *HostBind) bind(bindings []Binding) (err error) {
	var done []string
	defer func() {
		if err == nil {
			return
		}
		// Deepest first, so a nested mount never blocks its parent.
		sort.Sort(sort.Reverse(sort.StringSlice(done)))
		for _, t := range done {
			_ = h.mounter.Unmount(t, 0)
		}
	}()

	for _, b := range bindings {
		if err := os.MkdirAll(b.Target, 0o755); err != nil {
			return fmt.Errorf("expose: creating %s: %w", b.Target, err)
		}
		if err := h.mounter.Mount(b.Source, b.Target, "", syscall.MS_BIND, ""); err != nil {
			return fmt.Errorf("expose: binding %s onto %s: %w", b.Source, b.Target, err)
		}
		done = append(done, b.Target)
		if !b.ReadOnly {
			continue
		}
		// A bind inherits its source's flags; read-only has to be a second,
		// separate remount. Doing it in one call silently leaves it writable
		// — and here that means a game server writing into shared content.
		if err := h.mounter.Mount("", b.Target, "",
			syscall.MS_BIND|syscall.MS_REMOUNT|syscall.MS_RDONLY, ""); err != nil {
			return fmt.Errorf("expose: making %s read-only: %w", b.Target, err)
		}
	}
	return nil
}

// Unexpose removes a server's exposure.
//
// It does not consult the consumer guard: removing a bind from a stopped
// server's volume is how a consumer STOPS holding a generation, so refusing
// while one holds would make the state unreachable. Publication's teardown
// is where the refusal belongs, and it runs after this.
func (h *HostBind) Unexpose(ctx context.Context, rec *publish.Record, prof *profile.Profile,
	req Request) error {

	bindings, err := h.Plan(rec, prof, req)
	if err != nil {
		return err
	}
	fields := map[string]string{
		"generation": rec.Generation, "profile": rec.Profile,
		"server": req.ServerID, "driver": h.Name(),
	}
	return h.phase(req.OperationID, KindUnexpose, PhaseUnbind, fields, func() error {
		targets := make([]string, 0, len(bindings))
		for _, b := range bindings {
			targets = append(targets, b.Target)
		}
		// Deepest first, so a nested mount never blocks its parent.
		sort.Sort(sort.Reverse(sort.StringSlice(targets)))
		var errs []error
		for _, t := range targets {
			if err := h.unmountIfMounted(t); err != nil {
				errs = append(errs, fmt.Errorf("%s: %w", t, err))
			}
		}
		return errors.Join(errs...)
	})
}

// unmountIfMounted tolerates a target that is already gone: after a crash or
// a partial exposure that is the normal case, and the goal state is exactly
// what it already is.
func (h *HostBind) unmountIfMounted(target string) error {
	err := h.mounter.Unmount(target, 0)
	if err == nil || errors.Is(err, syscall.EINVAL) || errors.Is(err, syscall.ENOENT) {
		return nil
	}
	return err
}

// Exposed reports which of a request's bindings are currently mounted,
// according to the kernel rather than to any record srdm keeps.
func (h *HostBind) Exposed(rec *publish.Record, prof *profile.Profile, req Request) ([]Binding, error) {
	bindings, err := h.Plan(rec, prof, req)
	if err != nil {
		return nil, err
	}
	entries, err := mountinfo.Read(h.mountInfoPath)
	if err != nil {
		return nil, fmt.Errorf("expose: reading the mount table: %w", err)
	}
	var out []Binding
	for _, b := range bindings {
		e, ok := mountinfo.At(entries, b.Target)
		if !ok {
			continue
		}
		b.ReadOnly = e.ReadOnly()
		out = append(out, b)
	}
	return out, nil
}

// recordPaths is every mount point a published record names.
func recordPaths(rec *publish.Record) []string {
	var paths []string
	for _, c := range rec.Classes {
		paths = append(paths, c.OpMount, c.ExposePath)
	}
	return paths
}

// phase journals a step before and after it runs.
func (h *HostBind) phase(opID, kind, ph string, fields map[string]string, fn func() error) error {
	if opID == "" {
		opID = "expose-" + fields["server"]
	}
	if err := h.jnl.Emit(journal.Record{
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
		_ = h.jnl.Emit(journal.Record{
			OperationID: opID, Kind: kind, Phase: ph,
			Outcome: outcome, Fields: fields, Error: err.Error(),
		})
		return err
	}
	return h.jnl.Emit(journal.Record{
		OperationID: opID, Kind: kind, Phase: ph,
		Outcome: journal.OutcomeOK, Fields: fields,
	})
}
