#!/usr/bin/env python3
"""Build data/games/<platform>.json: the list of real games per system, with the identifiers
used to recognise them (disc IDs, serials, ROM hashes, title IDs). Run monthly by game-index.yml.

Sources (SOURCES below): No-Intro (cartridge systems) and Redump (disc systems) lists as packaged by
libretro-database (CC BY-SA 4.0), plus DOS.dat and the MAME 2016 arcade list from the same place;
MAME's naomi.cpp for NAOMI cartridge sets; titledb for Switch (MIT); Wikidata for Windows (CC0).
Downloads are cached in work/dats/.

Each game is one entry; regions and revisions of the same title share it ("one slot per game").
"""

import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from identify import NAME_ONLY  # noqa: E402

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
# Of the NAME_ONLY systems (named by the submitter), these have no identifiers a repo would contain at
# all, so their lists carry titles only (Switch has title IDs; keep them).
IDLESS = ("win32", "dos")
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
    "saturn": ["metadat/redump/Sega - Saturn.dat", "dat/Sega - Saturn.dat"],
    "md": ["metadat/no-intro/Sega - Mega Drive - Genesis.dat"],
    "segacd": ["metadat/redump/Sega - Mega-CD - Sega CD.dat"],
    "32x": ["metadat/no-intro/Sega - 32X.dat"],
    "sms": ["metadat/no-intro/Sega - Master System - Mark III.dat"],
    "gg": ["metadat/no-intro/Sega - Game Gear.dat"],
    "nes": ["metadat/no-intro/Nintendo - Nintendo Entertainment System.dat"],
    "snes": ["metadat/no-intro/Nintendo - Super Nintendo Entertainment System.dat"],
    "gb": ["metadat/no-intro/Nintendo - Game Boy.dat"],
    "gbc": ["metadat/no-intro/Nintendo - Game Boy Color.dat"],
    "vb": ["metadat/no-intro/Nintendo - Virtual Boy.dat"],
    "wiiu": ["dat/Nintendo - Wii U.dat", "metadat/no-intro/Nintendo - Wii U (Digital).dat"],
    "ps3": ["metadat/redump/Sony - PlayStation 3.dat", "metadat/no-intro/Sony - PlayStation 3 (PSN).dat"],
    "vita": ["metadat/no-intro/Sony - PlayStation Vita.dat", "metadat/no-intro/Sony - PlayStation Vita (PSN).dat"],
    "atari2600": ["metadat/no-intro/Atari - 2600.dat"],
    "jaguar": ["metadat/no-intro/Atari - Jaguar.dat", "metadat/redump/Atari - Jaguar CD.dat"],
    "lynx": ["metadat/no-intro/Atari - Lynx.dat"],
    "pce": ["metadat/no-intro/NEC - PC Engine - TurboGrafx 16.dat", "metadat/no-intro/NEC - PC Engine SuperGrafx.dat",
            "metadat/redump/NEC - PC Engine CD - TurboGrafx-CD.dat"],
    "ngp": ["metadat/no-intro/SNK - Neo Geo Pocket.dat", "metadat/no-intro/SNK - Neo Geo Pocket Color.dat"],
    "neocd": ["metadat/redump/SNK - Neo Geo CD.dat"],
    "arcade": [],
}
MAME_XML = "metadat/mame/MAME 2016 XML (Arcade Only).xml"


def get(url, name):
    path = CACHE / re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "decomp.fun game index"})
        with urllib.request.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8", errors="replace")


def parse_dat(text):
    """clrmamepro DAT -> [{name, region, serials, sha1s, roms}]"""
    games = []
    for block in re.findall(r"^game \($(.*?)^\)", text, re.S | re.M):
        name = re.search(r'^\s*name "(.*)"', block, re.M)
        if not name:
            continue
        serials = set(re.findall(r'serial "([^"]+)"', block))
        sha1s = {s.lower() for s in re.findall(r"sha1 ([0-9A-Fa-f]{40})", block)}
        region = re.search(r'^\s*region "(.*)"', block, re.M)
        roms = re.findall(r'rom \(\s*name "([^"]*)"', block) + re.findall(r'^\s+name "([^"]*)"', block, re.M)[1:]
        games.append({"name": name.group(1), "region": region.group(1) if region else name_region(name.group(1)),
                      "serials": serials, "sha1s": sha1s, "roms": roms})
    return games


