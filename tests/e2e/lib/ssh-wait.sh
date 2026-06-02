#!/usr/bin/env bash
# Poll for SSH on 127.0.0.1:<port> to come up using the given key, bounded by
# the given timeout. Used by run.sh after VM boot, before any work over SSH.

set -euo pipefail

KEY="${1:?usage: ssh-wait.sh <key> <port> <timeout-secs>}"
PORT="${2:?missing port}"
DEADLINE_SECS="${3:?missing timeout}"

START=$(date +%s)
until ssh -i "$KEY" -p "$PORT" \
          -o BatchMode=yes \
          -o ConnectTimeout=5 \
          -o StrictHostKeyChecking=no \
          -o UserKnownHostsFile=/dev/null \
          ubuntu@127.0.0.1 true 2>/dev/null; do
    now=$(date +%s)
    if [ $((now - START)) -ge "$DEADLINE_SECS" ]; then
        echo "ERROR: SSH did not come up within ${DEADLINE_SECS}s" >&2
        exit 1
    fi
    sleep 3
done
echo "[ssh]    up after $(( $(date +%s) - START ))s"
