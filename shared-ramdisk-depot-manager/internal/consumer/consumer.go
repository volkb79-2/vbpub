// Package consumer answers one question: is anything still holding this
// generation's content?
//
// It is a correctness gate, not politeness. A game container that still has
// the bind in its own rprivate namespace keeps the superblock — and every
// page — alive across the host unmount, so the memory is never returned and
// the hold cgroup never drains. Teardown must therefore refuse rather than
// proceed into a leak it cannot see, and it must name what is holding, or an
// operator has a refusal with nowhere to go.
//
// There is no stored registry, and that is deliberate. In the v1 host-bind
// shape nothing tells srdm who mounted what: Wings creates the container,
// Docker resolves the source in the host namespace, and srdm is not in the
// conversation. A table srdm maintained itself could only ever be a second
// opinion about the kernel's — and the kernel is the thing actually holding
// the pages. So the registry IS the resolution, done fresh at every ask.
// The master plan says so about the oracle that covers this: it is oracle 15
// "without the protocol's help — the mode that has to get it right by
// inspection".
//
// Two sources, because they fail differently:
//
//   - **/proc/<pid>/mountinfo across mount namespaces** is authoritative
//     about the hazard. The hazard is a surviving MOUNT, not an open file
//     descriptor (D-012): an open fd makes the unmount fail EBUSY rather
//     than succeeding and leaving a ghost. Matching is on the SUPERBLOCK —
//     the major:minor device — because a consumer's bind has a path srdm has
//     never seen and cannot predict, while the device is the same number on
//     both sides of the bind.
//   - **Docker** is authoritative about names, and sees a container that has
//     the bind configured but no process running it yet.
package consumer

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"srdm/internal/mountinfo"
)

// Device identifies a superblock: the same number on both sides of a bind,
// in every mount namespace it reaches.
type Device struct {
	Major int
	Minor int
}

func (d Device) String() string { return fmt.Sprintf("%d:%d", d.Major, d.Minor) }

// Holder is one live hold on a generation's content.
type Holder struct {
	// Kind is how this hold was found: KindMount or KindContainer.
	Kind string
	// MountPoint is where the holder has it, in the holder's own namespace —
	// a path srdm never chose and cannot predict.
	MountPoint string
	// Device is the superblock, and is what actually ties this hold to a
	// generation srdm published.
	Device Device
	// PID is one process in the holding mount namespace. Any of them would
	// do; this is the one that was found first.
	PID int
	// MountNamespace is the /proc/<pid>/ns/mnt link target, so two holds in
	// the same namespace are one holder.
	MountNamespace string
	// ContainerID and ContainerName are filled in when the hold can be
	// attributed to a container. Empty means srdm found a hold it cannot
	// name — which is still a refusal, because the pages are held either way.
	ContainerID   string
	ContainerName string
}

// Holder kinds.
const (
	// KindMount is a mount of the generation's superblock in another mount
	// namespace. This is the one that costs memory.
	KindMount = "mount"
	// KindContainer is a container configured with a bind of the
	// generation's path, found through Docker rather than through the mount
	// table — a container created but not yet running holds no pages, but it
	// will break on its next start if the source is gone.
	KindContainer = "container"
)

func (h Holder) String() string {
	name := h.ContainerName
	if name == "" && h.ContainerID != "" {
		name = h.ContainerID
	}
	if name == "" {
		name = fmt.Sprintf("an unattributed process (pid %d, mount namespace %s)",
			h.PID, h.MountNamespace)
	}
	if h.Kind == KindContainer {
		return fmt.Sprintf("container %s (bind of %s)", name, h.MountPoint)
	}
	return fmt.Sprintf("%s (holding %s, device %s)", name, h.MountPoint, h.Device)
}

// Report is the result of asking who holds a generation.
type Report struct {
	Holders []Holder
	// Degraded names sources that could not be consulted. It is never empty
	// silently: a check that did not fully run says so, and the caller
	// records it, because "nobody is holding this" and "I could not ask" are
	// different answers and only one of them is safe to act on.
	Degraded []string
}

// Held reports whether anything holds the generation.
func (r *Report) Held() bool { return len(r.Holders) > 0 }

// HeldError is teardown, activation or rollback declining because a consumer
// still holds the content.
type HeldError struct {
	Generation string
	Holders    []Holder
}

func (e *HeldError) Error() string {
	var who []string
	for _, h := range e.Holders {
		who = append(who, h.String())
	}
	return fmt.Sprintf("consumer: generation %s is still held by %d consumer(s): %s; "+
		"unmounting now would free nothing — the superblock and every page stay alive "+
		"in the holder's own namespace — so this is refused rather than proceeding into "+
		"a leak", e.Generation, len(e.Holders), strings.Join(who, "; "))
}

// DockerLister is the Docker surface, injected so resolution is exercisable
// with no daemon present.
type DockerLister interface {
	RunningContainers(ctx context.Context) ([]Container, error)
}

