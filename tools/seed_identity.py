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


ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}  # not "v"/"x": V-Force, Mega Man X
STOP = {"the", "a", "an", "of", "and", "to", "in", "for", "on"}


def _ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def tokens(s):
    words = re.findall(r"[a-z0-9]+", _ascii(s).lower().replace("&", " and "))
    return [ROMAN.get(w, w) for w in words if w not in STOP]


def comparable(s):
    """Mostly Latin script: どうぶつの森 e+ can't be compared with its romanisation."""
    letters = [c for c in s if c.isalnum()]
    return bool(letters) and sum(c.isascii() for c in _ascii(s) if c.isalnum()) >= 0.6 * len(letters)


def variants(s):
    """Full title, and its main title and subtitle: 'Zelda: The Wind Waker' -> full, 'Zelda', 'The Wind Waker'."""
    s = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", s).strip()
    out = [s]
    m = re.split(r"\s*:\s+|\s+-\s+|\s*:\s*(?=[A-Z])", s, maxsplit=1)
    if len(m) == 2:
        out += m
    return [set(tokens(v)) for v in out if tokens(v)]


def _same(a, b):
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


def similarity(a, b):
    """Token-set (Jaccard) similarity, tolerant of plurals/spelling (Microgames / Microgame$)."""
    if not a or not b:
        return 0.0
    hits = sum(1 for x in a if any(_same(x, y) for y in b))
    return hits / (len(a) + len(b) - hits)


def numbers(s):
    """Sequel numbers. '64' is the N64 naming habit (Gex 64, Dr. Mario 64), not a sequel."""
    return {t for t in tokens(re.sub(r"\s*[(\[][^)\]]*[)\]]", "", s)) if t.isdigit() and t != "64"}


STRONG = ("sha1", "disc", "serial", "title", "set", "tid")


def agrees(hit, name):
    """The detected game is the project's game.

    Titles are compared as token sets after removing bracketed notes ("(Fork: AI)"), also main title
    against subtitle ("Wind Waker" = "The Legend of Zelda - The Wind Waker"). A hit from a strong
    identifier (ROM hash, disc ID, serial, title ID, MAME set) is trusted unless the names clearly
    contradict: nothing in common, or a different sequel number ("Pikmin" vs "Pikmin 2"). Weaker
    hits need the names to match. Names that can't be compared (non-Latin script) trust strong hits."""
    titles = [hit["title"], *hit.get("aka", [])]
    if not comparable(name):
        return hit.get("by") in STRONG
    best, numbers_ok = 0.0, False
    for t in titles:
        if not comparable(t):
            continue
        if numbers(t) == numbers(name):
            numbers_ok = True
            for a in variants(name):
                for b in variants(t):
                    best = max(best, similarity(a, b))
    if hit.get("by") in STRONG:
        return numbers_ok and best > 0
    return numbers_ok and best >= 0.6


def one(p):
    rec = fetch.load_report(p["id"]) if hasattr(fetch, "load_report") else json.loads((fetch.REPORTS / f"{p['id']}.json").read_text())
    meta = rec.get("meta", {})
    if meta.get("missing"):
        return None
    repo = (p.get("source") or {}).get("repo") or f"{p['owner']}/{p['repo']}"
    vids = [v["id"] for v in rec.get("versions", [])]
    hit = identify.identify(repo, meta.get("default_branch", "main"), p.get("platform"), vids)
    key = f"{p['owner']}/{p['repo']}".lower()
    if hit and not agrees(hit, p["name"]):
        # The repo targets several games' builds (Silent Hill 2 and 3): the one its name says.
        other = next((g for g in hit.get("games", [])[1:] if agrees({**hit, **g}, p["name"])), None)
        if other:
            hit = {**hit, **other, "aliases": sorted(g["key"] for g in hit["games"] if g["key"] != other["key"])}
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
