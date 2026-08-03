package consumer

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// --- harness ---------------------------------------------------------------

// The generation under test: one class, one superblock. The op tmpfs and the
// read-only exposure are a bind of each other, so they share a device — which
// is the whole basis of resolution.
const (
	opMount    = "/run/srdm/.op/op-1/pak"
	exposePath = "/run/srdm/testgame/a1b2c3d4/pak"
	ourDevice  = "0:50"
	// A tmpfs that is not ours. Nothing about it may produce a holder.
	otherDevice = "0:77"

	selfNS   = "mnt:[4026531840]"
	holderNS = "mnt:[4026532999]"

	containerID = "3f2b1a9c8d7e6f504132231415161718192021222324252627282930313233ab"
)

// mountLine renders one mountinfo entry. The mount point is the holder's own
// path, which srdm never chose and cannot predict — only the device ties it
// back to a generation.
func mountLine(id int, device, root, point string) string {
	return mountLineOpt(id, device, root, point, "")
}

// mountLineOpt adds the propagation tags, which live before the " - "
// separator and are exactly why the format has one.
func mountLineOpt(id int, device, root, point, optional string) string {
	maj, min, _ := strings.Cut(device, ":")
	return fmt.Sprintf("%d 30 %s:%s %s %s rw,nosuid,nodev %s - tmpfs tmpfs rw,size=67108864,mode=755",
		id, maj, min, root, point, optional)
}

// ourPeerGroup is the propagation peer group srdm's own mounts belong to. On
// a systemd host these mounts are shared, because `/run` is.
const ourPeerGroup = "shared:5"

// ourMountTable is what srdm's own mount table says: the op tmpfs and its
// read-only exposure, both on the same superblock, both shared.
func ourMountTable() string {
	return strings.Join([]string{
		mountLineOpt(40, ourDevice, "/", opMount, ourPeerGroup),
		mountLineOpt(41, ourDevice, "/root", exposePath, ourPeerGroup),
		mountLine(42, otherDevice, "/", "/run/somebody-else"),
	}, "\n")
}

type fakeProc struct{ root string }

func newProc(t *testing.T) *fakeProc {
	t.Helper()
	return &fakeProc{root: t.TempDir()}
}

