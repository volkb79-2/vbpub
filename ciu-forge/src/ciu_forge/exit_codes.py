"""Exit code constants for ciu-forge (S8 = CIU S10.3)."""

OK = 0
FAILURE = 1        # build / publish / upload error
CONFIG_ERROR = 2   # missing required field, unknown key, parse error
PREREQ_MISSING = 3 # required env var absent, required delegated tool absent
