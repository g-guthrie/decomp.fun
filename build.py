#!/usr/bin/env python3
"""Build the decomp.fun static site into dist/.

    python3 fetch.py   # refresh progress from GitHub (optional)
    python3 build.py
    python3 -m http.server -d dist 8000

Inputs: data/site.json, data/projects.json (our projects), data/catalog.json
(the public index), data/reports/*.json (live reports), data/history/*.json.
Standard library only.
"""

import json
import os
import re
import shutil
import struct
import unicodedata
import zlib
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fetch import all_projects, contesting_projects
import api
import slots

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DIST = ROOT / "dist"

# key, name, short label, maker
PLATFORMS = [
    ("n64", "Nintendo 64", "N64", "Nintendo"),
    ("gc", "GameCube", "GC", "Nintendo"),
    ("wii", "Wii", "Wii", "Nintendo"),
    ("switch", "Switch", "NSW", "Nintendo"),
    ("gba", "Game Boy Advance", "GBA", "Nintendo"),
    ("nds", "Nintendo DS", "NDS", "Nintendo"),
    ("3ds", "Nintendo 3DS", "3DS", "Nintendo"),
    ("ps", "PlayStation", "PS1", "Sony"),
    ("ps2", "PlayStation 2", "PS2", "Sony"),
    ("psp", "PlayStation Portable", "PSP", "Sony"),
    ("xbox", "Xbox", "XBOX", "Microsoft"),
    ("xbox360", "Xbox 360", "360", "Microsoft"),
    ("win32", "Windows", "PC", "Microsoft"),
    ("nes", "NES", "NES", "Nintendo"),
    ("snes", "Super Nintendo", "SNES", "Nintendo"),
    ("gb", "Game Boy", "GB", "Nintendo"),
    ("gbc", "Game Boy Color", "GBC", "Nintendo"),
    ("vb", "Virtual Boy", "VB", "Nintendo"),
    ("wiiu", "Wii U", "WiiU", "Nintendo"),
    ("ps3", "PlayStation 3", "PS3", "Sony"),
    ("vita", "PlayStation Vita", "VITA", "Sony"),
    ("dos", "DOS", "DOS", "Microsoft"),
    ("sms", "Master System", "SMS", "Sega"),
    ("md", "Mega Drive / Genesis", "MD", "Sega"),
    ("segacd", "Sega CD", "SCD", "Sega"),
    ("32x", "32X", "32X", "Sega"),
    ("saturn", "Saturn", "SAT", "Sega"),
    ("gg", "Game Gear", "GG", "Sega"),
    ("dc", "Dreamcast", "DC", "Sega"),
    ("naomi", "Sega NAOMI", "NAOMI", "Sega"),
    ("arcade", "Arcade", "ARC", "Arcade"),
    ("atari2600", "Atari 2600", "2600", "Atari"),
    ("lynx", "Atari Lynx", "LYNX", "Atari"),
    ("jaguar", "Atari Jaguar", "JAG", "Atari"),
    ("pce", "PC Engine / TurboGrafx-16", "PCE", "NEC"),
    ("ngp", "Neo Geo Pocket", "NGP", "SNK"),
    ("neocd", "Neo Geo CD", "NGCD", "SNK"),
]
PLAT = {k: {"name": n, "short": s, "maker": m} for k, n, s, m in PLATFORMS}

SORTS = [
    ("updated", "Recently updated"),
    ("percent", "Highest %"),
    ("matched", "Most code decompiled"),
    ("size", "Largest games"),
    ("name", "Name"),
]
MAKERS = ["Nintendo", "Sony", "Microsoft", "Sega", "Atari", "NEC", "SNK", "Arcade"]
SITE_URL = "https://decomp.fun"

SIZE_UNITS = ["B", "kB", "MB", "GB", "TB"]

ICON_THEME = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/></svg>'
)

THEME_INIT = (
    "<script>try{var t=localStorage.getItem('theme');if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"
)


def e(s):
    return escape(str(s), quote=True)


def fmt_pct(v):
    if v != 0 and v != 100:
        v = min(max(v, 0.01), 99.99)
    return f"{v:.2f}%"


def fmt_pct1(v):
    """Short form for cards: one decimal, never rounds up to 100% or down to 0%."""
    if v == 0 or v == 100:
        return f"{v:.0f}%"
    return f"{min(max(v, 0.1), 99.9):.1f}%"


def fmt_size(n):
    n = float(n)
    i = 0
    while n >= 1000 and i < len(SIZE_UNITS) - 1:
        n /= 1000
        i += 1
    return f"{n:.2f} {SIZE_UNITS[i]}"