REGIONS = {"USA", "Europe", "Japan", "World", "Korea", "China", "Taiwan", "Hong Kong", "Asia", "Australia", "UK",
           "Germany", "France", "Spain", "Italy", "Netherlands", "Sweden", "Scandinavia", "Russia", "Brazil",
           "Canada", "Latin America", "Mexico", "Portugal", "Greece", "Poland", "Denmark", "Norway", "Finland",
           "Belgium", "Austria", "Switzerland", "India", "South Africa", "New Zealand", "Ireland", "Israel"}


def name_region(name):
    """'Game (USA, Europe) (Rev 1)' -> 'USA, Europe' (lists without a region field, e.g. GameTDB's)."""
    for group in re.findall(r"\(([^()]*)\)", name):
        parts = [r.strip() for r in group.split(",")]
        if parts and all(r in REGIONS for r in parts):
            return ", ".join(parts)
    return ""


# Not licensed retail games: homebrew, unlicensed carts, aftermarket releases, pirate copies, hacks.
UNLICENSED = re.compile(r"\((?:[^)]*\b)?(Homebrew|Unl|Aftermarket|Pirate|Hack|Hacker)\b[^)]*\)", re.I)
# GameTDB's lists (libretro dat/) also carry custom discs and tools, untagged; the redump list has the real games.
GAMETDB_JUNK = re.compile(
    r"(?i)\bcustom\b|\bhacks?\b|\bmod\b|\bhomebrew\b|project m\b|track pack|riivolution|translation|"
    r"\bloader\b|\binstaller\b|\bremaster(ed)?\b|\bnewer\b|\bfan (game|made)\b|\[[^\]]*\]")


NOT_RETAIL = re.compile(
    r"\((?:[^)]*\b)?(Proto|Prototype|Beta|Demo|Sample|Kiosk|Preview|Trial|DS Broadcast|Download Play)\b[^)]*\)", re.I)


def generic_serials(dat):
    """Placeholder serials shared by many unrelated titles (NTRJ, AGBJ, 0000: default header codes)."""
    titles = {}
    for g in dat:
        for s in g["serials"]:
            titles.setdefault(s, set()).add(game_key(base_title(g["name"])))
    return {s for s, t in titles.items() if len(t) >= 5 or re.fullmatch(r"0+|NTRJ|AGBJ", s)}


def licensed(g, rel):
    if any(UNLICENSED.search(n) for n in [g["name"], *g.get("roms", ())]):
        return False
    if rel.startswith("dat/") and ("GameCube" in rel or "Wii" in rel):
        # Untagged entries there are mostly homebrew and channels; real discs carry a region.
        return not GAMETDB_JUNK.search(g["name"]) and bool(g["region"])
    return True


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


STOPWORDS = {
    "the", "a", "an", "of", "and", "de", "la", "le", "les", "el", "los", "las", "der", "die", "das", "no", "to", "in",
    "for", "on", "ni", "wo", "ga", "game", "games", "edition", "version", "super", "new", "world", "adventure",
    "adventures", "party", "deluxe", "pro", "sports", "collection", "ultimate", "special", "series", "vol", "disc",
    "and", "with", "plus", "mini", "best", "club", "advance", "ds", "wii", "gamecube", "64", "3d", "dsi", "mega",
}
ASIAN = {"Japan", "Korea", "China", "Taiwan", "Hong Kong", "Asia"}


def title_words(title):
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()
            and not re.fullmatch(r"[ivxl]+", w)}


def titles_agree(ta, tb):
    """Two titles plausibly name one game: a shared significant word (prefix-equal for localised spellings,
    e.g. Princess / Princesas)."""
    wa, wb = set().union(*map(title_words, ta)), set().union(*map(title_words, tb))
    return any(a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5]) for a in wa for b in wb)


