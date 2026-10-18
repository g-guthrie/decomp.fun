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
import shutil
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fetch import all_projects
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
    ("dc", "Dreamcast", "DC", "Sega"),
    ("naomi", "Sega NAOMI", "NAOMI", "Sega"),
]
PLAT = {k: {"name": n, "short": s, "maker": m} for k, n, s, m in PLATFORMS}

SORTS = [
    ("updated", "Last updated"),
    ("name", "Name"),
    ("percent", "Matched code (percent)"),
    ("matched", "Matched code (size)"),
    ("size", "Total code size"),
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
    out = DIST / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)


def plat_badge(key):
    p = PLAT.get(key, {"name": key or "Unknown", "short": (key or "?").upper()})
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


class Project:
    def __init__(self, p):
        self.raw = p
        self.id = p["id"]
        self.owner = p["owner"]
        self.repo = p["repo"]
        self.name = p["name"]
        self.platform = p.get("platform") or "unknown"
        self.role = p.get("role")
        self.image = p.get("image")
        report_path = DATA / "reports" / f"{self.id}.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        self.meta = report.get("meta", {})
        self.description = p.get("description") or self.meta.get("description") or ""
        self.repository = self.meta.get("html_url") or f"https://github.com/{self.owner}/{self.repo}"
        self.versions = [Version(v) for v in report.get("versions", []) if v.get("commit")]
        if p.get("version"):  # put the preferred version first
            self.versions.sort(key=lambda v: v.id != p["version"])
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
        '<li><a href="/systems/">Systems</a></li><li><a href="/about/">About</a></li>'
        + "".join(f'<li><a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in site.get("nav_links", []))
        + f'<li><a id="theme-toggle" href="#" title="Toggle theme" aria-label="Toggle theme">{ICON_THEME}</a></li>'
    )
    return f"<nav><ul>{left}</ul><ul>{right}</ul></nav>"


def footer(site, built):
    return (
        '<footer class="container"><small class="muted">'
        f'{e(site["name"])} · progress is read from each project&rsquo;s own CI reports · '
        f'<a href="/about/">About &amp; removal requests</a></small>'
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
            f'data-tip="{fmt_pct(matched_pct)} perfect match{size(matched_size)}"></span>'
        )
    if fuzzy_pct is not None and fuzzy_pct - matched_pct > 0.0001:
        parts.append(
            f'<span class="fuzzy striped" style="width:{fuzzy_pct - matched_pct:.4f}%" data-tip="{fmt_pct(fuzzy_pct)} fuzzy match"></span>'
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


def card(p, now):
    v = p.v
    art = f'<div class="project-art"><img src="/{e(p.image)}" alt="" loading="lazy"></div>' if p.image else ""
    role = f'<span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    sha = v.commit.get("sha", "")
    commit = f' in commit <a href="{e(p.commit_url(sha))}" target="_blank" rel="noopener">{e(sha[:7])}</a>' if sha else ""
    search = " ".join([p.name, p.owner, p.repo, PLAT.get(p.platform, {}).get("name", "")]).lower()
    return (
        f'<article class="project" data-platform="{e(p.platform)}" data-name="{e(p.name.lower())}" '
        f'data-updated="{e(v.commit["timestamp"])}" data-percent="{v.code_pct:.4f}" data-matched="{v.matched_code}" '
        f'data-size="{v.total_code}" data-search="{e(search)}">'
        f'<a class="project-link" href="{p.url()}" aria-label="{e(p.name)}"></a>{art}'
        f'<div class="project-head"><h3>{e(p.name)}{role}</h3>{plat_badge(p.platform)}</div>'
        f"<h6>{headline(v)}</h6>{code_bar(v)}"
        f'<small class="muted">Updated <time data-ago datetime="{e(v.commit["timestamp"])}">{ago(v.updated, now)}</time>'
        f" by <a href=\"{e(p.repository)}\" target=\"_blank\" rel=\"noopener\">{e(p.owner)}</a>{commit}</small></article>"
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
        '<details class="dropdown sort-list"><summary>Last updated</summary><ul>'
        + "".join(f'<li><a href="/?sort={k}" data-sort="{k}">{e(l)}</a></li>' for k, l in SORTS)
        + "</ul></details>"
    )
    ordered = sorted(projects, key=lambda p: p.v.commit["timestamp"], reverse=True)
    intro = site["intro"]
    body = (
        f'<hgroup class="intro"><h2>{e(intro["heading"])}</h2></hgroup>'
        f'<blockquote>{e(intro["text"])}</blockquote>'
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
            + (f'<a href="/?platform={k}"><small>All {len(ps)} →</small></a>' if len(ps) > 5 else "")
            + "</article>"
        )
    body = (
        '<hgroup class="intro"><h2>Systems</h2><p class="muted">Every system with a tracked decompilation. '
        "The bar on each card is the combined matched code across projects with a live report.</p></hgroup>"
        f'<div class="systems">{"".join(cards)}</div>'
    )
    write("systems/index.html", page(site, built, f"Systems · {site['name']}", body, crumbs=[("Systems", "/systems/")]))


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
        f'<li><a href="{p.url(o.id, *([suffix] if suffix else []))}">{e(o.id)}</a></li>' for o in p.versions
    )
    return f'<details class="dropdown"><summary>Version: {e(v.id)}</summary><ul>{lis}</ul></details>'