def pct(a, b):
    return a / b * 100 if b else 0.0


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def ago(dt, now):
    secs = max(0, int((now - dt).total_seconds()))
    for unit, size in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = secs // size
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def ago_short(dt, now):
    secs = max(0, int((now - dt).total_seconds()))
    for unit, size in (("y", 31536000), ("mo", 2592000), ("w", 604800), ("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= size:
            return f"{secs // size} {unit} ago"
    return "just now"


PAGES = []  # canonical paths written, for sitemap.xml


def write(rel, content):
    if rel.endswith(".html") and "</head>" in content:
        path = "/" + rel.removesuffix("index.html") if rel != "404.html" else None
        if path:
            content = content.replace("</head>", f'<link rel="canonical" href="{SITE_URL}{path}"></head>', 1)
            content = content.replace("<meta property=\"og:url\" content=\"\">", f'<meta property="og:url" content="{SITE_URL}{path}">', 1)
            PAGES.append(path)
        else:
            content = content.replace("<meta property=\"og:url\" content=\"\">", "", 1)
    out = DIST / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)


def plat_badge(key, link=False):
    p = PLAT.get(key, {"name": key or "Unknown", "short": (key or "?").upper()})
    if link:
        return f'<a class="plat" data-p="{e(key)}" href="/?platform={e(key)}" title="Show all {e(p["name"])} projects">{e(p["short"])}</a>'
    return f'<span class="plat" data-p="{e(key)}" title="{e(p["name"])}">{e(p["short"])}</span>'


# ---------------------------------------------------------------- titles and build labels

TITLE_PREFIXES = ("Nickelodeon ", "Disney-Pixar ", "Disney/Pixar ", "Disney ", "Music Inspired by ")


def clean_title(t):
    """Database titles into readable ones: "Nickelodeon SpongeBob SquarePants - Battle for Bikini Bottom"
    -> "SpongeBob SquarePants: Battle for Bikini Bottom"."""
    t = (t or "").strip()
    for pre in TITLE_PREFIXES:
        if t.startswith(pre) and len(t) > len(pre) + 3:
            t = t[len(pre):]
    return t.replace(" - ", ": ")


def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def display_title(name, game_title):
    """The game's real title. The hand-curated repository name wins when it's the same title
    (it keeps accents and punctuation the databases drop) or a longer form of it."""
    t = clean_title(game_title)
    if not t:
        return name
    n, c = norm_title(name), norm_title(t)
    if n == c or n.startswith(c + " "):
        return name
    return t


REGIONS = {
    "us": "USA", "usa": "USA", "u": "USA", "en": "USA", "ntsc": "USA", "ntscu": "USA",
    "jp": "Japan", "jpn": "Japan", "jap": "Japan", "ja": "Japan", "j": "Japan", "ntscj": "Japan",
    "eu": "Europe", "eur": "Europe", "europe": "Europe", "pal": "Europe", "e": "Europe",
    "kr": "Korea", "kor": "Korea", "k": "Korea", "de": "Germany", "ger": "Germany",
    "fr": "France", "fra": "France", "cn": "China",
}


def build_label(vid):
    """"us" -> "USA build", "jp_rev1" -> "Japan build (rev1)", "default" -> "" (hidden)."""
    if not vid or vid.lower() in ("default", "build"):
        return ""
    if vid.islower() or (vid.isupper() and len(vid) <= 5):
        for pattern in (r"([a-z]+)(?:[-_]?((?:rev)?\d+|[a-z]+\d*))?", r"[a-z0-9]+-([a-z]+)"):
            m = re.fullmatch(pattern, vid.lower())
            if m and m.group(1) in REGIONS:
                extra = f" ({m.group(2)})" if m.lastindex and m.lastindex > 1 and m.group(2) else ""
                return f"{REGIONS[m.group(1)]} build{extra}"
    return f"{vid} build"


# ---------------------------------------------------------------- PNG (stdlib)


def png(w, h, pixels):
    """pixels: bytes/bytearray of RGB rows, w*h*3 long."""
    raw = b"".join(b"\x00" + bytes(pixels[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def hex_rgb(c):
    c = c.lstrip("#")
    return bytes(int(c[i:i + 2], 16) for i in (0, 2, 4))


def fill_rect(buf, w, x0, y0, x1, y1, color):
    x0, x1 = max(0, int(x0)), max(0, int(x1))
    if x1 <= x0:
        return
    row = color * (x1 - x0)
    for y in range(max(0, int(y0)), int(y1)):
        buf[(y * w + x0) * 3:(y * w + x1) * 3] = row


def write_bytes(rel, data):
    out = DIST / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def write_touch_icon():
    """180x180 apple-touch-icon in the favicon's design: four tiles, three done, one in progress."""
    S = 180
    buf = bytearray(hex_rgb("#181c25") * S * S)
    k = S / 64
    green, blue = hex_rgb("#29c45a"), hex_rgb("#2a5a9c")
    for (x, y), col in (((10, 12), green), ((34, 12), green), ((10, 34), green), ((34, 34), blue)):
        fill_rect(buf, S, x * k, y * k, (x + 20) * k, (y + 18) * k, col)
    fill_rect(buf, S, 38 * k, 41.5 * k, 50 * k, 44.5 * k, hex_rgb("#ffffff"))
    write_bytes("apple-touch-icon.png", png(S, S, buf))


def write_og_image(p, cells):
    """Per-project social preview (1200x630 PNG): the project's progress map, like its banner."""
    W, H = 1200, 630
    pad, bar_h = 40, 28
    buf = bytearray(hex_rgb("#181c25") * W * H)
    mw, mh = W - 2 * pad, H - 3 * pad - bar_h
    for c in cells:
        fill_rect(buf, W, pad + c["x"] * mw, pad + c["y"] * mh, pad + (c["x"] + c["w"]) * mw - 2,
                  pad + (c["y"] + c["h"]) * mh - 2, hex_rgb(banner_color(c["p"])))
    by = H - pad - bar_h
    fill_rect(buf, W, pad, by, W - pad, by + bar_h, hex_rgb("#2a3140"))
    fill_rect(buf, W, pad, by, pad + mw * p.v.code_pct / 100, by + bar_h, hex_rgb("#00895a"))
    fill_rect(buf, W, pad, by, pad + mw * p.v.linked_pct / 100, by + bar_h, hex_rgb("#00a66e"))
    rel = "/".join([p.owner, p.repo, "og.png"])
    write_bytes(rel, png(W, H, buf))
    return rel


# ---------------------------------------------------------------- data model


class Version:
    def __init__(self, raw, archived=False):
        self.id = raw["id"]
        self.commit = raw["commit"]
        self.archived = archived
        m = raw["measures"]
        self.total_code = m.get("total_code") or 0
        # Archived entries only carry percentages; keep them exact.
        if archived:
            self.code_pct = m["matched_code_percent"]
            self.linked_pct = m["complete_code_percent"]
        else:
            self.code_pct = pct(m["matched_code"], self.total_code)
            self.linked_pct = pct(m["linked_code"], self.total_code)
        self.fuzzy_pct = max(m.get("fuzzy_match_percent") or 0.0, self.code_pct)
        self.matched_code = round(self.total_code * self.code_pct / 100)
        self.linked_code = round(self.total_code * self.linked_pct / 100)
        self.total_data = m.get("total_data") or 0
        self.matched_data = m.get("matched_data") or 0
        self.linked_data = m.get("linked_data") or 0
        self.functions = (m.get("matched_functions") or 0, m.get("total_functions") or 0)
        self.units = raw.get("units", [])

    @property
    def updated(self):
        return parse_ts(self.commit["timestamp"])


def load_record(pid):
    """Full report from the cache, or the committed slim copy (data/latest) when that is newer or the
    cache is missing. Another run may have refreshed a project after this run's cache was restored."""
    def newest(rec):
        return max((v.get("commit", {}).get("timestamp", "") for v in rec.get("versions", [])), default="")

    full_path, slim_path = DATA / "reports" / f"{pid}.json", DATA / "latest" / f"{pid}.json"
    full = json.loads(full_path.read_text()) if full_path.exists() else {}
    slim = json.loads(slim_path.read_text()) if slim_path.exists() else {}
    if slim and (not full or newest(slim) > newest(full) or (slim.get("meta", {}).get("missing") and not full.get("meta", {}).get("missing"))):
        return slim
    return full


class Project:
    def __init__(self, p):
        self.raw = p
        self.id = p["id"]
        self.owner = p["owner"]
        self.repo = p["repo"]
        self.name = p["name"]
        self.platform = p.get("platform") or "unknown"
        self.role = p.get("role")
        self.image = None  # set to the generated banner when the project has per-file data
        report = load_record(self.id)
        self.meta = report.get("meta", {})
        self.description = p.get("description") or self.meta.get("description") or ""
        self.repository = self.meta.get("html_url") or f"https://github.com/{self.owner}/{self.repo}"
        from fetch import builds_for, order_versions

        raw_versions = [v for v in report.get("versions", []) if v.get("commit")]
        raw_versions = order_versions(raw_versions, p.get("version"), builds_for(f"{self.owner}/{self.repo}"))
        self.versions = [Version(v) for v in raw_versions]
        if not self.versions and p.get("timestamp"):
            self.versions = [
                Version(
                    {
                        "id": p.get("version") or "default",
                        "commit": {"sha": p.get("commit") or "", "message": "", "timestamp": p["timestamp"]},
                        "measures": {
                            "total_code": p.get("total_code") or 0,
                            "matched_code_percent": p.get("matched_code_percent", 0.0),
                            "complete_code_percent": p.get("complete_code_percent", 0.0),
                            "fuzzy_match_percent": p.get("fuzzy_match_percent", 0.0),
                        },
                    },
                    archived=True,
                )
            ]
        self.leader = self  # set by slots.assign
        self.others = []
        self.challengers = {}
        self.contesters = []
        self.contested_by = []
        self.game_title = ""
        self.title = self.name  # the game's real title, set in main() once identities are loaded
        self.alt = ""  # the repository's own name, when it differs from the title
        self.og_image = None
        self.disputed = None
        hist_path = DATA / "history" / f"{self.id}.json"
        self.history = json.loads(hist_path.read_text()) if hist_path.exists() else []

    @property
    def v(self):
        return self.versions[0]

    def url(self, *rest):
        return "/" + "/".join([self.owner, self.repo, *rest]) + "/"

    def commit_url(self, sha):
        return f"{self.repository.rstrip('/')}/commit/{sha}"


# ---------------------------------------------------------------- shared markup


def head(site, title, description=None, extra="", image=None, og_type="website"):
    desc = description or site["tagline"]
    img = f"{SITE_URL}/{image}" if image else f"{SITE_URL}/apple-touch-icon.png"
    card = "summary_large_image" if image else "summary"
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title>"
        '<meta name="color-scheme" content="dark light">'
        f'<meta name="description" content="{e(desc)}">'
        f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">'
        f'<meta property="og:site_name" content="{e(site["name"])}"><meta property="og:type" content="{og_type}">'
        '<meta property="og:url" content="">'
        f'<meta property="og:image" content="{e(img)}"><meta name="twitter:card" content="{card}">'
        '<meta name="theme-color" content="#181c25" media="(prefers-color-scheme: dark)">'
        '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        f"{THEME_INIT}"
        '<link rel="stylesheet" href="/static/vendor/pico.min.css">'
        '<link rel="stylesheet" href="/static/css/site.css">'
        f"{extra}</head>"
    )


def brand(site):
    name = site["name"]
    if "." in name:
        a, b = name.rsplit(".", 1)
        return f'<span class="brand">{e(a)}<span class="tld">.{e(b)}</span></span>'
    return f'<span class="brand">{e(name)}</span>'


def nav(site, crumbs=()):
    n = len(crumbs)
    left = f'<li><a href="/" aria-label="{e(site["name"])} home">{brand(site)}</a></li>' + "".join(
        f'<li><a href="{e(u)}"{" aria-current=page" if i == n - 1 else ""}>{e(t)}</a></li>' for i, (t, u) in enumerate(crumbs)
    )
    right = (
        '<li><a href="/systems/">Systems</a></li><li><a href="/submit/">Add a project</a></li><li><a href="/about/">About</a></li>'
        + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in site.get("nav_links", []))
        + f'<li><button type="button" id="theme-toggle" title="Switch light/dark" aria-label="Dark theme" aria-pressed="false">{ICON_THEME}</button></li>'
    )
    return f'<nav aria-label="Main"><ul aria-label="Breadcrumb">{left}</ul><ul>{right}</ul></nav>'


def footer(site, built):
    return (
        '<footer class="container"><small class="muted">'
        f'{e(site["name"])} · progress is read from each project&rsquo;s own CI reports · '
        '<a href="/about/">About</a> · <a href="/about/#agents">For scripts and agents</a></small>'
        f'<small class="muted">Site rebuilt <time datetime="{built.isoformat()}">{built.strftime("%Y-%m-%d %H:%M UTC")}</time></small>'
        "</footer>"
    )


def page(site, built, title, body, description=None, crumbs=(), extra_head="", scripts=(), image=None, og_type="website"):
    js = "".join(f'<script src="{s}" defer></script>' for s in ("/static/js/site.js", *scripts))
    return (
        head(site, title, description, extra_head + js, image, og_type)
        + f'<body><a class="skip" href="#main">Skip to content</a><header class="container">{nav(site, crumbs)}</header>'
        + f'<main class="container" id="main">{body}</main>'
        + footer(site, built)
        + "</body></html>"
    )


def bar(kind, total, linked_pct, matched_pct, fuzzy_pct=None, linked_size=None, matched_size=None, extra=""):
    parts = []
    sized = total > 0

    def size(x):
        return f" ({fmt_size(x)})" if sized and x is not None else ""

    if linked_pct > 0:
        parts.append(
            f'<span class="linked" style="width:{linked_pct:.4f}%" data-tip="{fmt_pct(linked_pct)} fully linked{size(linked_size)}"></span>'
        )
    if matched_pct - linked_pct > 0.0001:
        parts.append(
            f'<span class="matched striped" style="width:{matched_pct - linked_pct:.4f}%" '
            f'data-tip="{fmt_pct(matched_pct)} decompiled{size(matched_size)}"></span>'
        )
    if fuzzy_pct is not None and fuzzy_pct - matched_pct > 0.0001:
        parts.append(
            f'<span class="fuzzy striped" style="width:{fuzzy_pct - matched_pct:.4f}%" data-tip="{fmt_pct(fuzzy_pct)} including partly decompiled code"></span>'
        )
    label = f"{fmt_pct(matched_pct)} decompiled"
    if linked_pct > 0:
        label += f", {fmt_pct(linked_pct)} fully linked"
    if fuzzy_pct is not None and fuzzy_pct - matched_pct > 0.0001:
        label += f", {fmt_pct(fuzzy_pct)} including partly decompiled"
    return f'<div class="bar {kind}{" " + extra if extra else ""}" role="img" aria-label="{label}">{"".join(parts)}</div>'


def code_bar(v, extra=""):
    return bar("code", v.total_code, v.linked_pct, v.code_pct, v.fuzzy_pct, v.linked_code, v.matched_code, extra)


def legend(kind="code"):
    return (
        f'<p class="legend {kind}" aria-hidden="true"><span><i class="sw linked"></i>Fully linked</span>'
        '<span><i class="sw matched striped"></i>Decompiled</span>'
        + ('<span><i class="sw fuzzy striped"></i>Partly decompiled</span>' if kind == "code" else "")
        + "</p>"
    )


def headline(v):
    s = f"{fmt_pct(v.code_pct)} decompiled"
    if v.linked_pct > 0:
        s += f" | {fmt_pct(v.linked_pct)} fully linked"
    return s


# ---------------------------------------------------------------- directory


def banner_color(pct):
    if pct >= 100:
        return "#29c45a"
    t = pct / 100
    mix = lambda a, b: round(a + (b - a) * t)
    return f"#{mix(54, 20):02x}{mix(57, 120):02x}{mix(62, 190):02x}"


def write_banner(p):
    """The project's own progress map as a small SVG banner: original, no artwork rights involved,
    and it changes as the project progresses. Up to 160 cells (the rest are merged into one)."""
    units = sorted((u for u in p.v.units if u["total_code"] > 0), key=lambda u: -u["total_code"])
    if not units:
        return None
    if len(units) > 160:
        rest = units[159:]
        size = sum(u["total_code"] for u in rest)
        fuzzy = sum(u["total_code"] * u["fuzzy_match_percent"] for u in rest) / size
        units = units[:159] + [{"name": "", "total_code": size, "fuzzy_match_percent": fuzzy, "is_linked": False}]
    cells = layout_units(units)
    W, H = 400, 100
    rects = "".join(
        f'<rect x="{c["x"] * W:.1f}" y="{c["y"] * H:.1f}" width="{max(c["w"] * W - 0.6, 0.3):.1f}" '
        f'height="{max(c["h"] * H - 0.6, 0.3):.1f}" fill="{banner_color(c["p"])}"/>'
        for c in cells
    )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none">{rects}</svg>'
    rel = "/".join([p.owner, p.repo, "banner.svg"])
    write(rel, svg)
    return rel


def card(p, now):
    v = p.v
    art = f'<div class="project-art" aria-hidden="true"><img src="/{e(p.image)}" alt="" loading="lazy"></div>' if p.image else ""
    role = f' <span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    terms = [p.title, p.name, p.game_title, p.description, p.owner, p.repo, f"{p.owner}/{p.repo}", PLAT.get(p.platform, {}).get("name", "")]
    for o in p.others:
        terms += [o.name, o.owner, o.repo, f"{o.owner}/{o.repo}"]
    search = " ".join(t for t in terms if t).lower()
    more = len(p.others)
    others = f' <span class="tag subtle" title="Other repositories for this game">+{more}</span>' if more else ""
    archived = ' <span class="tag subtle">Last known</span>' if v.archived else ""
    stats = f"{fmt_pct1(v.code_pct)} decompiled"
    if v.linked_pct > 0:
        stats += f" · {fmt_pct1(v.linked_pct)} linked"
    alt = f'<p class="project-alt muted">{e(p.alt)}</p>' if p.alt else ""
    return (
        f'<article class="project" data-card data-platform="{e(p.platform)}" data-name="{e(p.title.lower())}" '
        f'data-updated="{e(v.commit["timestamp"])}" data-percent="{v.code_pct:.4f}" data-matched="{v.matched_code}" '
        f'data-size="{v.total_code}" data-search="{e(search)}">{art}'
        f'<div class="project-head"><h2><a class="project-link" data-card-link href="{p.url()}">{e(p.title)}</a>{role}{others}{archived}</h2>'
        f"{plat_badge(p.platform, link=True)}</div>{alt}"
        f'<p class="project-stats"><span>{stats}</span> <span title="Time since the last commit with a progress report">'
        f'· last commit <time data-ago="short" datetime="{e(v.commit["timestamp"])}">{ago_short(v.updated, now)}</time></span></p>'
        f'{code_bar(v, "thin")}</article>'
    )


def build_index(site, projects, built):
    now = built
    counts = {}
    for p in projects:
        counts[p.platform] = counts.get(p.platform, 0) + 1
    items = "".join(
        '<li class="plat-item"><label>'
        f'<input type="checkbox" name="platform" value="{k}" checked>{plat_badge(k)}'
        f'<span class="plat-name">{e(PLAT[k]["name"])}</span><span class="count">{counts[k]}</span></label>'
        f'<button type="button" class="secondary outline" aria-label="Show only {e(PLAT[k]["name"])}">Only</button></li>'
        for k, *_ in PLATFORMS
        if k in counts
    )
    plat_dd = (
        '<details class="dropdown plat-list"><summary aria-label="Systems: All systems">All systems</summary><ul>'
        f'<li><a href="/" data-all>All systems</a></li>{items}</ul></details>'
    )
    sort_dd = (
        '<details class="dropdown sort-list"><summary aria-label="Sort: Recently updated">Recently updated</summary><ul>'
        + "".join(f'<li><a href="/?sort={k}" data-sort="{k}">{e(l)}</a></li>' for k, l in SORTS)
        + "</ul></details>"
    )
    view = (
        '<div class="view-toggle" role="group" aria-label="Layout">'
        '<button type="button" class="secondary outline" data-view="cards" aria-pressed="true">Cards</button>'
        '<button type="button" class="secondary outline" data-view="list" aria-pressed="false">List</button></div>'
    )
    ordered = sorted(projects, key=lambda p: p.v.commit["timestamp"], reverse=True)
    intro = site["intro"]
    complete = sum(1 for p in projects if p.v.code_pct >= 99.995)
    systems = len({p.platform for p in projects})
    repos = len(projects) + sum(len(p.others) for p in projects)
    body = (
        f'<hgroup class="intro"><h1>{e(intro["heading"])}</h1><p>{e(intro["text"])}</p></hgroup>'
        f'<p class="stats"><strong>{len(projects)}</strong> games · <strong>{complete}</strong> complete · '
        f'<strong>{systems}</strong> systems · <strong>{repos}</strong> repositories · refreshed every 6 hours</p>'
        '<div class="filters">'
        '<input type="search" id="search" placeholder="Search games, teams, repositories…" aria-label="Search games">'
        f"{plat_dd}{sort_dd}</div>"
        f'<div class="list-bar"><p class="muted result-count" id="result-count" aria-live="polite">{len(projects)} games</p>{view}</div>'
        f"{legend()}"
        f'<div id="directory">{"".join(card(p, now) for p in ordered)}</div>'
        '<p id="empty" class="notice" hidden>No games match. <a href="/" id="clear-filters">Clear the search and filters</a></p>'
        '<noscript><p class="muted"><small>Search, filters and sorting need JavaScript; every game is listed above, newest first.</small></p></noscript>'
    )
    html = page(site, built, f"{site['name']} · {site['tagline']}", body)
    write("index.html", html)
    write("projects/index.html", html)


def build_systems(site, projects, built):
    groups = {}
    for p in projects:
        groups.setdefault(p.platform, []).append(p)
    by_maker = {}
    for k, name, short, maker in PLATFORMS:
        ps = groups.get(k)
        if not ps:
            continue
        total = sum(p.v.total_code for p in ps if not p.v.archived)
        matched = sum(p.v.matched_code for p in ps if not p.v.archived)
        linked = sum(p.v.linked_code for p in ps if not p.v.archived)
        done = sum(1 for p in ps if p.v.code_pct >= 99.995)
        top = sorted(ps, key=lambda p: -p.v.code_pct)[:5]
        share = pct(matched, total)
        meter = (
            bar("code", total, pct(linked, total), share)
            + f'<p class="sys-number"><strong>{fmt_pct1(share)}</strong> of {fmt_size(total)} of code decompiled</p>'
            if total else '<p class="sys-number muted">No live reports yet</p>'
        )
        by_maker.setdefault(maker, []).append(
            f'<article class="sys-card" data-card><h3><a data-card-link href="/?platform={k}">{plat_badge(k)} {e(name)}</a></h3>'
            f'<p class="muted"><small>{len(ps)} game{"s" if len(ps) != 1 else ""}'
            f'{f" · {done} complete" if done else ""}</small></p>{meter}'
            '<ul class="sys-top">'
            + "".join(f'<li><a href="{p.url()}">{e(p.title)}</a> <span class="muted">{fmt_pct1(p.v.code_pct)}</span></li>' for p in top)
            + "</ul></article>"
        )
    sections = "".join(
        f'<section><h2 class="maker">{e(m)}</h2><div class="systems">{"".join(by_maker[m])}</div></section>'
        for m in MAKERS + sorted(set(by_maker) - set(MAKERS))
        if m in by_maker
    )
    body = (
        '<hgroup class="intro"><h1>Systems</h1><p class="muted">Every system with a tracked decompilation. '
        "Each bar shows how much of that system&rsquo;s code is decompiled, across games with a live report.</p></hgroup>"
        f"{legend('sys')}{sections}"
    )
    write("systems/index.html", page(site, built, f"Systems · {site['name']}", body, "Matching decompilation progress by game system."))


# ---------------------------------------------------------------- treemap layout


def squarify(sizes, x, y, w, h):
    rects = []
    i = 0
    while i < len(sizes):
        short = min(w, h)
        row = [sizes[i]]
        j = i + 1

        def worst(r):
            s = sum(r)
            return max(max(short * short * v / (s * s), (s * s) / (short * short * v)) for v in r)

        while j < len(sizes) and worst(row + [sizes[j]]) <= worst(row):
            row.append(sizes[j])
            j += 1
        s = sum(row)
        if w >= h:
            rw = s / h
            yy = y
            for v in row:
                rects.append((x, yy, rw, v / rw))
                yy += v / rw
            x += rw
            w -= rw
        else:
            rh = s / w
            xx = x
            for v in row:
                rects.append((xx, y, v / rh, rh))
                xx += v / rh
            y += rh
            h -= rh
        i = j
    return rects


def layout_units(units):
    units = sorted((u for u in units if u["total_code"] > 0), key=lambda u: -u["total_code"])
    total = sum(u["total_code"] for u in units)
    if not total:
        return []
    sizes = [u["total_code"] / total * 2.0 for u in units]  # canvas is 2:1
    return [
        {
            "n": u["name"],
            "s": u["total_code"],
            "p": round(u["fuzzy_match_percent"], 2),
            "l": u["is_linked"],
            "x": round(x / 2, 6),
            "y": round(y, 6),
            "w": round(w / 2, 6),
            "h": round(h, 6),
        }
        for u, (x, y, w, h) in zip(units, squarify(sizes, 0.0, 0.0, 2.0, 1.0))
    ]


# ---------------------------------------------------------------- project pages


def version_switch(p, v, suffix=""):
    if len(p.versions) < 2:
        return ""
    lis = "".join(
        f'<li><strong aria-current="true">{e(build_label(o.id) or o.id)} ✓</strong></li>' if o is v else
        f'<li><a href="{p.url(*([] if o is p.versions[0] else [o.id]), *([suffix] if suffix else []))}">{e(build_label(o.id) or o.id)}</a></li>'
        for o in p.versions
    )
    label = build_label(v.id) or v.id
    return (f'<details class="dropdown version-switch"><summary aria-label="Build: {e(label)}. Choose another build">'
            f'{e(label)}</summary><ul>{lis}</ul></details>')


def slot_notice(p):
    """Where this repository stands for its game: listed, or behind another repository."""
    out = ""
    if p.disputed:
        others = ", ".join(e(k) for k in p.disputed.get("replaced_by", []))
        out += (
            '<div class="notice error"><small>This repository no longer represents its game: '
            f"{len(p.disputed.get('replaced_by', []))} independent repositories ({others}) name the same build of the game, "
            "agree with each other, and don&rsquo;t match this one&rsquo;s code size.</small></div>"
        )
    if p.contested_by:
        out += (
            '<div class="notice error"><small>Contested: '
            + ", ".join(e(k) for k in p.contested_by)
            + " independently name the same build of this game with a different code size. As one of the original "
            "listings it stays listed; check its repository before relying on it.</small></div>"
        )
    if p.contesters:
        out += (
            f'<div class="notice"><small>{len(p.contesters)} repositor{"y" if len(p.contesters) == 1 else "ies"} '
            f"contest{'s' if len(p.contesters) == 1 else ''} this game&rsquo;s identity: "
            + ", ".join(e(f"{c.owner}/{c.repo}") for c in p.contesters)
            + f". {slots.DISPUTE_QUORUM} that agree with each other replace this listing.</small></div>"
        )
    return out + _slot_notice(p)


def _slot_notice(p):
    if p.leader is not p:
        lead = p.leader
        out = (
            f'<div class="notice"><small>Another repository has more progress on this game, so the directory lists '
            f'<a href="{lead.url()}">{e(lead.owner)}/{e(lead.repo)}</a> ({fmt_pct(lead.v.code_pct)}) instead of this one '
            f"({fmt_pct(p.v.code_pct)}). The game&rsquo;s slot goes to whichever repository leads by "
            f"{slots.TAKEOVER_MARGIN:g}% for {slots.TAKEOVER_DAYS} days.</small>"
        )
        parent = (lead.meta.get("parent") or "").lower()
        if parent == f"{p.owner}/{p.repo}".lower():
            branch = p.meta.get("default_branch", "main")
            lead_branch = lead.meta.get("default_branch", "main")
            compare = f"https://github.com/{p.owner}/{p.repo}/compare/{branch}...{lead.owner}:{lead.repo}:{lead_branch}"
            out += (
                f'<p><a role="button" class="secondary" href="{e(compare)}" target="_blank" rel="noopener">'
                f"Open a pull request with {e(lead.owner)}&rsquo;s progress</a></p>"
            )
        return out + "</div>"
    if not p.others:
        return ""
    rows = "".join(
        f'<li><a href="{o.url()}">{e(o.owner)}/{e(o.repo)}</a> <span class="muted">{fmt_pct(o.v.code_pct)}</span>'
        + (
            f' <span class="tag subtle">ahead since {e(p.challengers[k][:10])}</span>'
            if (k := f"{o.owner}/{o.repo}".lower()) in p.challengers
            else ""
        )
        + "</li>"
        for o in p.others
    )
    return f'<div class="notice"><small>Other repositories for this game:</small><ul><small>{rows}</small></ul></div>'


def build_report(site, p, v, base, built):
    crumbs = [(p.title, p.url())]
    role = f' <span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    archived_repo = ' <span class="tag subtle">Repository archived</span>' if p.meta.get("archived") else ""
    last_known = f' <span class="tag warn">Last known numbers ({v.updated.strftime("%Y-%m-%d")})</span>' if v.archived else ""
    actions = (
        '<div class="report-actions">'
        f'<a class="secondary outline" role="button" href="{p.url()}history/">History</a>'
        f'<a class="secondary outline" role="button" href="{e(p.repository)}" target="_blank" rel="noopener">Repository ↗</a>'
        "</div>"
    )
    alt = f'<p class="report-alt muted"><small>Repository name: {e(p.alt)}</small></p>' if p.alt else ""
    stat = f'<p class="headline"><strong>{fmt_pct(v.code_pct)}</strong> decompiled'
    if v.linked_pct > 0:
        stat += f' · <span class="muted">{fmt_pct(v.linked_pct)} fully linked</span>'
    stat += "</p>"
    desc = f'<p class="report-desc">{e(p.description)}</p>' if p.description else ""
    plat = PLAT.get(p.platform, {"name": p.platform})["name"]
    label = build_label(v.id)
    facts = (f'<p class="muted facts"><small>{plat_badge(p.platform)} {e(plat)}{f" · {e(label)}" if label and len(p.versions) < 2 else ""}'
             f' · <a href="{e(p.repository)}" target="_blank" rel="noopener">{e(p.owner)}/{e(p.repo)}</a>')
    if v.functions[1]:
        facts += f" · {v.functions[0]:,} / {v.functions[1]:,} functions decompiled"
    facts += "</small></p>"

    notice = slot_notice(p)
    if v.archived:
        notice += (
            '<div class="notice"><small>No live report could be read from this project&rsquo;s GitHub Actions (GitHub deletes '
            "artifacts after 90 days), so these are the last known numbers, from the old decomp.dev index "
            f"({v.updated.strftime('%Y-%m-%d')}). They update by themselves once the project publishes a new report.</small></div>"
        )

    code = (f'<h2 class="section">Code {f"<small class=muted>({fmt_size(v.total_code)})</small>" if v.total_code else ""}</h2>'
            f"{code_bar(v)}")
    data = ""
    if v.total_data:
        data = (
            f'<h2 class="section">Data <small class="muted">({fmt_size(v.total_data)})</small></h2>'
            + bar("data", v.total_data, pct(v.linked_data, v.total_data), pct(v.matched_data, v.total_data), None, v.linked_data, v.matched_data)
        )
    sha = v.commit.get("sha", "")
    commit = ""
    if sha:
        msg = f" {e(v.commit.get('message', '').strip())}" if v.commit.get("message") else ""
        commit = (
            f'<h2 class="section">Latest commit</h2><p class="commit"><a href="{e(p.commit_url(sha))}" target="_blank" rel="noopener">'
            f'<code>{e(sha[:7])}</code></a>{msg}</p>'
            f'<p class="muted"><small>Committed <time data-ago datetime="{e(v.commit["timestamp"])}">{ago(v.updated, built)}</time></small></p>'
        )
    units = ""
    if not hasattr(v, "_layout"):
        v._layout = layout_units(v.units)
    layout = v._layout
    scripts = []
    if layout:
        data_base = base  # the default build's units sit at the project URL
        write(base.strip("/") + "/units.json", json.dumps(layout, separators=(",", ":")))
        head_ = f'<h2 class="section">Source files <small class="muted">({len(layout):,})</small></h2>'
        if all(u["p"] >= 100 for u in layout):
            units = head_ + f'<p class="notice">All {len(layout):,} source files are fully decompiled. Nothing left to map.</p>'
        else:
            done = sum(1 for u in layout if u["p"] >= 100)
            units = (
                head_
                + '<input id="unit-filter" type="search" placeholder="Filter files, e.g. camera <70% >10kb linked" aria-label="Filter source files">'
                f'<div class="treemap-wrap"><canvas id="treemap" data-src="{data_base}units.json" role="img" aria-label="Map of {len(layout):,} source files sized by code: '
                f'{done:,} fully decompiled"></canvas><p id="treemap-status" class="treemap-status muted" aria-live="polite">Loading source files…</p></div>'
                '<div class="tm-legend" aria-hidden="true"><span>0%</span><i class="tm-grad"></i><span>99%</span>'
                '<i class="sw tm-done"></i><span>100% decompiled</span></div>'
                '<p class="muted"><small>Each box is a source file, sized by its code; hover or touch one for details.</small></p>'
                f'<noscript><p class="muted"><small>The map needs JavaScript. The same data is at <a href="{data_base}units.json">units.json</a>.</small></p></noscript>'
            )
            scripts.append("/static/js/treemap.js")

    head_block = (
        f'<div class="report-top"><div class="report-head"><h1>{e(p.title)}{role}{archived_repo}{last_known}</h1>{alt}{stat}</div>{actions}</div>'
    )
    body = f"{head_block}{desc}{facts}{version_switch(p, v)}{notice}{legend()}{code}{data}{commit}{units}"
    short = PLAT.get(p.platform, {}).get("short", p.platform)
    title = f"{p.title} ({short}) · {site['name']}"
    src_repo = ((p.raw.get("source") or {}).get("repo") or f"{p.owner}/{p.repo}").lower()
    body = f'<div id="fresh" data-repo="{e(src_repo)}" data-sha="{e(p.v.commit.get("sha", ""))}" aria-live="polite" hidden></div>' + body
    scripts.append("/static/js/fresh.js")
    write(
        base.strip("/") + "/index.html",
        page(site, built, title, body, f"{p.title} ({PLAT.get(p.platform, {}).get('name', short)}) is {fmt_pct(v.code_pct)} decompiled.",
             crumbs, scripts=scripts, image=p.og_image),
    )


def build_history(site, p, v, base, built):
    if base == p.url():
        write("/".join([p.owner, p.repo, "history.json"]), json.dumps(p.history, separators=(",", ":")))
    crumbs = [(p.title, base), ("History", base + "history/")]
    archive_points = sum(1 for h in p.history if h.get("source") == "archive")
    note = ""
    if archive_points:
        note = (
            f'<p class="muted"><small>{archive_points} earlier point{"s" if archive_points != 1 else ""} come from monthly '
            "snapshots of the old decomp.dev index; newer points come from this project&rsquo;s own progress reports.</small></p>"
        )
    body = (
        f'<hgroup class="intro"><h1>{e(p.title)}: history</h1><p class="muted">Progress over time. Click a series name to show or hide it.</p></hgroup>'
        f'<div id="chart" data-src="{p.url()}history.json" aria-live="polite"><p class="muted">Loading history…</p></div>'
        '<noscript><p class="muted"><small>The chart needs JavaScript. The data is at '
        f'<a href="{p.url()}history.json">history.json</a>.</small></p></noscript>{note}'
        f'<p><a role="button" class="secondary" href="{base}">Back to {e(p.title)}</a></p>'
    )
    write(
        base.strip("/") + "/history/index.html",
        page(
            site,
            built,
            f"{p.title} history · {site['name']}",
            body,
            f"Decompilation progress history for {p.title}",
            crumbs,
            extra_head='<link rel="stylesheet" href="/static/vendor/uPlot.min.css"><script src="/static/vendor/uPlot.iife.min.js" defer></script>',
            scripts=["/static/js/history.js"],
            image=p.og_image,
        ),
    )


def ext(url, text):
    return f'<a href="{url}" target="_blank" rel="noopener">{text}</a>'


def build_about(site, projects, leaders, built):
    live = sum(1 for p in projects if not p.v.archived)
    name = e(site["name"])
    body = f"""
<hgroup class="intro"><h1>About {name}</h1><p class="muted">{e(site['tagline'])}</p></hgroup>
<p>{name} is a directory and leaderboard of <strong>matching decompilation</strong> projects: games being rewritten in C or C++
so that the source compiles back into exactly the original executable, byte for byte. Each game is listed once, with progress
read straight from the project&rsquo;s own builds. Everything runs by itself: projects are added, updated and removed without anyone
reviewing them by hand.</p>
<p>Right now: {len(leaders)} games from {len(projects)} repositories, {live} of them with a live progress report.</p>
<nav class="toc" aria-label="On this page"><ul>
<li><a href="#terms">Terms</a></li><li><a href="#numbers">Where the numbers come from</a></li>
<li><a href="#listing">One listing per game</a></li><li><a href="#adding">Adding and removing</a></li>
<li><a href="#history">History</a></li><li><a href="#sources">Data sources</a></li><li><a href="#privacy">Privacy</a></li>
<li><a href="#agents">For scripts and agents</a></li></ul></nav>

<h2 id="terms">Terms</h2>
<dl class="glossary">
<dt><i class="sw matched striped" aria-hidden="true"></i>Decompiled</dt>
<dd>Code whose rewritten source compiles to exactly the same machine code as the original. The main percentage is the share of
the game&rsquo;s code, measured in bytes, that matches.</dd>
<dt><i class="sw linked" aria-hidden="true"></i>Fully linked</dt>
<dd>Decompiled code in source files that are finished and built from source in the project&rsquo;s own build, rather than
taken from the original binary.</dd>
<dt><i class="sw fuzzy striped" aria-hidden="true"></i>Partly decompiled</dt>
<dd>Functions that are written but don&rsquo;t match exactly yet, counted by how close they are. Shown as the faint stripes at the
end of a bar.</dd>
<dt>Complete</dt>
<dd>100% of the code decompiled.</dd>
<dt>Source file (unit)</dt>
<dd>One file of the project&rsquo;s source, usually a single <code>.c</code> or <code>.cpp</code>. The map on each project page
has one box per file, sized by how much code it holds.</dd>
<dt>Build</dt>
<dd>One specific executable of a game, such as the USA or Japan release. A project can track several.</dd>
</dl>

<h2 id="numbers">Where the numbers come from</h2>
<p>Each project&rsquo;s own GitHub Actions build the game and upload a progress report in the
{ext("https://github.com/encounter/objdiff", "objdiff")} format. {name} reads the newest report from the default branch.</p>
<ul>
<li>Every project is refreshed every 6 hours.</li>
<li>Opening a project page asks GitHub (at most once every 10 minutes per project) whether there is newer progress. If there is,
that project is refreshed (about 2 minutes) and the page reloads by itself. This happens up to 4 times a day per project
and 100 times a day across the site; beyond that, the 6-hourly refresh picks it up.</li>
<li>GitHub deletes build artifacts after 90 days. A project that hasn&rsquo;t published a newer report keeps its last known
numbers, labelled as such on its page.</li>
</ul>

<h2 id="listing">One listing per game</h2>
<p>Several repositories can work on the same game. They share one listing: the one with the most code decompiled leads, and the
others are shown on its page.</p>
<ul>
<li><strong>Taking the lead.</strong> Another repository takes the listing after leading by at least {slots.TAKEOVER_MARGIN:g}
percentage point for {slots.TAKEOVER_DAYS} days in a row. A submitted repository must also have been listed for
{slots.CHALLENGER_MIN_DAYS} days. A fork can&rsquo;t take its parent&rsquo;s place; its page offers a link to open a pull request instead.</li>
<li><strong>Comparing builds.</strong> Repositories are compared build to build. The same build ID with the same code size is the
same executable. Different builds, such as USA and Japan, are both fine. The same build ID with a different code size is a conflict.</li>
<li><strong>Contesting.</strong> A submission that conflicts with the listed repository is shown as <em>contesting</em> on the
game&rsquo;s page. {slots.DISPUTE_QUORUM} independent contesters (different owners, no shared commits, repositories at least
{slots.CONTESTER_MIN_REPO_DAYS} days old) that agree with each other replace a submitted listing.</li>
<li><strong>Original listings</strong> (the ones inherited from decomp.dev) are never removed this way; they are marked
<em>contested</em> instead.</li>
</ul>

<h2 id="adding">Adding and removing projects</h2>
<p><strong>Adding</strong> is automatic: on <a href="/submit/">Add a project</a>, sign in with GitHub and paste the repository.
It is checked within a few minutes; if it passes, it appears in the directory, or on its game&rsquo;s page when the game is already listed.</p>
<p><strong>Removing</strong> is automatic too. A repository that is deleted or made private disappears after three refreshes in a row.
Archived repositories stay, tagged &ldquo;Repository archived&rdquo;. There is nobody to ask: the site only shows what is public on GitHub.</p>

<h2 id="history">History</h2>
<p>{name} picks up where decomp.dev left off; its author retired that index in October 2026. The starting list of
projects and the older history points were recovered from public
{ext("https://web.archive.org/", "Internet Archive")} snapshots of the decomp.dev index. Only facts were taken: names,
repositories, systems and percentages. None of decomp.dev&rsquo;s code is used; this site&rsquo;s code is its own.</p>

<h2 id="sources">Data sources and licences</h2>
<ul>
<li>Game lists from {ext("https://www.no-intro.org/", "No-Intro")} and {ext("http://redump.org/", "Redump")}, plus DOS.dat, via
{ext("https://github.com/libretro/libretro-database", "libretro-database")}
({ext("https://creativecommons.org/licenses/by-sa/4.0/", "CC BY-SA 4.0")}).</li>
<li>Arcade sets from {ext("https://www.mamedev.org/", "MAME")}.</li>
<li>Switch titles from {ext("https://github.com/blawar/titledb", "titledb")} (MIT).</li>
<li>Windows games from {ext("https://www.wikidata.org/", "Wikidata")} (CC0).</li>
<li>Built with {ext("https://picocss.com", "Pico CSS")} and {ext("https://github.com/leeoniya/uPlot", "uPlot")} (MIT,
<a href="/static/vendor/LICENSES.txt">licences</a>).</li>
<li>Card banners are drawn from each project&rsquo;s own progress map; no game artwork is used. Game names belong to their owners,
and all credit for the decompilations goes to the people doing them.</li>
</ul>

<h2 id="privacy">Privacy</h2>
<ul>
<li>No analytics, no ads, no third-party scripts.</li>
<li>Signing in with GitHub (only needed to add a project) sets a cookie for 7 days holding your GitHub username and numeric ID.
{name} sees only your public profile.</li>
<li>Submissions are public: your GitHub username is shown next to each one on <a href="/submit/">Add a project</a>.</li>
<li>Your theme and list layout choices stay in your own browser.</li>
</ul>

<h2 id="agents">For scripts and agents</h2>
<p>Start at <a href="/llms.txt"><code>/llms.txt</code></a>. Every page is static, and the data behind it is plain JSON:</p>
<ul>
<li><code>/&lt;owner&gt;/&lt;repo&gt;/history.json</code>: progress over time.</li>
<li><code>/&lt;owner&gt;/&lt;repo&gt;/&lt;build&gt;/units.json</code>: every source file of a build with its size and percentage decompiled.</li>
<li><a href="/fresh.json"><code>/fresh.json</code></a>: the commit each project page was built from.</li>
<li><a href="/submissions.json"><code>/submissions.json</code></a>: recent submissions and their results.</li>
</ul>
"""
    write("about/index.html", page(site, built, f"About · {site['name']}", body,
                                    "What decomp.fun is, where its numbers come from, and how listings work."))


STATUS_LABELS = {"listed": "Accepted", "contesting": "Contesting", "already": "Already listed", "rejected": "Not accepted"}


ACTIONS_EXAMPLE = """# .github/workflows/build.yml (the part decomp.fun reads)
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        version: [GALE01]          # one entry per build of the game
    steps:
      - uses: actions/checkout@v4
      # ... build the game and write build/${{ matrix.version }}/report.json
      #     (dtk-template: ninja build/${{ matrix.version }}/report.json)
      - uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.version }}_report
          path: build/${{ matrix.version }}/report.json"""


def status_pill(status):
    return f'<span class="status status-{e(status or "rejected")}">{STATUS_LABELS.get(status, "Not accepted")}</span>'


def write_game_titles():
    """Compact titles-only lists for the Add a project typeahead (Windows, DOS, Switch)."""
    import submit as sub

    for k in sub.NAME_ONLY:
        path = DATA / "games" / f"{k}.json"
        if path.exists():
            titles = sorted({g["title"] for g in json.loads(path.read_text()).get("games", [])}, key=str.lower)
            write(f"static/games/{k}.json", json.dumps(titles, ensure_ascii=False, separators=(",", ":")))


def build_submit(site, built):
    import submit as sub
    from fetch import load_submissions

    recent = sorted(load_submissions(), key=lambda x: x.get("submitted_at", ""), reverse=True)[:100]
    # Read by the Add a project page to show a result as soon as its check run deploys.
    write("submissions.json", json.dumps([
        {"repo": x["repo"], "status": x.get("status"), "reasons": x.get("reasons", []),
         "game": x.get("game", {}).get("title", ""), "submitted_at": x.get("submitted_at", ""),
         "last_check": x.get("last_check"),
         "url": f'/{x["project"]["owner"]}/{x["project"]["repo"]}/' if x.get("status") == "listed" and x.get("project") else None}
        for x in recent
    ], separators=(",", ":")))
    rows = []
    for x in recent:
        listed = x.get("status") == "listed"
        game = clean_title(x.get("game", {}).get("title", ""))
        name = f'<a href="/{e(x["project"]["owner"])}/{e(x["project"]["repo"])}/">{e(x["repo"])}</a>' if listed and x.get("project") else e(x["repo"])
        reasons = "".join(f"<li>{e(r)}</li>" for r in x.get("reasons", []))
        rows.append(
            f"<tr><td>{name}{f'<br><small class=muted>{e(game)}</small>' if game else ''}</td>"
            f"<td>{status_pill(x.get('status'))}</td>"
            f"<td><small>{f'<ul>{reasons}</ul>' if reasons else 'All checks passed.'}</small></td>"
            f'<td><small class="muted">{e(x.get("by", ""))}<br>{e(x.get("submitted_at", "")[:10])}</small></td></tr>'
        )
    table = (
        '<div class="overflow-auto"><table class="striped submissions"><thead><tr><th scope="col">Repository</th><th scope="col">Result</th>'
        f'<th scope="col">Details</th><th scope="col">By</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        if rows else '<p class="muted">No submissions yet.</p>'
    )
    options = "".join(f'<option value="{k}">{e(PLAT[k]["name"])}</option>' for k in sub.NAME_ONLY)
    body = f"""
<hgroup class="intro"><h1>Add a project</h1><p class="muted">Checks run automatically, usually within a few minutes. Nobody reviews submissions by hand.</p></hgroup>
<h2>What it needs</h2>
<ol class="requirements">
<li><strong>A public GitHub repository.</strong></li>
<li><strong>A progress report from its own GitHub Actions.</strong> A workflow on the default branch uploads an
{ext("https://github.com/encounter/objdiff", "objdiff")} report as an artifact with <code>report</code> in its name. Projects made
from {ext("https://github.com/encounter/dtk-template", "dtk-template")} already do this. Runs from pull requests of forks don&rsquo;t count.</li>
<li><strong>At least {sub.MIN_MATCHED_PERCENT:g}% of the code decompiled.</strong></li>
<li><strong>The code is really there.</strong> At least half of the source files the report counts as decompiled must exist in the repository.</li>
<li><strong>The game can be recognised.</strong> The repository contains its disc ID, serial, title ID or ROM hash, as decomp projects
normally do. Windows, DOS and Switch games: choose the system below and type the game&rsquo;s exact name.</li>
<li><strong>A GitHub account at least 30 days old.</strong> Up to 3 submissions per account per day.</li>
</ol>
<details class="example"><summary>Example GitHub Actions step</summary>
<pre><code>{e(ACTIONS_EXAMPLE)}</code></pre></details>
<p class="muted"><small>If the game is already listed, your repository joins its page and is compared build to build; it takes the
listing after leading by {slots.TAKEOVER_MARGIN:g} percentage point for {slots.TAKEOVER_DAYS} days in a row (and being listed
{slots.CHALLENGER_MIN_DAYS} days). See <a href="/about/#listing">one listing per game</a>.</small></p>

<article class="submit-box">
<h2>Submit a repository</h2>
<div id="signed-out"><p>Sign in with GitHub to submit. {e(site['name'])} only sees your public profile; your username is shown next to your submission.</p>
<a role="button" href="/auth/login">Sign in with GitHub</a></div>
<div id="signed-in" hidden>
<p class="muted"><small>Signed in as <strong id="who"></strong> · <a href="/auth/logout">Sign out</a></small></p>
<form id="submit-form">
<label>Repository<input name="repo" placeholder="owner/name or https://github.com/owner/name" required autocomplete="off" spellcheck="false"></label>
<label>Windows, DOS or Switch game?<select name="system" id="system"><option value="">No, it&rsquo;s a console or arcade game</option>{options}</select></label>
<label id="game-row" hidden>Game name, exactly as published<input name="game" id="game" list="game-list" placeholder="Start typing, e.g. Thief: Deadly Shadows" autocomplete="off">
<small id="game-hint" class="muted">Pick a name from the list.</small></label>
<datalist id="game-list"></datalist>
<button type="submit">Submit</button>
</form>
</div>
<noscript><p class="muted"><small>Submitting needs JavaScript.</small></p></noscript>
<div id="submit-result" class="notice" role="status" aria-live="polite" hidden></div>
</article>

<h2>After you&rsquo;re listed</h2>
<p>Your project gets its own page at <code>decomp.fun/&lt;owner&gt;/&lt;repo&gt;/</code>. It refreshes every 6 hours and whenever
someone opens the page after you push new progress. Nothing else to do. Archiving the repository tags it as archived;
making it private or deleting it removes it from the site automatically.</p>

<h2>Recent submissions</h2>
<p class="legend-status"><small>{" ".join(status_pill(k) for k in STATUS_LABELS)}</small></p>
{table}
"""
    write("submit/index.html", page(site, built, f"Add a project · {site['name']}", body,
                                     "Add a matching decompilation project to decomp.fun. Checks are automatic.",
                                     scripts=["/static/js/submit.js"]))


def build_fresh(projects):
    """What every project page was built from, so the Worker can tell when GitHub has newer progress."""
    out = {}
    for p in projects:
        src = p.raw.get("source") or {}
        repo = src.get("repo") or f"{p.owner}/{p.repo}"
        out[repo.lower()] = {
            "sha": p.v.commit.get("sha", ""),
            "kind": src.get("type", "auto"),
            "branch": src.get("branch") or p.meta.get("default_branch", "main"),
            "artifact": src.get("artifact"),
            "path": src.get("path"),
        }
    write("fresh.json", json.dumps(out, separators=(",", ":")))


def build_404(site, built):
    body = (
        '<hgroup class="intro"><h1>Page not found</h1><p class="muted">That page doesn&rsquo;t exist, or the project has moved or '
        "been removed.</p></hgroup>"
        "<p>Project pages live at <code>/&lt;owner&gt;/&lt;repo&gt;/</code>, the same as on GitHub, for example "
        '<a href="/zeldaret/tww/"><code>/zeldaret/tww/</code></a>.</p>'
        '<form action="/" method="get" role="search" class="search-404"><input type="search" name="q" '
        'placeholder="Search games, teams, repositories…" aria-label="Search games"><button type="submit">Search</button></form>'
        '<p><a href="/">All games</a> · <a href="/systems/">Systems</a> · <a href="/submit/">Add a project</a></p>'
    )
    write("404.html", page(site, built, f"Not found · {site['name']}", body))


def build_sitemap():
    urls = sorted(set(p for p in PAGES if p != "/projects/"))
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"<url><loc>{e(SITE_URL + u)}</loc></url>\n" for u in urls) + "</urlset>\n")
    write("robots.txt", f"User-agent: *\nAllow: /\nDisallow: /auth/\nDisallow: /api/\n\nSitemap: {SITE_URL}/sitemap.xml\n")


