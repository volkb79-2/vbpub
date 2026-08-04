package expose

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/wings"
)

// Phases, journaled before they execute and after they settle.
const (
	PhasePreflight = "preflight"
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

	// stateDir roots the per-server overlay upper/work layers an rw exposure
	// mounts (D-035). Deliberately separate from cfg (config.Wings): the
	// overlay state has nothing to do with the Wings deployment shape, and
	// keeping it a plain string keeps HostBind constructible from just the
	// Wings settings everywhere it always has been.
	stateDir string

	mountInfoPath string
	inspector     wings.ContainerInspector
	// chownWalk is injected in tests; nil means read the node config.
	chownWalk func() (wings.ChownWalk, error)
}

// Option configures a HostBind.
type Option func(*HostBind)

// WithMounter injects the mount surface.
func WithMounter(m Mounter) Option { return func(h *HostBind) { h.mounter = m } }

// WithGuard injects consumer resolution.
func WithGuard(g Guard) Option { return func(h *HostBind) { h.guard = g } }

// WithStateDir points the rw overlay's upper/work layers at a state root.
// Defaults to config.DefaultStateDir.
func WithStateDir(dir string) Option { return func(h *HostBind) { h.stateDir = dir } }

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

// NewHostBind returns the host-bind driver.
func NewHostBind(cfg config.Wings, jnl *journal.Journal, opts ...Option) (*HostBind, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("expose: a journal is required")
	}
	h := &HostBind{cfg: cfg, jnl: jnl, mounter: syscallMounter{}, stateDir: config.DefaultStateDir}
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
	bindings, err := plan(rec, prof, req, h.cfg.ServerVolume(req.ServerID), h.stateDir)
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

	// Nothing marks or unseals anymore (D-035): an rw exposure overlays the
	// sealed generation rather than writing to it, so there is nothing to
	// hand over and nothing that stops matching the release it came from.
	// bind() creates and owns the writable layer as part of mounting it.
	return h.phase(req.OperationID, KindExpose, PhaseBind, fields, func() error {
		return h.bind(bindings)
	})
}

// preflight is the hard preconditions.
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

	// 3. Consumers stopped. There is no single-write rule left to layer onto
	//    this (D-035): an overlay's writes land in a per-server upper, never
	//    in the generation itself, so a second rw consumer no longer shares
	//    anything with the first to collide over.
	rep, err := h.guard.Resolve(ctx, recordPaths(rec))
	if err != nil {
		return fmt.Errorf("expose: resolving consumers of %s/%s: %w",
			rec.Profile, rec.Generation, err)
	}
	fields["holders"] = fmt.Sprintf("%d", len(rep.Holders))
	if len(rep.Degraded) > 0 {
		fields["degraded"] = strings.Join(rep.Degraded, "; ")
	}
	// A generation already dirty-capable predates this package, or came from
	// a driver that still writes in place. Its content no longer provably
	// matches the release it came from, so a second consumer would be
	// reading something nobody verified — and the first consumer's next
	// write would be doing it underneath them. New rw exposures never set
	// this flag (D-035); it stays load-bearing for records that already
	// carry it.
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
	return nil
}

// bind performs the mounts, and unwinds them if any one fails.
//
// A half-exposed server is worse than an unexposed one: the game starts,
// finds some of its content, and fails somewhere further in. A refused mount
// is not a refused exposure either way — this unwind is what makes that true
// for the overlay bindings exactly as it already did for the ro binds.
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
		if b.Upper != "" {
			if err := h.mountOverlay(b); err != nil {
				return err
			}
			done = append(done, b.Target)
			continue
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

// mountOverlay mounts b.Target as an overlay of b.Source (D-027, D-035):
// b.Source, the sealed published exposure, as lowerdir; b.Upper as upperdir;
// b.Work as overlayfs's own scratch directory.
//
// Before mounting, mirrorDirTree recreates b.Source's directory structure —
// directories only, never files — inside b.Upper with permissive
// (world-writable) modes. That is what makes the writable layer writable to
// WHOEVER Wings runs the game container as, without srdm having to know or
// declare that uid: overlayfs merges a directory's CHILDREN from both
// layers, but takes its own mode and owner from whichever layer has an
// entry there, upper winning when both do. A lower-only directory's mode is
// the LOWER's — root-owned and sealed a-w — no matter how permissive the
// mount point above it is, so a directory that is never mirrored is never
// writable through the merged view. Measured against a real overlay mount
// with an unprivileged uid (D-035): mirrored directories accept create,
// delete and rename; a directory that exists only in the lower does not,
// and neither does a true in-place O_TRUNC open of a file that has not yet
// been copied up — real updaters replace files rather than truncate them in
// place, which is the shape this is built for.
func (h *HostBind) mountOverlay(b Binding) error {
	if err := mirrorDirTree(b.Source, b.Upper); err != nil {
		return fmt.Errorf("expose: preparing the writable overlay layer for %s: %w", b.Target, err)
	}
	if err := os.MkdirAll(b.Work, 0o700); err != nil {
		return fmt.Errorf("expose: creating %s: %w", b.Work, err)
	}
	data := fmt.Sprintf("lowerdir=%s,upperdir=%s,workdir=%s", b.Source, b.Upper, b.Work)
	if err := h.mounter.Mount("overlay", b.Target, "overlay", 0, data); err != nil {
		return fmt.Errorf("expose: overlaying %s onto %s: %w", b.Source, b.Target, err)
	}
	return nil
}

// mirrorDirTree recreates every directory under lower inside upper, mode
// 0o777. See mountOverlay for why this — and not some narrower permission —
// is what an overlay's upper layer needs to be writable by an uid srdm does
// not know.
//
// Idempotent: re-running it (a server re-exposed after a restart) only ever
// widens a directory back to 0o777 and never touches anything the writer
// itself created afterward, which already has the writer's own permissive
// permissions because the writer made it inside a directory it could
// already write to.
func mirrorDirTree(lower, upper string) error {
	return filepath.WalkDir(lower, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !d.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(lower, p)
		if err != nil {
			return err
		}
		target := filepath.Join(upper, rel)
		if err := os.MkdirAll(target, 0o777); err != nil {
			return err
		}
		return os.Chmod(target, 0o777)
	})
}

