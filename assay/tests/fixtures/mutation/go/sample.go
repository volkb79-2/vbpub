// Fixture module for P11's Go-adapter mutation-engine oracle test (O2).
//
// Read directly by tests/test_adapters_go_generate_mutants.py as literal
// committed source. Content is deliberately ordinary, real Go -- proving
// GoAdapter.generate_mutants returns "UNSUPPORTED" unconditionally means
// proving it does NOT consult this text at all (A-042/A-114): there is no
// Go toolchain anywhere in this devcontainer to prove a generated mutant
// would even be valid Go syntax, so this adapter never attempts one.
package sample

func Compare(a, b int) bool {
	if a < b {
		return true
	}
	return false
}