def _regions(g):
    return {r.strip() for r in g["regions"] if r.strip()}


def regional_variants(a, b, pat):
    """Different-title entries that are one game's releases for different markets: a Japanese/Asian title
    (romanised Japanese, e.g. Doubutsu no Mori +) and a Western one, with the same publisher code if known."""
    ra, rb = _regions(a), _regions(b)
    if not ra or not rb or ra & rb:
        return False
    if (ra <= ASIAN) == (rb <= ASIAN):
        return False
    ma, mb = makers(a, pat), makers(b, pat)
    return not (ma and mb) or bool(ma & mb)


def makers(g, pat):
    """Publisher (maker) codes from full 6-character IDs: GALE01 -> 01."""
    return {i[4:] for i in g["ids"] if len(i) == 6 and pat.match(i[:4])}


def add(index, platform, title, region, ids, sha1s, alias=True):
    key = game_key(title)
    if not key:
        return
    # Two lists can spell one game differently; a shared disc/cart ID means the same game, unless the shared
    # ID is a bare product code and the titles have nothing in common (third parties reuse product codes).
    owners = index.setdefault("_id_owner", {})
    for i in sorted(ids):
        o = owners.get(i)
        if o is None or o == key:
            continue
        weak = platform in CODE_PLATFORMS and len(i) == 4
        if not weak or titles_agree([title], [index[o]["title"], *index[o].get("aka", ())]):
            key = o
            break
    g = index.setdefault(key, {"title": title, "regions": set(), "ids": set(), "sha1": set()})
    if alias and game_key(g["title"]) != game_key(title):
        g.setdefault("aka", set()).add(title)
        # Show the most widely known name: a USA/World/Europe release's over a Japanese one's.
        new_rank = region_rank(r.strip() for r in region.split(","))
        if new_rank < region_rank(g["regions"]):
            g["aka"].discard(title)
            g["aka"].add(g["title"])
            g["title"] = title
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


def region_rank(regions):
    return min((REGION_PREF.index(r) for r in regions if r in REGION_PREF), default=len(REGION_PREF))


def group_regions(platform, index, previous=()):
    """Merge regional releases that share a product code's game part into one game.

    Third parties (and homebrew) reuse product codes, so sharing the code isn't enough: an entry joins a
    group only when its title agrees with a member's (a shared significant word) or it is that member's
    release for another market (see regional_variants), and never when the group already has a release
    for one of its regions. `previous` holds the keys published last time; a group keeps its old key."""
    pat = re.compile(CODE_PLATFORMS[platform].replace(" ", ""))
    by_code, loose = {}, {}
    for key, g in index.items():
        codes = sorted({i[:3] for i in g["ids"] if pat.match(i)})
        if codes:
            by_code.setdefault(codes[0], []).append((key, g))
        else:
            loose[key] = g

    def pref(kg):
        regions = kg[1]["regions"]
        rank = min((REGION_PREF.index(r) for r in regions if r in REGION_PREF), default=len(REGION_PREF))
        return (rank, "demo" in kg[1]["title"].lower(), kg[1]["title"], kg[0])

    out = dict(loose)
    for members in by_code.values():
        members.sort(key=pref)
        clusters = []
        for key, g in members:
            titles = [g["title"], *g.get("aka", ())]
            best, best_score = None, None
            for c in clusters:
                if any(_regions(g) & _regions(m) for _, m in c):
                    continue
                agree = any(titles_agree(titles, [m["title"], *m.get("aka", ())]) for _, m in c)
                if not agree and not any(regional_variants(g, m, pat) for _, m in c):
                    continue
                # The best fitting group, not the first: same title, then same publisher, then the widest release.
                score = (agree, bool(makers(g, pat) & set().union(*(makers(m, pat) for _, m in c))),
                         len(set().union(*(_regions(m) for _, m in c))))
                if best_score is None or score > best_score:
                    best, best_score = c, score
            if best is not None:
                best.append((key, g))
            else:
                clusters.append([(key, g)])
        for c in clusters:
            key, main = next(((k, g) for k, g in c if k in previous), c[0])
            merged = {"title": main["title"], "regions": set(), "ids": set(), "sha1": set(), "aka": set()}
            for k, g in c:
                merged["regions"] |= g["regions"]
                merged["ids"] |= g["ids"]
                merged["sha1"] |= g["sha1"]
                merged["aka"] |= {t for t in [g["title"], *g.get("aka", ())] if t != main["title"]}
            out[key] = merged  # index keys are unique, so a group's key never collides
    return out


