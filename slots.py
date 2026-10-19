"""One slot per game; the repository with the most matched code holds it.

Comparing two repositories for the same game is done build to build:
  same       they decompile the same executable (same build ID with the same code size,
             or no build IDs but the same code size / file layout)
  conflict   they name the same build ID, but their code sizes disagree: one of them is
             not the game it claims to be
  different  different builds of the game (e.g. USA and Japan); both are legitimate

Takeover: a challenger (same or different build) takes the slot after leading by
TAKEOVER_MARGIN points of matched code for TAKEOVER_DAYS days, ahead on at least STREAK_SHARE of
the runs in that time (one run behind is tolerated; STREAK_MISSES in a row end the streak). Against a
leader with no live report (archived numbers), a live report at equal or better progress counts as ahead.

Disputes: a submission that conflicts with the listed project is recorded as "contesting".
When at least DISPUTE_QUORUM independent contesters (different owners, not forks of each
other) agree with each other, the listed project loses the slot and the best of them takes it.

State: data/slots.json (leaders, challengers), data/disputes.json (projects that lost a dispute).
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import identify

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "slots.json"
DISPUTES = ROOT / "data" / "disputes.json"
IDENTITY = ROOT / "data" / "identity.json"
SUBMISSIONS = ROOT / "data" / "submissions"

TAKEOVER_MARGIN = 1.0  # percentage points of matched code
TAKEOVER_DAYS = 7
STREAK_MISSES = 2  # runs behind in a row that end a challenger's streak (one missed run is tolerated)
STREAK_SHARE = 0.9  # share of runs it must have been ahead on over the TAKEOVER_DAYS
CHALLENGER_MIN_DAYS = 30  # a submitted repository must be listed this long before it can take a slot
DISPUTE_QUORUM = 2
CONTESTER_MIN_REPO_DAYS = 30
SIZE_TOLERANCE = 0.02  # same executable: total code within 2%
LAYOUT_OVERLAP = 0.5


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def key(p):
    """A project's repository key, as used in identity.json, slots.json and disputes.json."""
    return f"{p.owner}/{p.repo}".lower()


def submission_path(repo_key):
    return SUBMISSIONS / (repo_key.replace("/", "__") + ".json")


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


def _close(a, b):
    return bool(a and b) and abs(a - b) <= SIZE_TOLERANCE * max(a, b)


def _names(version):
    return {re.sub(r"\.\w+$", "", u["name"].rsplit("/", 1)[-1]).lower() for u in version.units}


def compare(a, b, ident_a=None, ident_b=None):
    """same | conflict | different, for two projects identified as the same game."""
    def builds(p, ident):
        out = []
        for v in p.versions:
            ids = set(identify.build_ids(v.id))
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


def submission(p):
    path = submission_path(key(p))
    return json.loads(path.read_text()) if path.exists() else {}


def _age_days(iso, now):
    try:
        return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 86400
    except (AttributeError, ValueError):
        return 0


def independent(group, now):
    """Contesters that count: one per owner, no forks of each other, no shared commits (copies of one
    repository pushed under several accounts), and repositories at least CONTESTER_MIN_REPO_DAYS old."""
    owners, keys, seen_commits, chosen = set(), {key(p) for p in group}, set(), []
    for p in sorted(group, key=lambda p: -p.v.code_pct):
        sub = submission(p)
        parent = (p.meta.get("parent") or sub.get("repo_meta", {}).get("parent") or "").lower()
        commits = set(sub.get("commits", []))
        created = p.meta.get("created_at") or sub.get("repo_meta", {}).get("created_at")
        if (p.owner.lower() in owners or parent in keys or commits & seen_commits
                or _age_days(created, now) < CONTESTER_MIN_REPO_DAYS):
            continue
        owners.add(p.owner.lower())
        seen_commits |= commits
        chosen.append(p)
    return chosen


def can_take_over(p, leader, now):
    """Original listings may always compete. A submitted repository must have been listed for
    CHALLENGER_MIN_DAYS, and a fork can't take its own parent's place (it gets the PR link instead)."""
    if not p.raw.get("submitted"):
        return True
    if (p.meta.get("parent") or "").lower() == key(leader):
        return False
    return _age_days(submission(p).get("listed_at"), now) >= CHALLENGER_MIN_DAYS


# ---------------------------------------------------------------- takeover streaks


def beats(p, leader):
    """p is ahead of the slot's leader this run: TAKEOVER_MARGIN points more matched code, or, against a
    leader with no live report (archived numbers only), a live report at least as far along."""
    if leader.v.archived and not p.v.archived:
        return p.v.code_pct >= leader.v.code_pct
    return p.v.code_pct - leader.v.code_pct >= TAKEOVER_MARGIN


def _streak(v):
    """Stored challenger state; older state files stored only the start time."""
    if isinstance(v, str):
        return {"since": v, "runs": 0, "ahead": 0, "misses": 0}
    return {"since": v["since"], "runs": v.get("runs", 0), "ahead": v.get("ahead", 0), "misses": v.get("misses", 0)}


