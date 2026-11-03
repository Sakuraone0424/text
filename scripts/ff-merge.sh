#!/usr/bin/env bash
# OPTIONAL - not a required step (needs a computer that can run git). The normal GitHub merge works without it: the
# workflow recognises a merge commit that adds nothing the pull request run had not tested and reuses that result.
#
# Fast-forward a pull request branch into feat/60q-v2-spec: no merge commit, so the commit that lands is the very commit
# the pull request already tested, and the deployment (Railway "Wait for CI") reuses that result at once.
#
#   scripts/ff-merge.sh <branch>      e.g. scripts/ff-merge.sh claude/new-session-cqrb5o
#
# Refuses (and changes nothing) unless the branch is exactly the latest feat/60q-v2-spec plus new commits, i.e. already
# rebased. Run it only after the pull request's checks are green. Never forces anything.
set -euo pipefail
base="${BASE:-feat/60q-v2-spec}"
branch="${1:?usage: scripts/ff-merge.sh <branch>}"
git fetch --quiet origin "$base" "$branch"
if ! git merge-base --is-ancestor "origin/$base" "origin/$branch"; then
  echo "not a fast-forward: origin/$base has commits that origin/$branch does not contain." >&2
  echo "rebase the branch onto origin/$base, push it, wait for the new checks, then run this again." >&2
  exit 1
fi
sha="$(git rev-parse "origin/$branch")"
echo "fast-forwarding origin/$base to $sha"
git push origin "$sha:refs/heads/$base"      # a plain push: git itself refuses anything but a fast-forward
