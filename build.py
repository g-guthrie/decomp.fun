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
import shutil
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
    ("name", "Name"),
    ("percent", "Most decompiled (%)"),
    ("matched", "Most code decompiled"),
    ("size", "Largest games"),
]

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


def write(rel, content):
    if rel.endswith(".html") and "</head>" in content:
        path = "/" + rel.removesuffix("index.html") if rel != "404.html" else None
        if path:
            content = content.replace("</head>", f'<link rel="canonical" href="https://decomp.fun{path}"></head>', 1)
    out = DIST / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)


def plat_badge(key, link=False):
    p = PLAT.get(key, {"name": key or "Unknown", "short": (key or "?").upper()})
    if link:
        return f'<a class="plat" data-p="{e(key)}" href="/?platform={e(key)}" title="Show all {e(p["name"])} projects">{e(p["short"])}</a>'
    return f'<span class="plat" data-p="{e(key)}" title="{e(p["name"])}">{e(p["short"])}</span>'


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


def head(site, title, description=None, extra=""):
    desc = description or site["tagline"]
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title>"
        '<meta name="color-scheme" content="dark light">'
        f'<meta name="description" content="{e(desc)}">'
        f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">'
        f'<meta property="og:site_name" content="{e(site["name"])}">'
        '<meta name="theme-color" content="#181c25" media="(prefers-color-scheme: dark)">'
        '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
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
    left = f'<li><a href="/">{brand(site)}</a></li>' + "".join(f'<li><a href="{e(u)}">{e(t)}</a></li>' for t, u in crumbs)
    right = (
        '<li><a href="/systems/">Systems</a></li><li><a href="/submit/">Add a project</a></li><li><a href="/about/">About</a></li>'
        + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in site.get("nav_links", []))
        + f'<li><button type="button" id="theme-toggle" title="Switch light/dark" aria-label="Switch light or dark theme">{ICON_THEME}</button></li>'
    )
    return f"<nav><ul>{left}</ul><ul>{right}</ul></nav>"


def footer(site, built):
    return (
        '<footer class="container"><small class="muted">'
        f'{e(site["name"])} · progress is read from each project&rsquo;s own CI reports</small>'
        f'<small class="muted">Updated <time datetime="{built.isoformat()}">{built.strftime("%Y-%m-%d %H:%M UTC")}</time></small>'
        "</footer>"
    )


def page(site, built, title, body, description=None, crumbs=(), extra_head="", scripts=()):
    js = "".join(f'<script src="{s}" defer></script>' for s in ("/static/js/site.js", *scripts))
    return (
        head(site, title, description, extra_head + js)
        + f'<body><header class="container">{nav(site, crumbs)}</header>'
        + f'<main class="container">{body}</main>'
        + footer(site, built)
        + "</body></html>"
    )


def bar(kind, total, linked_pct, matched_pct, fuzzy_pct=None, linked_size=None, matched_size=None):
    parts = []
    sized = total > 0

    def size(x):
        return f" ({fmt_size(x)})" if sized and x is not None else ""

    if linked_pct > 0:
        parts.append(
            f'<span class="linked" style="width:{linked_pct:.4f}%" data-tip="{fmt_pct(linked_pct)} fully linked{size(linked_size)}"></span>'
        )
    if matched_pct - linked_pct > 0.0001:
        striped = " striped" if linked_pct > 0 else ""
        parts.append(
            f'<span class="matched{striped}" style="width:{matched_pct - linked_pct:.4f}%" '
            f'data-tip="{fmt_pct(matched_pct)} decompiled{size(matched_size)}"></span>'
        )
    if fuzzy_pct is not None and fuzzy_pct - matched_pct > 0.0001:
        parts.append(
            f'<span class="fuzzy striped" style="width:{fuzzy_pct - matched_pct:.4f}%" data-tip="{fmt_pct(fuzzy_pct)} including partly decompiled code"></span>'
        )
    return f'<div class="bar {kind}">{"".join(parts)}</div>'


