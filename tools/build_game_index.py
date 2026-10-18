#!/usr/bin/env python3
"""Build data/games/<platform>.json: the list of real games per system, with the identifiers
used to recognise them (disc IDs, serials, ROM hashes, title IDs).

Sources (game lists from the preservation groups, as packaged by libretro-database, CC BY-SA 4.0):
  No-Intro: cartridge systems (N64, GBA, DS, 3DS)    Redump: disc systems (GC, Wii, PS1/2/PSP, Xbox, 360, DC, NAOMI GD)
  MAME: NAOMI cartridge sets.

Each game is one entry; regions and revisions of the same title share it ("one slot per game").
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "games"
CACHE = ROOT / "work" / "dats"
LIBRETRO = "https://raw.githubusercontent.com/libretro/libretro-database/master/"
TITLEDB = "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json"  # Switch, MIT
WIKIDATA = "https://query.wikidata.org/sparql"  # PC, CC0
WIKIDATA_PC = (
    "SELECT ?g ?gLabel WHERE { ?g wdt:P31/wdt:P279* wd:Q7889; wdt:P400 ?p. "
    "VALUES ?p { wd:Q1406 wd:Q170434 wd:Q11677 }. "
    'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }'
)
# Any English-named video game or game series: PC releases are often only tagged with consoles.
WIKIDATA_ANY = (
    'SELECT ?g ?gLabel WHERE { VALUES ?c { wd:Q7889 wd:Q7058673 } ?g wdt:P31 ?c; rdfs:label ?gLabel. '
    'FILTER(LANG(?gLabel)="en") }'
)
# Systems with no IDs a repo would contain: the game is named by the submitter and must be on the list.
NAME_ONLY = ("switch", "win32")
MAME_NAOMI = "https://raw.githubusercontent.com/mamedev/mame/master/src/mame/sega/naomi.cpp"

SOURCES = {
    "n64": ["metadat/no-intro/Nintendo - Nintendo 64.dat"],
    "gba": ["metadat/no-intro/Nintendo - Game Boy Advance.dat"],
    "nds": ["metadat/no-intro/Nintendo - Nintendo DS.dat", "metadat/no-intro/Nintendo - Nintendo DSi.dat"],
    "3ds": ["metadat/no-intro/Nintendo - Nintendo 3DS.dat", "metadat/no-intro/Nintendo - New Nintendo 3DS.dat"],
    "gc": ["metadat/redump/Nintendo - GameCube.dat", "dat/Nintendo - GameCube.dat"],
    "wii": ["metadat/redump/Nintendo - Wii.dat", "dat/Nintendo - Wii.dat"],
    "ps": ["metadat/redump/Sony - PlayStation.dat"],
    "ps2": ["metadat/redump/Sony - PlayStation 2.dat"],
    "psp": ["metadat/redump/Sony - PlayStation Portable.dat"],
    "xbox": ["metadat/redump/Microsoft - Xbox.dat"],
    "xbox360": ["metadat/redump/Microsoft - Xbox 360.dat"],
    "dc": ["metadat/redump/Sega - Dreamcast.dat"],
    "naomi": ["metadat/redump/Sega - Naomi.dat"],
}


def get(url, name):
    path = CACHE / re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "decomp.fun game index"})
        with urllib.request.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8", errors="replace")


def parse_dat(text):
    """clrmamepro DAT -> [{name, region, serial, sha1s}]"""
    games = []
    for block in re.findall(r"^game \($(.*?)^\)", text, re.S | re.M):
        name = re.search(r'^\s*name "(.*)"', block, re.M)
        if not name:
            continue
        serials = set(re.findall(r'serial "([^"]+)"', block))
        sha1s = {s.lower() for s in re.findall(r"sha1 ([0-9A-Fa-f]{40})", block)}
        region = re.search(r'^\s*region "(.*)"', block, re.M)
        games.append({"name": name.group(1), "region": region.group(1) if region else "", "serials": serials, "sha1s": sha1s})
    return games


TAG = re.compile(r"\s*\((?:[^()]*)\)|\s*\[[^\]]*\]")


def base_title(name):
    """'Super Smash Bros. Melee (USA) (Rev 2)' -> 'Super Smash Bros. Melee'"""
    t = TAG.sub("", name).strip()
    t = re.sub(r"\s+-\s+Disc\s+\d+$", "", t, flags=re.I)
    # No-Intro writes "Legend of Zelda, The - ..." ; restore the article.
    m = re.match(r"^(.*?), (The|A|An)( - .*)?$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}{m.group(3) or ''}"
    return t


def game_key(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def norm_serial(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def ids_for(platform, serial):
    """Identifiers a repo might contain, derived from a database serial."""
    s = serial.upper()
    out = {norm_serial(s)}
    if platform in ("gc", "wii"):
        m = re.match(r"^(?:DL-DOL|RVL)-([A-Z0-9]{4})", s)
        if m:
            out.add(m.group(1))
        if re.fullmatch(r"[A-Z0-9]{6}", s):  # GameTDB-style full ID, e.g. GALE01
            out.add(s[:4])
    if platform in ("n64", "gba", "nds", "3ds") and re.fullmatch(r"[A-Z0-9]{4}", s):
        out.add(s)
    if platform in ("xbox", "xbox360"):
        m = re.fullmatch(r"([A-Z]{2})-(\d+)", s)
        if m:  # title ID = two ASCII letters + 16-bit number, e.g. TT-2137 -> 54540859
            out.add(f"{ord(m.group(1)[0]):02X}{ord(m.group(1)[1]):02X}{int(m.group(2)):04X}")
    return out


def add(index, platform, title, region, ids, sha1s):
    key = game_key(title)
    if not key:
        return
    # Two lists can spell one game differently; a shared disc/cart ID means the same game.
    owners = index.setdefault("_id_owner", {})
    key = next((owners[i] for i in ids if i in owners), key)
    g = index.setdefault(key, {"title": title, "regions": set(), "ids": set(), "sha1": set()})
    for i in ids:
        owners.setdefault(i, key)
    if region:
        g["regions"].update(r.strip() for r in region.split(","))
    g["ids"].update(ids)
    g["sha1"].update(sha1s)


def naomi_carts(index):
    src = get(MAME_NAOMI, "mame_naomi.cpp")
    # GAME( 2000, mvsc2, naomi, naomi, naomi, naomi_state, empty_init, ROT0, "Capcom", "Marvel Vs. Capcom 2 ...", ...
    for m in re.finditer(r'\bGAME\(\s*\w+\s*,\s*(\w+)\s*,\s*\w+\s*,[^"\n]*"[^"]*"\s*,\s*"([^"]+)"', src):
        setname, full = m.group(1), m.group(2)
        title = base_title(full)
        title = re.sub(r"\s+(Rev\.?\s*\w+|Ver\.?\s*[\w.]+)$", "", title, flags=re.I)
        add(index, "naomi", title, "", {setname.upper()}, set())


# Nintendo product codes: [type][2-char game][region], e.g. GZLE / GZLJ / GZLP are one game.
CODE_PLATFORMS = {"gc": r"^[GR][A-Z0-9]{3}$", "wii": r"^[R S][A-Z0-9]{3}$", "n64": r"^N[A-Z0-9]{3}$",
                  "gba": r"^[AB][A-Z0-9]{3}$", "nds": r"^[ABCIKTVY][A-Z0-9]{3}$", "3ds": r"^[AB][A-Z0-9]{3}$"}
REGION_PREF = ("USA", "World", "Europe", "Australia", "UK")


def group_regions(platform, index):
    """Merge regional releases that share a product code's game part into one game."""
    pat = re.compile(CODE_PLATFORMS[platform].replace(" ", ""))
    groups, loose = {}, {}
    for key, g in index.items():
        codes = sorted(i[:3] for i in g["ids"] if pat.match(i))
        if codes:
            groups.setdefault(codes[0], []).append((key, g))
        else:
            loose[key] = g
    out = dict(loose)
    for members in groups.values():
        def pref(kg):
            regions = kg[1]["regions"]
            rank = min((REGION_PREF.index(r) for r in regions if r in REGION_PREF), default=len(REGION_PREF))
            return (rank, "demo" in kg[1]["title"].lower(), kg[1]["title"])
        members.sort(key=pref)
        key, main = members[0]
        merged = {"title": main["title"], "regions": set(), "ids": set(), "sha1": set(), "aka": set()}
        for k, g in members:
            merged["regions"] |= g["regions"]
            merged["ids"] |= g["ids"]
            merged["sha1"] |= g["sha1"]
            if g["title"] != main["title"]:
                merged["aka"].add(g["title"])
        out[key if key not in out else key + "-" + sorted(merged["ids"])[0].lower()] = merged
    return out


