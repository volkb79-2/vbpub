"""Command-line entry points.

``cgprofile``, ``cgprofilectl`` and ``cgprofiled`` are the console scripts of the cgprofile wheel
(CGPROFILECTL-SPEC.md). ``cgprofile`` is the end-user client (``run``, ``mark``, ``report``, ``analyze``,
``skills``), ``cgprofilectl`` the socket client of the daemon; ``cgprofiled`` is still a
thin stub over ``legacy serve`` (collector child, daemon entry and report
subprocess) until package 3 replaces it and deletes ``legacy``.
"""
