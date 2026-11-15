#!/usr/bin/env bash
# One unattended update run (used by the scheduled Claude task twice a day, or on demand):
#   1. restore the saved state (database + active models) from the git branch "data"
#   2. load new results, the injury report and odds; retrain if due; predict; settle paper bets
#   3. build the phone page (phone/dist/index.html + data.json)
#   4. save the new state back to the "data" branch (single commit, force-pushed: no history growth)
# Prints one JSON line with the outcome. Exit code != 0 means: do not publish.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DATA="$ROOT/data"
STATE_BRANCH="${NBI_STATE_BRANCH:-data}"
mkdir -p "$DATA/artifacts"
export NBI_DATA_DIR="$DATA" NBI_DATABASE_URL="sqlite:///$DATA/nbi.db"

log() { echo "[update] $*" >&2; }

# ---- python environment (cached in .venv between runs on the same machine)
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q -r backend/requirements.txt >&2

# ---- restore state
if git fetch -q --depth 1 origin "$STATE_BRANCH" 2>/dev/null; then
  TMP="$(mktemp -d)"
  git --work-tree="$TMP" checkout FETCH_HEAD -- . 2>/dev/null
  git reset -q 2>/dev/null || true   # keep the main index clean
  if [ -f "$TMP/state/nbi.db.gz" ]; then
    gunzip -c "$TMP/state/nbi.db.gz" > "$DATA/nbi.db"
    cp "$TMP"/state/artifacts/* "$DATA/artifacts/" 2>/dev/null || true
    for f in "$DATA"/artifacts/*.pkl.gz; do [ -e "$f" ] && gunzip -f "$f"; done
    log "state restored from branch $STATE_BRANCH ($(git log -1 --format=%cI FETCH_HEAD))"
  fi
  rm -rf "$TMP"
else
  log "no state branch yet: the first run downloads three seasons (several hours)"
fi

# ---- update + export
cd backend
../.venv/bin/python -m app.cli init-db >/dev/null
if [ ! -s "$DATA/nbi.db" ] || ! ../.venv/bin/python - <<'PY'
from app.tasks.scheduler import needs_bootstrap
raise SystemExit(1 if needs_bootstrap() else 0)
PY
then
  log "bootstrapping data"
  ../.venv/bin/python -c "from app.tasks.scheduler import bootstrap; bootstrap()" >&2
fi
RESULT="$(../.venv/bin/python -m app.cli scheduled-update --out ../phone/data.json 2>/tmp/nbi-update.log)"
cd ..
python3 phone/build.py >/dev/null

# ---- save state (only the active models)
SAVE="$(mktemp -d)"
mkdir -p "$SAVE/state/artifacts"
gzip -c "$DATA/nbi.db" > "$SAVE/state/nbi.db.gz"
[ -f "$DATA/artifacts/correlations.json" ] && cp "$DATA/artifacts/correlations.json" "$SAVE/state/artifacts/"
PYTHONPATH="$ROOT/backend" .venv/bin/python - "$SAVE/state/artifacts" <<'PY'
import gzip, shutil, sys
from pathlib import Path
from app.db import ModelVersion, session_scope
from app.config import get_settings
out = Path(sys.argv[1])
with session_scope() as db:
    for mv in db.query(ModelVersion).filter_by(active=True):
        src = Path(mv.artifact_path)
        src = src if src.exists() else get_settings().artifacts_dir / src.name
        with open(src, "rb") as f, gzip.open(out / (src.name + ".gz"), "wb", 6) as g:
            shutil.copyfileobj(f, g)
PY
REMOTE="$(git remote get-url origin)"
(
  cd "$SAVE"
  git init -q && git checkout -q -b "$STATE_BRANCH"
  git -c user.name="NBA update" -c user.email="noreply@anthropic.com" add -A
  git -c user.name="NBA update" -c user.email="noreply@anthropic.com" commit -qm "state $(date -u +%FT%TZ)"
  for i in 1 2 3 4; do git push -q -f "$REMOTE" "$STATE_BRANCH" 2>&1 && break; sleep $((i * 5)); done
) >&2
rm -rf "$SAVE"
echo "{\"update\":$RESULT,\"page\":\"phone/dist/index.html\",\"data\":\"phone/dist/data.json\"}"
