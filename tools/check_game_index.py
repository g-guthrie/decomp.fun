#!/usr/bin/env python3
"""Compare data/games/*.json with the committed copies (git HEAD) and fail if any system's list
shrank by more than MAX_SHRINK, so a broken upstream source never replaces a good list.

    python3 tools/check_game_index.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMES = ROOT / "data" / "games"
MAX_SHRINK = 0.20


def committed(name):
    r = subprocess.run(["git", "show", f"HEAD:data/games/{name}"], cwd=ROOT, capture_output=True)
    return len(json.loads(r.stdout).get("games", [])) if r.returncode == 0 else None


def main():
    bad = []
    for f in sorted(GAMES.glob("*.json")):
        new = len(json.loads(f.read_text()).get("games", []))
        old = committed(f.name)
        change = "new" if old is None else f"{old} -> {new}"
        print(f"{f.stem}: {change}")
        if old and new < old * (1 - MAX_SHRINK):
            bad.append(f"{f.stem}: {old} -> {new} games")
    r = subprocess.run(["git", "ls-tree", "--name-only", "HEAD", "data/games/"], cwd=ROOT, capture_output=True, text=True)
    for path in r.stdout.split():
        if not (ROOT / path).exists():
            bad.append(f"{Path(path).stem}: file disappeared")
    if bad:
        print("Game lists shrank by more than {:.0%}; not committing:\n  ".format(MAX_SHRINK) + "\n  ".join(bad), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
