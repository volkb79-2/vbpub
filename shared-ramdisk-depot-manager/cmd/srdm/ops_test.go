package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/config"
	"srdm/internal/journal"
)

// THE WINGS NODE TOKEN IS A SECRET (D-032), exactly as a profile's own
// Credentials are. newOpEnv reads it from Wings' config.yml to build the
// power driver and must register it with the journal's scrubber before
// anything else can possibly emit a record naming it — this is the
// equivalent of internal/journal's own
// TestRegisteredSecretsNeverReachAnySink / TestAddSecretsScrubsSubsequentRecords,
// exercised against the actual construction path an operator's CLI
// invocation runs, not a hand-built journal.Option.
func TestNewOpEnvRegistersTheWingsTokenWithTheJournal(t *testing.T) {
	const token = "wings-node-token-should-never-be-logged"

	dir := t.TempDir()
	wingsConfig := filepath.Join(dir, "wings-config.yml")
	if err := os.WriteFile(wingsConfig, []byte("debug: false\ntoken: "+token+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	// Never dialed by this test: NewWingsDriver only reads the config file
	// and builds a client at construction time, it does not probe the
	// network — so an unreachable address is fine here.
	cfg.Wings.APIURL = "https://127.0.0.1:1"
	cfg.Wings.ConfigPath = wingsConfig

	env, err := newOpEnv(cfg, "", false)
	if err != nil {
		t.Fatalf("newOpEnv: %v", err)
	}
	defer env.close()

	// Force the token into a record the way a bug — not the design — might:
	// a message or field that quotes it. The scrubber, not the caller's
	// discipline, is what must stop it landing on disk.
	if err := env.jnl.Emit(journal.Record{
		OperationID: "op-1", Kind: "test", Outcome: journal.OutcomeOK,
		Message: "connected using " + token,
		Fields:  map[string]string{"detail": "bearer=" + token, "release_id": "rel-1"},
	}); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(filepath.Join(cfg.JournalDir(), journal.EventsFile))
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	if strings.Contains(body, token) {
		t.Fatalf("the wings token reached the journal:\n%s", body)
	}
	// The test must be able to fail: a control value that is NOT the
	// secret has to survive, and the redaction marker has to appear, or an
	// empty/broken journal would pass this vacuously.
	if !strings.Contains(body, "rel-1") {
		t.Error("a non-secret control value did not survive scrubbing")
	}
	if !strings.Contains(body, journal.Redacted) {
		t.Error("the scrubbed record carries no redaction marker")
	}
}

// Unconfigured Wings API settings must not stop every OTHER verb from
// working — only `update` itself needs the power driver, and an operator
// who has not set --wings-api-url yet is not blocked from running
// activate, attach, status and so on.
func TestNewOpEnvToleratesAnUnconfiguredWingsAPI(t *testing.T) {
	dir := t.TempDir()
	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	// cfg.Wings.APIURL left empty on purpose.

	env, err := newOpEnv(cfg, "", false)
	if err != nil {
		t.Fatalf("newOpEnv failed with no wings API configured: %v", err)
	}
	env.close()
}
