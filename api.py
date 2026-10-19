"""Machine-readable outputs for decomp.fun: static JSON under /v1/, an Atom feed, shields.io
badge endpoints, per-build "what to work on" lists, /llms.txt and the default-build redirects.

Called once from build.py after the project list and game slots are settled:

    api.build(projects, assigned, built, PLAT, write)

Everything here is derived from data the site already has; nothing is fetched. Standard library only.
"""

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ORIGIN = "https://decomp.fun"
API_VERSION = 1
TODO_LIMIT = 200
CHANGES_LIMIT = 200
# Cloudflare static assets: at most 2,000 static and 100 dynamic redirect rules.
MAX_STATIC_REDIRECTS = 2000
FILE_LIMIT = 20000  # files per deploy on Workers Free

ROOT = Path(__file__).resolve().parent
GAMES = ROOT / "data" / "games"

GAME_LIST_LICENSE = {
    "note": "Game titles and identifiers come from No-Intro and Redump game lists as packaged by "
    "libretro-database (CC BY-SA 4.0), MAME, titledb (Switch) and Wikidata (PC, CC0). "
    "Reuse of the game-list fields must credit those sources and share alike.",
    "license": "CC-BY-SA-4.0",
    "sources": [
        "https://github.com/libretro/libretro-database",
        "https://github.com/mamedev/mame",
        "https://github.com/blawar/titledb",
        "https://www.wikidata.org",
    ],
    "fields": ["game_title", "games[].title", "systems[].games_known"],
}
PROGRESS_NOTE = (
    "Progress numbers are read from each project's own CI (objdiff report artifacts on the default branch). "
    "They are facts about public repositories; all credit belongs to the projects."
)


def _dump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _r(x, n=4):
    return round(float(x), n)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_key(p):
    return f"{p.owner}/{p.repo}".lower()


def detail_path(p):
    return f"/v1/{p.owner}/{p.repo}.json"


def badge_path(p):
    return f"/v1/badge/{p.owner}/{p.repo}.json"


def todo_path(p, v):
    return f"/v1/{p.owner}/{p.repo}/{v.id}/todo.json"


def page_path(p, i, v):
    """The default build lives at the project URL; other builds have their own page."""
    return p.url() if i == 0 else p.url(v.id)


def version_json(p, i, v):
    out = {
        "id": v.id,
        "default": i == 0,
        "live": not v.archived,  # false: last known numbers from the archived decomp.dev index
        "commit": {"sha": v.commit.get("sha", ""), "timestamp": v.commit.get("timestamp", "")},
        "total_code": v.total_code,
        "matched_code": v.matched_code,
        "decompiled_percent": _r(v.code_pct),
        "linked_percent": _r(v.linked_pct),
        "partly_percent": _r(v.fuzzy_pct),
        "functions": {"matched": v.functions[0], "total": v.functions[1]},
        "url": ORIGIN + page_path(p, i, v),
    }
    if v.total_data:
        out["data"] = {"total": v.total_data, "matched": v.matched_data, "linked": v.linked_data}
    return out


def project_json(p, slot_of):
    return {
        "owner": p.owner,
        "repo": p.repo,
        "name": p.name,
        "url": ORIGIN + p.url(),
        "api": ORIGIN + detail_path(p),
        "repository": p.repository,
        "game_title": p.game_title,
        "platform": {"key": p.platform, "name": p.plat_name},
        "slot": slot_of.get(repo_key(p)),
        "leader": p.leader is p,
        "listed_as": f"{p.leader.owner}/{p.leader.repo}",
        "archived": bool(p.meta.get("archived")),
        "updated": p.v.commit.get("timestamp", ""),
        "versions": [version_json(p, i, v) for i, v in enumerate(p.versions)],
    }


def todo_units(v):
    left = lambda u: (100 - u["fuzzy_match_percent"]) * u["total_code"] / 100
    units = [u for u in v.units if u.get("total_code", 0) > 0 and u.get("fuzzy_match_percent", 0) < 100]
    units.sort(key=lambda u: -left(u))
    return [
        {
            "name": u["name"],
            "size": u["total_code"],
            "percent": _r(u["fuzzy_match_percent"], 2),
            "remaining": round(left(u)),
            "linked": bool(u.get("is_linked")),
        }
        for u in units
    ]