// Resolver resolves holders.
type Resolver struct {
	procRoot string
	selfNS   string
	docker   DockerLister
}

// Option configures a Resolver.
type Option func(*Resolver)

// WithProcRoot points resolution at a synthetic /proc, so the namespace and
// device logic is testable without privilege.
func WithProcRoot(path string) Option { return func(r *Resolver) { r.procRoot = path } }

// WithDocker injects the Docker surface. A nil lister disables that source,
// which is reported as degraded rather than passed over.
func WithDocker(d DockerLister) Option { return func(r *Resolver) { r.docker = d } }

// New returns a Resolver over the real /proc and the real Docker socket.
func New(opts ...Option) *Resolver {
	r := &Resolver{procRoot: "/proc", docker: NewDocker("")}
	for _, o := range opts {
		o(r)
	}
	return r
}

// SelfNamespace overrides which mount namespace counts as srdm's own.
//
// Everything in that namespace is srdm looking at its own mounts, not a
// consumer holding them — and getting that backwards would make teardown
// refuse forever.
func (r *Resolver) SelfNamespace(ns string) { r.selfNS = ns }

// Resolve returns every hold on the given paths.
//
// The paths are srdm's own mount points for one generation. Their
// superblocks are what is looked for elsewhere; the paths themselves appear
// nowhere in a consumer's namespace, which is exactly why matching on them
// would find nothing.
func (r *Resolver) Resolve(ctx context.Context, paths []string) (*Report, error) {
	rep := &Report{}

	self, err := mountinfo.Read(filepath.Join(r.procRoot, "self", "mountinfo"))
	if err != nil {
		return nil, fmt.Errorf("consumer: reading srdm's own mount table: %w", err)
	}
	devices := ourDevices(self, paths)
	if len(devices) == 0 {
		// Nothing of this generation is mounted here, so there is no
		// superblock for anyone to be holding. That is a real answer, not a
		// gap: after a crash mid-teardown it is the normal one.
		return rep, nil
	}

	selfNS := r.selfNS
	if selfNS == "" {
		selfNS, err = r.namespaceOf("self")
		if err != nil {
			return nil, fmt.Errorf("consumer: reading srdm's own mount namespace: %w", err)
		}
	}

	holders, err := r.mountHolders(devices, selfNS)
	if err != nil {
		return nil, err
	}
	rep.Holders = holders

	if r.docker == nil {
		rep.Degraded = append(rep.Degraded, "docker: no client configured")
		return rep, nil
	}
	containers, err := r.docker.RunningContainers(ctx)
	if err != nil {
		// The mount table already answered the question that costs memory,
		// so an unreachable Docker does not invalidate the result — but it
		// does mean names are missing and a created-but-not-started
		// container would not be seen. Say so.
		rep.Degraded = append(rep.Degraded, "docker: "+err.Error())
		return rep, nil
	}
	attribute(rep.Holders, containers, r.procRoot)
	rep.Holders = append(rep.Holders, bindingContainers(containers, paths, rep.Holders)...)
	sortHolders(rep.Holders)
	return rep, nil
}

// ourDevices collects the superblocks srdm has mounted at the given paths.
func ourDevices(entries []mountinfo.Entry, paths []string) map[Device]bool {
	devices := make(map[Device]bool)
	for _, p := range paths {
		if e, ok := mountinfo.At(entries, p); ok {
			devices[Device{Major: e.Major, Minor: e.Minor}] = true
		}
	}
	return devices
}

// mountHolders scans every mount namespace but srdm's own for the devices.
//
// EVERY mount of one of those superblocks counts, with no exception for
// propagation, and that is a measured position rather than a lazy one.
//
// The tempting filter is to excuse a mount that carries `master:<one of our
// peer groups>`, on the theory that it is downstream of srdm's own and will
// be removed by the same unmount. Measured on systemd 257 / kernel 7.1,
// 2026-08-03: it is not. Unmounting the host side left the copies in a slave
// namespace in place, with no master tag and the content still readable
// through them. Propagation delivers a mount; it does not take it back.
//
// So there is nothing to filter on: a mount of the superblock is a mount of
// the superblock, and every one of them keeps every page alive. See D-019 —
// which is also why srdm publishes into a private mount root, so that
// nothing acquires such a copy by accident in the first place.
func (r *Resolver) mountHolders(devices map[Device]bool, selfNS string) ([]Holder, error) {
	pids, err := r.pids()
	if err != nil {
		return nil, err
	}

	// One namespace, one holder: every process in it sees the same mounts,
	// so reading them per-process would report the same hold once per thread
	// of a busy container.
	seen := make(map[string]bool)
	var out []Holder
	for _, pid := range pids {
		ns, err := r.namespaceOf(strconv.Itoa(pid))
		if err != nil {
			// A process that exited between the listing and the read, or a
			// kernel thread with no namespace link. Neither is a holder.
			continue
		}
		if ns == selfNS || seen[ns] {
			continue
		}
		seen[ns] = true

		entries, err := mountinfo.Read(filepath.Join(r.procRoot, strconv.Itoa(pid), "mountinfo"))
		if err != nil {
			continue
		}
		for _, e := range entries {
			d := Device{Major: e.Major, Minor: e.Minor}
			if !devices[d] {
				continue
			}
			out = append(out, Holder{
				Kind: KindMount, MountPoint: e.MountPoint, Device: d,
				PID: pid, MountNamespace: ns,
			})
		}
	}
	sortHolders(out)
	return out, nil
}