def code_bar(v):
    return bar("code", v.total_code, v.linked_pct, v.code_pct, v.fuzzy_pct, v.linked_code, v.matched_code)


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
    art = f'<div class="project-art"><img src="/{e(p.image)}" alt="" loading="lazy"></div>' if p.image else ""
    role = f'<span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    sha = v.commit.get("sha", "")
    commit = f' in commit <a href="{e(p.commit_url(sha))}" target="_blank" rel="noopener">{e(sha[:7])}</a>' if sha else ""
    terms = [p.name, p.game_title, p.description, p.owner, p.repo, f"{p.owner}/{p.repo}", PLAT.get(p.platform, {}).get("name", "")]
    for o in p.others:
        terms += [o.name, o.owner, o.repo, f"{o.owner}/{o.repo}"]
    search = " ".join(t for t in terms if t).lower()
    more = len(p.others)
    others = f' <span class="tag subtle">+{more} other repo{"s" if more != 1 else ""}</span>' if more else ""
    return (
        f'<article class="project" data-platform="{e(p.platform)}" data-name="{e(p.name.lower())}" '
        f'data-updated="{e(v.commit["timestamp"])}" data-percent="{v.code_pct:.4f}" data-matched="{v.matched_code}" '
        f'data-size="{v.total_code}" data-search="{e(search)}">'
        f'<a class="project-link" href="{p.url()}" aria-label="{e(p.name)}"></a>{art}'
        f'<div class="project-head"><h3>{e(p.name)}{role}{others}</h3>{plat_badge(p.platform, link=True)}</div>'
        f"<h6>{headline(v)}</h6>{code_bar(v)}"
        f'<small class="muted">Updated <time data-ago datetime="{e(v.commit["timestamp"])}">{ago(v.updated, now)}</time>'
        f'{commit} · <a href="{e(p.repository)}" target="_blank" rel="noopener">{e(p.owner)}/{e(p.repo)} ↗</a></small></article>'
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
        '<button class="secondary outline">Only</button></li>'
        for k, *_ in PLATFORMS
        if k in counts
    )
    plat_dd = (
        '<details class="dropdown plat-list"><summary>All systems</summary><ul>'
        f'<li><a href="/" data-all>All systems</a></li>{items}</ul></details>'
    )
    sort_dd = (
        '<details class="dropdown sort-list"><summary>Recently updated</summary><ul>'
        + "".join(f'<li><a href="/?sort={k}" data-sort="{k}">{e(l)}</a></li>' for k, l in SORTS)
        + "</ul></details>"
    )
    ordered = sorted(projects, key=lambda p: p.v.commit["timestamp"], reverse=True)
    intro = site["intro"]
    complete = sum(1 for p in projects if p.v.code_pct >= 99.995)
    systems = len({p.platform for p in projects})
    body = (
        f'<hgroup class="intro"><h2>{e(intro["heading"])}</h2><p>{e(intro["text"])}</p></hgroup>'
        f'<p class="stats"><strong>{len(projects)}</strong> games · <strong>{complete}</strong> complete · '
        f'<strong>{systems}</strong> systems · updated every 6 hours</p>'
        '<div class="filters">'
        '<input type="search" id="search" placeholder="Search games, teams, repos…" aria-label="Search projects">'
        f"{plat_dd}{sort_dd}</div>"
        f'<p class="muted result-count" id="result-count">{len(projects)} projects</p>'
        f'<div id="directory">{"".join(card(p, now) for p in ordered)}</div>'
    )
    html = page(site, built, f"{site['name']} · {site['tagline']}", body)
    write("index.html", html)
    write("projects/index.html", html)


