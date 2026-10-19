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
        out.append(f"{matched:.2f}% of the code is decompiled; at least {MIN_MATCHED_PERCENT:g}% is required.")
    return out


def listed_names():
    """Every listed repository under its configured name and its current GitHub name (renames)."""
    names = {}
    for p in fetch.all_projects():
        key = f"{p['owner']}/{p['repo']}".lower()
        names[key] = key
        current = (fetch.load_report(p["id"]).get("meta", {}).get("full_name") or "").lower()
        if current:
            names[current] = key
    return names


def source_proof(repo, branch, versions):
    """Share of the report's decompiled files that exist as source in the repository.
    A fabricated report names files that aren't there."""
    try:
        tree = fetch.gh(f"repos/{repo}/git/trees/{branch}?recursive=1").get("tree", [])
    except fetch.GhError:
        return None
    stems = {re.sub(r"\.[A-Za-z0-9]+$", "", t["path"].rsplit("/", 1)[-1]).lower() for t in tree if t["type"] == "blob"}
    done = [u for v in versions for u in v.get("units", []) if u["fuzzy_match_percent"] >= 99.99]
    if not done:
        return 0.0
    found = sum(1 for u in done if re.sub(r"\.[A-Za-z0-9]+$", "", u["name"].rsplit("/", 1)[-1]).lower() in stems)
    return found / len(done)


def recent_commits(repo, n=100):
    try:
        return [c["sha"] for c in fetch.gh(f"repos/{repo}/commits?per_page={n}")]
    except fetch.GhError:
        return []


def check(repo, by, system=None, game=None):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {"repo": repo, "by": by, "submitted_at": now, "status": "rejected", "reasons": []}

    # Canonical name first: GitHub follows renames and transfers.
    try:
        info = fetch.gh(f"repos/{repo}")
    except fetch.GhError:
        entry["reasons"] = ["Repository not found, or it isn't public."]
        return entry
    if info.get("private"):
        entry["reasons"] = ["Repository isn't public."]
        return entry
    repo = info["full_name"]
    key = repo.lower()
    entry["repo"] = repo
    if key in listed_names():
        entry.update(status="already", reasons=["Already on the site."])
        return entry

    owner, name = info["owner"]["login"], info["name"]
    pid = f"{owner}__{name}".lower()
    project = {"id": pid, "owner": owner, "repo": name, "name": name, "submitted": True}
    entry["repo_meta"] = {"id": info["id"], "created_at": info["created_at"], "fork": info.get("fork", False),
                          "parent": (info.get("parent") or {}).get("full_name")}

    # Progress report from the repo's own CI.
    try:
        fetch.fetch_project({**project, "opt_in": True})
    except Exception as ex:
        entry["reasons"] = [f"Couldn't read the repository's Actions artifacts: {ex}"]
        return entry
    record = fetch.load_report(pid)
    versions = record.get("versions") or []
    if not versions:
        entry["reasons"] = [
            "No objdiff progress report found. The repository's GitHub Actions must upload one on the default branch "
            "(not from a fork), as an artifact whose name contains \"report\" (for example GALE01_report)."
        ]
        return entry

    # Which game is it? Read from the repository, never from the form (except PC/DOS/Switch names).
    hit = identify.identify(repo, info["default_branch"], None, [v["id"] for v in versions])
    reasons = []
    if not hit and system in NAME_ONLY and game:
        g = game_by_name(system, game)
        if g:
            hit = {"platform": system, "key": g["key"], "title": g["title"], "by": "name", "aliases": [], "builds": []}
        else:
            reasons.append(f"\"{game}\" isn't on the list of known {system} games.")
    if not hit:
        if not (system in NAME_ONLY and game):
            reasons.append(
                "Couldn't identify the game from the repository. It must contain the game's disc ID, serial, title ID "
                "or ROM hash (as decomp-toolkit and splat projects do). PC, DOS and Switch projects: pick the system and game name."
            )
        entry["reasons"] = reasons
        return entry

    # Main build of this repository, then the report checks on it.
    versions = fetch.order_versions(versions, None, hit.get("builds", []))
    reasons += report_problems(versions[0])
    proof = source_proof(repo, info["default_branch"], versions)
    entry["source_proof"] = proof
    if proof is not None and proof < 0.5:
        reasons.append(
            f"Only {proof:.0%} of the files the report counts as decompiled exist in the repository; at least half must."
        )

    project.update(name=hit["title"], platform=hit["platform"], version=versions[0]["id"])
    entry["game"] = {"platform": hit["platform"], "key": hit["key"], "title": hit["title"], "by": hit["by"],
                     "builds": hit.get("builds", [])}
    entry["project"] = project
    entry["reasons"] = reasons
    if reasons:
        return entry

    # The game's slot: free, or compared build to build with the repositories already in it.
    import build  # Project wrapper gives versions, sizes and unit names
    import slots

    identity = slots.identities()
    disputes = slots.load(slots.DISPUTES, {})
    slot = f"{hit['platform']}:{hit['key']}"
    holders = [k for k in identity if k != key and slots.slot_of(identity, disputes, k) == slot]
    listed = {f"{p['owner']}/{p['repo']}".lower(): p for p in fetch.all_projects()}
    mine = build.Project(project)
    mine_ident = {"builds": entry["game"]["builds"]}
    verdicts = {}
    for h in holders:
        if h in listed:
            hp = build.Project(listed[h])
            if hp.versions:
                verdicts[h] = slots.compare(mine, hp, mine_ident, identity.get(h))

    current = slots.load(slots.STATE, {}).get(slot, {}).get("leader")
    if current in verdicts and verdicts[current] == "conflict":
        entry["commits"] = recent_commits(repo)  # used to tell independent contesters from copies
        entry.update(status="contesting", reasons=[
            f"Contesting {current}, which is listed as {hit['title']}: both name the same build, but their code sizes "
            f"don't match, so one of them isn't that game. If at least {slots.DISPUTE_QUORUM} independent repositories "
            f"(different owners, no shared commits) agree with each other against it, they replace it, unless it is one "
            f"of the original listings, which are only ever marked as contested."
        ])
        return entry
    note = []
    if current:
        note = [
            f"Accepted as a challenger: {current} currently holds {hit['title']}. This repository takes the slot "
            f"once it leads by {slots.TAKEOVER_MARGIN:g}% more code decompiled for {slots.TAKEOVER_DAYS} days in a row "
            f"(and has been listed for at least {slots.CHALLENGER_MIN_DAYS} days)."
        ]
    entry.update(status="listed", reasons=note, listed_at=now)
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
    out = SUBMISSIONS / (re.sub(r"[^a-z0-9_.-]", "_", entry["repo"].lower().replace("/", "__")) + ".json")
    existing = json.loads(out.read_text()) if out.exists() else None
    if existing and existing.get("status") in ("listed", "contesting") and entry["status"] in ("already", "rejected"):
        # A resubmission never replaces a listed or contesting record; it only notes the re-check.
        existing["last_check"] = {"at": entry["submitted_at"], "by": entry["by"], "status": entry["status"],
                                  "reasons": entry["reasons"]}
        out.write_text(json.dumps(existing, indent=1) + "\n")
    elif entry["status"] == "already" and not existing:
        # Listed some other way (inherited or hand-configured): log the result without creating a project.
        out.write_text(json.dumps({**entry, "status": "already"}, indent=1) + "\n")
    else:
        out.write_text(json.dumps(entry, indent=1) + "\n")
    print(f"{entry['repo']}: {entry['status']}" + "".join(f"\n  - {r}" for r in entry["reasons"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
