#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# set_approval_gate.sh — turn on the "approved by a second person" gate.
#
# GitHub forbids approving your own pull request, so a single-maintainer
# repository cannot use required_approving_review_count > 0 without deadlocking
# every PR. This script adds a second collaborator and only then raises the
# approval requirement, in that order, so `main` is never frozen.
#
#   ./scripts/set_approval_gate.sh --check
#       Report the current gate and whether a second collaborator exists.
#
#   ./scripts/set_approval_gate.sh --reviewer <github-username>
#       Add <github-username> as a write collaborator, then set
#       required_approving_review_count=1 and require_last_push_approval=true.
#
#   ./scripts/set_approval_gate.sh --disable
#       Return to 0 approvals (single-maintainer mode).
#
# Requires: curl, python3, and a GitHub token with `repo` scope in the macOS
# keychain (the same credential `git push` uses).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

OWNER="DipeshPansuriya"
REPO="MLX_Model_App"
RULESET_ID="28005254"
API="https://api.github.com/repos/$OWNER/$REPO"

MODE="${1:---check}"
REVIEWER="${2:-}"

token() {
    printf 'protocol=https\nhost=github.com\n\n' \
        | git credential-osxkeychain get 2>/dev/null \
        | sed -n 's/^password=//p'
}

TOKEN="$(token)"
if [ -z "$TOKEN" ]; then
    echo "No GitHub token in the keychain. Run 'git push' once to store one." >&2
    exit 2
fi

gh_api() {
    local method="$1" path="$2" data="${3:-}"
    if [ -n "$data" ]; then
        curl -sS --max-time 40 -X "$method" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/vnd.github+json" \
            -H "Content-Type: application/json" \
            -d "$data" "$API$path"
    else
        curl -sS --max-time 40 -X "$method" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/vnd.github+json" \
            "$API$path"
    fi
}

current_gate() {
    gh_api GET "/rulesets/$RULESET_ID" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for r in d["rules"]:
    if r["type"] == "pull_request":
        p = r["parameters"]
        print(p["required_approving_review_count"],
              p["require_code_owner_review"],
              p["require_last_push_approval"])
        break
'
}

collaborators() {
    gh_api GET "/collaborators?per_page=100" | python3 -c '
import json, sys
for c in json.load(sys.stdin):
    perms = c["permissions"]
    if perms.get("push") or perms.get("admin") or perms.get("maintain"):
        print(c["login"])
'
}

case "$MODE" in
    --check)
        read -r approvals co_review last_push <<<"$(current_gate)"
        echo "ruleset $RULESET_ID"
        echo "  required_approving_review_count : $approvals"
        echo "  require_code_owner_review       : $co_review"
        echo "  require_last_push_approval      : $last_push"
        echo
        echo "write collaborators:"
        collaborators | sed 's/^/  - /'
        count=$(collaborators | wc -l | tr -d ' ')
        echo
        if [ "$count" -lt 2 ] && [ "$approvals" != "0" ]; then
            echo "WARNING: $approvals approval(s) required but only $count collaborator(s)."
            echo "         Every PR will be unmergeable until a second collaborator approves."
        elif [ "$count" -lt 2 ]; then
            echo "Single-maintainer mode: 0 approvals, PR and checks still required."
            echo "Run with --reviewer <username> to enable the approval gate."
        else
            echo "Approval gate: $approvals approval(s) from $count collaborator(s)."
        fi
        ;;

    --reviewer)
        if [ -z "$REVIEWER" ]; then
            echo "usage: $0 --reviewer <github-username>" >&2
            exit 2
        fi

        echo "1/3 adding $REVIEWER as a write collaborator…"
        gh_api PUT "/collaborators/$REVIEWER" '{"permission":"write"}' >/dev/null

        echo "2/3 confirming the invitation is pending or accepted…"
        state=$(gh_api GET "/invitations?per_page=100" | python3 -c "
import json, sys
login = sys.argv[1].lower()
for i in json.load(sys.stdin):
    if i['invitee']['login'].lower() == login:
        print('pending')
        break
else:
    print('accepted-or-existing')
" "$REVIEWER")
        echo "    $state"

        echo "3/3 raising the approval requirement to 1…"
        body=$(python3 - "$RULESET_ID" <<'PY'
import json
print(json.dumps({
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": True,
                "required_review_thread_resolution": True,
                "allowed_merge_methods": ["squash", "merge"],
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": "Secret scan"},
                    {"context": "Security audit"},
                    {"context": "Lint and format"},
                    {"context": "Syntax gates"},
                    {"context": "Documentation links"},
                    {"context": "Hook installer"},
                    {"context": "Automated review"},
                ],
            },
        },
    ]
}))
PY
)
        gh_api PUT "/rulesets/$RULESET_ID" "$body" >/dev/null

        echo
        "$0" --check
        echo
        echo "Done. $REVIEWER must accept the invitation, then approve each PR."
        ;;

    --disable)
        echo "returning to single-maintainer mode (0 approvals)…"
        body=$(python3 <<'PY'
import json
print(json.dumps({
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": True,
                "allowed_merge_methods": ["squash", "merge"],
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": "Secret scan"},
                    {"context": "Security audit"},
                    {"context": "Lint and format"},
                    {"context": "Syntax gates"},
                    {"context": "Documentation links"},
                    {"context": "Hook installer"},
                    {"context": "Automated review"},
                ],
            },
        },
    ]
}))
PY
)
        gh_api PUT "/rulesets/$RULESET_ID" "$body" >/dev/null
        "$0" --check
        ;;

    *)
        echo "usage: $0 [--check | --reviewer <username> | --disable]" >&2
        exit 2
        ;;
esac
