# Upstream submission — prepared, NOT submitted

Order of operations when the user decides to submit:

1. Create the fork(s) on GitHub; set `FORK_REPO` in `../patchstack/stack.conf`.
2. Pelican first (faster merge cadence, owns panel+wings):
   ```bash
   cd ../build/wings-pelican
   git remote add fork git@github.com:OWNER/wings-pelican-fork.git
   git push fork cgroup/main
   gh pr create --repo pelican-dev/wings --base main --head OWNER:cgroup/main \
     --title "Add cgroup parent support: node-wide docker.cgroup_parent + optional per-server override" \
     --body-file ../../pr/pelican-wings-pr.md
   gh issue create --repo pelican-dev/wings --title "RFC: staged path for cgroup v2 resource guarantees" \
     --body-file ../../pr/rfc-issue.md
   ```
3. Pterodactyl in parallel: rebase onto `develop` first
   (`../patchstack/scripts/rebase.sh pterodactyl develop`), then the same
   `gh pr create` against `pterodactyl/wings` `develop` with
   `pterodactyl-wings-pr.md`.
4. Strip/adjust the PR-draft headers (the "Status: DRAFT" blocks) — they are
   for this repo, not for the PR body.
5. Decide whether to keep commit 0003 (integration tests) in the PR — it is
   self-contained and CI-friendly (create-only, driver-agnostic); include by
   default, drop on maintainer pushback.

Also decide per-project whether to keep the `Co-Authored-By: Claude` trailers
in the commits (`git rebase -i` + reword to strip them if not wanted).