def build_systems(site, projects, built):
    groups = {}
    for p in projects:
        groups.setdefault(p.platform, []).append(p)
    cards = []
    for k, name, short, maker in PLATFORMS:
        ps = groups.get(k)
        if not ps:
            continue
        total = sum(p.v.total_code for p in ps if not p.v.archived)
        matched = sum(p.v.matched_code for p in ps if not p.v.archived)
        done = sum(1 for p in ps if p.v.code_pct >= 99.995)
        top = sorted(ps, key=lambda p: -p.v.code_pct)[:5]
        cards.append(
            f'<article><a href="/?platform={k}"><h4>{plat_badge(k)} {e(name)}</h4></a>'
            f'<p class="muted"><small>{e(maker)} · {len(ps)} project{"s" if len(ps) != 1 else ""}'
            f'{f" · {done} complete" if done else ""}</small></p>'
            + (bar("code", total, 0, pct(matched, total)) if total else "")
            + "<small><ul>"
            + "".join(f'<li><a href="{p.url()}">{e(p.name)}</a> <span class="muted">{fmt_pct(p.v.code_pct)}</span></li>' for p in top)
            + "</ul></small>"
            + "</article>"
        )
    body = (
        '<hgroup class="intro"><h2>Systems</h2><p class="muted">Every system with a tracked decompilation. '
        "The bar on each card shows how much of that system&rsquo;s code is decompiled, across projects with a live report.</p></hgroup>"
        f'<div class="systems">{"".join(cards)}</div>'
    )
    write("systems/index.html", page(site, built, f"Systems · {site['name']}", body))


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
        f'<li><strong>{e(o.id)} ✓</strong></li>' if o is v else
        f'<li><a href="{p.url(*([] if o is p.versions[0] else [o.id]), *([suffix] if suffix else []))}">{e(o.id)}</a></li>'
        for o in p.versions
    )
    return f'<details class="dropdown"><summary>Version: {e(v.id)}</summary><ul>{lis}</ul></details>'


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
    crumbs = [(p.name, p.url())]
    role = f' <span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    archived_repo = ' <span class="tag subtle">Repository archived</span>' if p.meta.get("archived") else ""
    actions = (
        '<div class="report-actions">'
        f'<a class="secondary outline" role="button" href="{p.url()}history/">History</a>'
        f'<a class="secondary outline" role="button" href="{e(p.repository)}" target="_blank" rel="noopener">Repository ↗</a>'
        "</div>"
    )
    sub = f'<h4 class="muted">{fmt_pct(v.linked_pct)} fully linked</h4>' if v.linked_pct > 0 else ""
    desc = f'<p class="report-desc">{e(p.description)}</p>' if p.description else '<p class="report-desc"></p>'
    plat = PLAT.get(p.platform, {"name": p.platform})["name"]
    facts = f'<p class="muted"><small>{plat_badge(p.platform)} {e(plat)} · {e(v.id)} · maintained by <a href="https://github.com/{e(p.owner)}" target="_blank" rel="noopener">{e(p.owner)}</a>'
    if v.functions[1]:
        facts += f" · {v.functions[0]:,} / {v.functions[1]:,} functions decompiled"
    facts += "</small></p>"

    notice = slot_notice(p)
    if v.archived:
        notice += (
            '<div class="notice"><small>No live report could be read from this project&rsquo;s CI, so these numbers come from the '
            f"last public snapshot of the old decomp.dev index ({v.updated.strftime('%Y-%m-%d')}). "
            "They&rsquo;ll update automatically once the project publishes a report artifact.</small></div>"
        )

    code = f'<h6>Code {f"<small class=muted>({fmt_size(v.total_code)})</small>" if v.total_code else ""}</h6>{code_bar(v)}'
    data = ""
    if v.total_data:
        data = (
            f'<h6>Data <small class="muted">({fmt_size(v.total_data)})</small></h6>'
            + bar("data", v.total_data, pct(v.linked_data, v.total_data), pct(v.matched_data, v.total_data), None, v.linked_data, v.matched_data)
        )
    sha = v.commit.get("sha", "")
    commit = ""
    if sha:
        msg = f" | {e(v.commit.get('message', ''))}" if v.commit.get("message") else ""
        commit = (
            f'<h6>Latest commit</h6><pre><a href="{e(p.commit_url(sha))}" target="_blank" rel="noopener">{e(sha[:7])}</a>{msg}</pre>'
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
        units = (
            f'<h6>Units <small class="muted">({len(layout):,})</small></h6>'
            '<input id="unit-filter" type="search" placeholder="Filter units, e.g. camera <70% >10kb linked" aria-label="Filter units">'
            f'<canvas id="treemap" data-src="{data_base}units.json" role="img" aria-label="Map of {len(layout):,} source files sized by code: '
            f'{sum(1 for u in layout if u["p"] >= 100):,} fully decompiled"></canvas>'
            '<p class="muted"><small>Each box is a source file, sized by its code. Green is decompiled, '
            "blue shading is partly decompiled, grey is untouched.</small></p>"
        )
        scripts.append("/static/js/treemap.js")

    body = (
        f'<div class="report-top"><div class="report-head"><h3>{e(p.name)} is {fmt_pct(v.code_pct)} decompiled{role}{archived_repo}</h3>{sub}</div>{actions}</div>'
        f"{desc}{facts}{version_switch(p, v)}{notice}{code}{data}{commit}{units}"
    )
    title = f"{p.name} ({PLAT.get(p.platform, {}).get('short', p.platform)}) · {site['name']}"
    src_repo = ((p.raw.get("source") or {}).get("repo") or f"{p.owner}/{p.repo}").lower()
    body = f'<div id="fresh" data-repo="{e(src_repo)}" data-sha="{e(p.v.commit.get("sha", ""))}" hidden></div>' + body
    scripts.append("/static/js/fresh.js")
    write(
        base.strip("/") + "/index.html",
        page(site, built, title, body, f"{p.name} is {fmt_pct(v.code_pct)} decompiled", crumbs, scripts=scripts),
    )


def build_history(site, p, v, base, built):
    if base == p.url():
        write("/".join([p.owner, p.repo, "history.json"]), json.dumps(p.history, separators=(",", ":")))
    crumbs = [(p.name, base), ("History", base + "history/")]
    archive_points = sum(1 for h in p.history if h.get("source") == "archive")
    note = ""
    if archive_points:
        note = (
            f'<p class="muted"><small>{archive_points} earlier point{"s" if archive_points != 1 else ""} come from monthly '
            "snapshots of the old decomp.dev index; newer points come from this project&rsquo;s CI reports.</small></p>"
        )
    body = (
        f"<h3>History for {e(p.name)}</h3>"
        f'<div id="chart" data-src="{p.url()}history.json"></div>{note}'
        f'<div role="group"><a role="button" class="secondary" href="{base}">Back to report</a></div>'
    )
    write(
        base.strip("/") + "/history/index.html",
        page(
            site,
            built,
            f"{p.name} history · {site['name']}",
            body,
            f"Decompilation progress history for {p.name}",
            crumbs,
            extra_head='<link rel="stylesheet" href="/static/vendor/uPlot.min.css"><script src="/static/vendor/uPlot.iife.min.js" defer></script>',
            scripts=["/static/js/history.js"],
        ),
    )


def build_about(site, projects, built):
    live = sum(1 for p in projects if not p.v.archived)
    contact = site.get("contact_url", "#")
    body = f"""
<hgroup class="intro"><h2>About {e(site['name'])}</h2><p class="muted">{e(site['tagline'])}</p></hgroup>
<p>{e(site['intro']['text'])}</p>
<h4>Where the numbers come from</h4>
<p>Each project&rsquo;s own continuous integration publishes a progress report (the
<a href="https://github.com/encounter/objdiff" target="_blank" rel="noopener">objdiff</a> report format).
{e(site['name'])} reads the newest report for each project from GitHub and rebuilds these pages.
Right now {live} of {len(projects)} projects have a live report; the rest show their last known numbers.</p>
<p>The starting list of projects and the older history points were recovered from public Internet Archive snapshots of the
decomp.dev index, which its author retired in October 2026. Only facts were taken (names, repositories, systems and
percentages). This site&rsquo;s code is independent.</p>
<h4>Listing and removal</h4>
<p>Every project here is a public repository and every card links straight to it. New projects are added through
<a href="/submit/">Add a project</a>, which checks them automatically. Each game appears once: when several repositories
work on the same game, the one with the most code decompiled is listed and the others are shown on its page.
For anything else, <a href="{e(contact)}" target="_blank" rel="noopener">get in touch</a>.</p>
<p class="muted"><small>Game lists: No-Intro and Redump via libretro-database (CC BY-SA 4.0), MAME, titledb and Wikidata.</small></p>
<h4>Credits</h4>
<p>Built with <a href="https://picocss.com" target="_blank" rel="noopener">Pico CSS</a> and
<a href="https://github.com/leeoniya/uPlot" target="_blank" rel="noopener">uPlot</a>
(<a href="/static/vendor/LICENSES.txt">licenses</a>). Game names and artwork belong to their owners.
All credit for the decompilations goes to the people working on them.</p>
"""
    write("about/index.html", page(site, built, f"About · {site['name']}", body))


STATUS_LABELS = {"listed": "Accepted", "contesting": "Contesting", "already": "Already listed", "rejected": "Not accepted"}


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
        game = x.get("game", {}).get("title", "")
        name = f'<a href="/{e(x["project"]["owner"])}/{e(x["project"]["repo"])}/">{e(x["repo"])}</a>' if listed and x.get("project") else e(x["repo"])
        reasons = "".join(f"<li>{e(r)}</li>" for r in x.get("reasons", []))
        rows.append(
            f"<tr><td>{name}<br><small class=muted>{e(game)}</small></td>"
            f'<td class="status-{e(x.get("status", ""))}">{STATUS_LABELS.get(x.get("status"), "Not accepted")}</td>'
            f"<td><small>{f'<ul>{reasons}</ul>' if reasons else 'All checks passed.'}</small></td>"
            f'<td><small class="muted">{e(x.get("by", ""))}<br>{e(x.get("submitted_at", "")[:10])}</small></td></tr>'
        )
    table = (
        '<div class="overflow-auto"><table class="striped"><thead><tr><th>Repository</th><th>Result</th><th>Details</th>'
        f'<th>By</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        if rows else '<p class="muted">No submissions yet.</p>'
    )
    options = "".join(f'<option value="{k}">{e(PLAT[k]["name"])}</option>' for k in sub.NAME_ONLY)
    body = f"""
<hgroup class="intro"><h2>Add a project</h2><p class="muted">Submissions are checked automatically. Nobody reviews them by hand.</p></hgroup>
<h4>Requirements</h4>
<ul>
<li>A public GitHub repository whose GitHub Actions publish an <a href="https://github.com/encounter/objdiff" target="_blank" rel="noopener">objdiff</a>
progress report on the default branch, as an artifact with <code>report</code> in its name (projects built from
<a href="https://github.com/encounter/dtk-template" target="_blank" rel="noopener">dtk-template</a> do this already).</li>
<li>At least {sub.MIN_MATCHED_PERCENT:g}% of the code decompiled.</li>
<li>The game is identified from the repository itself: its disc ID, serial, title ID or ROM hash, as decomp projects
normally contain. PC, DOS and Switch games have no such IDs, so for those you pick the system and the game&rsquo;s exact name.</li>
<li>One project per game. If the game is already listed, your repository must decompile the same executable, and it takes
the game&rsquo;s place once it leads by {slots.TAKEOVER_MARGIN:g}% more code decompiled for {slots.TAKEOVER_DAYS} days in a row.</li>
<li>Your GitHub account is at least 30 days old. Up to 3 submissions per account per day.</li>
</ul>
<article>
<div id="signed-out"><p>Sign in with GitHub to submit. decomp.fun only sees your public profile.</p>
<a role="button" href="/auth/login">Sign in with GitHub</a></div>
<div id="signed-in" hidden>
<p class="muted"><small>Signed in as <strong id="who"></strong> · <a href="/auth/logout">Sign out</a></small></p>
<form id="submit-form">
<label>Repository<input name="repo" placeholder="owner/name or https://github.com/owner/name" required></label>
<label>PC, DOS or Switch game?<select name="system" id="system"><option value="">No, it&rsquo;s a console or arcade game</option>{options}</select></label>
<label id="game-row" hidden>Game name, exactly as published<input name="game" placeholder="e.g. Thief: Deadly Shadows"></label>
<button type="submit">Submit</button>
</form>
</div>
<div id="submit-result" class="notice" hidden></div>
</article>
<h4>Recent submissions</h4>
{table}
"""
    write("submit/index.html", page(site, built, f"Add a project · {site['name']}", body,
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
    body = '<hgroup class="intro"><h2>Not found</h2><p class="muted">That page doesn&rsquo;t exist.</p></hgroup><a role="button" href="/">Back to projects</a>'
    write("404.html", page(site, built, f"Not found · {site['name']}", body))


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
    leaders = [p for p in projects if p.leader is p]

    build_index(site, leaders, built)
    build_systems(site, leaders, built)
    build_about(site, projects, built)
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
    live = sum(1 for p in projects if not p.v.archived)
    print(f"Built {len(projects)} projects ({live} live, {len(projects) - live} archived) into dist/; {len(leaders)} games")
    if skipped:
        print(f"Skipped (no data at all): {', '.join(skipped)}")
    if gone:
        print(f"Not listed (repository deleted or private): {', '.join(gone)}")


if __name__ == "__main__":
    main()
