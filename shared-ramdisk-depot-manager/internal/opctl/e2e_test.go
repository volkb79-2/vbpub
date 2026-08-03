//go:build e2e

// The operator surface against a real node: real tmpfs mounts, real hold
// units, a real server volume, and a real second mount namespace holding a
// generation.
//
// The unit tests assert the ORDER. Only this can say that the order produces
// a server whose volume actually holds the new content and no longer holds
// the old, and that the memory of the generation it left comes back.
package opctl

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/expose"
	"srdm/internal/harvest"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

const (
	workerEnv = "SRDM_E2E_WORKER"
	serverID  = "7f1c2e3d-0000-4000-8000-abcdefabcdef"
	otherID   = "8a2d3f4e-1111-4000-8000-abcdefabcdef"
)

func TestMain(m *testing.M) {
	if os.Getenv(workerEnv) == "1" && len(os.Args) > 1 && os.Args[1] == hold.WorkerSubcommand {
		os.Exit(hold.RunWorker(os.Args[2:], os.Stderr))
	}
	os.Exit(m.Run())
}

func requireRoot(t *testing.T) {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
}

// node is a whole miniature deployment driven only through opctl.
type node struct {
	t      *testing.T
	cfg    config.Config
	jnl    *journal.Journal
	prof   *profile.Profile
	st     *store.Store
	pub    *publish.Publisher
	ctl    *Controller
	asg    *assign.Store
	volume string
	// prefix makes every release id unique to this test. The generation id is
	// derived from the release id and the hold unit names from the
	// generation, so two tests sharing a release id race for a transient unit
	// name — and a unit outlives the test that made it for as long as systemd
	// takes to reap it.
	prefix string
}

// rid is the full release id for a short name.
func (n *node) rid(short string) string { return n.prefix + "-" + short }