def _run(st, ahead, stamp):
    """Advance a challenger's streak by one run. One run behind (a report caught mid-push, a flaky CI
    run) doesn't end it; STREAK_MISSES behind in a row does."""
    if st is None:
        return {"since": stamp, "runs": 1, "ahead": 1, "misses": 0} if ahead else None
    st = {**st, "runs": st["runs"] + 1}
    if ahead:
        return {**st, "ahead": st["ahead"] + 1, "misses": 0}
    st["misses"] += 1
    return st if st["misses"] < STREAK_MISSES else None


def _ready(st, now):
    """Ahead now, for TAKEOVER_DAYS, on at least STREAK_SHARE of the runs since."""
    if st["misses"] or now - datetime.fromisoformat(st["since"]) < timedelta(days=TAKEOVER_DAYS):
        return False
    return not st["runs"] or st["ahead"] / st["runs"] >= STREAK_SHARE


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

    # Disputes first: a listed project contested by enough independent, mutually agreeing repos.
    # Original listings (inherited or hand-configured) are never removed, only marked contested.
    promoted, contested = [], {}
    by_key = {key(p): p for p in list(projects) + list(contesters)}
    for k, d in list(disputes.items()):  # re-check standing disputes every run
        demoted = by_key.get(k)
        winners = [by_key[w] for w in d.get("replaced_by", []) if w in by_key]
        still = [w for w in winners if demoted and compare(w, demoted, identity.get(key(w)), identity.get(k)) == "conflict"]
        if not demoted or len(independent(still, now)) < DISPUTE_QUORUM:
            disputes.pop(k)
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
        best = []
        for c in against:
            group = [d for d in against if d is c or compare(c, d, identity.get(key(c)), identity.get(key(d))) == "same"]
            group = independent(group, now)
            if len(group) > len(best):
                best = group
        if len(best) < DISPUTE_QUORUM:
            continue
        if not lead.raw.get("submitted"):
            contested[key(lead)] = [key(c) for c in best]
            continue
        winner = max(best, key=lambda c: c.v.code_pct)
        disputes[key(lead)] = {"slot": slot, "at": now.isoformat(timespec="seconds"), "replaced_by": [key(c) for c in best]}
        promoted += best
        state[slot] = {"leader": key(winner), "since": now.isoformat(timespec="seconds"), "challengers": {},
                       "history": state.get(slot, {}).get("history", []) + [
                           {"from": key(lead), "to": key(winner), "at": now.isoformat(timespec="seconds"), "reason": "dispute"}]}

    all_listed = list(projects) + promoted
    by_slot = {}
    for p in all_listed:
        by_slot.setdefault(slot_of(identity, disputes, key(p)), []).append(p)

    result, new_state = {}, {}
    stamp = now.isoformat(timespec="seconds")
    for slot, members in by_slot.items():
        members.sort(key=lambda p: -p.v.code_pct)
        keys = {key(p): p for p in members}
        prev = state.get(slot, {})
        history = prev.get("history", [])
        since = prev.get("since", stamp)
        if prev.get("leader") in keys:
            leader_key = prev["leader"]
        else:
            leader_key = key(members[0])
            if prev.get("leader"):  # the leader left the slot (deleted, delisted, re-identified)
                history = history + [{"from": prev["leader"], "to": leader_key, "at": stamp, "reason": "leader gone"}]
                since = stamp
        leader = keys[leader_key]
        old_streaks = {k: _streak(v) for k, v in prev.get("challengers", {}).items()} if prev.get("leader") == leader_key else {}
        challengers = {}
        for p in members:
            k = key(p)
            if k == leader_key:
                continue
            if not (can_take_over(p, leader, now) and compare(p, leader, identity.get(k), identity.get(leader_key)) != "conflict"):
                continue
            st = _run(old_streaks.get(k), beats(p, leader), stamp)
            if st:
                challengers[k] = st
        ready = [k for k, st in challengers.items() if _ready(st, now)]
        if ready:
            new_key = max(ready, key=lambda k: keys[k].v.code_pct)
            history = history + [{"from": leader_key, "to": new_key, "at": stamp, "reason": "progress"}]
            leader_key, leader = new_key, keys[new_key]
            challengers = {}  # everyone now measures against the new leader
            since = stamp
        new_state[slot] = {"leader": leader_key, "since": since, "challengers": challengers, "history": history}
        contesting = [c for c in contest_by_slot.get(slot, []) if c not in promoted]
        result[slot] = {"leader": leader, "others": [p for p in members if p is not leader],
                        "challengers": {k: st["since"] for k, st in challengers.items() if st["misses"] == 0},
                        "contesters": contesting,
                        "contested_by": contested.get(key(leader), [])}
    listed_now = {key(p) for p in all_listed}
    for slot, prev in state.items():
        # A game's slot whose leader is gone (deleted, made private, delisted) keeps its record, with the
        # departure noted once. A leader that was only re-identified into another slot just moves.
        if slot in new_state or slot.startswith("repo:") or prev.get("leader") in listed_now:
            continue
        if prev.get("leader"):
            prev = {**prev, "leader": None, "challengers": {}, "history": prev.get("history", []) + [
                {"from": prev["leader"], "to": None, "at": stamp, "reason": "leader gone"}]}
        new_state[slot] = prev

    if record:
        STATE.write_text(json.dumps(dict(sorted(new_state.items())), indent=1) + "\n")
        DISPUTES.write_text(json.dumps(dict(sorted(disputes.items())), indent=1) + "\n")
    return result, promoted
