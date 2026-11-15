"""Resumable bulk download of raw box scores (cached on disk). Run: python -m app.ingest.download_bref 2026 2027 2025"""
import sys
import time

from app.sources import bref
from app.sources.http import RateLimited, SourceError


def main(years: list[int]) -> None:
    for y in years:
        games = [g for g in bref.fetch_schedule(y) if g["has_box"]]
        todo = [g for g in games if not bref._cache("box", g["game_id"]).exists()]
        print(f"season {y}: {len(games)} played, {len(todo)} to download", flush=True)
        for i, g in enumerate(todo):
            try:
                bref._get(f"/boxscores/{g['game_id']}.html", bref._cache("box", g["game_id"]))
            except RateLimited as e:
                print("rate limited, stopping:", e, flush=True)
                return
            except SourceError as e:
                print("skip", g["game_id"], e, flush=True)
            if i % 50 == 0:
                print(f"  {y}: {i}/{len(todo)} {time.strftime('%H:%M:%S')}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
