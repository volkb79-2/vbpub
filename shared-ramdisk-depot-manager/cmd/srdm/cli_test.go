package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestFlagParserDiagnosticsStartWithHeadline(t *testing.T) {
	fs := newFlagSet("status")
	var output bytes.Buffer
	fs.SetOutput(&output)
	fs.Usage = func() { writeFlagUsage(&output, fs) }
	if err := parseFlagSet(fs, []string{"--not-a-status-option"}); err == nil {
		t.Fatal("invalid flag unexpectedly parsed")
	}
	if first := strings.Split(output.String(), "\n")[0]; first != cliHeadline() {
		t.Fatalf("first diagnostic line = %q, want %q", first, cliHeadline())
	}
	if !strings.Contains(output.String(), "srdm: flag provided but not defined") {
		t.Fatalf("parse diagnostic missing from output: %q", output.String())
	}
}
