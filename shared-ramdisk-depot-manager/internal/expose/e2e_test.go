//go:build e2e

// The privileged half of exposure: what a game server would actually find
// under its volume, and what happens when it writes there.
//
// The unit tests assert the plan and the refusals. Only this can say whether
// a write through an exposed bind returns EROFS, whether per-instance state
// survives untouched beneath the mounts, and whether republishing really
// does discard everything written through an rw exposure — which is the
// documentation's loudest claim and therefore the one most worth checking.
package expose

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
	"srdm/internal/wings"
)

const workerEnv = "SRDM_E2E_WORKER"

func TestMain(m *testing.M) {
	// The hold worker is srdm invoked on itself; here the running process is
	// a test binary, so the same trick is played on it.
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

// node is a whole miniature deployment: a store, a published generation, and
// a server volume with per-instance state already in it.
type node struct {
	cfg     config.Config
	jnl     *journal.Journal
	prof    *profile.Profile
	rel     *store.Release
	pub     *publish.Publisher
	rec     *publish.Record
	volume  string
	savedAt string
}

var content = map[string]string{
	"Engine/libengine.so":      "engine bytes",
	"WS/Binaries/server":       "server bytes",
	"WS/Content/Paks/game.pak": "pak bytes",
	"WS/Saved/world.db":        "never published",
}

func newNode(t *testing.T) *node {
	t.Helper()
	requireRoot(t)

	dir := t.TempDir()
	// Publication seals its trees read-only, so restore write access before
	// TempDir's own cleanup runs. Cleanups are last-registered-first.
	t.Cleanup(func() { restoreWritable(dir) })

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	cfg.Wings = config.Wings{
		BindRoot:   filepath.Join(dir, "pterodactyl"),
		VolumeRoot: filepath.Join(dir, "pterodactyl", "volumes"),
		ConfigPath: filepath.Join(dir, "wings.yml"),
	}

	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	n := &node{cfg: cfg, jnl: jnl, prof: e2eProfile(t)}
	n.rel = n.stage(t)

	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	holder, err := hold.New(hold.WithExe(exe), hold.WithWorkerEnv(workerEnv+"=1"))
	if err != nil {
		t.Fatal(err)
	}
	n.pub, err = publish.New(cfg, jnl, publish.WithHolder(holder))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { n.cleanUp(t) })

	// The Wings bind root has to be a SHARED mount, which is what it is on a
	// systemd host (/ is shared and /var/lib inherits it). The gate
	// container's /tmp is private, so the harness makes it so explicitly —
	// otherwise the propagation precondition would fail for a reason
	// production never has.
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

	// The server's volume, with its own state already in it — which is what
	// the bounded-waiver oracle is about.
	n.volume = cfg.Wings.ServerVolume(serverID)
	n.savedAt = filepath.Join(n.volume, "WS", "Saved", "world.db")
	if err := os.MkdirAll(filepath.Dir(n.savedAt), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(n.savedAt, []byte("a world nobody else may touch"), 0o644); err != nil {
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
				Paths: []string{"Engine", "WS/Binaries"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

func (n *node) stage(t *testing.T) *store.Release {
	t.Helper()
	st, err := store.Open(n.cfg, n.jnl)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := st.Begin("op-stage")
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range content {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	rel, err := st.Promote(tx, n.prof, store.PromoteOpts{
		ReleaseID:  "rel-" + strings.ReplaceAll(t.Name(), "/", "-"),
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return rel
}

// publishGeneration publishes and returns the record.
func (n *node) publishGeneration(t *testing.T, opID string) *publish.Record {
	t.Helper()
	rec, err := n.pub.Publish(context.Background(), opID, "testgame", n.rel, n.prof)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	n.rec = rec
	return rec
}

// driver returns a host-bind driver for this node. The container inspector
// is injected because the gate has no Docker socket; its logic is covered by
// the unit tests, and what is under test here is everything downstream of it.
func (n *node) driver(t *testing.T, opts ...Option) *HostBind {
	t.Helper()
	cfg := n.cfg.Wings
	cfg.ChownSkipPatch = true // the node asserts F1; precondition 2 is unit-tested
	base := []Option{
		WithMarker(n.pub),
		WithInspector(fakeE2EInspector{}),
	}
	d, err := NewHostBind(cfg, n.jnl, append(base, opts...)...)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

type fakeE2EInspector struct{}

func (fakeE2EInspector) BindPropagation(context.Context, string) (string, string, error) {
	return "rslave", "wings", nil
}

func (n *node) cleanUp(t *testing.T) {
	t.Helper()
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
	// Deepest first.
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

func restoreWritable(root string) {
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		_ = os.Chmod(path, info.Mode().Perm()|0o200)
		return nil
	})
}

func hashOf(t *testing.T, path string) string {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

func liveUnder(t *testing.T, root string) []string {
	t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range mountinfo.Under(entries, root) {
		out = append(out, e.MountPoint)
	}
	return out
}

// --- oracle 19: the invariant-14 waiver stays in bounds -------------------

// host-bind waives invariant 14 deliberately: managed content DOES appear
// under the server's host volume path, where Wings' own filesystem
// operations walk over it. The waiver is the price of needing no Wings
// patch. This is what keeps it bounded — content at exactly the declared
// class paths, and the server's own state untouched beneath it.
func TestExposedContentAppearsAtTheDeclaredPathsAndNowhereElse(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1")
	d := n.driver(t)

	before := hashOf(t, n.savedAt)

	if err := d.Expose(context.Background(), rec, n.prof, request(AccessRO)); err != nil {
		t.Fatalf("Expose: %v", err)
	}

	// Exactly the declared class paths carry a mount, and nothing else does.
	want := map[string]bool{
		filepath.Join(n.volume, "Engine"):          true,
		filepath.Join(n.volume, "WS/Binaries"):     true,
		filepath.Join(n.volume, "WS/Content/Paks"): true,
	}
	got := liveUnder(t, n.volume)
	if len(got) != len(want) {
		t.Fatalf("mounts under the volume are %v, want exactly %d declared class paths",
			got, len(want))
	}
	for _, m := range got {
		if !want[m] {
			t.Errorf("managed content appeared at %s, which no class declares", m)
		}
	}

	// The content is there and readable.
	body, err := os.ReadFile(filepath.Join(n.volume, "WS/Content/Paks/game.pak"))
	if err != nil || string(body) != "pak bytes" {
		t.Errorf("the pak class did not arrive: %q, %v", body, err)
	}

	// And the server's own state is untouched, not shadowed, not replaced.
	if after := hashOf(t, n.savedAt); after != before {
		t.Errorf("world.db changed across exposure: %s -> %s", before, after)
	}
	if _, err := os.Stat(filepath.Join(n.volume, "WS", "Saved")); err != nil {
		t.Errorf("per-instance state is no longer reachable: %v", err)
	}

	// Unexpose puts it back exactly as it was.
	if err := d.Unexpose(context.Background(), rec, n.prof, request(AccessRO)); err != nil {
		t.Fatalf("Unexpose: %v", err)
	}
	if left := liveUnder(t, n.volume); len(left) != 0 {
		t.Errorf("unexpose left %v mounted", left)
	}
	if after := hashOf(t, n.savedAt); after != before {
		t.Errorf("world.db changed across teardown: %s -> %s", before, after)
	}
}

// ro is the safe mode, and what makes it safe is the mount rather than the
// modes: EROFS is the mount refusing, and it holds even for root. A
// chmod-only seal would give EACCES and would not.
func TestAReadOnlyExposureRefusesWritesWithEROFS(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1")

	if err := n.driver(t).Expose(context.Background(), rec, n.prof, request(AccessRO)); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(n.volume, "WS/Content/Paks", "smuggled.pak")
	err := os.WriteFile(target, []byte("x"), 0o644)
	if err == nil {
		t.Fatal("writing into a read-only exposure succeeded")
	}
	var errno syscall.Errno
	if !errors.As(err, &errno) || errno != syscall.EROFS {
		t.Errorf("writing gave %v, want EROFS", err)
	}

	// And the server's own state is still writable — the whole point of
	// binding the class paths rather than the volume root.
	if err := os.WriteFile(filepath.Join(n.volume, "WS", "Saved", "new.db"),
		[]byte("mine"), 0o644); err != nil {
		t.Errorf("the server can no longer write its own state: %v", err)
	}
}

// --- oracle 20: preconditions refuse, they do not warn --------------------

// The host half of the propagation precondition, against the real mount
// table. With the bind root private there is nothing for the container's
// rslave to be a slave of, so every mount srdm made would be invisible.
func TestExposeRefusesWhenTheHostSideIsPrivate(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1")

	// Undo what the harness set up, which is exactly the state a host has
	// when somebody made the subtree private.
	if err := syscall.Mount("", n.cfg.Wings.BindRoot, "", syscall.MS_PRIVATE, ""); err != nil {
		t.Fatal(err)
	}

	err := n.driver(t).Expose(context.Background(), rec, n.prof, request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal, got %v", err)
	}
	if !strings.Contains(err.Error(), "make-rshared") {
		t.Errorf("the refusal does not name the remedy: %v", err)
	}
	if left := liveUnder(t, n.volume); len(left) != 0 {
		t.Errorf("a refused exposure mounted %v anyway", left)
	}
}

// --- oracle 22: ephemerality is observable --------------------------------

// The documentation's loudest warning, checked rather than asserted: a write
// through an rw exposure does not survive a republish. A generation is
// ephemeral, and republishing is how a written-through one is repaired —
// which is only true if the republished content really does come from the
// store.
func TestAWriteThroughAnRWExposureDoesNotSurviveRepublish(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1")
	d := n.driver(t)

	if err := d.Expose(context.Background(), rec, n.prof, request(AccessRW)); err != nil {
		t.Fatalf("Expose rw: %v", err)
	}

	// Write through it, into the generation's own tmpfs.
	written := filepath.Join(n.volume, "WS/Content/Paks", "written-through.pak")
	if err := os.WriteFile(written, []byte("a write nobody promoted"), 0o644); err != nil {
		t.Fatalf("writing through the rw exposure: %v", err)
	}
	if _, err := os.Stat(written); err != nil {
		t.Fatalf("the write did not land, so this test proves nothing: %v", err)
	}

	// The generation is now marked as having been writable, durably.
	marked, err := n.pub.LoadRecord("testgame", rec.Generation)
	if err != nil {
		t.Fatal(err)
	}
	if !marked.DirtyCapable {
		t.Error("an rw exposure did not mark the generation dirty-capable, so nothing " +
			"downstream would know it had been written through")
	}

	// Republish: unexpose, tear down, publish again from the store.
	if err := d.Unexpose(context.Background(), rec, n.prof, request(AccessRW)); err != nil {
		t.Fatalf("Unexpose: %v", err)
	}
	if err := n.pub.Teardown(context.Background(), "op-down", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	fresh := n.publishGeneration(t, "op-2")
	if err := d.Expose(context.Background(), fresh, n.prof, request(AccessRO)); err != nil {
		t.Fatalf("re-expose: %v", err)
	}

	if _, err := os.Stat(written); !os.IsNotExist(err) {
		t.Fatalf("a write through an rw exposure survived a republish (%v). Generations are "+
			"documented as ephemeral and repaired by republishing; if that is not true, the "+
			"documentation is the thing that is wrong", err)
	}
	// And the republished generation is clean again.
	if fresh.DirtyCapable {
		t.Error("a freshly published generation was already marked dirty-capable")
	}
	if body, err := os.ReadFile(filepath.Join(n.volume, "WS/Content/Paks/game.pak")); err != nil ||
		string(body) != "pak bytes" {
		t.Errorf("the republished content is not what the store holds: %q, %v", body, err)
	}
}

// --- the driver against a real Wings config -------------------------------

// The one key srdm reads out of the node config, read from a real file
// rather than an injected answer.
func TestTheChownWalkIsReadFromTheRealNodeConfig(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1")

	if err := os.WriteFile(n.cfg.Wings.ConfigPath, []byte(
		"system:\n  root_directory: /var/lib/pterodactyl\n  check_permissions_on_boot: true\n"),
		0o644); err != nil {
		t.Fatal(err)
	}
	// No F1 asserted, so the walk decides.
	cfg := n.cfg.Wings
	d, err := NewHostBind(cfg, n.jnl, WithMarker(n.pub), WithInspector(fakeE2EInspector{}))
	if err != nil {
		t.Fatal(err)
	}
	err = d.Expose(context.Background(), rec, n.prof, request(AccessRO))
	if !IsRefusal(err) {
		t.Fatalf("want a refusal on a node that will chown-walk, got %v", err)
	}

	if err := os.WriteFile(n.cfg.Wings.ConfigPath, []byte(
		"system:\n  check_permissions_on_boot: false\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := d.Expose(context.Background(), rec, n.prof, request(AccessRO)); err != nil {
		t.Fatalf("a node that disables the walk was refused anyway: %v", err)
	}
	if _, err := os.Stat(filepath.Join(n.volume, "Engine", "libengine.so")); err != nil {
		t.Errorf("the exposure did not arrive: %v", err)
	}
	_ = wings.ChownWalk{}
}