def switch_titles(index):
    data = json.loads(get(TITLEDB, "switch_US.en.json"))
    for t in data.values():
        name, tid = t.get("name"), (t.get("id") or "").upper()
        # Base games only (title IDs ending in 000); skip updates, DLC and demos.
        if name and tid.endswith("000") and not re.search(r"(?i)\bdemo\b", name):
            add(index, "switch", base_title(re.sub(r"[\u2122\u00ae\u00a9]", "", name)), t.get("region") or "", {tid}, set())


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


def arcade_sets(index):
    """MAME: one game per parent set; clones (regional/revision sets) join their parent."""
    xml = get(LIBRETRO + urllib.parse.quote(MAME_XML), "mame2016.xml")
    parents, clones = {}, []
    for m in re.finditer(r'<(?:game|machine) name="([^"]+)"([^>]*)>(.*?)</(?:game|machine)>', xml, re.S):
        name, attrs, body = m.groups()
        if 'isbios="yes"' in attrs or 'isdevice="yes"' in attrs or 'runnable="no"' in attrs:
            continue
        desc = re.search(r"<description>(.*?)</description>", body, re.S)
        title = base_title(desc.group(1).replace("&amp;", "&")) if desc else name
        clone = re.search(r'cloneof="([^"]+)"', attrs)
        if clone:
            clones.append((name, clone.group(1)))
        else:
            parents[name] = title
            add(index, "arcade", title, "", {name.upper()}, set())
    owners = index.get("_id_owner", {})
    for name, parent in clones:
        key = owners.get(parent.upper())
        if key:
            index[key]["ids"].add(name.upper())
            owners[name.upper()] = key


def dos_titles(index):
    for g in parse_dat(get(LIBRETRO + urllib.parse.quote("dat/DOS.dat"), "dat_DOS.dat")):
        add(index, "dos", base_title(g["name"]), "", set(), set())


def previous_keys(platform):
    """Keys of the currently published list, so regrouping never renames an existing game's slot."""
    path = OUT / f"{platform}.json"
    try:
        return {g["key"] for g in json.loads(path.read_text())["games"]}
    except (OSError, ValueError, KeyError):
        return set()


def build(platform):
    index = {}
    if platform == "arcade":
        arcade_sets(index)
    if platform == "dos":
        dos_titles(index)
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
        dat = parse_dat(text)
        generic = generic_serials(dat)
        for g in dat:
            # Prototypes, betas, demos and kiosk discs aren't the game; they also reuse product codes.
            if any(NOT_RETAIL.search(n) for n in [g["name"], *g.get("roms", ())]):
                continue
            if not licensed(g, rel):
                continue
            ids = set()
            for s in g["serials"] - generic:
                for part in re.split(r"[,;]\s*", s):
                    if part:
                        ids |= ids_for(platform, part)
            title = base_title(g["name"])
            if rel.startswith("dat/"):
                # GameTDB after redump: an entry reusing a redump disc's code under another title is a mod or
                # custom disc of that game. Otherwise it only contributes IDs (GALE01), never a new alias.
                owners = index.get("_id_owner", {})
                if any(len(i) == 4 and i in owners and not titles_agree([title], [index[owners[i]]["title"]])
                       for i in ids):
                    continue
            add(index, platform, title, g["region"], ids, g["sha1s"], alias=not rel.startswith("dat/"))
    if platform == "naomi":
        naomi_carts(index)
    games = []
    index.pop("_id_owner", None)
    if platform in CODE_PLATFORMS:
        index = group_regions(platform, index, previous_keys(platform))
    for key, g in sorted(index.items()):
        if platform in IDLESS:
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