def slot_notice(p):
    """Where this repository stands for its game: listed, or behind another repository."""
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
    crumbs = [("Projects", "/"), (p.name, p.url())]
    role = f' <span class="tag subtle">{e(p.role)}</span>' if p.role else ""
    archived_repo = ' <span class="tag subtle">Repository archived</span>' if p.meta.get("archived") else ""
    actions = (
        '<div class="report-actions">'
        f'<a class="secondary outline" role="button" href="{base}history/">History</a>'
        f'<a class="secondary outline" role="button" href="{e(p.repository)}" target="_blank" rel="noopener">Repository ↗</a>'
        "</div>"
    )
    sub = f'<h4 class="muted">{fmt_pct(v.linked_pct)} fully linked</h4>' if v.linked_pct > 0 else ""
    desc = f'<p class="report-desc">{e(p.description)}</p>' if p.description else '<p class="report-desc"></p>'
    plat = PLAT.get(p.platform, {"name": p.platform})["name"]
    facts = f'<p class="muted"><small>{plat_badge(p.platform)} {e(plat)} · {e(v.id)} · maintained by <a href="https://github.com/{e(p.owner)}" target="_blank" rel="noopener">{e(p.owner)}</a>'
    if v.functions[1]:
        facts += f" · {v.functions[0]:,} / {v.functions[1]:,} functions matched"
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
        data_base = p.url(v.id)
        if base == data_base:
            write(base.strip("/") + "/units.json", json.dumps(layout, separators=(",", ":")))
        units = (
            f'<h6>Units <small class="muted">({len(layout):,})</small></h6>'
            '<input id="unit-filter" type="search" placeholder="Filter units, e.g. camera <70% >10kb linked" aria-label="Filter units">'
            f'<canvas id="treemap" data-src="{data_base}units.json"></canvas>'
            '<p class="muted"><small>Each box is a translation unit sized by code. Green is an exact match; '
            "blue shading shows partial (fuzzy) matches; grey is untouched.</small></p>"
        )
        scripts.append("/static/js/treemap.js")

    body = (
        f'{actions}<div class="report-head"><h3>{e(p.name)} is {fmt_pct(v.code_pct)} decompiled{role}{archived_repo}</h3>{sub}</div>'
        f"{desc}{facts}{version_switch(p, v)}{notice}{code}{data}{commit}{units}"
    )
    title = f"{p.name} ({PLAT.get(p.platform, {}).get('short', p.platform)}) · {site['name']}"
    write(
        base.strip("/") + "/index.html",
        page(site, built, title, body, f"{p.name} is {fmt_pct(v.code_pct)} decompiled", crumbs, scripts=scripts),
    )


def build_history(site, p, v, base, built):
    if base == p.url():
        write("/".join([p.owner, p.repo, "history.json"]), json.dumps(p.history, separators=(",", ":")))
    crumbs = [("Projects", "/"), (p.name, base), ("History", base + "history/")]
    archive_points = sum(1 for h in p.history if h.get("source") == "archive")
    note = ""
    if archive_points:
        note = (
            f'<p class="muted"><small>{archive_points} earlier point{"s" if archive_points != 1 else ""} come from monthly '
            "snapshots of the old decomp.dev index; newer points come from this project&rsquo;s CI reports.</small></p>"
        )
    body = (
        f"<h3>History for {e(p.name)}</h3>{version_switch(p, v, 'history')}"
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
<p>Every project here is a public repository and every card links straight to it. If you maintain a project and want it
removed, renamed, or given a banner, <a href="{e(contact)}" target="_blank" rel="noopener">get in touch</a> and it will be
handled promptly, no questions asked. Projects can also ask to be added.</p>
<h4>Credits</h4>
<p>Built with <a href="https://picocss.com" target="_blank" rel="noopener">Pico CSS</a> and
<a href="https://github.com/leeoniya/uPlot" target="_blank" rel="noopener">uPlot</a>
(<a href="/static/vendor/LICENSES.txt">licenses</a>). Game names and artwork belong to their owners.
All credit for the decompilations goes to the people working on them.</p>
"""
    write("about/index.html", page(site, built, f"About · {site['name']}", body, crumbs=[("About", "/about/")]))


def build_404(site, built):
    body = '<hgroup class="intro"><h2>Not found</h2><p class="muted">That page doesn&rsquo;t exist.</p></hgroup><a role="button" href="/">Back to projects</a>'
    write("404.html", page(site, built, f"Not found · {site['name']}", body))


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
        if p.image:
            src = DATA / p.image
            if src.exists():
                (DIST / p.image).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, DIST / p.image)
            else:
                p.image = None

    for slot in slots.assign(projects).values():
        lead = slot["leader"]
        lead.others = sorted(slot["others"], key=lambda o: -o.v.code_pct)
        lead.challengers = slot["challengers"]
        for o in slot["others"]:
            o.leader = lead
    leaders = [p for p in projects if p.leader is p]

    build_index(site, leaders, built)
    build_systems(site, leaders, built)
    build_about(site, projects, built)
    build_404(site, built)
    for p in projects:
        for i, v in enumerate(p.versions):
            bases = [p.url(v.id)] + ([p.url()] if i == 0 else [])
            for base in bases:
                build_report(site, p, v, base, built)
                build_history(site, p, v, base, built)
    live = sum(1 for p in projects if not p.v.archived)
    print(f"Built {len(projects)} projects ({live} live, {len(projects) - live} archived) into dist/; {len(leaders)} games")
    if skipped:
        print(f"Skipped (no data at all): {', '.join(skipped)}")
    if gone:
        print(f"Not listed (repository deleted or private): {', '.join(gone)}")


if __name__ == "__main__":
    main()
