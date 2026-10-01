"""Exit code constants for cmru (S8 = CIU S10.3)."""

OK = 0
FAILURE = 1        # build / publish / upload / native version-writer error
CONFIG_ERROR = 2   # missing required field, unknown key, parse error
PREREQ_MISSING = 3 # unavailable registry metadata, required env var/tool absent
