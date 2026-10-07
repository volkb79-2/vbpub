"""Exit code constants for cmru (SPEC S8: CIU S10.3's 0-3 plus ``REFUSED = 4``)."""

OK = 0             # done, declined confirmation, or nothing to do
FAILURE = 1        # build / publish / upload / native version-writer error
CONFIG_ERROR = 2   # missing required field, unknown key, parse error
PREREQ_MISSING = 3 # unavailable registry metadata, required env var/tool absent
REFUSED = 4        # refused or blocked by policy/verification; nothing changed (CLI-16)
