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
    """The detected game is the project's game: one full title contains the other.
    Bracketed notes in the project's name, like "(Fork: AI)", are ignored."""
    a = norm(re.sub(r"\s*[(\[][^)\]]*[)\]]", "", name))
    for t in [hit["title"], *hit.get("aka", [])]:
        b = norm(t)
        if b and a and (a == b or (len(min(a, b, key=len)) >= 5 and (a in b or b in a))):
            return True
    return False


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
        return key, {"platform": hit["platform"], "game": hit["key"], "title": hit["title"], "by": hit["by"],
                     "aliases": hit["aliases"], "builds": hit.get("builds", [])}
    # Inherited projects only: fall back to the project's own listed name on its system's game list,
    # so a later submission of the same game lands in the same slot instead of making a duplicate.
    g = by_name(p.get("platform"), p["name"])
    if g:
        return key, {"platform": p["platform"], "game": g["key"], "title": g["title"], "by": "name", "aliases": [], "builds": []}
    return key, {"platform": p.get("platform"), "game": None, "title": p["name"], "by": "unidentified" if not hit else f"disagrees:{hit['title']}"}


def by_name(platform, name):
    """Exact match on the system's game list, ignoring punctuation, accents and subtitles after ':'."""
    path = identify.GAMES / f"{platform}.json"
    if not platform or not path.exists():
        return None
    games = json.loads(path.read_text())["games"]
    # Full name first; the part before a colon only when nothing matches the full name.
    for want in (norm(name), norm(name.split(":")[0]) if ":" in name else ""):
        if not want:
            continue
        for g in games:
            if any(norm(t) == want for t in [g["title"], *g.get("aka", [])]):
                return g
    return None


def main():
    with ThreadPoolExecutor(6) as pool:
        result = dict(r for r in pool.map(one, fetch.all_projects()) if r)
    OUT.write_text(json.dumps(dict(sorted(result.items())), indent=1) + "\n")
    ok = sum(1 for v in result.values() if v["game"])
    print(f"{ok} identified, {len(result) - ok} keep their own slot")


if __name__ == "__main__":
    main()
