package journal

import (
	"os"
	"path/filepath"
	"testing"
)

// Secrets registered after construction must scrub from that point on. The
// CLI registers a profile's credentials before the first Emit, but a
// long-lived daemon learns them when a profile is loaded.
func TestAddSecretsScrubsSubsequentRecords(t *testing.T) {
	const secret = "late-registered-token-value"
	j, dir := newTestJournal(t)

	j.AddSecrets(secret)
	if err := j.Emit(Record{
		OperationID: "op-1", Kind: "stage", Outcome: OutcomeOK,
		Message: "using " + secret,
	}); err != nil {
		t.Fatal(err)
	}

	assertNotInJournal(t, dir, secret)
	assertInJournal(t, dir, Redacted)
}

func TestCloseIsIdempotent(t *testing.T) {
	j, _ := newTestJournal(t)
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}
	// The store defers a Close and the CLI calls one explicitly; a double
	// close must not be an error either caller has to think about.
	if err := j.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
}

// A record with no operation id is a stream event, not part of an
// operation's durable history, and must not create a stray record file.
func TestRecordWithoutAnOperationIdWritesNoOperationFile(t *testing.T) {
	j, dir := newTestJournal(t)
	if err := j.Emit(Record{Kind: "daemon", Outcome: OutcomeOK, Message: "started"}); err != nil {
		t.Fatal(err)
	}

	ops, err := ListOperations(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(ops) != 0 {
		t.Fatalf("ListOperations = %v, want none", ops)
	}
	events, err := ReadEvents(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		t.Fatalf("the event was not written to the stream: %v", events)
	}
}

func TestListOperationsIgnoresNonRecords(t *testing.T) {
	j, dir := newTestJournal(t)
	if err := j.Emit(Record{OperationID: "op-1", Kind: "promote", Outcome: OutcomeOK}); err != nil {
		t.Fatal(err)
	}
	opsDir := filepath.Join(dir, OperationsDir)
	if err := os.WriteFile(filepath.Join(opsDir, "notes.txt"), []byte("hi"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(opsDir, "subdir"), 0o755); err != nil {
		t.Fatal(err)
	}

	ops, err := ListOperations(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(ops) != 1 || ops[0] != "op-1" {
		t.Fatalf("ListOperations = %v, want [op-1]", ops)
	}
}

func TestReadersOnAnEmptyJournalDirectory(t *testing.T) {
	dir := t.TempDir()
	// Reading a journal that was never written must be an empty answer, not
	// an error: `srdm journal list` on a fresh node is a normal thing to do.
	events, err := ReadEvents(dir)
	if err != nil || len(events) != 0 {
		t.Fatalf("ReadEvents on an empty dir = %v, %v", events, err)
	}
	ops, err := ListOperations(dir)
	if err != nil || len(ops) != 0 {
		t.Fatalf("ListOperations on an empty dir = %v, %v", ops, err)
	}
}

func TestNewRefusesAnUnusableDirectory(t *testing.T) {
	dir := t.TempDir()
	blocker := filepath.Join(dir, "journal")
	if err := os.WriteFile(blocker, []byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := New(blocker); err == nil {
		t.Fatal("a journal opened over a regular file")
	}
}