def history_changes(projects):
    out = []
    for p in projects:
        prev = None
        for h in sorted(p.history, key=lambda h: h.get("timestamp", "")):
            m = h.get("measures") or {}
            cur = (m.get("matched_code_percent"), m.get("complete_code_percent"))
            if cur[0] is None:
                continue
            if prev is not None and (abs(cur[0] - prev[0]) > 1e-6 or abs((cur[1] or 0) - (prev[1] or 0)) > 1e-6):
                out.append(
                    {
                        "owner": p.owner,
                        "repo": p.repo,
                        "name": p.name,
                        "platform": p.platform,
                        "url": ORIGIN + p.url(),
                        "timestamp": h.get("timestamp", ""),
                        "commit_sha": h.get("commit_sha", ""),
                        "commit_message": (h.get("commit_message") or "").split("\n", 1)[0][:200],
                        "source": h.get("source", ""),
                        "decompiled_percent": _r(cur[0]),
                        "decompiled_delta": _r(cur[0] - prev[0]),
                        "linked_percent": _r(cur[1] or 0),
                        "linked_delta": _r((cur[1] or 0) - (prev[1] or 0)),
                    }
                )
            prev = cur
    out.sort(key=lambda c: c["timestamp"], reverse=True)
    return out[:CHANGES_LIMIT]


def atom(changes, built):
    def x(s):
        return escape(str(s), quote=True)

    entries = []
    for c in changes:
        sign = "+" if c["decompiled_delta"] >= 0 else ""
        title = f'{c["name"]}: {c["decompiled_percent"]:.2f}% decompiled ({sign}{c["decompiled_delta"]:.2f})'
        summary = f'{c["owner"]}/{c["repo"]} {c["commit_sha"][:7]} {c["commit_message"]}'.strip()
        entries.append(
            "<entry>"
            f'<id>tag:decomp.fun,2026:{x(c["owner"])}/{x(c["repo"])}/{x(c["commit_sha"] or c["timestamp"])}</id>'
            f"<title>{x(title)}</title>"
            f'<link href="{x(c["url"])}"/>'
            f'<updated>{x(c["timestamp"])}</updated>'
            f"<summary>{x(summary)}</summary>"
            "</entry>"
        )
    updated = changes[0]["timestamp"] if changes else _iso(built)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>decomp.fun: decompilation progress</title>"
        f'<id>{ORIGIN}/feed.xml</id>'
        f'<link rel="self" href="{ORIGIN}/feed.xml"/><link href="{ORIGIN}/"/>'
        f"<updated>{updated}</updated>"
        "<author><name>decomp.fun</name></author>"
        + "".join(entries)
        + "</feed>\n"
    )


def badge(p):
    v = p.v
    pc = v.code_pct
    shown = pc if pc in (0, 100) else min(max(pc, 0.1), 99.9)
    color = (
        "brightgreen" if pc >= 100 else "green" if pc >= 75 else "yellowgreen" if pc >= 50
        else "yellow" if pc >= 25 else "orange" if pc >= 10 else "red"
    )
    return {"schemaVersion": 1, "label": "decomp.fun", "message": f"{shown:.1f}%", "color": color, "cacheSeconds": 3600}


def known_games():
    out = {}
    for f in sorted(GAMES.glob("*.json")):
        try:
            out[f.stem] = len(json.loads(f.read_text()).get("games", []))
        except (OSError, ValueError):
            pass
    return out


def redirects(projects):
    """Default builds are served at the project URL; their old version URLs redirect there.
    Version history pages were identical to the project's history page: one dynamic rule."""
    dynamic = ["/:owner/:repo/:build/history/ /:owner/:repo/history/ 301"]
    static = []
    # Projects with several builds link to their default build by name, so those go first if the cap is hit.
    for p in sorted(projects, key=lambda p: -len(p.versions)):
        static.append(f"{p.url(p.v.id)} {p.url()} 301")
    dropped = max(0, len(static) - MAX_STATIC_REDIRECTS)
    static = static[:MAX_STATIC_REDIRECTS]
    return "\n".join(["# Generated by api.py", *static, *dynamic]) + "\n", dropped