func newNode(t *testing.T) *node {
	t.Helper()
	requireRoot(t)

	dir := t.TempDir()
	t.Cleanup(func() { reclaim(dir) })

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	owner := config.WriteOwner{UID: 65534, GID: 65534}
	cfg.Wings = config.Wings{
		BindRoot:   filepath.Join(dir, "pterodactyl"),
		VolumeRoot: filepath.Join(dir, "pterodactyl", "volumes"),
		ConfigPath: filepath.Join(dir, "wings.yml"),
		WriteOwner: &owner,
	}

	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	n := &node{t: t, cfg: cfg, jnl: jnl, prof: e2eProfile(t),
		prefix: strings.ReplaceAll(t.Name(), "/", "-")}
	if n.st, err = store.Open(cfg, jnl); err != nil {
		t.Fatal(err)
	}
	if n.asg, err = assign.New(cfg); err != nil {
		t.Fatal(err)
	}

	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	holder, err := hold.New(hold.WithExe(exe), hold.WithWorkerEnv(workerEnv+"=1"))
	if err != nil {
		t.Fatal(err)
	}
	if n.pub, err = publish.New(cfg, jnl, publish.WithHolder(holder)); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { n.cleanUp() })

	wingsCfg := cfg.Wings
	wingsCfg.ChownSkipPatch = true // asserted; precondition 2 is unit-tested
	drv, err := expose.NewHostBind(wingsCfg, jnl,
		expose.WithMarker(n.pub), expose.WithInspector(fakeInspector{}))
	if err != nil {
		t.Fatal(err)
	}
	hrv, err := harvest.New(cfg, jnl, n.st)
	if err != nil {
		t.Fatal(err)
	}
	if n.ctl, err = New(cfg, jnl, n.st, n.pub, drv, WithHarvester(hrv)); err != nil {
		t.Fatal(err)
	}

	// The Wings bind root has to be SHARED, which is what it is on a systemd
	// host; the gate container's /tmp is private.
	if err := os.MkdirAll(cfg.Wings.BindRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mount(cfg.Wings.BindRoot, cfg.Wings.BindRoot, "", syscall.MS_BIND, ""); err != nil {
		t.Fatalf("binding the Wings root onto itself: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(cfg.Wings.BindRoot, syscall.MNT_DETACH) })
	if err := syscall.Mount("", cfg.Wings.BindRoot, "", syscall.MS_SHARED, ""); err != nil {
		t.Fatalf("making the Wings root shared: %v", err)
	}

	n.volume = cfg.Wings.ServerVolume(serverID)
	// Per-instance state already in it, which nothing srdm does may touch.
	saved := filepath.Join(n.volume, "WS", "Saved", "world.db")
	if err := os.MkdirAll(filepath.Dir(saved), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(saved, []byte("a world nobody else may touch"), 0o644); err != nil {
		t.Fatal(err)
	}
	return n
}

func e2eProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged,
				Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20, NoExec: true},
			{Name: "code", Kind: profile.KindManaged,
				Paths: []string{"Engine"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

// stage promotes a release whose pak content names it, so which generation a
// server is reading is visible in the bytes rather than inferred.
func (n *node) stage(short string) *store.Release {
	n.t.Helper()
	releaseID := n.rid(short)
	tx, err := n.st.Begin("op-stage-" + releaseID)
	if err != nil {
		n.t.Fatal(err)
	}
	for rel, body := range map[string]string{
		"Engine/libengine.so":      "engine of " + short,
		"WS/Content/Paks/game.pak": "pak of " + short,
	} {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			n.t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			n.t.Fatal(err)
		}
	}
	rel, err := n.st.Promote(tx, n.prof, store.PromoteOpts{
		ReleaseID: releaseID, Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		n.t.Fatal(err)
	}
	return rel
}

// paking is what the server's volume currently shows.
func (n *node) paking() string {
	n.t.Helper()
	body, err := os.ReadFile(filepath.Join(n.volume, "WS/Content/Paks/game.pak"))
	if err != nil {
		return "<absent: " + err.Error() + ">"
	}
	return string(body)
}

func (n *node) liveUnder(root string) []string {
	n.t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		n.t.Fatal(err)
	}
	var out []string
	for _, e := range mountinfo.Under(entries, root) {
		out = append(out, e.MountPoint)
	}
	return out
}

func (n *node) cleanUp() {
	entries, err := mountinfo.Read("")
	if err != nil {
		return
	}
	var points []string
	for _, root := range []string{n.cfg.Wings.VolumeRoot, n.cfg.RunDir} {
		for _, e := range mountinfo.Under(entries, root) {
			points = append(points, e.MountPoint)
		}
	}
	for i := range points {
		for j := i + 1; j < len(points); j++ {
			if points[j] > points[i] {
				points[i], points[j] = points[j], points[i]
			}
		}
	}
	for _, m := range points {
		_ = syscall.Unmount(m, syscall.MNT_DETACH)
	}
}

func reclaim(root string) {
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		_ = os.Lchown(path, 0, 0)
		_ = os.Chmod(path, info.Mode().Perm()|0o700)
		return nil
	})
}

type fakeInspector struct{}

func (fakeInspector) BindPropagation(context.Context, string) (string, string, error) {
	return "rslave", "wings", nil
}

// spawnConsumer holds its own bind of path in its own mount namespace — what
// a game container is, as far as the superblock is concerned.
func spawnConsumer(t *testing.T, path string) (stop func()) {
	t.Helper()
	script := fmt.Sprintf("mkdir -p /tmp/held.$$ && mount --bind %q /tmp/held.$$ && "+
		"echo ready && exec sleep 600", path)
	cmd := exec.Command("unshare", "--mount", "--propagation", "slave", "/bin/sh", "-c", script)
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the consumer namespace: %v", err)
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
		t.Fatalf("the consumer never reported ready (%v)", sc.Err())
	}
	return stop
}

// --- the operator surface, end to end -------------------------------------

