#!/usr/bin/env python3
"""One-off: record which game each inherited project decompiles (data/identity.json).

Inherited projects keep their place no matter what; detection only decides which of them
share a game. A detected title that disagrees with the project's own name is not trusted,
and that project keeps a slot of its own.
"""
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch  # noqa: E402
import identify  # noqa: E402

OUT = fetch.DATA / "identity.json"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def agrees(hit, name):
    titles = [hit["title"], *hit.get("aka", [])]
    a = norm(name)
    return any(norm(t)[:8] in a or a[:8] in norm(t) for t in titles if norm(t))


def one(p):
    rec = fetch.load_report(p["id"]) if hasattr(fetch, "load_report") else json.loads((fetch.REPORTS / f"{p['id']}.json").read_text())
    meta = rec.get("meta", {})
    if meta.get("missing"):
        return None
    repo = (p.get("source") or {}).get("repo") or f"{p['owner']}/{p['repo']}"
    vids = [v["id"] for v in rec.get("versions", [])]
    hit = identify.identify(repo, meta.get("default_branch", "main"), p.get("platform"), vids)
    key = f"{p['owner']}/{p['repo']}".lower()
    if hit and agrees(hit, p["name"]):
        return key, {"platform": hit["platform"], "game": hit["key"], "title": hit["title"], "by": hit["by"], "aliases": hit["aliases"]}
    return key, {"platform": p.get("platform"), "game": None, "title": p["name"], "by": "unidentified" if not hit else f"disagrees:{hit['title']}"}


def main():
    with ThreadPoolExecutor(6) as pool:
        result = dict(r for r in pool.map(one, fetch.all_projects()) if r)
    OUT.write_text(json.dumps(dict(sorted(result.items())), indent=1) + "\n")
    ok = sum(1 for v in result.values() if v["game"])
    print(f"{ok} identified, {len(result) - ok} keep their own slot")


if __name__ == "__main__":
    main()