// process writes one entry into the synthetic /proc.
func (p *fakeProc) process(t *testing.T, pid, ns, mounts, cgroup string) {
	t.Helper()
	dir := filepath.Join(p.root, pid)
	if err := os.MkdirAll(filepath.Join(dir, "ns"), 0o755); err != nil {
		t.Fatal(err)
	}
	// A dangling symlink is exactly what /proc/<pid>/ns/mnt is: the target is
	// an identity, not a path.
	if err := os.Symlink(ns, filepath.Join(dir, "ns", "mnt")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "mountinfo"), []byte(mounts+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if cgroup != "" {
		if err := os.WriteFile(filepath.Join(dir, "cgroup"), []byte(cgroup+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

// resolver builds a Resolver over the synthetic tree, with srdm itself
// present as "self" and as a pid in its own namespace.
func (p *fakeProc) resolver(t *testing.T, docker DockerLister) *Resolver {
	t.Helper()
	p.process(t, "self", selfNS, ourMountTable(), "")
	p.process(t, "1", selfNS, ourMountTable(), "0::/init.scope")
	return New(WithProcRoot(p.root), WithDocker(docker))
}

func paths() []string { return []string{opMount, exposePath} }

type fakeDocker struct {
	containers []Container
	err        error
}

func (f *fakeDocker) RunningContainers(context.Context) ([]Container, error) {
	return f.containers, f.err
}

func resolve(t *testing.T, r *Resolver) *Report {
	t.Helper()
	rep, err := r.Resolve(context.Background(), paths())
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

// --- the hold that costs memory -------------------------------------------

// A bind of our superblock in another mount namespace. This is the shape a
// game container has, and the shape that keeps every page alive across the
// host unmount.
func TestAMountOfOurSuperblockInAnotherNamespaceIsAHolder(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"), "")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if !rep.Held() {
		t.Fatalf("a consumer holding the superblock was not found: %+v", rep)
	}
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %+v, want exactly one", rep.Holders)
	}
	h := rep.Holders[0]
	if h.Kind != KindMount {
		t.Errorf("Kind = %q, want %q", h.Kind, KindMount)
	}
	// The holder's own path, not ours. srdm has never seen this path and
	// could not have matched on it.
	if h.MountPoint != "/home/container/paks" {
		t.Errorf("MountPoint = %q, want the holder's own path", h.MountPoint)
	}
	if h.PID != 4242 || h.MountNamespace != holderNS {
		t.Errorf("holder = pid %d in %s, want pid 4242 in %s", h.PID, h.MountNamespace, holderNS)
	}
}

// srdm's own mounts must never count. Getting this backwards does not fail
// loudly — it makes teardown refuse forever, on every generation, because
// srdm is always holding its own content.
func TestOurOwnMountsAreNotHolders(t *testing.T) {
	p := newProc(t)
	// Another process in srdm's namespace, seeing exactly the same mounts.
	p.process(t, "999", selfNS, ourMountTable(), "")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if rep.Held() {
		t.Fatalf("srdm reported itself as a consumer of its own generation: %+v", rep.Holders)
	}
}

// A different superblock is a different generation, or somebody else's tmpfs
// entirely. Matching on anything looser — the path, the filesystem type —
// would pin generations to unrelated mounts.
func TestAMountOfADifferentSuperblockIsNotAHolder(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, otherDevice, "/root", "/home/container/paks"), "")
	r := p.resolver(t, &fakeDocker{})

	if rep := resolve(t, r); rep.Held() {
		t.Fatalf("an unrelated tmpfs was reported as holding this generation: %+v", rep.Holders)
	}
}

// Every process in a namespace sees the same mounts. Reporting per process
// would name a busy container once per thread and turn one refusal into
// hundreds of lines.
func TestOneNamespaceIsOneHolderHoweverManyProcesses(t *testing.T) {
	p := newProc(t)
	held := mountLine(90, ourDevice, "/root", "/home/container/paks")
	for _, pid := range []string{"4242", "4243", "4244"} {
		p.process(t, pid, holderNS, held, "")
	}
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %d, want 1 — three processes share one mount namespace", len(rep.Holders))
	}
}

// Nothing of the generation is mounted here, so there is no superblock for
// anyone to hold. After a crash mid-teardown this is the normal case, and
// treating it as unknown would deadlock recovery.
func TestNothingMountedMeansNothingHeld(t *testing.T) {
	p := newProc(t)
	p.process(t, "self", selfNS, mountLine(42, otherDevice, "/", "/run/somebody-else"), "")
	p.process(t, "4242", holderNS, mountLine(90, ourDevice, "/root", "/home/container/paks"), "")
	r := New(WithProcRoot(p.root), WithDocker(&fakeDocker{}))

	rep := resolve(t, r)
	if rep.Held() {
		t.Fatalf("holders were reported for a generation srdm has not mounted: %+v", rep.Holders)
	}
}

// --- propagation: seeing a mount is not holding it ------------------------

// A namespace that received srdm's mount by PROPAGATION is not a consumer.
// It will receive the unmount too, so it cannot outlive it and cannot keep a
// page alive.
//
// This is not a nicety. On a systemd host `/run` is shared, so every service
// with a private mount namespace — PrivateTmp, ProtectSystem, any `unshare` —
// gets a slave copy of every publication mount. Without this, teardown would
// refuse forever, on every generation, and the refusal would look exactly
// like a real consumer.
// A mount delivered by PROPAGATION is a hold like any other, and this is the
// test that says so — because the opposite is the intuitive answer and it is
// wrong.
//
// The intuition: it carries `master:<our peer group>`, so it is downstream
// of srdm's own mount and srdm's unmount will take it away with it. Measured
// on kernel 7.1, 2026-08-03: it will not. Unmounting the host side left the
// copy in place — it merely lost the master tag — and the content stayed
// readable through it. Propagation delivers a mount; it does not take it
// back. Filtering on the tag would have excluded a real holder, which is the
// silent leak this whole check exists to prevent. See D-019.
func TestAPropagatedCopyIsStillAHolder(t *testing.T) {
	p := newProc(t)
	// The shape propagation creates: our peer group, and OUR mount point.
	p.process(t, "4242", holderNS,
		mountLineOpt(90, ourDevice, "/root", exposePath, "master:5"), "")
	r := p.resolver(t, &fakeDocker{})

	if rep := resolve(t, r); !rep.Held() {
		t.Fatal("a copy of srdm's mount in another namespace was excused as downstream; " +
			"measured, srdm's unmount does not remove it and the pages stay")
	}
}

// The measurement that made the obvious rule wrong, 2026-08-03.
//
// A consumer that creates its own bind inside a slave namespace gets a mount
// tagged with OUR peer group — and that mount survives the host unmount with
// its content still readable. Filtering on the tag alone would drop a real
// holder and turn this check into the silent leak it exists to prevent.
// What distinguishes the two is the mount point: propagation puts its copy
// where ours is, and a consumer's bind is somewhere else by construction.
func TestASlaveTaggedBindAtItsOwnPathIsStillAHolder(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLineOpt(90, ourDevice, "/root", "/home/container/paks", "master:5"), "")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if !rep.Held() {
		t.Fatal("a bind that carries our peer group but lives at the consumer's own path " +
			"was filtered away; measured on systemd 257, that mount SURVIVES the host " +
			"unmount with its content intact, so this is a leak")
	}
	if rep.Holders[0].MountPoint != "/home/container/paks" {
		t.Errorf("MountPoint = %q", rep.Holders[0].MountPoint)
	}
}

