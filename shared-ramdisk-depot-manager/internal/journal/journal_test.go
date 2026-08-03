package journal

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var testTime = time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)

func newTestJournal(t *testing.T, opts ...Option) (*Journal, string) {
	t.Helper()
	dir := t.TempDir()
	opts = append([]Option{
		WithClock(func() time.Time { return testTime }),
		// The journald socket is a property of the host; a unit test that
		// opens one is testing the host, not the journal.
		WithJournaldSocket(""),
	}, opts...)
	j, err := New(dir, opts...)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = j.Close() })
	return j, dir
}

func TestEmitWritesBothDurableSinks(t *testing.T) {
	j, dir := newTestJournal(t)

	if err := j.Emit(Record{
		OperationID: "op-1", Kind: "promote", Phase: "classify",
		Outcome: OutcomeStarted, Fields: map[string]string{"release_id": "rel-1"},
	}); err != nil {
		t.Fatal(err)
	}
	if err := j.Emit(Record{
		OperationID: "op-1", Kind: "promote", Phase: "classify", Outcome: OutcomeOK,
	}); err != nil {
		t.Fatal(err)
	}

	events, err := ReadEvents(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 {
		t.Fatalf("events stream has %d records, want 2", len(events))
	}
	if events[0].SchemaVersion != SchemaVersion {
		t.Errorf("record carries schema_version %d", events[0].SchemaVersion)
	}
	if !events[0].Time.Equal(testTime) {
		t.Errorf("record time is %v, want the injected clock's value", events[0].Time)
	}

	op, err := LoadOperation(dir, "op-1")
	if err != nil {
		t.Fatal(err)
	}
	if len(op.Events) != 2 {
		t.Fatalf("operation record has %d events, want 2", len(op.Events))
	}
	if op.Outcome != OutcomeOK {
		t.Errorf("operation outcome is %q, want the latest record's", op.Outcome)
	}
	if op.Kind != "promote" {
		t.Errorf("operation kind is %q", op.Kind)
	}

	ids, err := ListOperations(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 || ids[0] != "op-1" {
		t.Fatalf("ListOperations = %v", ids)
	}
}

// --- O5: no credentials, ever ---------------------------------------------

func TestRegisteredSecretsNeverReachAnySink(t *testing.T) {
	const secret = "steam-token-abcdef123456"
	j, dir := newTestJournal(t, WithSecrets(secret))

	if err := j.Emit(Record{
		OperationID: "op-1", Kind: "stage", Phase: "acquire", Outcome: OutcomeFailed,
		Message: "login failed for " + secret,
		Error:   "steamcmd rejected " + secret,
		Fields: map[string]string{
			"source":   "steam://" + secret + "@depot",
			"release":  "rel-1",
			"password": "should be dropped by key name alone",
		},
	}); err != nil {
		t.Fatal(err)
	}

	assertNotInJournal(t, dir, secret)
	assertNotInJournal(t, dir, "should be dropped by key name alone")

	// The test must be able to fail: a control value that is NOT a secret
	// has to survive, or an empty journal would pass this vacuously.
	assertInJournal(t, dir, "rel-1")
	assertInJournal(t, dir, Redacted)
}

// A credential-shaped field name is emptied whether or not its value was
// ever registered — the registration list is a backstop, not the only
// defense.
func TestCredentialShapedKeysAreDroppedWithoutRegistration(t *testing.T) {
	j, dir := newTestJournal(t)

	if err := j.Emit(Record{
		OperationID: "op-1", Kind: "stage", Outcome: OutcomeOK,
		Fields: map[string]string{
			"steam_password": "hunter2-never-registered",
			"api_key":        "k-never-registered",
			"AUTH_HEADER":    "Bearer never-registered",
			"Session":        "sess-never-registered",
			"release_id":     "rel-1",
		},
	}); err != nil {
		t.Fatal(err)
	}

	for _, leaked := range []string{
		"hunter2-never-registered", "k-never-registered",
		"Bearer never-registered", "sess-never-registered",
	} {
		assertNotInJournal(t, dir, leaked)
	}
	assertInJournal(t, dir, "rel-1")
}

func TestDeniedKeyMatching(t *testing.T) {
	denied := []string{
		"password", "steam_password", "TOKEN", "auth", "x-auth-header",
		"credential", "secret", "key", "api_key", "apikey", "login",
		"session", "cookie", "bearer", "PrivateKey", "identity",
	}
	for _, k := range denied {
		if !DeniedKey(k) {
			t.Errorf("DeniedKey(%q) = false; it is credential-shaped", k)
		}
	}
	// These must NOT be denied, or ordinary operational fields disappear and
	// the journal stops being able to explain anything.
	allowed := []string{
		"release_id", "profile", "channel", "phase", "operation_id",
		"content_digest", "provenance", "monkey", "class", "path",
	}
	for _, k := range allowed {
		if DeniedKey(k) {
			t.Errorf("DeniedKey(%q) = true; it is an ordinary field", k)
		}
	}
}

// A secret too short to substring-match safely is not registered at all —
// scrubbing "ab" would corrupt every record containing those letters. The
// profile loader refuses such credentials, so this bound never silently
// becomes a leak.
func TestShortSecretsAreNotRegistered(t *testing.T) {
	s := newScrubber()
	s.add("ab", "abcd")
	if len(s.secrets) != 1 || s.secrets[0] != "abcd" {
		t.Fatalf("scrubber registered %v; only values of at least %d bytes are usable",
			s.secrets, MinSecretLength)
	}
}

func TestLongerSecretsAreScrubbedFirst(t *testing.T) {
	// If "tok" were replaced before "tok-secret-value", the longer secret
	// would be left as "[redacted]-secret-value" — a partial leak.
	s := newScrubber()
	s.add("tok-", "tok-secret-value")
	got := s.string("using tok-secret-value now")
	if strings.Contains(got, "secret-value") {
		t.Fatalf("a longer secret was only partially scrubbed: %q", got)
	}
}

func TestScrubberHandlesJSONEscapedSecrets(t *testing.T) {
	s := newScrubber()
	s.add(`quote"inside`)
	// This is the shape the value takes once encoding/json has written it.
	got := s.bytes([]byte(`{"m":"quote\"inside"}`))
	if strings.Contains(string(got), "inside") {
		t.Fatalf("an escaped secret survived the byte pass: %s", got)
	}
}

// --- events stream resilience ---------------------------------------------

// A crash can leave a torn final line in an append-only stream. Everything
// written before it is still evidence and must remain readable.
func TestReadEventsToleratesATruncatedFinalLine(t *testing.T) {
	j, dir := newTestJournal(t)
	if err := j.Emit(Record{OperationID: "op-1", Kind: "promote", Outcome: OutcomeOK}); err != nil {
		t.Fatal(err)
	}
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}

	path := filepath.Join(dir, EventsFile)
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(body, []byte(`{"schema_ver`)...), 0o644); err != nil {
		t.Fatal(err)
	}

	events, err := ReadEvents(dir)
	if err != nil {
		t.Fatalf("a torn final line made the whole stream unreadable: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("recovered %d events, want 1", len(events))
	}
}

// A malformed line that is NOT the last one is corruption, not a torn
// write, and must not be quietly skipped.
func TestReadEventsRejectsCorruptionMidStream(t *testing.T) {
	j, dir := newTestJournal(t)
	if err := j.Emit(Record{OperationID: "op-1", Kind: "promote", Outcome: OutcomeOK}); err != nil {
		t.Fatal(err)
	}
	if err := j.Emit(Record{OperationID: "op-1", Kind: "promote", Outcome: OutcomeOK}); err != nil {
		t.Fatal(err)
	}
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}

	path := filepath.Join(dir, EventsFile)
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.SplitN(string(body), "\n", 2)
	if err := os.WriteFile(path, []byte("{not json}\n"+lines[1]), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadEvents(dir); err == nil {
		t.Fatal("corruption in the middle of the stream was accepted")
	}
}

// --- helpers --------------------------------------------------------------

// assertNotInJournal greps every byte the journal wrote, rather than
// inspecting parsed records: a leak through a field nobody thought to check
// is exactly the failure O5 is about.
func assertNotInJournal(t *testing.T, dir, needle string) {
	t.Helper()
	forEachJournalFile(t, dir, func(path string, body []byte) {
		if strings.Contains(string(body), needle) {
			t.Errorf("%s contains %q", path, needle)
		}
	})
}

func assertInJournal(t *testing.T, dir, needle string) {
	t.Helper()
	found := false
	forEachJournalFile(t, dir, func(_ string, body []byte) {
		if strings.Contains(string(body), needle) {
			found = true
		}
	})
	if !found {
		t.Errorf("no journal file contains %q; the assertion above proves nothing", needle)
	}
}

func forEachJournalFile(t *testing.T, dir string, fn func(path string, body []byte)) {
	t.Helper()
	err := filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		fn(path, body)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