def build(projects, assigned, built, plat, write):
    """projects: listed build.Project objects (slots assigned). assigned: slots.assign() result."""
    gen = _iso(built)
    slot_of = {}
    for slot, s in assigned.items():
        for m in [s["leader"], *s["others"]]:
            slot_of[repo_key(m)] = slot
    for p in projects:
        p.plat_name = plat.get(p.platform, {}).get("name", p.platform)

    rows = [project_json(p, slot_of) for p in sorted(projects, key=lambda p: (p.owner.lower(), p.repo.lower()))]
    write("v1/projects.json", _dump({
        "api_version": API_VERSION, "generated": gen, "count": len(rows),
        "note": PROGRESS_NOTE, "attribution": GAME_LIST_LICENSE, "projects": rows,
    }))

    ident_title = {repo_key(p): p.game_title for p in projects}
    games = {}
    for slot, s in sorted(assigned.items()):
        lead = s["leader"]
        platform, _, game = slot.partition(":")
        games[slot] = {
            "platform": platform if platform != "repo" else lead.platform,
            "game": game if platform != "repo" else None,
            "identified": platform != "repo",
            "title": ident_title.get(repo_key(lead)) or lead.name,
            "leader": f"{lead.owner}/{lead.repo}",
            "decompiled_percent": _r(lead.v.code_pct),
            "url": ORIGIN + lead.url(),
            "others": [f"{o.owner}/{o.repo}" for o in sorted(s["others"], key=lambda o: -o.v.code_pct)],
            "challengers": s.get("challengers", {}),  # repo -> ahead by the takeover margin since
            "contesters": [f"{c.owner}/{c.repo}" for c in s.get("contesters", [])],
            "contested_by": list(s.get("contested_by", [])),
        }
    write("v1/games.json", _dump({
        "api_version": API_VERSION, "generated": gen, "count": len(games),
        "note": "Each game has one slot (platform:game, or repo:owner/name when the game could not be identified). "
        "The leader is the listed repository; a challenger takes over after leading by the takeover margin for long enough.",
        "attribution": GAME_LIST_LICENSE, "games": games,
    }))

    known = known_games()
    leaders = [p for p in projects if p.leader is p]
    systems = []
    for key, info in plat.items():
        here = [p for p in projects if p.platform == key]
        systems.append({
            "key": key, "name": info["name"], "short": info["short"], "maker": info["maker"],
            "projects": len(here), "games": sum(1 for p in leaders if p.platform == key),
            "games_known": known.get(key, 0),
            "url": f"{ORIGIN}/?platform={key}",
        })
    write("v1/systems.json", _dump({
        "api_version": API_VERSION, "generated": gen, "attribution": GAME_LIST_LICENSE, "systems": systems,
    }))

    todo_files = 0
    for p in projects:
        builds = []
        for i, v in enumerate(p.versions):
            b = version_json(p, i, v)
            todo = todo_units(v) if v.units else []
            if todo:
                write(todo_path(p, v).lstrip("/"), _dump({
                    "api_version": API_VERSION, "generated": gen,
                    "project": f"{p.owner}/{p.repo}", "build": v.id,
                    "commit": b["commit"],
                    "note": "Source units with code left to decompile, most remaining bytes first. "
                    "remaining = (100 - percent) / 100 * size. percent counts partly matched code.",
                    "units_total": sum(1 for u in v.units if u.get("total_code", 0) > 0),
                    "units_left": len(todo),
                    "units": todo[:TODO_LIMIT],
                }))
                todo_files += 1
                b["todo"] = ORIGIN + todo_path(p, v)
            if v.units:
                b["units"] = ORIGIN + page_path(p, i, v) + "units.json"
            builds.append(b)
        slot = slot_of.get(repo_key(p))
        s = assigned.get(slot, {})
        d = project_json(p, slot_of)
        d.update({
            "api_version": API_VERSION, "generated": gen,
            "description": p.description,
            "default_branch": p.meta.get("default_branch"),
            "stars": p.meta.get("stars"),
            "source": (p.raw.get("source") or {}).get("type", "auto"),
            "versions": builds,
            "history": ORIGIN + p.url() + "history.json",
            "badge": ORIGIN + badge_path(p),
            "others": [f"{o.owner}/{o.repo}" for o in s.get("others", [])] if p.leader is p else [],
            "contesters": [f"{c.owner}/{c.repo}" for c in s.get("contesters", [])] if p.leader is p else [],
            "contested_by": list(s.get("contested_by", [])) if p.leader is p else [],
            "attribution": GAME_LIST_LICENSE,
        })
        write(detail_path(p).lstrip("/"), _dump(d))
        write(badge_path(p).lstrip("/"), _dump(badge(p)))

    changes = history_changes(projects)
    write("v1/changes.json", _dump({"api_version": API_VERSION, "generated": gen, "count": len(changes), "changes": changes}))
    write("feed.xml", atom(changes, built))
    write("v1/index.json", _dump(index_json(gen)))
    write("llms.txt", LLMS_TXT)

    text, dropped = redirects(projects)
    write("_redirects", text)
    if dropped:
        print(f"api: {dropped} default-build redirects dropped (over Cloudflare's {MAX_STATIC_REDIRECTS} static rules)")
    # Open to any origin (read-only public data).
    with open(ROOT / "dist" / "_headers", "a") as f:
        f.write(
            "/v1/*\n  Access-Control-Allow-Origin: *\n"
            "/feed.xml\n  Cache-Control: public, max-age=600\n"
            "/llms.txt\n  Access-Control-Allow-Origin: *\n"
        )
    files = sum(1 for f in (ROOT / "dist").rglob("*") if f.is_file())
    print(f"api: {len(rows)} projects, {len(games)} games, {len(changes)} changes, {todo_files} todo lists; "
          f"dist has {files:,} files (Cloudflare limit {FILE_LIMIT:,})")
    if files > FILE_LIMIT * 0.9:
        print(f"api: WARNING: dist/ is within 10% of Cloudflare's {FILE_LIMIT:,}-file limit")


