#!/usr/bin/env bash
# Protect `main`: changes arrive by pull request, CI must pass on an
# up-to-date branch, history stays linear, and nobody (admins included) can
# force-push or delete it. No approving review is required, because a sole
# maintainer cannot approve their own pull request.
#
#     tools/ci/protect-main.sh OWNER/REPO
#
# Needs the gh CLI logged in as an admin of the repository. GitHub only
# allows this on a public repository, or a private one on a paid plan.
set -euo pipefail
repo=${1:?usage: tools/ci/protect-main.sh OWNER/REPO}

gh api -X PUT "repos/$repo/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [{"context": "tests"}, {"context": "content-guard"}]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
echo "main is protected on $repo"
