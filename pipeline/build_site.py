"""Assemble the static site: page assets, the engine's Python sources and the exported data.

    python -m pipeline.build_site --out _site   (run after pipeline.refresh has written _site/data)
"""
import argparse
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "site").iterdir():
        shutil.copy2(f, out / f.name)
    eng = out / "engine"
    shutil.rmtree(eng, ignore_errors=True)
    eng.mkdir()
    files = sorted(p.name for p in (ROOT / "engine").glob("*.py"))
    for name in files:
        shutil.copy2(ROOT / "engine" / name, eng / name)
    (eng / "manifest.json").write_text(json.dumps(files))
    (out / ".nojekyll").touch()

    meta_path = out / "data" / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"{meta_path} missing: run `python -m pipeline.refresh --out {out / 'data'}` first")
    meta = json.loads(meta_path.read_text())
    repo = os.environ.get("GITHUB_REPOSITORY")
    meta["repo"] = f"https://github.com/{repo}" if repo else meta.get("repo")
    meta_path.write_text(json.dumps(meta, indent=1))
    print(f"site assembled in {out}: {len(files)} engine files")


if __name__ == "__main__":
    main()
