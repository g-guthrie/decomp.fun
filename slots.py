"""One slot per game; the repository with the most matched code holds it.

Comparing two repositories for the same game is done build to build:
  same       they decompile the same executable (same build ID with the same code size,
             or no build IDs but the same code size / file layout)
  conflict   they name the same build ID, but their code sizes disagree: one of them is
             not the game it claims to be
  different  different builds of the game (e.g. USA and Japan); both are legitimate

Takeover: a challenger (same or different build) takes the slot after leading by
TAKEOVER_MARGIN points of matched code for TAKEOVER_DAYS days in a row.

Disputes: a submission that conflicts with the listed project is recorded as "contesting".
When at least DISPUTE_QUORUM independent contesters (different owners, not forks of each
other) agree with each other, the listed project loses the slot and the best of them takes it.

State: data/slots.json (leaders, challengers), data/disputes.json (projects that lost a dispute).
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "slots.json"
DISPUTES = ROOT / "data" / "disputes.json"
IDENTITY = ROOT / "data" / "identity.json"
SUBMISSIONS = ROOT / "data" / "submissions"

TAKEOVER_MARGIN = 1.0  # percentage points of matched code
TAKEOVER_DAYS = 7
DISPUTE_QUORUM = 2
SIZE_TOLERANCE = 0.02  # same executable: total code within 2%
LAYOUT_OVERLAP = 0.5


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def identities():
    """repo key -> {platform, game, builds} for inherited and accepted/contesting submissions."""
    ident = load(IDENTITY, {})
    for f in sorted(SUBMISSIONS.glob("*.json")):
        sub = json.loads(f.read_text())
        if sub.get("status") in ("listed", "contesting") and sub.get("game"):
            ident.setdefault(sub["repo"].lower(), {
                "platform": sub["game"]["platform"], "game": sub["game"]["key"], "builds": sub["game"].get("builds", []),
            })
    return ident


def slot_of(identity, disputes, repo_key):
    if repo_key in disputes:
        return f"repo:{repo_key}"
    ident = identity.get(repo_key)
    if ident and ident.get("game"):
        return f"{ident['platform']}:{ident['game']}"
    return f"repo:{repo_key}"


# ---------------------------------------------------------------- comparing repositories


def _ids(version):
    import identify

    return identify.build_ids(version.id)


def _close(a, b):
    return bool(a and b) and abs(a - b) <= SIZE_TOLERANCE * max(a, b)


def _names(version):
    return {re.sub(r"\.\w+$", "", u["name"].rsplit("/", 1)[-1]).lower() for u in version.units}


def compare(a, b, ident_a=None, ident_b=None):
    """same | conflict | different, for two projects identified as the same game."""
    def builds(p, ident):
        out = []
        for v in p.versions:
            ids = set(_ids(v))
            if len(p.versions) == 1 and ident:
                ids |= set(ident.get("builds", []))
            out.append((v, ids))
        return out

    ba, bb = builds(a, ident_a), builds(b, ident_b)
    for va, ia in ba:
        for vb, ib in bb:
            if ia & ib:
                if va.archived or vb.archived or not (va.total_code and vb.total_code):
                    return "same"  # no sizes to compare
                return "same" if _close(va.total_code, vb.total_code) else "conflict"
    for va, _ in ba:
        for vb, _ in bb:
            if _close(va.total_code, vb.total_code):
                return "same"
            na, nb = _names(va), _names(vb)
            if len(na) >= 10 and len(nb) >= 10 and len(na & nb) / len(na | nb) >= LAYOUT_OVERLAP:
                return "same"
    return "different"


def independent(group):
    """At most one repository per owner, and none that is a fork of another in the group."""
    owners, keys, chosen = set(), {f"{p.owner}/{p.repo}".lower() for p in group}, []
    for p in sorted(group, key=lambda p: -p.v.code_pct):
        parent = (p.meta.get("parent") or "").lower()
        if p.owner.lower() in owners or parent in keys:
            continue
        owners.add(p.owner.lower())
        chosen.append(p)
    return chosen


# ---------------------------------------------------------------- assignment


def assign(projects, contesters=(), now=None, record=True):
    """Decide every slot's leader.

    projects: listed build.Project objects. contesters: Project objects for submissions that
    conflict with a listed project. Returns (slots, promoted) where slots maps a slot to
    {"leader", "others", "challengers", "contesters"} and promoted lists contesters that won a
    dispute (they become listed). Writes data/slots.json and data/disputes.json when record=True.
    """
    now = now or datetime.now(timezone.utc)
    identity = identities()
    disputes = load(DISPUTES, {})
    state = load(STATE, {})
    key = lambda p: f"{p.owner}/{p.repo}".lower()

    # Disputes first: a listed project contested by enough independent, mutually agreeing repos.
    promoted = []
    contest_by_slot = {}
    for c in contesters:
        contest_by_slot.setdefault(slot_of(identity, {}, key(c)), []).append(c)
    by_slot = {}
    for p in projects:
        by_slot.setdefault(slot_of(identity, disputes, key(p)), []).append(p)
    for slot, cs in contest_by_slot.items():
        holders = by_slot.get(slot, [])
        if not holders:
            continue
        lead_key = state.get(slot, {}).get("leader")
        lead = next((h for h in holders if key(h) == lead_key), max(holders, key=lambda h: h.v.code_pct))
        against = [c for c in cs if compare(c, lead, identity.get(key(c)), identity.get(key(lead))) == "conflict"]
        # The largest group of contesters that agree with each other.
        best = []
        for c in against:
            group = [d for d in against if d is c or compare(c, d, identity.get(key(c)), identity.get(key(d))) == "same"]
            group = independent(group)
            if len(group) > len(best):
                best = group
        if len(best) >= DISPUTE_QUORUM:
            disputes[key(lead)] = {
                "slot": slot, "at": now.isoformat(timespec="seconds"),
                "replaced_by": [key(c) for c in best],
            }
            promoted += best
            state[slot] = {"leader": key(max(best, key=lambda c: c.v.code_pct)), "since": now.isoformat(timespec="seconds"),
                           "challengers": {}, "history": state.get(slot, {}).get("history", []) + [
                               {"from": key(lead), "to": key(max(best, key=lambda c: c.v.code_pct)),
                                "at": now.isoformat(timespec="seconds"), "reason": "dispute"}]}

    all_listed = list(projects) + promoted
    by_slot = {}
    for p in all_listed:
        by_slot.setdefault(slot_of(identity, disputes, key(p)), []).append(p)

    result, new_state = {}, {}
    for slot, members in by_slot.items():
        members.sort(key=lambda p: -p.v.code_pct)
        keys = {key(p): p for p in members}
        prev = state.get(slot, {})
        leader_key = prev.get("leader") if prev.get("leader") in keys else key(members[0])
        leader = keys[leader_key]
        challengers = {}
        for p in members:
            k = key(p)
            if k == leader_key:
                continue
            ahead = p.v.code_pct - leader.v.code_pct >= TAKEOVER_MARGIN
            if ahead and compare(p, leader, identity.get(k), identity.get(leader_key)) != "conflict":
                challengers[k] = prev.get("challengers", {}).get(k, now.isoformat(timespec="seconds"))
        ready = [k for k, since in challengers.items() if now - datetime.fromisoformat(since) >= timedelta(days=TAKEOVER_DAYS)]
        history = prev.get("history", [])
        since = prev.get("since", now.isoformat(timespec="seconds"))
        if ready:
            new_key = max(ready, key=lambda k: keys[k].v.code_pct)
            history = history + [{"from": leader_key, "to": new_key, "at": now.isoformat(timespec="seconds"), "reason": "progress"}]
            leader_key, leader = new_key, keys[new_key]
            challengers.pop(new_key, None)
            since = now.isoformat(timespec="seconds")
        new_state[slot] = {"leader": leader_key, "since": since, "challengers": challengers, "history": history}
        contesting = [c for c in contest_by_slot.get(slot, []) if c not in promoted]
        result[slot] = {"leader": leader, "others": [p for p in members if p is not leader],
                        "challengers": challengers, "contesters": contesting}

    if record:
        STATE.write_text(json.dumps(dict(sorted(new_state.items())), indent=1) + "\n")
        DISPUTES.write_text(json.dumps(dict(sorted(disputes.items())), indent=1) + "\n")
    return result, promoted
