package main

import (
	"flag"
	"fmt"
	"io"
	"os"
)

func cliHeadline() string {
	return fmt.Sprintf("SRDM %s — shared-ramdisk-depot-manager", Version)
}

// newFlagSet keeps every subcommand parser independently usable while making
// its help and parse failures identify the build that produced them.
func newFlagSet(name string) *flag.FlagSet {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	fs.Usage = func() {
		writeFlagUsage(fs.Output(), fs)
	}
	return fs
}

func writeFlagUsage(w io.Writer, fs *flag.FlagSet) {
	fmt.Fprintf(w, "%s\n\nUsage of %s:\n", cliHeadline(), fs.Name())
	fs.PrintDefaults()
}

type flagParseError struct {
	err error
}

func (e *flagParseError) Error() string { return e.err.Error() }

func (e *flagParseError) Unwrap() error { return e.err }

func parseFlagSet(fs *flag.FlagSet, args []string) error {
	// ContinueOnError writes the parse failure before returning it. Suppress
	// that write so the caller can emit one ordered diagnostic: headline,
	// usage, then the parse error. main recognizes the marker and therefore
	// does not prepend a second headline or repeat the error.
	output := fs.Output()
	fs.SetOutput(io.Discard)
	err := fs.Parse(args)
	fs.SetOutput(output)
	if err == nil {
		return nil
	}
	fs.Usage()
	fmt.Fprintf(output, "srdm: %v\n", err)
	return &flagParseError{err: err}
}