func (r *Resolver) pids() ([]int, error) {
	entries, err := os.ReadDir(r.procRoot)
	if err != nil {
		return nil, fmt.Errorf("consumer: reading %s: %w", r.procRoot, err)
	}
	var pids []int
	for _, e := range entries {
		n, err := strconv.Atoi(e.Name())
		if err != nil {
			continue // not a pid directory
		}
		pids = append(pids, n)
	}
	sort.Ints(pids)
	return pids, nil
}

// namespaceOf reads a process's mount namespace identity.
func (r *Resolver) namespaceOf(pid string) (string, error) {
	return os.Readlink(filepath.Join(r.procRoot, pid, "ns", "mnt"))
}

// attribute fills in container identity for mount holders, by matching the
// holding process's cgroup path against the running containers.
//
// The cgroup path is used rather than Docker's own PID field because a
// container's PID field names only its main process, and the hold can be
// held by any process in the namespace.
func attribute(holders []Holder, containers []Container, procRoot string) {
	byID := make(map[string]Container, len(containers))
	for _, c := range containers {
		byID[c.ID] = c
	}
	for i := range holders {
		id := containerIDOfPID(procRoot, holders[i].PID)
		if id == "" {
			continue
		}
		holders[i].ContainerID = id
		if c, ok := byID[id]; ok {
			holders[i].ContainerName = c.Name
		}
	}
}

// containerIDOfPID reads the container id out of a process's cgroup path.
//
// Both shapes appear in the wild: cgroupfs writes `/docker/<64 hex>` and
// systemd writes `.../docker-<64 hex>.scope`.
func containerIDOfPID(procRoot string, pid int) string {
	body, err := os.ReadFile(filepath.Join(procRoot, strconv.Itoa(pid), "cgroup"))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(body), "\n") {
		_, path, ok := strings.Cut(strings.TrimSpace(line), "::")
		if !ok {
			// cgroup v1 lines are `<id>:<controllers>:<path>`; take the tail.
			fields := strings.SplitN(strings.TrimSpace(line), ":", 3)
			if len(fields) != 3 {
				continue
			}
			path = fields[2]
		}
		for _, seg := range strings.Split(path, "/") {
			seg = strings.TrimSuffix(seg, ".scope")
			seg = strings.TrimPrefix(seg, "docker-")
			if isContainerID(seg) {
				return seg
			}
		}
	}
	return ""
}

func isContainerID(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, r := range s {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
	}
	return true
}

// bindingContainers finds containers configured with a bind of one of the
// paths but with no mount holding it yet.
//
// Such a container costs no memory, so it is not the leak — but it is
// pinned: tearing the generation down leaves its next start with a source
// that is not there.
func bindingContainers(containers []Container, paths []string, already []Holder) []Holder {
	held := make(map[string]bool, len(already))
	for _, h := range already {
		if h.ContainerID != "" {
			held[h.ContainerID] = true
		}
	}
	var out []Holder
	for _, c := range containers {
		if held[c.ID] {
			continue
		}
		for _, m := range c.Mounts {
			if !pathWithin(m.Source, paths) {
				continue
			}
			out = append(out, Holder{
				Kind: KindContainer, MountPoint: m.Source,
				ContainerID: c.ID, ContainerName: c.Name,
			})
			break
		}
	}
	return out
}

// pathWithin reports whether source is at or beneath any of the paths,
// matching whole components so "/run/srdm/g1" does not also select
// "/run/srdm/g10".
func pathWithin(source string, paths []string) bool {
	source = strings.TrimSuffix(source, "/")
	for _, p := range paths {
		p = strings.TrimSuffix(p, "/")
		if source == p || strings.HasPrefix(source, p+"/") {
			return true
		}
	}
	return false
}

func sortHolders(h []Holder) {
	sort.Slice(h, func(i, j int) bool {
		if h[i].Kind != h[j].Kind {
			return h[i].Kind < h[j].Kind
		}
		if h[i].MountNamespace != h[j].MountNamespace {
			return h[i].MountNamespace < h[j].MountNamespace
		}
		return h[i].MountPoint < h[j].MountPoint
	})
}