// Unexpose removes a server's exposure.
//
// It does not consult the consumer guard: removing a bind from a stopped
// server's volume is how a consumer STOPS holding a generation, so refusing
// while one holds would make the state unreachable. Publication's teardown
// is where the refusal belongs, and it runs after this.
//
// An rw binding's overlay upper/work layers are discarded here, after the
// mount is gone (D-035's "retained or discarded" decision, resolved
// discarded — see the LOG). That is safe rather than lossy because harvest
// is what reads a per-server overlay's merged view, and every documented rw
// flow reads it BEFORE calling this: by the time Unexpose runs, anything
// worth keeping has already been captured into a release. Best-effort:
// a stray directory left behind wastes disk, not correctness, and must
// never block a server's teardown.
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
		for _, b := range bindings {
			if b.Upper == "" {
				continue
			}
			_ = os.RemoveAll(filepath.Dir(b.Upper))
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

// RWServers returns every server currently holding an rw (overlay) exposure
// of rec, discovered from srdm's own mount table rather than from Docker or
// from any stored registry — the same reasoning as D-018, and simpler here:
// srdm mounted these overlays itself, in its own mount namespace, so it does
// not need to ask anything external about its own mounts.
//
// harvest uses this to default `--from-server` when exactly one server
// holds rw, and to refuse when more than one does — there is then no single
// truth to read without being told which.
func (h *HostBind) RWServers(rec *publish.Record) ([]string, error) {
	entries, err := mountinfo.Read(h.mountInfoPath)
	if err != nil {
		return nil, fmt.Errorf("expose: reading the mount table: %w", err)
	}
	lowers := make([]string, 0, len(rec.Classes))
	for _, c := range rec.Classes {
		lowers = append(lowers, c.ExposePath)
	}

	seen := make(map[string]bool)
	var servers []string
	for _, e := range entries {
		if e.FSType != "overlay" {
			continue
		}
		if !underAny(e.MountPoint, h.cfg.VolumeRoot) {
			continue
		}
		if !anyLowerUnder(overlayLowerDirs(e), lowers) {
			continue
		}
		server, ok := firstPathComponent(h.cfg.VolumeRoot, e.MountPoint)
		if !ok || seen[server] {
			continue
		}
		seen[server] = true
		servers = append(servers, server)
	}
	sort.Strings(servers)
	return servers, nil
}

// overlayLowerDirs parses the colon-separated `lowerdir=` mount option. See
// internal/consumer's copy of the same parse — duplicated rather than
// shared, because internal/mountinfo is out of this package's scope and the
// parse is a handful of lines either way.
func overlayLowerDirs(e mountinfo.Entry) []string {
	for _, opt := range e.SuperOptions {
		if v, ok := strings.CutPrefix(opt, "lowerdir="); ok {
			return strings.Split(v, ":")
		}
	}
	return nil
}

// anyLowerUnder reports whether any lowerdir element is at or beneath any of
// roots, matching whole path components.
func anyLowerUnder(lowerdirs, roots []string) bool {
	for _, l := range lowerdirs {
		if underAny(l, roots...) {
			return true
		}
	}
	return false
}

// underAny reports whether path is at or beneath any of roots, matching
// whole path components so "/run/srdm-old" is not beneath "/run/srdm".
func underAny(path string, roots ...string) bool {
	path = strings.TrimSuffix(path, "/")
	for _, root := range roots {
		root = strings.TrimSuffix(root, "/")
		if path == root || strings.HasPrefix(path, root+"/") {
			return true
		}
	}
	return false
}

// firstPathComponent returns the first path segment of target relative to
// root — the server id, since every rw binding's Target is
// VolumeRoot/<serverID>/<class path>.
func firstPathComponent(root, target string) (string, bool) {
	rel, err := filepath.Rel(root, target)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
		return "", false
	}
	seg, _, _ := strings.Cut(filepath.ToSlash(rel), "/")
	if seg == "" {
		return "", false
	}
	return seg, true
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
