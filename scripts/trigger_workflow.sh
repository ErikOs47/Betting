#!/usr/bin/env bash
# Used by the Claude task: start the GitHub workflow through the API (GitHub's own schedule does not fire
# reliably), wait until it has saved a new state on branch "data", then hand over to publish_prep.sh.
# The token is never in this repo: the session's network proxy adds it to requests to api.github.com
# (a fine-grained token limited to this repository and "Actions: read and write").
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
log() { echo "[trigger] $*" >&2; }
SLUG="$(git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')"
BEFORE="$(git ls-remote origin data | cut -f1)"
CODE="$(curl -s -o /tmp/nbi-dispatch.out -w '%{http_code}' -X POST \
  -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
  "https://api.github.com/repos/$SLUG/actions/workflows/update.yml/dispatches" -d '{"ref":"main"}')"
if [ "$CODE" = 204 ]; then
  DISPATCH=started
  for i in $(seq 1 60); do   # up to 20 minutes; a run usually takes 2-6
    sleep 20
    [ "$(git ls-remote origin data | cut -f1)" != "$BEFORE" ] && { DISPATCH=saved; break; }
  done
  [ "$DISPATCH" = saved ] || log "workflow started but no new state after 20 minutes"
else
  DISPATCH="failed-$CODE"
  log "dispatch failed (HTTP $CODE): $(head -c 300 /tmp/nbi-dispatch.out)"
fi
NBI_DISPATCH="$DISPATCH" bash scripts/publish_prep.sh