// What a slave-propagation namespace actually looks like: copies of our
// mounts at our paths, plus its own bind elsewhere. All three keep the
// superblock alive, so all three are reported — and the operator is told
// once per mount, which is the honest count of what is holding on.
func TestEveryCopyInTheNamespaceIsReported(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS, strings.Join([]string{
		mountLineOpt(90, ourDevice, "/", opMount, "master:5"),
		mountLineOpt(91, ourDevice, "/root", exposePath, "master:5"),
		mountLineOpt(92, ourDevice, "/root", "/home/container/paks", "master:5"),
	}, "\n"), "")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if len(rep.Holders) != 3 {
		t.Fatalf("Holders = %+v, want all three — none of them goes away when srdm "+
			"unmounts", rep.Holders)
	}
}

// Docker's own shape: an rprivate bind, no propagation tag at all. This is
// the 2026-07-31 ghost-mount case and the one that must never be filtered.
func TestAnUntaggedBindIsAHolder(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"), "")
	r := p.resolver(t, &fakeDocker{})

	if rep := resolve(t, r); !rep.Held() {
		t.Fatal("an rprivate bind — exactly what Docker gives a container — was not found")
	}
}

// A copy at our path but from somebody ELSE's peer group is not downstream
// of srdm, so srdm's unmount does not reach it.
func TestACopyFromADifferentPeerGroupIsStillAHolder(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLineOpt(90, ourDevice, "/root", exposePath, "master:99"), "")
	r := p.resolver(t, &fakeDocker{})

	if rep := resolve(t, r); !rep.Held() {
		t.Fatal("a mount downstream of an unrelated peer group was filtered away as if " +
			"srdm's unmount would reach it")
	}
}

// --- naming the holder -----------------------------------------------------

// A refusal an operator cannot act on is barely better than a leak. The
// container is identified from the holding process's cgroup path, not from
// Docker's PID field, because that names only a container's main process and
// the hold can be held by any process in the namespace.
func TestAHolderIsNamedFromItsCgroupAndDocker(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"),
		"0::/system.slice/docker-"+containerID+".scope")
	r := p.resolver(t, &fakeDocker{containers: []Container{
		{ID: containerID, Name: "soulmask-01"},
		{ID: strings.Repeat("f", 64), Name: "something-else"},
	}})

	rep := resolve(t, r)
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %+v", rep.Holders)
	}
	h := rep.Holders[0]
	if h.ContainerID != containerID {
		t.Errorf("ContainerID = %q, want %q", h.ContainerID, containerID)
	}
	if h.ContainerName != "soulmask-01" {
		t.Errorf("ContainerName = %q, want soulmask-01", h.ContainerName)
	}
	if !strings.Contains(h.String(), "soulmask-01") {
		t.Errorf("the holder does not name the container an operator has to stop: %s", h)
	}
}

