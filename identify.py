"""Work out which game a repository decompiles, from what's in the repository.

Decomp projects pin the exact binary they rebuild, so the repo contains a disc ID
(GALE01), a serial (SLUS_204.86), a title ID (54540859), a MAME set name (mvsc2) or a
ROM hash (*.sha1, `sha1:` in splat configs). Those are matched against data/games/*.json.
Nothing the submitter types is trusted.
"""

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAMES = ROOT / "data" / "games"


class GhError(Exception):
    pass


def gh(path, raw=False):
    args = ["gh", "api", path] if not raw else ["gh", "api", "-H", "Accept: application/vnd.github.raw", path]
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        raise GhError(r.stderr.decode(errors="replace").strip()[:200])
    return r.stdout.decode("utf-8", "replace") if raw else json.loads(r.stdout)


@lru_cache(maxsize=None)
def index(platform):
    path = GAMES / f"{platform}.json"
    if not path.exists():
        return {}, {}
    ids, hashes = {}, {}
    for g in json.loads(path.read_text())["games"]:
        for i in g.get("ids", ()):
            ids.setdefault(i, g)
        for h in g.get("sha1", ()):
            hashes.setdefault(h, g)
    return ids, hashes


def platforms():
    return sorted(p.stem for p in GAMES.glob("*.json"))


SERIAL = re.compile(r"(?<![A-Z0-9])([A-Z]{4})[-_ ]?(\d{3})\.?(\d{2})(?![0-9])")  # SLUS_204.86, SLUS-20486
DISC_ID = re.compile(r"^([A-Z0-9]{4})([0-9A-Z]{2})(?:_.*)?$")  # GALE01, G98E01_PIKIDEMO
CODE4 = re.compile(r"^([A-Z0-9]{4})$")  # A2DE (DS), NSME (N64)
TITLE_ID = re.compile(r"^([0-9A-F]{8})$")  # 54540859
SHA1_LINE = re.compile(r"(?i)sha-?1[\"']?\s*[:=]\s*[\"']?([0-9a-f]{40})")
SHA1 = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")
SEGA_PRODUCT = re.compile(r"(?<![A-Z0-9])((?:MK|HDR|GDL|GS|T)-\d{4,5}[A-Z]?)(?![A-Z0-9])")  # MK-51052, T-1215N, GDL-0007A
MAME_SET = re.compile(r'"set"\s*:\s*"([a-z0-9_]+)"')

# Which identifier kinds can name a game on which systems.
KIND_PLATFORMS = {
    "sha1": None,  # any
    "set": ("naomi", "arcade"),
    "serial": ("ps", "ps2", "psp", "ps3", "vita"),
    "title": ("xbox", "xbox360"),
    "disc": ("gc", "wii"),
    "code": ("n64", "gba", "nds", "3ds", "wiiu"),
    "product": ("dc", "naomi", "saturn", "segacd"),
    "vset": ("naomi", "arcade"),  # a version named like a MAME set: weakest signal, checked last
}
KINDS = ("sha1", "set", "serial", "title", "disc", "code", "product", "vset")


def empty():
    return {k: set() for k in KINDS}


def merge(a, b):
    for k in a:
        a[k] |= b[k]
    return a


def from_token(token, is_version=False):
    """Identifiers in one name: a report version, a directory or a file name.
    Real IDs are written in capitals, so lower-case words (shader, assets) never count."""
    out = empty()
    for a, b, c in SERIAL.findall(token.upper()):
        out["serial"].add(f"{a}{b}{c}")
    stem = re.sub(r"\.(yaml|yml|json|sha1|txt|lcf|ld)$", "", token, flags=re.I)
    m = DISC_ID.match(stem)
    if m and m.group(1)[0] in "GDRSHW" and re.search(r"\d", m.group(2)):
        out["disc"].add(m.group(1))
    if is_version and CODE4.match(stem):
        out["code"].add(stem)
    # Arcade projects often name their version after the MAME set (e.g. "mvsc2").
    if is_version and re.fullmatch(r"[a-z][a-z0-9_]{3,15}", token) and token not in ("main", "base", "retail", "release", "debug", "demo"):
        out["vset"].add(token.upper())
    if TITLE_ID.match(stem) and re.search(r"\d", stem):
        out["title"].add(stem)
    out["product"] |= {re.sub(r"[^A-Z0-9]", "", m) for m in SEGA_PRODUCT.findall(token.upper())}
    return out