# ---------------------------------------------------------------- descriptions

ENDPOINTS = [
    {"path": "/v1/index.json", "description": "This description."},
    {"path": "/v1/projects.json", "description": "Every listed project.",
     "fields": {"projects[].owner, repo": "GitHub owner and repository, with the casing used in every decomp.fun URL",
                "projects[].name": "display name", "projects[].url": "project page", "projects[].api": "project detail JSON",
                "projects[].repository": "GitHub URL", "projects[].game_title": "identified game (CC BY-SA 4.0 game lists)",
                "projects[].platform": "{key, name}", "projects[].slot": "game slot key (see games.json)",
                "projects[].leader": "true if this repository is the one listed for its game",
                "projects[].listed_as": "the repository listed for this game",
                "projects[].archived": "the GitHub repository is archived",
                "projects[].updated": "timestamp of the default build's commit",
                "projects[].versions[]": "{id, default, live, commit{sha,timestamp}, total_code, matched_code, decompiled_percent, "
                "linked_percent, partly_percent, functions{matched,total}, data{total,matched,linked}?, url}"}},
    {"path": "/v1/games.json", "description": "One entry per game slot.",
     "fields": {"games.<slot>": "{platform, game, identified, title, leader, decompiled_percent, url, others[], "
                "challengers{repo: since}, contesters[], contested_by[]}"}},
    {"path": "/v1/systems.json", "description": "Every system.",
     "fields": {"systems[]": "{key, name, short, maker, projects, games, games_known, url}"}},
    {"path": "/v1/{owner}/{repo}.json", "description": "One project: everything in projects.json plus description, "
     "default_branch, stars, source, history, badge, others, contesters, contested_by, and per build todo and units links."},
    {"path": "/v1/{owner}/{repo}/{build}/todo.json", "description": "What to work on: up to 200 source units with code "
     "left, most remaining bytes first.", "fields": {"units[]": "{name, size, percent, remaining, linked}"}},
    {"path": "/{owner}/{repo}/history.json", "description": "Progress history of the default build.",
     "fields": {"[]": "{timestamp, commit_sha, commit_message, source (report|archive|readme), measures{matched_code_percent, "
                "complete_code_percent, fuzzy_match_percent, matched_data_percent?, complete_data_percent?}}"}},
    {"path": "/{owner}/{repo}/units.json, /{owner}/{repo}/{build}/units.json",
     "description": "Every source unit of a build (default build at the project path), for the treemap.",
     "fields": {"[]": "{n: name, s: code bytes, p: percent decompiled incl. partly, l: linked, x, y, w, h: treemap cell}"}},
    {"path": "/v1/changes.json", "description": "The latest 200 progress changes across all projects, newest first.",
     "fields": {"changes[]": "{owner, repo, name, platform, url, timestamp, commit_sha, commit_message, source, "
                "decompiled_percent, decompiled_delta, linked_percent, linked_delta}"}},
    {"path": "/feed.xml", "description": "Atom feed of changes.json."},
    {"path": "/v1/badge/{owner}/{repo}.json", "description": "shields.io endpoint badge.",
     "fields": {"schemaVersion": 1, "label": "decomp.fun", "message": "decompiled percent", "color": "by percent"}},
    {"path": "/health.json", "description": "Site build status.", "fields": {"built": "ISO time", "projects": "count",
     "live": "projects with a live report", "fetch_failed": "projects that failed to update in the last run"}},
    {"path": "/api/fresh?repo={owner}/{repo}", "method": "GET", "description": "Ask the site to re-read this project now "
     "if its CI has a newer report. Rate-limited.", "fields": {"status": "unknown | current | updating | busy | error"}},
]


