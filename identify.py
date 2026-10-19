"""Work out which game a repository decompiles, from what's in the repository.

Decomp projects pin the exact binary they rebuild, so the repo contains a disc ID
(GALE01), a serial (SLUS_204.86), a title ID (54540859, Switch 01007EF00011E000), a MAME set
name (mvsc2) or a ROM hash (*.sha1, `sha1:` in splat configs). Those are matched against data/games/*.json.
Nothing the submitter types is trusted.
"""

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAMES = ROOT / "data" / "games"

# Systems whose game can't be read from the repository: the submitter names it and it must be on the
# system's list. (Switch has title IDs, but many repositories carry none.) Mirrored in worker/index.js.
NAME_ONLY = ("win32", "switch", "dos")


class GhError(Exception):
    pass


def gh(path, raw=False):
    """Like fetch.gh, but kept apart on purpose: raw=True returns decoded text (file contents),
    and JSON calls use the GitHub CLI's default Accept header."""
    args = ["gh", "api", path] if not raw else ["gh", "api", "-H", "Accept: application/vnd.github.raw", path]
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        raise GhError(r.stderr.decode(errors="replace").strip()[:200])
    return r.stdout.decode("utf-8", "replace") if raw else json.loads(r.stdout)


@lru_cache(maxsize=None)
def index(platform):
    """identifier -> [games] and sha1 -> [games] for one system. Product codes can be shared by
    unrelated games (third parties reuse them), so an identifier may name several."""
    path = GAMES / f"{platform}.json"
    if not path.exists():
        return {}, {}
    ids, hashes = {}, {}
    for g in json.loads(path.read_text())["games"]:
        for i in g.get("ids", ()):
            ids.setdefault(i, []).append(g)
        for h in g.get("sha1", ()):
            hashes.setdefault(h, []).append(g)
    return ids, hashes


def platforms():
    return sorted(p.stem for p in GAMES.glob("*.json"))


SERIAL = re.compile(r"(?<![A-Z0-9])([A-Z]{4})[-_ ]?(\d{3})\.?(\d{2})(?![0-9])")  # SLUS_204.86, SLUS-20486
DISC_ID = re.compile(r"^([A-Z0-9]{4})([0-9A-Z]{2})(?:_.*)?$")  # GALE01, G98E01_PIKIDEMO
CODE4 = re.compile(r"^([A-Z0-9]{4})$")  # A2DE (DS), NSME (N64)
TITLE_ID = re.compile(r"^([0-9A-F]{8})$")  # 54540859
# Switch: 0100XXXXXXXXX000 is a base game; ...800 is its update (same game).
SWITCH_TID = re.compile(r"(?i)(?<![0-9a-z])(?:0x)?(0100[0-9a-f]{9}[08]00)(?![0-9a-z])")
SHA1_LINE = re.compile(r"(?i)sha-?1[\"']?\s*[:=]\s*[\"']?([0-9a-f]{40})")
SHA1 = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")
SEGA_PRODUCT = re.compile(r"(?<![A-Z0-9])((?:MK|HDR|GDL|GS|T)-\d{4,5}[A-Z]?)(?![A-Z0-9])")  # MK-51052, T-1215N, GDL-0007A
MAME_SET = re.compile(r'"set"\s*:\s*"([a-z0-9_]+)"')
# A quoted build name in a config file: "GALE01", 'SLUS_204.86'.
QUOTED = re.compile(r"""["']([A-Z0-9]{6}|[A-Z]{4}[-_]\d{3}\.?\d{2})["']""")

# Which identifier kinds can name a game on which systems.
KIND_PLATFORMS = {
    "sha1": None,  # any
    "set": ("naomi", "arcade"),
    "tid": ("switch",),
    "serial": ("ps", "ps2", "psp", "ps3", "vita"),
    "title": ("xbox", "xbox360"),
    "disc": ("gc", "wii"),
    "code": ("n64", "gba", "nds", "3ds", "wiiu"),
    "product": ("dc", "naomi", "saturn", "segacd"),
    "vset": ("naomi", "arcade"),  # a version named like a MAME set: weakest signal, needs arcade evidence
}
KINDS = ("sha1", "set", "tid", "serial", "title", "disc", "code", "product", "vset")  # strongest first
STRONG_KINDS = ("sha1", "set", "tid", "serial", "title", "disc")

