#!/usr/bin/env python3
"""Check one submitted repository and record the outcome in data/submissions.json.

    python3 submit.py owner/repo --by github-login [--system win32 --game "Thief: Deadly Shadows"]

Run by the workflow when the site receives a submission. Every check is automatic:
  * the repository exists and is public, and isn't already on the site
  * its GitHub Actions publish a real objdiff progress report that adds up
  * at least MIN_MATCHED_PERCENT of the code is matched
  * the game is identified from the repository itself (disc ID, serial, title ID, ROM hash),
    or for PC/Switch, the named game is on that system's list
  * the game's slot is free, or this repository decompiles the same binary as the current
    holder (then it becomes a challenger and takes over with more progress; see slots.py)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import fetch
import identify

SUBMISSIONS = fetch.DATA / "submissions"
IDENTITY = fetch.DATA / "identity.json"
MIN_MATCHED_PERCENT = 5.0
NAME_ONLY = ("win32", "switch", "dos")


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, data):
    path.write_text(json.dumps(dict(sorted(data.items())), indent=1) + "\n")


def game_by_name(system, name):
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    path = identify.GAMES / f"{system}.json"
    if not path.exists() or not key:
        return None
    return next((g for g in json.loads(path.read_text())["games"] if g["key"] == key), None)


def report_problems(v):
    m = v["measures"]
    out = []
    unit_total = sum(u["total_code"] for u in v.get("units", []))
    if (
        not m["total_code"]
        or m["matched_code"] > m["total_code"]
        or m["linked_code"] > m["total_code"]
        or not v.get("units")
        or unit_total > m["total_code"] * 1.01
        or unit_total < m["total_code"] * 0.5
        or (m.get("total_functions") or 0) < (m.get("matched_functions") or 0)
    ):
        out.append("The progress report's numbers don't add up (totals, matched sizes and per-file sizes disagree).")
    matched = fetch.pct(m["matched_code"], m["total_code"])
    if matched < MIN_MATCHED_PERCENT:
        out.append(f"{matched:.2f}% of the code is matched; at least {MIN_MATCHED_PERCENT:g}% is required.")
    return out


def check(repo, by, system=None, game=None):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    key = repo.lower()
    entry = {"repo": repo, "by": by, "submitted_at": now, "status": "rejected", "reasons": []}
    listed = {f"{p['owner']}/{p['repo']}".lower() for p in fetch.all_projects()}
    if key in listed:
        entry.update(status="listed", reasons=["Already on the site."])
        return entry

    try:
        info = fetch.gh(f"repos/{repo}")
    except fetch.GhError:
        entry["reasons"] = ["Repository not found, or it isn't public."]
        return entry
    if info.get("private"):
        entry["reasons"] = ["Repository isn't public."]
        return entry
    owner, name = info["owner"]["login"], info["name"]
    pid = f"{owner}__{name}".lower()
    project = {"id": pid, "owner": owner, "repo": name, "name": name, "submitted": True}

    # Progress report from the repo's own CI.
    try:
        fetch.fetch_project({**project, "opt_in": True})
    except fetch.GhError as ex:
        entry["reasons"] = [f"Couldn't read the repository's Actions artifacts: {ex}"]
        return entry
    record = fetch.load_report(pid)
    versions = record.get("versions") or []
    if not versions:
        entry["reasons"] = [
            "No objdiff progress report found. The repository's GitHub Actions must upload one on the default branch, "
            "as an artifact whose name contains \"report\" (for example GALE01_report)."
        ]
        return entry
    reasons = report_problems(versions[0])

    # Which game is it? Read from the repository, never from the form (except PC/Switch names).
    hit = identify.identify(f"{owner}/{name}", info["default_branch"], None, [v["id"] for v in versions])
    if not hit and system in NAME_ONLY and game:
        g = game_by_name(system, game)
        if g:
            hit = {"platform": system, "key": g["key"], "title": g["title"], "by": "name", "aliases": []}
        else:
            reasons.append(f"\"{game}\" isn't on the list of known {system} games.")
    if not hit:
        if not (system in NAME_ONLY and game):
            reasons.append(
                "Couldn't identify the game from the repository. It must contain the game's disc ID, serial, title ID "
                "or ROM hash (as decomp-toolkit and splat projects do). PC and Switch projects: pick the system and game name."
            )
        entry["reasons"] = reasons
        return entry

    project.update(name=hit["title"], platform=hit["platform"])
    entry["game"] = {"platform": hit["platform"], "key": hit["key"], "title": hit["title"], "by": hit["by"]}

    # Slot: free, or held by a repository decompiling the same binary.
    identity = load(IDENTITY)
    slot = f"{hit['platform']}:{hit['key']}"
    holders = [k for k, v in identity.items() if v.get("game") and f"{v['platform']}:{v['game']}" == slot]
    holders += [s["repo"].lower() for s in fetch.load_submissions() if s.get("status") == "listed" and s.get("game")
                and f"{s['game']['platform']}:{s['game']['key']}" == slot and s["repo"].lower() != key]
    if holders and not reasons:
        import build  # Project wrapper gives sizes and unit names for the comparison
        import slots

        mine = build.Project({**project, "version": versions[0]["id"]})
        for h in holders:
            hp = next((p for p in fetch.all_projects() if f"{p['owner']}/{p['repo']}".lower() == h), None)
            if hp and build.Project(hp).versions and slots.same_binary(mine, build.Project(hp)):
                break
        else:
            reasons.append(
                f"{hit['title']} is already listed, and this repository doesn't decompile the same binary as "
                f"{holders[0]} (different code size and file layout)."
            )

    entry["reasons"] = reasons
    if reasons:
        return entry
    note = []
    if holders:
        current = json.loads((fetch.DATA / "slots.json").read_text()).get(slot, {}).get("leader") if (fetch.DATA / "slots.json").exists() else None
        if current:
            note = [
                f"Accepted as a challenger: {current} currently holds {hit['title']}. This repository takes the slot "
                "once it leads by 1% of matched code for 7 days in a row."
            ]
    entry.update(status="listed", project=project, reasons=note)
    if versions:
        fetch.append_history(pid, versions[0])
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--by", required=True)
    ap.add_argument("--system")
    ap.add_argument("--game")
    a = ap.parse_args()
    repo = re.sub(r"^(https?://)?(www\.)?github\.com/", "", a.repo.strip()).strip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        print(f"invalid repository: {a.repo}")
        return 1
    entry = check(repo, a.by, a.system, a.game)
    SUBMISSIONS.mkdir(parents=True, exist_ok=True)
    out = SUBMISSIONS / (re.sub(r"[^a-z0-9_.-]", "_", repo.lower().replace("/", "__")) + ".json")
    out.write_text(json.dumps(entry, indent=1) + "\n")
    print(f"{repo}: {entry['status']}" + "".join(f"\n  - {r}" for r in entry["reasons"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