// cgroupfs writes /docker/<id>; systemd writes .../docker-<id>.scope. Both
// appear on real hosts, and reading only one shape loses the name on the
// other.
func TestBothCgroupLayoutsYieldTheContainerID(t *testing.T) {
	cases := map[string]string{
		"systemd":  "0::/system.slice/docker-" + containerID + ".scope",
		"cgroupfs": "0::/docker/" + containerID,
		"v1":       "1:name=systemd:/docker/" + containerID,
		"neither":  "0::/system.slice/sshd.service",
	}
	for name, cgroup := range cases {
		p := newProc(t)
		p.process(t, "4242", holderNS,
			mountLine(90, ourDevice, "/root", "/home/container/paks"), cgroup)
		r := p.resolver(t, &fakeDocker{containers: []Container{{ID: containerID, Name: "soulmask-01"}}})

		rep := resolve(t, r)
		if len(rep.Holders) != 1 {
			t.Fatalf("%s: Holders = %+v", name, rep.Holders)
		}
		want := containerID
		if name == "neither" {
			want = ""
		}
		if got := rep.Holders[0].ContainerID; got != want {
			t.Errorf("%s: ContainerID = %q, want %q", name, got, want)
		}
	}
}

// A hold srdm cannot attribute is still a hold. The pages are held whether
// or not anything can say by whom, so this must refuse — and say plainly
// that it could not name it, rather than pretend there is nothing there.
func TestAnUnattributableHoldStillRefuses(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/mnt/held"), "0::/system.slice/sshd.service")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if !rep.Held() {
		t.Fatal("a hold with no container behind it was ignored")
	}
	desc := rep.Holders[0].String()
	if !strings.Contains(desc, "4242") || !strings.Contains(desc, "unattributed") {
		t.Errorf("the holder gives an operator nothing to go on: %s", desc)
	}
}

// --- the container that holds nothing yet ---------------------------------

// Created but not running: no mount, no pages, no leak — and still pinned,
// because tearing the generation down leaves its next start with a source
// that is not there.
func TestAContainerBoundToThePathWithNoMountIsAHolder(t *testing.T) {
	p := newProc(t)
	r := p.resolver(t, &fakeDocker{containers: []Container{{
		ID: containerID, Name: "soulmask-02",
		Mounts: []Mount{
			{Source: "/var/lib/pterodactyl/volumes/abc", Destination: "/home/container"},
			{Source: exposePath, Destination: "/home/container/paks", ReadOnly: true},
		},
	}}})

	rep := resolve(t, r)
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %+v, want the bound container", rep.Holders)
	}
	if rep.Holders[0].Kind != KindContainer {
		t.Errorf("Kind = %q, want %q", rep.Holders[0].Kind, KindContainer)
	}
	if rep.Holders[0].ContainerName != "soulmask-02" {
		t.Errorf("ContainerName = %q", rep.Holders[0].ContainerName)
	}
}

// A container already found through the mount table must not be reported
// twice — once as the mount that costs memory and once as the bind that
// configured it.
func TestAContainerHoldingAMountIsReportedOnce(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"),
		"0::/docker/"+containerID)
	r := p.resolver(t, &fakeDocker{containers: []Container{{
		ID: containerID, Name: "soulmask-01",
		Mounts: []Mount{{Source: exposePath, Destination: "/home/container/paks"}},
	}}})

	rep := resolve(t, r)
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %+v, want one entry for one container", rep.Holders)
	}
	if rep.Holders[0].Kind != KindMount {
		t.Errorf("Kind = %q, want the mount — that is the hold that costs memory",
			rep.Holders[0].Kind)
	}
}

// Whole path components only, or a generation would pin its own neighbours.
func TestABindOfANeighbouringPathIsNotAHolder(t *testing.T) {
	p := newProc(t)
	r := p.resolver(t, &fakeDocker{containers: []Container{{
		ID: containerID, Name: "other-generation",
		Mounts: []Mount{{Source: exposePath + "-old", Destination: "/home/container/paks"}},
	}}})

	if rep := resolve(t, r); rep.Held() {
		t.Fatalf("a bind of a neighbouring path was reported as a holder: %+v", rep.Holders)
	}
}

// --- degrading loudly ------------------------------------------------------