# Where an identifier was seen. "path" (any other directory or file name) is weak: it only counts
# when something else in the repository points at the same game.
VERSION, CONFIG, CONTENT, PATH = "version", "config", "content", "path"


def empty():
    """kind -> {identifier: {sources}}"""
    return {k: {} for k in KINDS}


def merge(a, b):
    for k in a:
        for i, src in b[k].items():
            a[k].setdefault(i, set()).update(src)
    return a


def _add(out, kind, ident, source):
    out[kind].setdefault(ident, set()).add(source)


def from_token(token, is_version=False, source=None):
    """Identifiers in one name: a report version, a directory or a file name.
    Real IDs are written in capitals, so lower-case words (shader, assets) never count."""
    out = empty()
    src = source or (VERSION if is_version else PATH)
    for a, b, c in SERIAL.findall(token.upper()):
        _add(out, "serial", f"{a}{b}{c}", src)
    stem = re.sub(r"\.(yaml|yml|json|sha1|txt|lcf|ld)$", "", token, flags=re.I)
    m = DISC_ID.match(stem)
    if m and m.group(1)[0] in "GDRSHW" and re.search(r"\d", m.group(2)):
        _add(out, "disc", m.group(1), src)
    if is_version and CODE4.match(stem):
        _add(out, "code", stem, src)
    # Arcade projects often name their version after the MAME set (e.g. "mvsc2").
    if is_version and re.fullmatch(r"[a-z][a-z0-9_]{3,15}", token) and token not in ("main", "base", "retail", "release", "debug", "demo"):
        _add(out, "vset", token.upper(), src)
    if TITLE_ID.match(stem) and re.search(r"\d", stem):
        _add(out, "title", stem, src)
    for t in SWITCH_TID.findall(token):
        _add(out, "tid", t.upper()[:13] + "000", src)
    for m in SEGA_PRODUCT.findall(token.upper()):
        _add(out, "product", re.sub(r"[^A-Z0-9]", "", m), src)
    return out


def from_content(text, is_sha1_file, path=""):
    """Identifiers inside a config file. sha1 sources carry the file name, to tell which build a hash is for."""
    out = empty()
    for h in (SHA1.findall(text)[:1] if is_sha1_file else SHA1_LINE.findall(text)):
        _add(out, "sha1", h.lower(), f"{CONTENT}:{path}")
    for s in MAME_SET.findall(text):
        _add(out, "set", s.upper(), CONTENT)
    for m in SEGA_PRODUCT.findall(text.upper()):
        _add(out, "product", re.sub(r"[^A-Z0-9]", "", m), CONTENT)
    for t in SWITCH_TID.findall(text):
        _add(out, "tid", t.upper()[:13] + "000", CONTENT)
    for q in QUOTED.findall(text):
        f = from_token(q, source=CONTENT)
        for kind in ("serial", "disc"):
            merge(out, {k: (f[k] if k == kind else {}) for k in KINDS})
    return out


def from_path(path):
    """Identifiers in a tree path. Known config layouts count; any other name is a weak hint:
    config/<ID>/..., <ID>.yml/.yaml, *.<ID>.yaml, <ID>.sha1 / *.<ID>.sha1."""
    out = empty()
    parts = path.split("/")
    for i, part in enumerate(parts[:3]):
        if i > 0 and parts[i - 1].lower() in ("config", "configs"):
            merge(out, from_token(part, source=CONFIG))
        else:
            merge(out, from_token(part))
    name = parts[-1]
    m = re.fullmatch(r"(?i)(.+)\.(ya?ml|sha1)", name)
    if m:
        stem = m.group(1)
        segments = {stem, *stem.split("."), stem.split(".", 1)[-1]}
        for seg in segments:
            merge(out, from_token(seg, source=CONFIG))
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
        merge(found, from_path(t["path"]))
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
        merge(found, from_content(text, t["path"].endswith(".sha1"), t["path"]))
    return found


REGION_ORDER = ("USA", "World", "Europe")


def _demo(g):
    return bool(re.search(r"(?i)demo|sampler|kiosk|preview|trial", g["title"]))


def pick(hits):
    """Several games matched on one system by one repo: the repo targets regional builds of a
    single game. Prefer the full game over demos/samplers, then the most widely released entry."""
    def rank(g):
        region = min((REGION_ORDER.index(r) for r in g["regions"] if r in REGION_ORDER), default=9)
        return (_demo(g), region, -len(g["ids"]))
    return sorted(hits, key=rank)[0]