def from_content(text, is_sha1_file):
    out = empty()
    out["sha1"] |= {h.lower() for h in (SHA1.findall(text)[:1] if is_sha1_file else SHA1_LINE.findall(text))}
    out["set"] |= {s.upper() for s in MAME_SET.findall(text)}
    out["product"] |= {re.sub(r"[^A-Z0-9]", "", m) for m in SEGA_PRODUCT.findall(text.upper())}
    return out


CONFIG_FILE = re.compile(r"(\.sha1$|\.ya?ml$|(^|/)(target|config|project)\.json$|\.toml$|configure\.py$)", re.I)
SKIP_DIRS = (".github/", "tools/", "extern/", "lib/", "include/", "src/", "asm/", "docs/", "node_modules/")


def repo_candidates(repo, branch, version_ids=()):
    found = empty()
    for v in version_ids:
        merge(found, from_token(v, is_version=True))
    try:
        tree = gh(f"repos/{repo}/git/trees/{branch}?recursive=1")["tree"]
    except GhError:
        tree = []
    for t in tree:
        if t["path"].startswith(SKIP_DIRS):
            continue
        for part in t["path"].split("/")[:3]:
            merge(found, from_token(part))
    configs = [
        t for t in tree
        if t["type"] == "blob" and CONFIG_FILE.search(t["path"]) and t.get("size", 0) < 200_000
        and t["path"].count("/") <= 3 and not t["path"].startswith(SKIP_DIRS)
    ]
    configs.sort(key=lambda t: (not t["path"].endswith(".sha1"), t["path"].count("/"), t.get("size", 0)))
    for t in configs[:12]:
        try:
            text = gh(f"repos/{repo}/contents/{t['path']}?ref={branch}", raw=True)
        except GhError:
            continue
        merge(found, from_content(text, t["path"].endswith(".sha1")))
    return found


REGION_ORDER = ("USA", "World", "Europe")


def pick(hits):
    """Several games matched on one system by one repo: the repo targets regional builds of a
    single game. Prefer the full game over demos/samplers, then the most widely released entry."""
    def rank(g):
        demo = bool(re.search(r"(?i)demo|sampler|kiosk|preview|trial", g["title"]))
        region = min((REGION_ORDER.index(r) for r in g["regions"] if r in REGION_ORDER), default=9)
        return (demo, region, -len(g["ids"]))
    return sorted(hits, key=rank)[0]


def match(found, platform_hint=None):
    """(platform, game, aliases) for the candidates. Hashes beat set names beat IDs.
    With a known system, only games on that system count."""
    for kind in KINDS:
        plats = KIND_PLATFORMS[kind] or platforms()
        if platform_hint:
            plats = (platform_hint,) if platform_hint in plats else ()
        for plat in plats:
            ids, hashes = index(plat)
            hits, matched = {}, set()
            for c in found[kind]:
                g = hashes.get(c) if kind == "sha1" else ids.get(c)
                if g:
                    hits[g["key"]] = g
                    matched.add(c)
            if hits:
                g = pick(list(hits.values()))
                return {
                    "platform": plat, "key": g["key"], "title": g["title"], "by": kind,
                    "aliases": sorted(k for k in hits if k != g["key"]),
                    "builds": sorted(matched),  # the exact identifiers that proved it
                }
    return None


def identify(repo, branch, platform_hint=None, version_ids=()):
    # Report version names are the strongest signal; try them alone first.
    own = empty()
    for v in version_ids:
        merge(own, from_token(v, is_version=True))
    return match(own, platform_hint) or match(repo_candidates(repo, branch, version_ids), platform_hint)


def build_ids(version_id):
    """Identifiers naming one specific build, from a report version name (GALE01, SLUS_204.86)."""
    f = from_token(version_id, is_version=True)
    return {i for kind in ("serial", "title", "disc", "code", "product") for i in f[kind]}
