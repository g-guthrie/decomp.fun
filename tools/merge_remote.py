#!/usr/bin/env python3
"""Before building, take anything newer that another run committed meanwhile, file by file.

Runs overlap (the hourly refresh, a submission check, a refresh-on-view). Each one restored its
data at the start; this makes sure a deploy never puts older numbers or older submission results
over newer ones. Never produces conflicts: for each file the newer copy simply wins.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def newest_commit(rec):
    return max((v.get("commit", {}).get("timestamp", "") for v in rec.get("versions", [])), default="")


def stamp_submission(rec):
    return max(rec.get("submitted_at", ""), (rec.get("last_check") or {}).get("at", ""))


def main():
    if git("fetch", "-q", "origin", "main").returncode != 0:
        print("merge_remote: fetch failed, building with local data")
        return 0
    taken = 0
    for folder, stamp in (("data/latest", newest_commit), ("data/submissions", stamp_submission)):
        listing = git("ls-tree", "--name-only", "origin/main", f"{folder}/").stdout.split()
        for path in listing:
            if not path.endswith(".json"):
                continue
            theirs = json.loads(git("show", f"origin/main:{path}").stdout or "{}")
            local = ROOT / path
            mine = json.loads(local.read_text()) if local.exists() else {}
            if not mine or stamp(theirs) > stamp(mine):
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(json.dumps(theirs, indent=1 if folder == "data/submissions" else None,
                                            separators=None if folder == "data/submissions" else (",", ":")) + ("\n" if folder == "data/submissions" else ""))
                taken += 1
    print(f"merge_remote: took {taken} newer file(s) from origin/main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
