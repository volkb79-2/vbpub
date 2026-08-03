package store

import (
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/journal"
	"srdm/internal/profile"
)

// fixedTime is the only clock the tests use. Nothing here decides a verdict
// from a timestamp; a fixed value simply keeps journal records comparable.
var fixedTime = time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)

// testConfig roots a whole store under a fresh directory. The filesystem is
// an input, so every test gets its own.
func testConfig(dir string) config.Config {
	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	// Ownership normalization needs privilege srdm does not have in a test,
	// and claiming otherwise would be worse than skipping it. Mode
	// normalization still runs.
	cfg.Owner = config.Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
	return cfg
}

func newTestJournal(t *testing.T, cfg config.Config) *journal.Journal {
	t.Helper()
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixedTime }),
		// No journald in a test: the socket is a property of the host, and a
		// unit test that talks to one is testing the host.
		journal.WithJournaldSocket(""),
	)
	if err != nil {
		t.Fatalf("journal.New: %v", err)
	}
	t.Cleanup(func() { _ = jnl.Close() })
	return jnl
}

func newTestStore(t *testing.T, dir string) *Store {
	t.Helper()
	cfg := testConfig(dir)
	st, err := Open(cfg, newTestJournal(t, cfg))
	if err != nil {
		t.Fatalf("store.Open: %v", err)
	}
	return st
}

// testProfile mirrors the shape of the real Soulmask profile: an
// incompressible pak class, a compressible code class, and per-instance
// state that is classified but never published.
func testProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := newTestProfile()
	if err := p.Validate(); err != nil {
		t.Fatalf("profile.Validate: %v", err)
	}
	return p
}

// newTestProfile builds the same profile without a *testing.T, so the crash
// oracle's child process — which has no test context — agrees with its
// parent on classification without shipping a document between them.
func newTestProfile() *profile.Profile {
	return &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{
				Name: "pak", Kind: profile.KindManaged,
				Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20, NoExec: true,
			},
			{
				Name: "code", Kind: profile.KindManaged,
				Paths: []string{"Engine", "WS/Binaries"}, MemoryMin: 200 << 20,
			},
			{
				Name: "state", Kind: profile.KindExcluded,
				Paths: []string{"WS/Saved", "WS/Config"},
			},
		},
	}
}

// contentA is a well-formed tree: every path classifies.
var contentA = map[string]string{
	"Engine/libengine.so":      "engine bytes",
	"WS/Binaries/server":       "server bytes",
	"WS/Content/Paks/game.pak": "pak bytes, quite long so a flip keeps the size",
	"WS/Content/Paks/game.sig": "sig",
}

// writeTree materializes a content map and normalizes every mode, so the
// result does not depend on the umask the suite happens to run under.
func writeTree(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for rel, content := range files {
		p := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", filepath.Dir(p), err)
		}
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
	}
	normalizeModes(t, root)
}

func normalizeModes(t *testing.T, root string) {
	t.Helper()
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Mode()&fs.ModeSymlink != 0 {
			return nil
		}
		if d.IsDir() {
			return os.Chmod(path, 0o755)
		}
		return os.Chmod(path, 0o644)
	})
	if err != nil {
		t.Fatalf("normalize modes under %s: %v", root, err)
	}
}

// makeFifo creates a named pipe, for the "a release may not contain one"
// case.
func makeFifo(path string) error { return syscall.Mkfifo(path, 0o644) }

// promoteContent copies a content map into a fresh transaction and promotes
// it, returning the release.
func promoteContent(t *testing.T, st *Store, p *profile.Profile, opID, releaseID, channel string, files map[string]string) *Release {
	t.Helper()
	tx, err := st.Begin(opID)
	if err != nil {
		t.Fatalf("Begin(%s): %v", opID, err)
	}
	writeTree(t, tx.Root, files)
	rel, err := st.Promote(tx, p, PromoteOpts{
		ReleaseID:  releaseID,
		Channel:    channel,
		Provenance: Provenance{Kind: ProvenanceStaged, Source: "test"},
	})
	if err != nil {
		t.Fatalf("Promote(%s): %v", releaseID, err)
	}
	return rel
}
