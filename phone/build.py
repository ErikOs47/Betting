"""Assemble the publishable phone page: dist/index.html (with nbmath.js inlined) + dist/data.json."""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build(data: Path | None = None) -> Path:
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    page = (HERE / "index.src.html").read_text(encoding="utf-8")
    page = page.replace("<script>/*NBMATH*/</script>", "<script>\n" + (HERE / "nbmath.js").read_text(encoding="utf-8") + "\n</script>")
    (dist / "index.html").write_text(page, encoding="utf-8")
    # local preview only: the skeleton claude.ai adds when publishing (charset, viewport, small reset)
    (HERE / "preview").mkdir(exist_ok=True)
    (HERE / "preview" / "index.html").write_text(
        '<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<style>:root{padding-block:env(safe-area-inset-top,0px) env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui}'
        'img{max-width:100%}[hidden]{display:none!important}</style></head><body>' + page + "</body></html>", encoding="utf-8")
    src = data or HERE / "data.json"
    if src.exists() and src.resolve() != (dist / "data.json").resolve():
        shutil.copyfile(src, dist / "data.json")
        shutil.copyfile(src, HERE / "preview" / "data.json")
    return dist


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
