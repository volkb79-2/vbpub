package store

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"srdm/internal/journal"
	"srdm/internal/profile"
)

// --- O5: the journal has no secrets ---------------------------------------
//
// This runs a real promotion, refusal and recovery against a profile that
// carries credential-shaped fields, then greps every byte the journal wrote.
// Inspecting parsed records would only prove that the fields we thought to
// check are clean; the failure mode O5 is about is a credential arriving
// through a field nobody thought about.
func TestJournalNeverContainsProfileCredentials(t *testing.T) {
	const (
		steamPassword = "hunter2-do-not-log-this"
		steamToken    = "tok-9f3c1a-do-not-log-this"
		steamLogin    = "depot-robot-account"
	)

	dir := t.TempDir()
	cfg := testConfig(dir)

	p := newTestProfile()
	p.Credentials = map[string]string{
		"steam_password": steamPassword,
		"steam_token":    steamToken,
		"steam_login":    steamLogin,
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}

	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixedTime }),
		journal.WithJournaldSocket(""),
		// This is the wiring the CLI does: a profile's secrets are handed to
		// the journal before anything is written.
		journal.WithSecrets(p.Secrets()...),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer jnl.Close()

	st, err := Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}

	// A successful promotion whose provenance quotes a credential-bearing
	// source URL — the realistic way one leaks.
	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, contentA)
	if _, err := st.Promote(tx, p, PromoteOpts{
		ReleaseID: "rel-1",
		Channel:   "stable",
		Provenance: Provenance{
			Kind:   ProvenanceStaged,
			Source: "steam://" + steamLogin + ":" + steamPassword + "@depot/3017301",
		},
	}); err != nil {
		t.Fatal(err)
	}

	// A refusal, so the error path is exercised too: errors are where
	// unexpected strings usually end up.
	tx2, err := st.Begin("op-2")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx2.Root, map[string]string{
		"WS/Content/Paks/game.pak": "pak",
		"WS/" + steamToken:         "an unclassified path whose NAME is a credential",
	})
	if _, err := st.Promote(tx2, p, PromoteOpts{ReleaseID: "rel-2"}); err == nil {
		t.Fatal("the refusal did not happen; this test needs the error path")
	}

	if _, err := st.Recover("op-3"); err != nil {
		t.Fatal(err)
	}

	// The whole journal tree, byte for byte.
	leaks := map[string]string{
		"steam password": steamPassword,
		"steam token":    steamToken,
		"steam login":    steamLogin,
	}
	err = filepath.WalkDir(cfg.JournalDir(), func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for name, secret := range leaks {
			if strings.Contains(string(body), secret) {
				t.Errorf("%s leaked into %s", name, path)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	// The negative control. Without it, a journal that failed to write
	// anything at all would pass every assertion above.
	assertJournalContains(t, cfg.JournalDir(), "rel-1")
	assertJournalContains(t, cfg.JournalDir(), string(journal.OutcomeRefused))
	assertJournalContains(t, cfg.JournalDir(), journal.Redacted)
}

// A profile is never embedded in a record, so even an unregistered
// credential cannot ride along inside one.
func TestJournalRecordsCarryNoProfileStruct(t *testing.T) {
	dir := t.TempDir()
	cfg := testConfig(dir)
	jnl := newTestJournal(t, cfg)
	st, err := Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}

	p := newTestProfile()
	// Deliberately NOT registered with the journal: this asserts the
	// structural property, not the scrubber.
	p.Credentials = map[string]string{"steam_token": "unregistered-secret-value"}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}

	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)

	body, err := os.ReadFile(filepath.Join(cfg.JournalDir(), "operations", "op-1.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), "unregistered-secret-value") {
		t.Fatal("an unregistered credential reached the journal; a record must never " +
			"embed a profile")
	}
	if strings.Contains(string(body), "credentials") {
		t.Fatal("the operation record has a credentials field at all")
	}
}

func TestProfileWithCredentialsStillClassifies(t *testing.T) {
	// Guards against a scrubber that mangles ordinary strings: if a
	// credential value happened to appear inside a path, redaction must not
	// silently rewrite what got classified.
	p := newTestProfile()
	p.Credentials = map[string]string{"steam_token": "Engine"}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	class, err := p.Classify("Engine/libengine.so")
	if err != nil {
		t.Fatal(err)
	}
	if class.Name != "code" {
		t.Fatalf("classification changed because a credential shares its text: %q", class.Name)
	}
	var _ *profile.Profile = p
}

func assertJournalContains(t *testing.T, dir, needle string) {
	t.Helper()
	found := false
	err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if strings.Contains(string(body), needle) {
			found = true
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Errorf("the journal contains no %q; the leak assertions above prove nothing", needle)
	}
}