// Everything P01–P07 built, driven the way an operator drives it, with the
// only evidence being what is in the server's volume.
func TestActivateAttachAndSwapMoveTheServerOntoTheNewContent(t *testing.T) {
	n := newNode(t)
	n.stage("rel-1")
	n.stage("rel-2")

	// Activate with nothing attached: the generation exists, the volume does
	// not have it yet.
	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatalf("Activate: %v", err)
	}
	if got := n.paking(); !strings.HasPrefix(got, "<absent") {
		t.Errorf("activating with no server attached put content in a volume: %q", got)
	}

	// Attach, and the content arrives at the declared class paths.
	if err := n.ctl.Attach(ctx(), "op-2", serverID, "ro", n.prof); err != nil {
		t.Fatalf("Attach: %v", err)
	}
	if got := n.paking(); got != "pak of rel-1" {
		t.Fatalf("after attach the volume holds %q", got)
	}

	// Swap. This is the operation the whole package exists for: the server's
	// volume must end up showing rel-2 with nothing left of rel-1.
	if _, err := n.ctl.Activate(ctx(), "op-3", n.rid("rel-2"), n.prof); err != nil {
		t.Fatalf("Activate rel-2: %v", err)
	}
	if got := n.paking(); got != "pak of rel-2" {
		t.Fatalf("after the swap the volume holds %q, want rel-2's content", got)
	}

	// The old generation is gone: no mounts and no record.
	gen1 := publish.GenerationID(n.rid("rel-1"))
	if _, err := n.pub.LoadRecord("testgame", gen1); err == nil {
		t.Error("the replaced generation still has a published record")
	}
	for _, m := range n.liveUnder(n.cfg.RunDir) {
		if strings.Contains(m, gen1) {
			t.Errorf("the replaced generation is still mounted at %s", m)
		}
	}

	// The server's own state is untouched throughout — the invariant-14
	// waiver staying bounded across an activation, not just an exposure.
	body, err := os.ReadFile(filepath.Join(n.volume, "WS", "Saved", "world.db"))
	if err != nil || string(body) != "a world nobody else may touch" {
		t.Errorf("per-instance state changed across the swap: %q, %v", body, err)
	}

	// And the durable record says what happened, with a way back.
	a, err := n.asg.Load("testgame")
	if err != nil {
		t.Fatal(err)
	}
	if a.Release != n.rid("rel-2") || a.Previous != n.rid("rel-1") {
		t.Errorf("the assignment is release %q previous %q", a.Release, a.Previous)
	}
}

// Rollback is the same machinery in the other direction, and the assertion is
// that it puts the actual bytes back.
func TestRollbackPutsTheServerBackOnThePreviousRelease(t *testing.T) {
	n := newNode(t)
	n.stage("rel-1")
	n.stage("rel-2")

	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatal(err)
	}
	if err := n.ctl.Attach(ctx(), "op-2", serverID, "ro", n.prof); err != nil {
		t.Fatal(err)
	}
	if _, err := n.ctl.Activate(ctx(), "op-3", n.rid("rel-2"), n.prof); err != nil {
		t.Fatal(err)
	}
	if _, err := n.ctl.Rollback(ctx(), "op-4", n.prof); err != nil {
		t.Fatalf("Rollback: %v", err)
	}

	if got := n.paking(); got != "pak of rel-1" {
		t.Fatalf("after rollback the volume holds %q", got)
	}
	a, _ := n.asg.Load("testgame")
	if a.Release != n.rid("rel-1") || a.Previous != n.rid("rel-2") {
		t.Errorf("after rollback the assignment is release %q previous %q", a.Release, a.Previous)
	}
}

// Oracle 24 for activate, against a real second mount namespace: with a
// consumer still running, the operation is refused with the holder named, and
// nothing is attempted.
func TestActivateIsRefusedWhileARealConsumerHoldsTheGeneration(t *testing.T) {
	n := newNode(t)
	n.stage("rel-1")
	n.stage("rel-2")

	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatal(err)
	}
	if err := n.ctl.Attach(ctx(), "op-2", serverID, "ro", n.prof); err != nil {
		t.Fatal(err)
	}
	rec, err := n.pub.LoadRecord("testgame", publish.GenerationID(n.rid("rel-1")))
	if err != nil {
		t.Fatal(err)
	}
	stop := spawnConsumer(t, rec.Classes[0].ExposePath)

	_, err = n.ctl.Activate(ctx(), "op-3", n.rid("rel-2"), n.prof)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal while a consumer holds the live generation, got %v", err)
	}
	var heldErr *HeldError
	if !errors.As(err, &heldErr) {
		t.Fatalf("want a *HeldError, got %T", err)
	}
	// Nothing moved: the server is still reading what it was, and the record
	// still says so.
	if got := n.paking(); got != "pak of rel-1" {
		t.Errorf("a refused activation changed the volume to %q", got)
	}
	a, _ := n.asg.Load("testgame")
	if a.Release != n.rid("rel-1") {
		t.Errorf("a refused activation recorded %q", a.Release)
	}

	// After a clean stop it proceeds. A refusal that never lifted would be
	// indistinguishable from an operation nobody can perform.
	stop()
	if _, err := n.ctl.Activate(ctx(), "op-4", n.rid("rel-2"), n.prof); err != nil {
		t.Fatalf("activation was still refused after the consumer stopped: %v", err)
	}
	if got := n.paking(); got != "pak of rel-2" {
		t.Errorf("after the consumer stopped the swap left %q", got)
	}
}

