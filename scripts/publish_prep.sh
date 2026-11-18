#!/usr/bin/env bash
# Used by the Claude task: take the page the GitHub workflow saved on the "data" branch.
# Only if that snapshot is older than 3 hours (workflow late or failed) run the update here instead
# (state cannot be saved from a Claude task, the page is still fresh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p phone/dist
git fetch -q --depth 1 origin data
git show FETCH_HEAD:phone/index.html > phone/dist/index.html
git show FETCH_HEAD:phone/data.json > phone/dist/data.json
AGE_MIN=$(python3 -c "import json,datetime as d;g=json.load(open('phone/dist/data.json'))['generatedAt'].rstrip('Z');print(int((d.datetime.now(d.timezone.utc).replace(tzinfo=None)-d.datetime.fromisoformat(g)).total_seconds()//60))")
if [ "$AGE_MIN" -gt 180 ]; then
  echo "[prep] snapshot is ${AGE_MIN} min old: running the update here" >&2
  bash scripts/scheduled_update.sh >/dev/null
  AGE_MIN=0; SRC=local
else
  SRC=workflow
fi
python3 -c "import json;d=json.load(open('phone/dist/data.json'));t=d['suggestions']['track']['props'];print(json.dumps({'dispatch':'${NBI_DISPATCH:-none}','source':'$SRC','ageMin':$AGE_MIN,'generatedAt':d['generatedAt'],'games':len(d['games']),'lastResult':d['data']['lastResult'],'tracked':t['n']}))"
