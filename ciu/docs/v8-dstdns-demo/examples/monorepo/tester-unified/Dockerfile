# examples/monorepo/tester-unified/Dockerfile — the ONE shared tester image of the monorepo
# (vbpub's real one is vbpub/tester-unified/Dockerfile). Placeholder for the fixture: the root's
# ephemeral environment references the tag the estate's build step produces (tester-unified:local),
# and appy/tester builds its own tag from this directory through build.context (S6.2).
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest
