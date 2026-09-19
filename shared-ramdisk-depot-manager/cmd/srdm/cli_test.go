package main

import (
	"bytes"
	"io"
	"os"
	"strings"
	"testing"
)

func TestTopLevelVersionFlag(t *testing.T) {
	original := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writer
	runErr := run([]string{"--version"})
	writer.Close()
	os.Stdout = original
	output, readErr := io.ReadAll(reader)
	reader.Close()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if runErr != nil {
		t.Fatalf("run(--version) error = %v", runErr)
	}
	if got, want := string(output), "srdm "+Version+"\n"; got != want {
		t.Fatalf("version output = %q, want %q", got, want)
	}
}

func TestLegacyVersionVerbRemainsBare(t *testing.T) {
	original := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writer
	runErr := run([]string{"version"})
	writer.Close()
	os.Stdout = original
	output, readErr := io.ReadAll(reader)
	reader.Close()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if runErr != nil {
		t.Fatalf("run(version) error = %v", runErr)
	}
	if got, want := string(output), Version+"\n"; got != want {
		t.Fatalf("legacy version output = %q, want %q", got, want)
	}
}

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