def index_json(gen):
    return {
        "api_version": API_VERSION,
        "generated": gen,
        "name": "decomp.fun",
        "description": "Directory and leaderboard of matching decompilation projects for video games, with progress read "
        "from each project's own CI. All /v1 files are static JSON, regenerated every 6 hours and after refreshes.",
        "base_url": ORIGIN,
        "docs": ORIGIN + "/llms.txt",
        "cors": "Access-Control-Allow-Origin: * on /v1/*",
        "endpoints": ENDPOINTS,
        "measurement": "decompiled_percent = matched code bytes / total code bytes; linked_percent = code in units that link "
        "fully; partly_percent includes partly matched (fuzzy) code. From the objdiff report on the default branch.",
        "attribution": GAME_LIST_LICENSE,
    }


LLMS_TXT = """\
# decomp.fun

> decomp.fun is a directory and leaderboard of matching decompilation projects for video games: games being
> rewritten in C/C++ that compile back into the exact original binary. Progress is read automatically from each
> project's own CI. It is aimed at people (and their AI agents) decompiling games. Everything below is static and
> public; no key is needed.

## How progress is measured

- Each project's GitHub Actions publish an objdiff (https://github.com/encounter/objdiff) progress report as an
  artifact with "report" in its name, on the default branch. decomp.fun reads the newest one every 6 hours.
  Projects built from dtk-template (https://github.com/encounter/dtk-template) already do this.
- Each artifact is one build (version), e.g. GALE01_report -> build GALE01. The first build listed is the default.
- decompiled_percent = matched code bytes / total code bytes. linked_percent = code in units that link fully.
  partly_percent also counts partly matched ("fuzzy") code. functions = matched / total functions.
- GitHub deletes artifacts after 90 days. A project with no CI run since then keeps its last numbers
  ("live": false means they come from the archived decomp.dev index).
- One project is listed per game (a "slot"). When several repositories work on the same game, the one listed is
  the leader; another takes the slot after leading by the takeover margin for several days in a row.

## Data (JSON, CORS open, regenerated every 6 hours)

- https://decomp.fun/v1/index.json : machine-readable description of every endpoint and field.
- https://decomp.fun/v1/projects.json : every listed project.
  projects[]: owner, repo, name, url, api, repository, game_title, platform {key, name}, slot, leader (bool),
  listed_as, archived (GitHub repo archived), updated, versions[].
  versions[]: id, default, live, commit {sha, timestamp}, total_code, matched_code, decompiled_percent,
  linked_percent, partly_percent, functions {matched, total}, data {total, matched, linked} (if any), url.
- https://decomp.fun/v1/games.json : games.<slot> -> platform, game, identified, title, leader, decompiled_percent,
  url, others[], challengers {repo: since}, contesters[], contested_by[]. Slot keys are "platform:game-key", or
  "repo:owner/name" when the game could not be identified.
- https://decomp.fun/v1/systems.json : systems[]: key, name, short, maker, projects, games, games_known, url.
- https://decomp.fun/v1/OWNER/REPO.json : one project: the projects.json fields plus description, default_branch,
  stars, source, history (URL), badge (URL), others, contesters, contested_by; each version adds todo and units URLs.
  Use the owner/repo casing from projects.json (URLs are case-sensitive).
- https://decomp.fun/v1/OWNER/REPO/BUILD/todo.json : what to work on next in one build. units[] (up to 200): name,
  size (code bytes), percent (decompiled incl. partly), remaining (bytes left = (100 - percent) / 100 * size),
  linked; sorted by remaining, largest first. Also units_total and units_left. Unit-level only; functions are not
  included.
- https://decomp.fun/OWNER/REPO/history.json : progress over time for the default build. []: timestamp,
  commit_sha, commit_message, source (report | archive | readme), measures {matched_code_percent,
  complete_code_percent, fuzzy_match_percent, ...}.
- https://decomp.fun/OWNER/REPO/units.json (default build) and https://decomp.fun/OWNER/REPO/BUILD/units.json
  (other builds): every source unit. []: n (name), s (code bytes), p (percent incl. partly), l (linked),
  x, y, w, h (treemap cell).
- https://decomp.fun/v1/changes.json : the latest 200 progress changes across all projects, newest first.
  changes[]: owner, repo, name, platform, url, timestamp, commit_sha, commit_message, source,
  decompiled_percent, decompiled_delta, linked_percent, linked_delta.
- https://decomp.fun/feed.xml : the same changes as an Atom feed.
- https://decomp.fun/health.json : built (time), projects, live, fetch_failed.

## Pages

- https://decomp.fun/ : all games. /systems/ : by system. /OWNER/REPO/ : a project (default build).
  /OWNER/REPO/BUILD/ : another build. /OWNER/REPO/history/ : history chart. /submit/ : add a project.

## Add a project

Open https://decomp.fun/submit/ and sign in with GitHub (public profile only). Checks, all automatic:
- public GitHub repository whose Actions publish an objdiff report artifact ("report" in its name) on the default branch;
- at least 5% of the code decompiled;
- the game is identified from the repository (disc ID, serial, title ID or ROM hash). PC, DOS and Switch games:
  pick the system and give the game's exact published name;
- one project per game: if the game is listed, your repository must decompile the same executable, and it takes the
  slot once it leads on progress for long enough;
- the GitHub account is at least 30 days old; up to 3 submissions per account per day.
Results appear at https://decomp.fun/submit/ (and https://decomp.fun/submissions.json) within a few minutes.

## Refresh a project immediately from CI

Add this as the last step of the workflow that uploads the report artifact (after the upload):

    - name: Update decomp.fun
      if: github.event_name == 'push' && github.ref_name == github.event.repository.default_branch
      run: curl -fsS "https://decomp.fun/api/fresh?repo=${{ github.repository }}" || true

Or in any shell: curl -fsS "https://decomp.fun/api/fresh?repo=OWNER/REPO"
Response: {"status": "updating"} (a refresh started; the page updates in a few minutes), "current" (nothing newer
seen), "busy" (daily refresh limit reached; the 6-hourly update will pick it up), "unknown" (not listed), "error".
It is rate-limited server-side (each project is checked at most every 10 minutes, and refreshed a few times a day), so
calling it on every push is fine. Only listed projects can be refreshed.

## Badge

    ![decomp.fun](https://img.shields.io/endpoint?url=https://decomp.fun/v1/badge/OWNER/REPO.json)

https://decomp.fun/v1/badge/OWNER/REPO.json is a shields.io endpoint: {"schemaVersion": 1, "label": "decomp.fun",
"message": "94.5%", "color": ...} for the default build's decompiled percent.

## Attribution

Game titles and identifiers come from No-Intro and Redump game lists as packaged by libretro-database
(CC BY-SA 4.0), MAME, titledb and Wikidata. Reuse of those fields must credit them and share alike.
Progress data are facts read from public repositories; credit for the decompilations belongs to their authors.
Site source: https://github.com/g-guthrie/decomp.fun
"""