def _hits(found, platform_hint, version_ids):
    """Every (platform, game) the candidates point at: {(plat, key): {"game", "ids": {(kind, id): sources}}}."""
    arcade = bool(found["set"]) or platform_hint in ("naomi", "arcade")
    out = {}
    for kind in KINDS:
        if kind == "vset" and not arcade:
            continue  # frogger, tetris, pacman, progress... are MAME set names too
        plats = KIND_PLATFORMS[kind] or platforms()
        if platform_hint:
            plats = (platform_hint,) if platform_hint in plats else ()
        for plat in plats:
            ids, hashes = index(plat)
            for c, sources in found[kind].items():
                for g in (hashes if kind == "sha1" else ids).get(c, ()):
                    h = out.setdefault((plat, g["key"]), {"game": g, "ids": {}})
                    h["ids"].setdefault((kind, c), set()).update(sources)
    _resolve_sha1(out, version_ids)
    # Weak hints count only alongside something else for the same game.
    for k in list(out):
        if all(src == {PATH} for src in out[k]["ids"].values()):
            del out[k]
    return out


def _resolve_sha1(out, version_ids):
    """Hashes naming different games: a repo can carry another game's hash (a sister project's config,
    test data). Keep hashes whose file is named after one of the repo's own builds; failing that, only
    games another kind of identifier agrees with."""
    sha_games = {k for k, h in out.items() if any(kind == "sha1" for kind, _ in h["ids"])}
    if len({k[1] for k in sha_games}) <= 1:
        return
    own = [v.lower() for v in version_ids if len(v) >= 2]

    def named(k):
        return any(own and any(v in src.lower() for v in own) for (kind, _), srcs in out[k]["ids"].items()
                   if kind == "sha1" for src in srcs)

    keep = {k for k in sha_games if named(k)}
    if len({k[1] for k in keep}) != 1:
        keep = {k for k in sha_games if any(kind != "sha1" for kind, _ in out[k]["ids"])}
    for k in sha_games - keep:
        h = out[k]
        h["ids"] = {ki: s for ki, s in h["ids"].items() if ki[0] != "sha1"}
        if not h["ids"]:
            del out[k]


def match(found, platform_hint=None, version_ids=()):
    """(platform, game, aliases) for the candidates. With a known system, only games on that system count.

    A repository can mention several systems (a GameCube repo with a PS2 serial in a config): the
    system with the most supporting identifiers wins, ties going to the strongest kind of identifier.
    On that system, the game with the most support wins (full games over demos)."""
    hits = _hits(found, platform_hint, version_ids)
    if not hits:
        return None

    def strength(ids):
        return min(KINDS.index(kind) for kind, _ in ids)

    by_plat = {}
    for (plat, _), h in hits.items():
        by_plat.setdefault(plat, []).append(h)

    def plat_rank(plat):
        ids = {ki for h in by_plat[plat] for ki in h["ids"]}
        return (-len(ids), strength(ids), plat)

    plat = min(by_plat, key=plat_rank)
    games = by_plat[plat]
    region = lambda g: min((REGION_ORDER.index(r) for r in g["regions"] if r in REGION_ORDER), default=9)
    games.sort(key=lambda h: (_demo(h["game"]), -len(h["ids"]), strength(h["ids"]), region(h["game"]),
                              -len(h["game"]["ids"]), h["game"]["key"]))
    ranked = [{"key": h["game"]["key"], "title": h["game"]["title"], "aka": h["game"].get("aka", []),
               "by": KINDS[strength(h["ids"])],
               "builds": sorted({i for _, i in h["ids"]})}  # the exact identifiers that proved it
              for h in games]
    return {"platform": plat, **ranked[0], "aliases": sorted(g["key"] for g in ranked[1:]), "games": ranked}


def identify(repo, branch, platform_hint=None, version_ids=()):
    # Report version names are the strongest signal; try them alone first.
    own = empty()
    for v in version_ids:
        merge(own, from_token(v, is_version=True))
    return (match(own, platform_hint, version_ids)
            or match(repo_candidates(repo, branch, version_ids), platform_hint, version_ids))


def build_ids(version_id):
    """Identifiers naming one specific build, from a report version name (GALE01, SLUS_204.86)."""
    f = from_token(version_id, is_version=True)
    return {i for kind in ("serial", "title", "disc", "code", "product", "tid") for i in f[kind]}