// An unreachable Docker does not invalidate the answer that costs memory —
// the mount table already gave it — but it does mean names are missing and a
// created-but-not-started container would go unseen. Silence would be the
// wrong answer: "nobody is holding this" and "I could not ask" are different,
// and only one of them is safe to act on.
func TestAnUnreachableDockerIsReportedAsDegraded(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"), "")
	r := p.resolver(t, &fakeDocker{err: errors.New("dial unix /var/run/docker.sock: no such file")})

	rep := resolve(t, r)
	if len(rep.Degraded) != 1 || !strings.Contains(rep.Degraded[0], "docker") {
		t.Fatalf("Degraded = %v, want the Docker failure named", rep.Degraded)
	}
	// And the hold is still found, because the mount table is what matters.
	if !rep.Held() {
		t.Fatal("an unreachable Docker lost a hold the mount table could see")
	}
}

func TestNoDockerClientIsAlsoDegraded(t *testing.T) {
	p := newProc(t)
	p.process(t, "self", selfNS, ourMountTable(), "")
	r := New(WithProcRoot(p.root), WithDocker(nil))

	rep := resolve(t, r)
	if len(rep.Degraded) == 0 {
		t.Fatal("a resolver with no Docker source reported a complete check")
	}
}

// A process that exits between listing /proc and reading it is normal on a
// busy host, and is not a holder. Neither is a kernel thread with no
// namespace link.
func TestVanishedAndNamespacelessProcessesAreSkipped(t *testing.T) {
	p := newProc(t)
	// A pid directory with no ns/mnt link at all.
	if err := os.MkdirAll(filepath.Join(p.root, "7"), 0o755); err != nil {
		t.Fatal(err)
	}
	p.process(t, "4242", holderNS,
		mountLine(90, ourDevice, "/root", "/home/container/paks"), "")
	r := p.resolver(t, &fakeDocker{})

	rep := resolve(t, r)
	if len(rep.Holders) != 1 {
		t.Fatalf("Holders = %+v, want just the real one", rep.Holders)
	}
}

// --- the refusal -----------------------------------------------------------

// The error has to carry what an operator needs: which generation, and what
// to stop.
func TestHeldErrorNamesTheGenerationAndTheHolders(t *testing.T) {
	err := &HeldError{
		Generation: "testgame/a1b2c3d4",
		Holders: []Holder{{
			Kind: KindMount, MountPoint: "/home/container/paks",
			Device: Device{0, 50}, PID: 4242, MountNamespace: holderNS,
			ContainerID: containerID, ContainerName: "soulmask-01",
		}},
	}
	msg := err.Error()
	for _, want := range []string{"testgame/a1b2c3d4", "soulmask-01", "refused"} {
		if !strings.Contains(msg, want) {
			t.Errorf("the refusal does not mention %q: %s", want, msg)
		}
	}
	// And it says why unmounting anyway would not help, or the next person
	// reads the refusal as bureaucracy and adds a --force.
	if !strings.Contains(msg, "free nothing") {
		t.Errorf("the refusal does not explain what proceeding would cost: %s", msg)
	}
}

func TestDeviceStringIsTheMountinfoForm(t *testing.T) {
	if got := (Device{Major: 0, Minor: 50}).String(); got != "0:50" {
		t.Fatalf("Device.String = %q, want 0:50", got)
	}
}

// A /proc that cannot be read at all is an error, not an empty answer.
func TestAnUnreadableProcIsAnErrorNotAnEmptyAnswer(t *testing.T) {
	r := New(WithProcRoot(filepath.Join(t.TempDir(), "nonexistent")), WithDocker(&fakeDocker{}))
	if _, err := r.Resolve(context.Background(), paths()); err == nil {
		t.Fatal("resolution over an unreadable /proc reported that nothing holds the generation")
	}
}

// Guard against the harness itself lying: the synthetic pids must actually
// parse as pids, or every test above would pass by finding nothing.
func TestTheHarnessProducesReadablePids(t *testing.T) {
	p := newProc(t)
	p.process(t, "4242", holderNS, mountLine(90, ourDevice, "/root", "/x"), "")
	p.process(t, "self", selfNS, ourMountTable(), "")
	r := New(WithProcRoot(p.root))
	pids, err := r.pids()
	if err != nil {
		t.Fatal(err)
	}
	if len(pids) != 1 || pids[0] != 4242 {
		t.Fatalf("pids = %v, want [4242] — \"self\" is not a pid", pids)
	}
	if _, err := strconv.Atoi("self"); err == nil {
		t.Fatal("this test's premise is wrong")
	}
}
