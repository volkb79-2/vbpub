//go:build e2e

// The privileged half of consumer resolution: real mount namespaces, read
// through the real /proc.
//
// Nothing above this can be trusted on the strength of the synthetic tree
// alone. That tree proves srdm reasons correctly about mountinfo it was
// handed; only this proves it reads the right thing in the first place —
// that a bind in another namespace really does report the same major:minor,
// that srdm's own namespace really is excluded, and that a holder really
// does vanish from /proc when its process does.
package consumer

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"srdm/internal/mountinfo"
)

func requireRoot(t *testing.T) {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
}

// publishedTmpfs mounts a tmpfs and a read-only bind of it under a private
// root, which is the shape publication produces: one superblock, two mount
// points, and nothing delivered to anybody else's namespace.
func publishedTmpfs(t *testing.T) (opMount, exposePath string) {
	t.Helper()
	requireRoot(t)

	root := t.TempDir()
	// The isolation publication performs: the root becomes a mount point of
	// its own, marked private, so nothing beneath it propagates anywhere.
	if err := syscall.Mount(root, root, "", syscall.MS_BIND, ""); err != nil {
		t.Fatalf("binding the root onto itself: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(root, syscall.MNT_DETACH) })
	if err := syscall.Mount("", root, "", syscall.MS_PRIVATE, ""); err != nil {
		t.Fatalf("making the root private: %v", err)
	}

	opMount = filepath.Join(root, "op")
	exposePath = filepath.Join(root, "expose")
	for _, d := range []string{opMount, exposePath} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := syscall.Mount("tmpfs", opMount, "tmpfs",
		syscall.MS_NODEV|syscall.MS_NOSUID, "size=8388608,mode=0755"); err != nil {
		t.Fatalf("mount tmpfs: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(opMount, syscall.MNT_DETACH) })

	content := filepath.Join(opMount, "root")
	if err := os.MkdirAll(content, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(content, "game.pak"), []byte("pak bytes"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mount(content, exposePath, "", syscall.MS_BIND, ""); err != nil {
		t.Fatalf("bind: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(exposePath, syscall.MNT_DETACH) })
	return opMount, exposePath
}

// spawnNamespace runs a parked process in its own mount namespace, after
// running setup inside it. The returned stop waits for the process to be
// reaped, which is a real synchronization point rather than a delay: after
// it, the pid and its mount namespace are gone from /proc.
func spawnNamespace(t *testing.T, propagation, setup string) (stop func()) {
	t.Helper()
	requireRoot(t)

	// The shell prints ready only after setup has run, so nothing below
	// races it. Then it execs, so the process that remains IS the sleep.
	script := setup + "echo ready && exec sleep 600"
	cmd := exec.Command("unshare", "--mount", "--propagation", propagation, "/bin/sh", "-c", script)
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the namespace: %v", err)
	}
	stopped := false
	stop = func() {
		if stopped {
			return
		}
		stopped = true
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}
	t.Cleanup(stop)

	sc := bufio.NewScanner(out)
	if !sc.Scan() || strings.TrimSpace(sc.Text()) != "ready" {
		stop()
		t.Fatalf("the namespace never reported ready (%v)", sc.Err())
	}
	return stop
}

// startConsumer holds its own bind of path — what a game container is, as
// far as the superblock is concerned.
func startConsumer(t *testing.T, path string) (stop func()) {
	t.Helper()
	return spawnNamespace(t, "slave",
		fmt.Sprintf("mkdir -p /tmp/held.$$ && mount --bind %q /tmp/held.$$ && ", path))
}

// startBystander is a service that merely HAS its own mount namespace and
// wants nothing from srdm — systemd-resolved with PrivateTmp, say. It is
// started before publication, exactly as a long-running daemon is.
func startBystander(t *testing.T) (stop func()) {
	t.Helper()
	return spawnNamespace(t, "slave", "")
}

func resolveLive(t *testing.T, paths ...string) *Report {
	t.Helper()
	// No Docker in the gate container, which is fine and is reported as
	// degraded: the mount table is what answers the question that costs
	// memory.
	rep, err := New().Resolve(context.Background(), paths)
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

func holdingPaths(rep *Report) []string {
	var out []string
	for _, h := range rep.Holders {
		out = append(out, h.MountPoint)
	}
	return out
}

// --- the hold, through the real /proc -------------------------------------

func TestALiveConsumerIsFoundThroughTheRealProc(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)

	// Before: nobody. This half matters as much as the other — srdm's own
	// namespace has every process on the host in it, and every one of them
	// can see these mounts.
	if rep := resolveLive(t, opMount, exposePath); rep.Held() {
		t.Fatalf("srdm reported a holder before any consumer existed: %+v", rep.Holders)
	}

	stop := startConsumer(t, exposePath)

	rep := resolveLive(t, opMount, exposePath)
	if !rep.Held() {
		t.Fatalf("a consumer holding a bind in its own namespace was not found: %+v", rep)
	}
	var found bool
	for _, h := range rep.Holders {
		if strings.HasPrefix(h.MountPoint, "/tmp/held.") {
			found = true
			if h.Kind != KindMount {
				t.Errorf("Kind = %q, want %q", h.Kind, KindMount)
			}
			if h.PID == 0 || h.MountNamespace == "" {
				t.Errorf("the holder is not identified: %+v", h)
			}
			t.Logf("found %s", h)
		}
	}
	if !found {
		// The path is the consumer's own — srdm never chose it and could not
		// have matched on it. Only the superblock connects the two.
		t.Fatalf("the consumer's own bind is not among the holders: %v", holdingPaths(rep))
	}

	// And it all goes away when the consumer does. Waiting for the process
	// to be reaped is the synchronization point; nothing here sleeps.
	stop()
	if rep := resolveLive(t, opMount, exposePath); rep.Held() {
		t.Fatalf("a stopped consumer is still reported as holding: %v", holdingPaths(rep))
	}
}

// The op tmpfs and the exposure are one superblock, so a consumer bound to
// either is found by asking about either. An operator who pointed a
// container at the op mount instead of the exposure gets the same leak.
func TestAConsumerOfTheOpMountIsFoundByAskingAboutTheExposure(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)
	startConsumer(t, opMount)

	if rep := resolveLive(t, exposePath); !rep.Held() {
		t.Fatalf("a consumer of the op tmpfs was invisible when asking about the "+
			"exposure, though both are the same superblock: %+v", rep)
	}
}

// A superblock srdm never published must not produce a holder, however many
// namespaces have it mounted.
//
// The consumer is started BEFORE publication, which is also what makes the
// assertion clean: `unshare` copies the whole mount table into a new
// namespace, so a namespace created afterwards would hold copies of the
// generation as well and this would be measuring two things at once.
func TestAnUnrelatedTmpfsInAnotherNamespaceIsNotAHolder(t *testing.T) {
	requireRoot(t)

	other := t.TempDir()
	if err := syscall.Mount("tmpfs", other, "tmpfs", 0, "size=1048576"); err != nil {
		t.Fatalf("mount the unrelated tmpfs: %v", err)
	}
	defer func() { _ = syscall.Unmount(other, syscall.MNT_DETACH) }()
	startConsumer(t, other)

	opMount, exposePath := publishedTmpfs(t)
	if rep := resolveLive(t, opMount, exposePath); rep.Held() {
		t.Fatalf("an unrelated tmpfs was reported as holding this generation: %v",
			holdingPaths(rep))
	}
}

// --- D-019: why publication isolates its own root -------------------------

// A service that has its own mount namespace and wants nothing from srdm
// must not end up holding a generation.
//
// On a systemd host `/run` is shared, so a mount created beneath it is
// DELIVERED to every namespace that is a slave of the root — every service
// with PrivateTmp, ProtectSystem or its own unshare. Those copies are real
// holds (see below), so without the private publication root every
// generation would acquire a holder per such service and teardown would be
// refused forever, correctly and uselessly.
func TestABystanderNamespaceDoesNotReceiveAPrivatelyPublishedGeneration(t *testing.T) {
	// The bystander exists BEFORE publication, exactly as a long-running
	// daemon does. That ordering is the whole point: propagation delivers
	// mounts made after the namespace was created.
	startBystander(t)
	opMount, exposePath := publishedTmpfs(t)

	rep := resolveLive(t, opMount, exposePath)
	if rep.Held() {
		t.Fatalf("a bystander namespace received the publication mounts and now holds "+
			"the generation: %v — the operation root is not private", holdingPaths(rep))
	}
}

// Why there is no propagation filter, stated as the thing that was tried.
//
// A copy of srdm's mount in another namespace carries `master:<our peer
// group>`, which looks downstream: srdm's unmount ought to take it away
// again, and excusing it would keep teardown from refusing over namespaces
// that hold nothing. That filter was written, and then deleted, because its
// soundness could not be established. Measured 2026-08-03 on kernel 7.1: a
// copy carrying exactly that tag SURVIVED the host unmount, with the content
// still readable through it, when the namespace also held its own bind of
// the same superblock. In other arrangements the same tag is removed as
// expected. Nothing in mountinfo tells the two apart.
//
// Over-filtering is a silent leak; under-filtering is a refusal an operator
// can see and act on. So srdm does not filter, and instead does not hand the
// copies out in the first place — the private publication root above. This
// test pins the consequence that matters: a namespace holding its own bind
// keeps the content across srdm's unmount, so refusing was right.
func TestAConsumersOwnBindSurvivesTheHostUnmount(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)
	startConsumer(t, exposePath)

	rep := resolveLive(t, opMount, exposePath)
	if !rep.Held() {
		t.Fatal("the consumer was not found")
	}
	var pid int
	var held string
	for _, h := range rep.Holders {
		if strings.HasPrefix(h.MountPoint, "/tmp/held.") {
			pid, held = h.PID, h.MountPoint
		}
	}
	if pid == 0 {
		t.Fatalf("the consumer's own bind is not among %v", holdingPaths(rep))
	}

	// srdm unmounts its side, as an unguarded teardown would.
	if err := syscall.Unmount(exposePath, 0); err != nil {
		t.Fatalf("unmounting the exposure: %v", err)
	}
	if err := syscall.Unmount(opMount, 0); err != nil {
		t.Fatalf("unmounting the op tmpfs: %v", err)
	}

	entries, err := mountinfo.Read(fmt.Sprintf("/proc/%d/mountinfo", pid))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := mountinfo.At(entries, held); !ok {
		t.Fatalf("%s went away with srdm's unmount. If a consumer's bind really is "+
			"removed that way, the pages come back on their own and the whole refusal "+
			"is unnecessary — verify that before relaxing it", held)
	}
	// Not a husk: the content is still there, which is the same thing as
	// saying the pages were never freed.
	body, err := os.ReadFile(fmt.Sprintf("/proc/%d/root%s/game.pak", pid, held))
	if err != nil {
		t.Fatalf("reading through the surviving bind: %v", err)
	}
	if string(body) != "pak bytes" {
		t.Fatalf("read %q through the surviving bind", body)
	}
	t.Logf("%s survived srdm's unmount with its content intact", held)
}

// With no Docker socket in the gate, resolution still answers the question
// that costs memory — and says plainly that it could not name anything.
func TestResolutionDegradesRatherThanFailsWithoutDocker(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)
	stop := startConsumer(t, exposePath)
	defer stop()

	rep := resolveLive(t, opMount, exposePath)
	if !rep.Held() {
		t.Fatal("the hold was lost along with the Docker source")
	}
	if len(rep.Degraded) == 0 {
		t.Error("no Docker socket, and the check reported itself complete")
	}
	// The refusal still has to be actionable, even with nothing to name it.
	if !strings.Contains(rep.Holders[0].String(), "unattributed") {
		t.Errorf("an unnamed holder does not say so: %s", rep.Holders[0])
	}
}

// --- oracle 27 / D-028: the overlay recognizer, for real -------------------

// startOverlayConsumer mounts a real overlay of lower inside a fresh mount
// namespace — what access: rw's exposure looks like from a consumer's own
// side, and the shape D-028 measured as invisible to device matching.
//
// Upper/work go on /tmp inside the namespace, which is tmpfs and not itself
// an overlay — D-027's other measured fact, "an overlay cannot be its own
// upper", would make this mount fail outright on a container whose own root
// is overlay2-backed.
func startOverlayConsumer(t *testing.T, lower string) (stop func()) {
	t.Helper()
	return spawnNamespace(t, "slave", fmt.Sprintf(
		"mkdir -p /tmp/ovl.$$/upper /tmp/ovl.$$/work /tmp/ovl.$$/merged && "+
			"mount -t overlay overlay -o lowerdir=%q,upperdir=/tmp/ovl.$$/upper,workdir=/tmp/ovl.$$/work "+
			"/tmp/ovl.$$/merged && ", lower))
}

// The measurement D-028 exists to act on: a real overlay, in a real second
// mount namespace, whose lowerdir is srdm's own ExposePath. Device matching
// alone (everything srdm had before P10) would report nothing, because an
// overlay reports its own device and the lower's never appears in its
// mountinfo line — only the `lowerdir=` PATH does.
func TestARealOverlayWhoseLowerdirIsOurExposePathIsFound(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)

	if rep := resolveLive(t, opMount, exposePath); rep.Held() {
		t.Fatalf("srdm reported a holder before any consumer existed: %+v", rep.Holders)
	}

	stop := startOverlayConsumer(t, exposePath)

	rep := resolveLive(t, opMount, exposePath)
	if !rep.Held() {
		t.Fatalf("a real overlay whose lowerdir is our ExposePath was not found: %+v", rep)
	}
	var found *Holder
	for i := range rep.Holders {
		if rep.Holders[i].Kind == KindOverlay {
			found = &rep.Holders[i]
		}
	}
	if found == nil {
		t.Fatalf("no KindOverlay holder among %+v", rep.Holders)
	}
	if found.LowerDir != exposePath {
		t.Errorf("LowerDir = %q, want %q", found.LowerDir, exposePath)
	}
	if !strings.HasSuffix(found.MountPoint, "/merged") {
		t.Errorf("MountPoint = %q, want the consumer's own merged mount point", found.MountPoint)
	}
	t.Logf("found %s", found)

	// And it goes away when the consumer does — the same shape as a bind.
	stop()
	if rep := resolveLive(t, opMount, exposePath); rep.Held() {
		t.Fatalf("a stopped overlay consumer is still reported as holding: %v", holdingPaths(rep))
	}
}

// D-028's decisive check, mirrored from tools/overlay-holder-probe.sh: with
// only the (pre-P10) device recognizer, this holder would be invisible —
// the unmount would succeed, free nothing, and leave the content readable.
// Asserted here as "the overlay's own device is unrelated to srdm's",
// which is what makes device matching blind to it in the first place.
func TestTheOverlaysOwnDeviceNamesNothingSrdmMounted(t *testing.T) {
	opMount, exposePath := publishedTmpfs(t)
	// The namespace's own overlay is not its only holder: it is also a
	// "slave" propagation namespace, so it receives propagated copies of
	// srdm's own opMount/exposePath binds (D-019) — real holders, sorted
	// before "overlay" by Kind, which is why the overlay entry has to be
	// found by Kind rather than assumed to be Holders[0].
	stop := startOverlayConsumer(t, exposePath)
	defer stop()

	rep := resolveLive(t, opMount, exposePath)
	if !rep.Held() {
		t.Fatal("the overlay consumer was not found")
	}
	var found *Holder
	for i := range rep.Holders {
		if rep.Holders[i].Kind == KindOverlay {
			found = &rep.Holders[i]
		}
	}
	if found == nil {
		t.Fatalf("no KindOverlay holder among %+v", rep.Holders)
	}
	if d := found.Device; d != (Device{}) {
		t.Errorf("the overlay holder carries Device %s; an overlay's own device names "+
			"nothing srdm mounted, so a non-zero one here would mean this matched by "+
			"device rather than by lowerdir path", d)
	}
}
