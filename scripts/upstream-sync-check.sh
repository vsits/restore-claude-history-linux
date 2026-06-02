#!/bin/bash
# Daily upstream-sync sweep.
#
# Fetches garrettmoss/restore-claude-history, compares to our main, and posts
# a comment to the internal tracking issue if new commits have appeared.
# Triggers no other action; Lead triages and decides what to port.
#
# Cron-mounted at ~/.claude/crons/restore-claude-builder/upstream-sync-check
# Logs to ~/.claude/rcb-upstream-sync.log

set -u
REPO_DIR="${RCB_REPO_DIR:-/home/manager/git_repos/restore-claude-history-linux}"
GH_REPO="cnighswonger/restore-claude-history-linux"
UPSTREAM_BRANCH="upstream/master"
STATE_FILE="$HOME/.claude/rcb-upstream-last-seen-sha"
LOG="$HOME/.claude/rcb-upstream-sync.log"
ISSUE_TITLE="Upstream sync — pending review from garrettmoss/restore-claude-history"

log() { echo "$(date -Is) $*" >> "$LOG"; }

cd "$REPO_DIR" || { log "ERROR: cannot cd to $REPO_DIR"; exit 1; }

git fetch upstream --quiet 2>>"$LOG" || { log "ERROR: git fetch upstream failed"; exit 1; }

UPSTREAM_HEAD=$(git rev-parse "$UPSTREAM_BRANCH" 2>/dev/null)
LAST_SEEN=$(cat "$STATE_FILE" 2>/dev/null || echo "")

if [ -z "$UPSTREAM_HEAD" ]; then
    log "ERROR: cannot resolve $UPSTREAM_BRANCH"
    exit 1
fi

if [ "$UPSTREAM_HEAD" = "$LAST_SEEN" ]; then
    log "no change (head $UPSTREAM_HEAD)"
    exit 0
fi

# Range to report: from last-seen (or our main if first run) to current upstream head.
RANGE_FROM="${LAST_SEEN:-origin/main}"
NEW_COMMITS=$(git log --reverse --format='- `%h` %s' "$RANGE_FROM..$UPSTREAM_HEAD" 2>/dev/null)
NEW_FILES=$(git log --name-only --pretty=format: "$RANGE_FROM..$UPSTREAM_HEAD" 2>/dev/null \
    | sort -u | grep -v '^$' | sed 's/^/- /')

if [ -z "$NEW_COMMITS" ]; then
    log "fetch advanced but no new commits in $RANGE_FROM..$UPSTREAM_HEAD"
    echo "$UPSTREAM_HEAD" > "$STATE_FILE"
    exit 0
fi

# Get a token via the team-lead bot identity (authorized to write internal
# tracking issues; allowlisted in AGENTS.md "Inbound issues").
TOKEN_SCRIPT="$HOME/.claude/github-apps/generate-token.sh"
if [ ! -x "$TOKEN_SCRIPT" ]; then
    log "ERROR: $TOKEN_SCRIPT not executable"
    exit 1
fi
TOKEN=$("$TOKEN_SCRIPT" team-lead 2>/dev/null)
if [ -z "$TOKEN" ]; then
    log "ERROR: generate-token.sh team-lead returned empty"
    exit 1
fi

# Locate the open tracking issue (by exact title), or create it.
ISSUE_NUM=$(GH_TOKEN=$TOKEN gh issue list --repo "$GH_REPO" \
    --state open --search "in:title \"$ISSUE_TITLE\"" \
    --json number,title --jq ".[] | select(.title == \"$ISSUE_TITLE\") | .number" \
    2>>"$LOG" | head -1)

BODY=$(cat <<EOF
## Upstream sync sweep — $(date -u +%Y-%m-%d)

Detected new commits on \`garrettmoss/restore-claude-history\` since our last sync.

**Range:** \`$RANGE_FROM\` → \`$UPSTREAM_HEAD\`

### New commits

$NEW_COMMITS

### Files touched

$NEW_FILES

### Triage instructions

For each commit, classify into one of:

- **apply** — bug fix or improvement in logic shared with Linux port (e.g. recovery loop, mtime/ACL handling, picker semantics). Open a PR cherry-picking with \`Upstream-SHA: <hash>\` in the trailer.
- **port** — applies in concept but needs translation (macOS-specific API replaced by our backend layer). Open a tracking issue with the design question.
- **skip** — macOS-specific (Time Machine, APFS local snapshots, Spotlight, \`tmutil\`, \`mount_apfs\`) or doc-only changes that don't apply to the Linux port's narrative.

After triage, comment on this issue with the classification per commit, then close the sweep. Sweep auto-files a new issue if more upstream commits land while this one is open — those go in a fresh sweep at the next cron tick, not as comments on this one.
EOF
)

if [ -z "$ISSUE_NUM" ]; then
    log "creating new tracking issue"
    ISSUE_URL=$(GH_TOKEN=$TOKEN gh issue create --repo "$GH_REPO" \
        --title "$ISSUE_TITLE" \
        --label "documentation" \
        --body "$BODY" 2>>"$LOG")
    log "created: $ISSUE_URL"
else
    log "commenting on existing issue #$ISSUE_NUM"
    GH_TOKEN=$TOKEN gh issue comment "$ISSUE_NUM" --repo "$GH_REPO" --body "$BODY" >>"$LOG" 2>&1
fi

echo "$UPSTREAM_HEAD" > "$STATE_FILE"
log "advanced state to $UPSTREAM_HEAD"
