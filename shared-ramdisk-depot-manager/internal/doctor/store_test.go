package doctor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// buildStore promotes one real release into cfg's store, so the integrity
// check runs against the actual on-disk format rather than a mock of it.
func buildStore(t *testing.T, cfg config.Config, p *profile.Profile) *store.Release {
	t.Helper()
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC) }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	st, err := store.Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range map[string]string{
		"Engine/libengine.so":      "engine bytes",
		"WS/Content/Paks/game.pak": "pak bytes",
	} {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	release, err := st.Promote(tx, p, store.PromoteOpts{
		ReleaseID:  "rel-1",
		Channel:    "stable",
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return release
}

func TestStoreIntegrityPassesForAVerifiedRelease(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	buildStore(t, cfg, fixtureProfile(t))

	checks := Run(cfg, fixtureProfile(t), f.options())

	if c := find(t, checks, "store-integrity"); c.Status != StatusPass {
		t.Fatalf("store-integrity is %s for a freshly promoted release: %s", c.Status, c.Detail)
	}
	if c := find(t, checks, "release/rel-1"); c.Status != StatusPass {
		t.Fatalf("release/rel-1 is %s: %s", c.Status, c.Detail)
	}
}

// The check has to be able to fail, or it launders a corrupt store as
// healthy. Both shapes of corruption are covered: content that drifted, and
// a release that never finished.
func TestStoreIntegrityCatchesDriftedContent(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	release := buildStore(t, cfg, fixtureProfile(t))

	target := filepath.Join(release.RootDir(), "WS", "Content", "Paks", "game.pak")
	if err := os.WriteFile(target, []byte("pak bytez"), 0o644); err != nil {
		t.Fatal(err)
	}

	checks := Run(cfg, fixtureProfile(t), f.options())

	c := find(t, checks, "store-integrity")
	if c.Status != StatusFail {
		t.Fatalf("store-integrity is %s after content drifted", c.Status)
	}
	rc := find(t, checks, "release/rel-1")
	if rc.Status != StatusFail {
		t.Fatalf("release/rel-1 is %s after content drifted", rc.Status)
	}
	if !strings.Contains(rc.Detail, "game.pak") {
		t.Errorf("the detail does not name the drifted file: %s", rc.Detail)
	}
	if rc.Fix == "" {
		t.Error("a failing release check must name the remedy")
	}
	if len(Failed(checks)) < 2 {
		t.Errorf("Failed() reported %d checks, want the summary and the release",
			len(Failed(checks)))
	}
}

func TestStoreIntegrityCatchesAReleaseThatNeverCompleted(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	release := buildStore(t, cfg, fixtureProfile(t))

	if err := os.Remove(filepath.Join(release.Dir, store.CompleteFile)); err != nil {
		t.Fatal(err)
	}

	checks := Run(cfg, fixtureProfile(t), f.options())
	rc := find(t, checks, "release/rel-1")
	if rc.Status != StatusFail {
		t.Fatalf("release/rel-1 is %s with no %s", rc.Status, store.CompleteFile)
	}
	if !strings.Contains(rc.Detail, store.CompleteFile) {
		t.Errorf("the detail does not mention %s: %s", store.CompleteFile, rc.Detail)
	}
}

// doctor must never need write access: an operator inspecting a store on a
// read-only mount, or one owned by another uid, still gets an answer.
func TestStoreIntegrityDoesNotOpenTheStoreForWriting(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	buildStore(t, cfg, fixtureProfile(t))

	// Removing the transaction directory would break store.Open, which
	// creates it. If the integrity check still works, it is not going
	// through Open.
	if err := os.RemoveAll(cfg.TxDir()); err != nil {
		t.Fatal(err)
	}
	checks := Run(cfg, fixtureProfile(t), f.options())
	if c := find(t, checks, "store-integrity"); c.Status != StatusPass {
		t.Fatalf("store-integrity is %s against a store it may not modify: %s", c.Status, c.Detail)
	}
	if _, err := os.Stat(cfg.TxDir()); err == nil {
		t.Error("doctor created the transaction directory; it must be read-only")
	}
}