def switch_titles(index):
    data = json.loads(get(TITLEDB, "switch_US.en.json"))
    for t in data.values():
        name, tid = t.get("name"), (t.get("id") or "").upper()
        # Base games only (title IDs ending in 000); skip updates, DLC and demos.
        if name and tid.endswith("000") and not re.search(r"(?i)\bdemo\b", name):
            add(index, "switch", base_title(name), t.get("region") or "", {tid}, set())


def pc_titles(index):
    import csv
    import io

    for query, name in ((WIKIDATA_PC, "pc_wikidata.csv"), (WIKIDATA_ANY, "any_games_wikidata.csv")):
        path = CACHE / name
        if not path.exists():
            url = WIKIDATA + "?" + urllib.parse.urlencode({"query": query})
            req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "decomp.fun game index (https://decomp.fun)"})
            with urllib.request.urlopen(req, timeout=300) as r:
                path.write_bytes(r.read())
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))):
            label = row.get("gLabel", "")
            if label and not re.fullmatch(r"Q\d+", label):
                add(index, "win32", label, "", {row["g"].rsplit("/", 1)[-1]}, set())


def build(platform):
    index = {}
    if platform == "switch":
        switch_titles(index)
    if platform == "win32":
        pc_titles(index)
    for rel in SOURCES.get(platform, []):
        try:
            text = get(LIBRETRO + urllib.parse.quote(rel), rel)
        except OSError as ex:
            print(f"  {platform}: {rel}: {ex}", file=sys.stderr)
            continue
        for g in parse_dat(text):
            ids = set()
            for s in g["serials"]:
                for part in re.split(r"[,;]\s*", s):
                    if part:
                        ids |= ids_for(platform, part)
            add(index, platform, base_title(g["name"]), g["region"], ids, g["sha1s"])
    if platform == "naomi":
        naomi_carts(index)
    games = []
    index.pop("_id_owner", None)
    if platform in CODE_PLATFORMS:
        index = group_regions(platform, index)
    for key, g in sorted(index.items()):
        if platform in NAME_ONLY:
            games.append({"key": key, "title": g["title"]})
            continue
        entry = {"key": key, "title": g["title"], "regions": sorted(g["regions"]), "ids": sorted(g["ids"]), "sha1": sorted(g["sha1"])}
        if g.get("aka"):
            entry["aka"] = sorted(g["aka"])
        games.append(entry)
    return games


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for platform in [*SOURCES, *NAME_ONLY]:
        games = build(platform)
        (OUT / f"{platform}.json").write_text(json.dumps({"platform": platform, "games": games}, separators=(",", ":")))
        print(f"{platform}: {len(games)} games, {sum(len(g.get('ids', ())) for g in games)} ids, {sum(len(g.get('sha1', ())) for g in games)} hashes")


if __name__ == "__main__":
    main()
