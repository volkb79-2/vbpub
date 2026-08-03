package journal

import (
	"encoding/binary"
	"strings"
	"testing"
)

func TestJournaldEncodesFieldsInTheNativeProtocol(t *testing.T) {
	sink := journaldSink{path: "/nonexistent"}
	got := string(sink.encode(Record{
		SchemaVersion: SchemaVersion,
		OperationID:   "op-1",
		Kind:          "promote",
		Phase:         "classify",
		Outcome:       OutcomeOK,
		Message:       "all paths classified",
		Fields:        map[string]string{"release id": "rel-1"},
	}, newScrubber()))

	for _, want := range []string{
		"PRIORITY=6\n",
		"SYSLOG_IDENTIFIER=srdm\n",
		"SRDM_OPERATION_ID=op-1\n",
		"SRDM_PHASE=classify\n",
		"SRDM_OUTCOME=ok\n",
		// A profile-supplied key is normalized into journald's namespace and
		// prefixed, so it can never collide with MESSAGE or a _-reserved
		// field.
		"SRDM_F_RELEASE_ID=rel-1\n",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("encoded record is missing %q:\n%s", want, got)
		}
	}
	if !strings.Contains(got, "MESSAGE=promote classify ok: all paths classified\n") {
		t.Errorf("MESSAGE is not the human-readable summary:\n%s", got)
	}
}

func TestJournaldPrioritiesTrackTheOutcome(t *testing.T) {
	sink := journaldSink{path: "/nonexistent"}
	for outcome, want := range map[Outcome]string{
		OutcomeOK:      "PRIORITY=6\n",
		OutcomeStarted: "PRIORITY=6\n",
		OutcomeRefused: "PRIORITY=4\n",
		OutcomeFailed:  "PRIORITY=3\n",
	} {
		got := string(sink.encode(Record{Kind: "promote", Outcome: outcome}, newScrubber()))
		if !strings.Contains(got, want) {
			t.Errorf("outcome %q encoded without %q", outcome, strings.TrimSpace(want))
		}
	}
}

// A value containing a newline must use the length-prefixed binary form.
// In the plain form the newline would terminate the field, and the rest of
// the value would be parsed as new fields — which is how a multi-line error
// message becomes a forged journal entry.
func TestJournaldUsesTheBinaryFormForMultilineValues(t *testing.T) {
	sink := journaldSink{path: "/nonexistent"}
	value := "line one\nPRIORITY=0"
	got := sink.encode(Record{Kind: "promote", Outcome: OutcomeFailed, Error: value}, newScrubber())

	marker := []byte("SRDM_ERROR\n")
	idx := indexOf(got, marker)
	if idx < 0 {
		t.Fatalf("SRDM_ERROR was not written in the binary form:\n%s", got)
	}
	start := idx + len(marker)
	if len(got) < start+8 {
		t.Fatal("the encoded record is too short to carry a length prefix")
	}
	size := binary.LittleEndian.Uint64(got[start : start+8])
	if int(size) != len(value) {
		t.Fatalf("length prefix is %d, want %d", size, len(value))
	}
	// The injected "PRIORITY=0" must sit inside the counted payload, not as
	// a field of its own.
	if strings.Contains(string(got[:idx]), "PRIORITY=0") {
		t.Fatal("a newline in a value produced a forged field")
	}
}

func TestNormalizeFieldName(t *testing.T) {
	cases := map[string]string{
		"release_id":  "RELEASE_ID",
		"release id":  "RELEASE_ID",
		"release-id":  "RELEASE_ID",
		"content.sha": "CONTENT_SHA",
		"":            "UNNAMED",
		"1st":         "F1ST",
		"héllo":       "H_LLO",
	}
	for in, want := range cases {
		if got := normalizeFieldName(in); got != want {
			t.Errorf("normalizeFieldName(%q) = %q, want %q", in, got, want)
		}
	}
}

// A journald sink pointed at a socket that is not there must be a no-op:
// the durable records are the ones srdm reasons from, and a node without a
// running journald still has to work.
func TestJournaldSendIsBestEffort(t *testing.T) {
	journaldSink{path: "/nonexistent/socket"}.send(Record{Kind: "promote"}, newScrubber())
	journaldSink{path: ""}.send(Record{Kind: "promote"}, newScrubber())
}

func indexOf(hay, needle []byte) int {
	return strings.Index(string(hay), string(needle))
}
