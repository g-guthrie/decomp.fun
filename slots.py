"""One slot per game; the repository with the most matched code holds it.

A challenger takes over only after leading by TAKEOVER_MARGIN percentage points for
TAKEOVER_DAYS in a row, and only if it is decompiling the same binary as the leader
(same code size, or largely the same file layout). State: data/slots.json.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "slots.json"
IDENTITY = ROOT / "data" / "identity.json"

TAKEOVER_MARGIN = 1.0  # percentage points of matched code
TAKEOVER_DAYS = 7
SIZE_TOLERANCE = 0.02  # same binary: total code within 2%
LAYOUT_OVERLAP = 0.5  # or at least half the file names in common


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def slot_of(identity, repo_key):
    ident = identity.get(repo_key)
    if ident and ident.get("game"):
        return f"{ident['platform']}:{ident['game']}"
    return f"repo:{repo_key}"


def unit_names(project):
    return {re.sub(r"\.\w+$", "", u["name"].rsplit("/", 1)[-1]).lower() for u in project.v.units}


def same_binary(a, b):
    """Two projects decompile the same executable."""
    ta, tb = a.v.total_code, b.v.total_code
    if ta and tb and abs(ta - tb) <= SIZE_TOLERANCE * max(ta, tb):
        return True
    na, nb = unit_names(a), unit_names(b)
    if len(na) >= 10 and len(nb) >= 10:
        return len(na & nb) / len(na | nb) >= LAYOUT_OVERLAP
    return False


def assign(projects, now=None):
    """Decide the leader of every slot. Returns {slot: {"leader": project, "others": [project...]}}
    and updates data/slots.json. `projects` are build.Project objects."""
    now = now or datetime.now(timezone.utc)
    identity = load(IDENTITY, {})
    state = load(STATE, {})
    by_slot = {}
    for p in projects:
        key = f"{p.owner}/{p.repo}".lower()
        by_slot.setdefault(slot_of(identity, key), []).append((key, p))

    result = {}
    new_state = {}
    for slot, members in by_slot.items():
        members.sort(key=lambda kp: -kp[1].v.code_pct)
        keys = {k: p for k, p in members}
        prev = state.get(slot, {})
        leader_key = prev.get("leader") if prev.get("leader") in keys else members[0][0]
        leader = keys[leader_key]
        challengers = {}
        for k, p in members:
            if k == leader_key:
                continue
            ahead = p.v.code_pct - leader.v.code_pct >= TAKEOVER_MARGIN
            eligible = ahead and (same_binary(p, leader) or not p.raw.get("submitted"))
            if not eligible:
                continue
            since = prev.get("challengers", {}).get(k, now.isoformat(timespec="seconds"))
            challengers[k] = since
        # The challenger that has led longest (past the hold period) takes over.
        ready = [
            (since, k) for k, since in challengers.items()
            if now - datetime.fromisoformat(since) >= timedelta(days=TAKEOVER_DAYS)
        ]
        changed_at = prev.get("since", now.isoformat(timespec="seconds"))
        if ready:
            _, new_key = max(ready, key=lambda r: keys[r[1]].v.code_pct)
            history = prev.get("history", []) + [{"from": leader_key, "to": new_key, "at": now.isoformat(timespec="seconds")}]
            leader_key, leader = new_key, keys[new_key]
            challengers.pop(new_key, None)
            changed_at = now.isoformat(timespec="seconds")
        else:
            history = prev.get("history", [])
        new_state[slot] = {"leader": leader_key, "since": changed_at, "challengers": challengers, "history": history}
        others = [p for k, p in members if k != leader_key]
        result[slot] = {"leader": leader, "others": others, "challengers": challengers, "history": history}

    STATE.write_text(json.dumps(dict(sorted(new_state.items())), indent=1) + "\n")
    return result