// Detach removes one server's mounts and leaves the other's, which is the
// difference between a per-server operation and a per-generation one.
func TestDetachRemovesOnlyThatServersMounts(t *testing.T) {
	n := newNode(t)
	n.stage("rel-1")
	otherVolume := n.cfg.Wings.ServerVolume(otherID)
	if err := os.MkdirAll(otherVolume, 0o755); err != nil {
		t.Fatal(err)
	}

	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatal(err)
	}
	for i, id := range []string{serverID, otherID} {
		if err := n.ctl.Attach(ctx(), fmt.Sprintf("op-attach-%d", i), id, "ro", n.prof); err != nil {
			t.Fatal(err)
		}
	}
	if err := n.ctl.Detach(ctx(), "op-detach", serverID, n.prof); err != nil {
		t.Fatalf("Detach: %v", err)
	}

	if left := n.liveUnder(n.volume); len(left) != 0 {
		t.Errorf("detach left %v mounted under the detached server's volume", left)
	}
	if got := n.liveUnder(otherVolume); len(got) != 2 {
		t.Errorf("detach disturbed the other server: %v", got)
	}
	if _, ok := mustLoad(t, n.asg).Server(otherID); !ok {
		t.Error("detaching one server removed the other from the assignment")
	}
}

func mustLoad(t *testing.T, s *assign.Store) *assign.Assignment {
	t.Helper()
	a, err := s.Load("testgame")
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// Teardown takes the generation down and keeps the assignment, which is the
// state a boot has to notice: assigned, not published. It is what P08b's
// whole job is defined against.
func TestTeardownLeavesAnAssignedButUnpublishedProfile(t *testing.T) {
	n := newNode(t)
	n.stage("rel-1")

	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatal(err)
	}
	if err := n.ctl.Attach(ctx(), "op-2", serverID, "ro", n.prof); err != nil {
		t.Fatal(err)
	}
	if err := n.ctl.ReleaseGeneration(ctx(), "op-3", n.prof); err != nil {
		t.Fatalf("ReleaseGeneration: %v", err)
	}

	if left := n.liveUnder(n.cfg.RunDir); len(left) > 1 {
		t.Errorf("teardown left %v mounted (only the private op root should remain)", left)
	}
	if left := n.liveUnder(n.volume); len(left) != 0 {
		t.Errorf("teardown left %v mounted under the server volume", left)
	}
	a := mustLoad(t, n.asg)
	if a.Release != n.rid("rel-1") || len(a.Servers) != 1 {
		t.Errorf("teardown changed the assignment to %+v", a)
	}

	statuses, err := n.ctl.Status(ctx())
	if err != nil {
		t.Fatal(err)
	}
	if len(statuses) != 1 || statuses[0].Published {
		t.Errorf("status does not report the profile as assigned-but-unpublished: %+v", statuses)
	}
}

// gc against a real store with a real live generation: the release the
// generation was built from survives even though retention would collect it.
func TestGCKeepsThePublishedReleaseOnARealNode(t *testing.T) {
	n := newNode(t)
	for _, id := range []string{"rel-1", "rel-2", "rel-3", "rel-4"} {
		n.stage(id)
	}
	n.cfg.Retention = 1
	n.ctl.cfg.Retention = 1

	if _, err := n.ctl.Activate(ctx(), "op-1", n.rid("rel-1"), n.prof); err != nil {
		t.Fatal(err)
	}
	res, err := n.ctl.GC("op-gc", false)
	if err != nil {
		t.Fatalf("GC: %v", err)
	}
	if res.Kept[n.rid("rel-1")] != KeptAssigned {
		t.Errorf("the live release was kept for %q", res.Kept[n.rid("rel-1")])
	}
	if _, err := n.st.Load(n.rid("rel-1")); err != nil {
		t.Errorf("gc collected the release a live generation was built from: %v", err)
	}
	// And the generation it was collected around is still readable, which is
	// the point of the pin rather than a side effect of it.
	rec, err := n.pub.LoadRecord("testgame", publish.GenerationID(n.rid("rel-1")))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(rec.Classes[0].ExposePath)); err != nil {
		t.Errorf("the live generation did not survive gc: %v", err)
	}
}