def security_headers():
    """Cloudflare static-asset headers: CSP (only our own scripts plus the hashed theme snippet),
    no framing, HTTPS only, and long caching for static files."""
    import base64
    import hashlib

    theme = THEME_INIT.removeprefix("<script>").removesuffix("</script>")
    digest = base64.b64encode(hashlib.sha256(theme.encode()).digest()).decode()
    csp = (
        "default-src 'none'; "
        f"script-src 'self' 'sha256-{digest}'; "
        "style-src 'self' 'unsafe-inline'; "  # progress bar widths are inline styles
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    write("_headers", (
        "/*\n"
        f"  Content-Security-Policy: {csp}\n"
        "  Strict-Transport-Security: max-age=31536000; includeSubDomains\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()\n"
        "/static/*\n"
        "  Cache-Control: public, max-age=86400\n"
        "/*.json\n"
        "  Cache-Control: public, max-age=60\n"
    ))


def main():
    site = json.loads((DATA / "site.json").read_text())
    built = datetime.now(timezone.utc).replace(microsecond=0)
    projects = [Project(p) for p in all_projects()]
    gone = [p.id for p in projects if p.meta.get("missing")]
    projects = [p for p in projects if not p.meta.get("missing")]
    skipped = [p.id for p in projects if not p.versions]
    projects = [p for p in projects if p.versions]

    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(ROOT / "public", DIST)
    for p in projects:
        p.image = write_banner(p)

    contesters = [c for c in (Project(p) for p in contesting_projects()) if c.versions]
    # Only the scheduled refresh records slot state; submission/refresh runs build with what's committed.
    record = os.environ.get("GITHUB_EVENT_NAME") != "repository_dispatch"
    assigned, promoted = slots.assign(projects, contesters, record=record)
    for c in promoted:  # won a dispute: now a listed project
        sub_path = DATA / "submissions" / (f"{c.owner}__{c.repo}".lower() + ".json")
        if sub_path.exists():
            sub = json.loads(sub_path.read_text())
            sub.update(status="listed", reasons=["Replaced a listed project whose game didn't match (dispute)."])
            sub_path.write_text(json.dumps(sub, indent=1) + "\n")
        projects.append(c)
    disputed = slots.load(slots.DISPUTES, {})
    for slot in assigned.values():
        lead = slot["leader"]
        lead.others = sorted(slot["others"], key=lambda o: -o.v.code_pct)
        lead.challengers = slot["challengers"]
        lead.contesters = slot["contesters"]
        lead.contested_by = slot.get("contested_by", [])
        for o in slot["others"]:
            o.leader = lead
    ident = slots.identities()
    for p in projects:
        p.disputed = disputed.get(f"{p.owner}/{p.repo}".lower())
        p.game_title = (ident.get(f"{p.owner}/{p.repo}".lower()) or {}).get("title", "")
        p.title = display_title(p.name, p.game_title)
        p.alt = p.name if norm_title(p.name) != norm_title(p.title) else ""
        if p.v.units:
            p.og_image = write_og_image(p, layout_units(p.v.units))
    leaders = [p for p in projects if p.leader is p]

    write_touch_icon()
    write_game_titles()
    build_index(site, leaders, built)
    build_systems(site, leaders, built)
    build_about(site, projects, leaders, built)
    build_404(site, built)
    build_fresh(projects)
    security_headers()
    write("health.json", json.dumps({
        "built": built.isoformat(),
        "projects": len(projects),
        "live": sum(1 for p in projects if not p.v.archived),
        "fetch_failed": sum(1 for p in projects if p.meta.get("missing_runs")),
    }))
    build_submit(site, built)
    for p in projects:
        # The default build is the project page (its /<build>/ URL redirects there, see api.py);
        # history is per project, so only the project has a history page.
        build_history(site, p, p.v, p.url(), built)
        for i, v in enumerate(p.versions):
            build_report(site, p, v, p.url() if i == 0 else p.url(v.id), built)
    api.build(projects, assigned, built, PLAT, write)
    build_sitemap()
    live = sum(1 for p in projects if not p.v.archived)
    print(f"Built {len(projects)} projects ({live} live, {len(projects) - live} archived) into dist/; {len(leaders)} games")
    if skipped:
        print(f"Skipped (no data at all): {', '.join(skipped)}")
    if gone:
        print(f"Not listed (repository deleted or private): {', '.join(gone)}")


if __name__ == "__main__":
    main()
