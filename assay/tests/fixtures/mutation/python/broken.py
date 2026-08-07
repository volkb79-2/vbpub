"""A deliberately unparseable Python fixture (O2's UNSUPPORTED half).

Read directly by tests/test_adapters_python_generate_mutants.py as literal
committed source -- a real `SyntaxError`, never a synthesized/inline string,
matching the discipline the module docstring for `sample.py` states.
"""

def broken(:
    return 1
