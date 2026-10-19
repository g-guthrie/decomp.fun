#!/usr/bin/env python3
"""Backfill history points from git history, for projects the archive never tracked.

  mvc2-naomi: the CI-generated progress table in README.md, one point per commit that changed it
  topspin4:   reports/54540859/report.json, one point per commit that changed it
"""

import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fetch import HISTORY, from_objdiff, gh, pct  # noqa: E402


def commits(repo, path, pages=3):
    out = []
    for page in range(1, pages + 1):
        batch = gh(f"repos/{repo}/commits?path={path}&per_page=100&page={page}")
        out += batch
        if len(batch) < 100:
            break
    return out


def file_at(repo, path, sha):
    c = gh(f"repos/{repo}/contents/{path}?ref={sha}")
    return base64.b64decode(c["content"]).decode("utf-8", "replace")


def merge(pid, points):
    path = HISTORY / f"{pid}.json"
    hist = json.loads(path.read_text()) if path.exists() else []
    seen = {h.get("commit_sha") for h in hist}
    added = [p for p in points if p["commit_sha"] not in seen]
    hist = sorted(hist + added, key=lambda h: h["timestamp"])
    path.write_text(json.dumps(hist, indent=1))
    print(f"{pid}: +{len(added)} points ({len(hist)} total)")


def naomi(pid="mvc2-naomi", repo="g-guthrie/mvc2-naomi-decomp"):
    row = lambda label, text: re.search(rf"\| {re.escape(label)} \|[^|]*\*\*([\d.]+)%\*\*", text)
    points = []
    for c in commits(repo, "README.md"):
        text = file_at(repo, "README.md", c["sha"])
        code = row("Matched reviewed code", text)
        data = row("Data", text)
        if not code:
            continue
        points.append(
            {
                "timestamp": c["commit"]["committer"]["date"],
                "commit_sha": c["sha"],
                "commit_message": c["commit"]["message"].split("\n")[0],
                "source": "readme",
                "measures": {
                    "matched_code_percent": float(code.group(1)),
                    "complete_code_percent": float(code.group(1)),
                    "fuzzy_match_percent": None,
                    "matched_data_percent": float(data.group(1)) if data else None,
                    "complete_data_percent": float(data.group(1)) if data else None,
                },
            }
        )
    merge(pid, points)


def objdiff_file(pid, repo, path):
    points = []
    for c in commits(repo, path):
        try:
            m = from_objdiff(json.loads(file_at(repo, path, c["sha"])))["measures"]
        except (json.JSONDecodeError, KeyError):
            continue
        points.append(
            {
                "timestamp": c["commit"]["committer"]["date"],
                "commit_sha": c["sha"],
                "commit_message": c["commit"]["message"].split("\n")[0],
                "source": "report",
                "measures": {
                    "matched_code_percent": pct(m["matched_code"], m["total_code"]),
                    "complete_code_percent": pct(m["linked_code"], m["total_code"]),
                    "fuzzy_match_percent": m["fuzzy_match_percent"],
                    "matched_data_percent": pct(m["matched_data"], m["total_data"]) if m["total_data"] else None,
                    "complete_data_percent": pct(m["linked_data"], m["total_data"]) if m["total_data"] else None,
                },
            }
        )
    merge(pid, points)


if __name__ == "__main__":
    naomi()
    objdiff_file("topspin4", "g-guthrie/topspin4-decomp", "reports/54540859/report.json")
