#!/usr/bin/env python3
"""Pull the latest progress reports from GitHub into data/reports/ and data/history/.

Needs the GitHub CLI (`gh`) logged in: report artifacts can only be downloaded
with a token, even from public repositories.

    python3 fetch.py              # every project
    python3 fetch.py melee topspin4   # only ids/repos containing these strings

Sources, per project:
  * explicit "source" in data/projects.json (objdiff_file, objdiff_artifact, naomi_proof)
  * otherwise auto-discovery: the newest non-expired Actions artifact whose name
    contains "report" on the default branch, one per artifact name (= version)
Projects with no reachable report keep their last known (archived) numbers.
"""

import io
import json
import re
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REPORTS = DATA / "reports"
HISTORY = DATA / "history"


class GhError(Exception):
    pass


def gh(path, raw=False):
    r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path], capture_output=True)
    if r.returncode != 0:
        raise GhError(f"{path}: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout if raw else json.loads(r.stdout)


def gh_raw_file(repo, path, ref=None):
    q = f"?ref={ref}" if ref else ""
    r = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}{q}"], capture_output=True
    )
    if r.returncode != 0:
        raise GhError(r.stderr.decode(errors="replace")[:200])
    return r.stdout


def num(v):
    if v is None:
        return 0
    return float(v) if isinstance(v, float) else int(v)


def pct(a, b):
    return a / b * 100 if b else 0.0


# ---------------------------------------------------------------- normalisers


def from_objdiff(report):
    m = report.get("measures", {})
    total_code = num(m.get("total_code"))
    total_data = num(m.get("total_data"))
    measures = {
        "total_code": total_code,
        "matched_code": num(m.get("matched_code")),
        "linked_code": num(m.get("complete_code")),
        "fuzzy_match_percent": float(m.get("fuzzy_match_percent") or 0.0),
        "total_data": total_data,
        "matched_data": num(m.get("matched_data")),
        "linked_data": num(m.get("complete_data")),
        "total_functions": num(m.get("total_functions")),
        "matched_functions": num(m.get("matched_functions")),
    }
    units = []
    for u in report.get("units", []):
        um = u.get("measures", {})
        size = num(um.get("total_code"))
        if size <= 0:
            continue
        units.append(
            {
                "name": u.get("name", "?"),
                "total_code": size,
                "fuzzy_match_percent": round(float(um.get("fuzzy_match_percent") or 0.0), 4),
                "is_linked": num(um.get("complete_code")) >= size,
            }
        )
    categories = [{"id": c.get("id"), "name": c.get("name")} for c in report.get("categories", []) if c.get("id")]
    return {"measures": measures, "units": units, "categories": categories}


def from_naomi_proof(proof):
    """mvc2-naomi-decomp's build proof -> the same shape (mirrors its tools/report.py)."""
    matched = {"code": 0, "data": 0}
    units = []
    for u in proof["units"]:
        code_size = sum(num(s["size"]) for s in u["sections"] if s["kind"] == "code")
        if u.get("credited"):
            got = {"code": 0, "data": 0}
            for s in u["sections"]:
                if s["kind"] in got:
                    got[s["kind"]] += num(s["size"])
                    for inner in s.get("interior", ()):
                        if inner["kind"] != s["kind"] and inner["kind"] in got:
                            got[s["kind"]] -= num(inner["size"])
                            got[inner["kind"]] += num(inner["size"])
        else:
            got = {"code": u.get("function_bytes", 0), "data": u.get("pool_bytes", 0)}
        for k in matched:
            matched[k] += got[k]
        if code_size > 0:
            equal = sum(num(s.get("equal_bytes", 0)) for s in u["sections"] if s["kind"] == "code")
            fuzzy = 100.0 if u.get("credited") else pct(equal, code_size)
            name = u.get("source") or u.get("id", "?")
            name = re.sub(r"^src/(verified|candidates)/", "", name)
            units.append({"name": name, "total_code": code_size, "fuzzy_match_percent": round(fuzzy, 4), "is_linked": bool(u.get("credited"))})
    total_code = proof["mapping"]["reviewed_code_bytes"]
    total_data = proof["mapping"]["reviewed_data_bytes"]
    known = sum(u["total_code"] for u in units)
    if total_code > known:
        units.append({"name": "(not yet in a unit)", "total_code": total_code - known, "fuzzy_match_percent": 0.0, "is_linked": False})
    fuzzy_bytes = sum(u["total_code"] * u["fuzzy_match_percent"] / 100 for u in units)
    measures = {
        "total_code": total_code,
        "matched_code": matched["code"],
        "linked_code": matched["code"],
        "fuzzy_match_percent": max(pct(fuzzy_bytes, total_code), pct(matched["code"], total_code)),
        "total_data": total_data,
        "matched_data": matched["data"],
        "linked_data": matched["data"],
    }
    return {"measures": measures, "units": units, "categories": []}


# ---------------------------------------------------------------- objdiff binary reports
# Minimal protobuf reader for objdiff's report.proto (Report / Measures / ReportUnit /
# ReportCategory). Sections and functions are skipped.

MEASURE_FIELDS = {
    1: ("fuzzy_match_percent", "f"), 2: ("total_code", "i"), 3: ("matched_code", "i"),
    4: ("matched_code_percent", "f"), 5: ("total_data", "i"), 6: ("matched_data", "i"),
    7: ("matched_data_percent", "f"), 8: ("total_functions", "i"), 9: ("matched_functions", "i"),
    10: ("matched_functions_percent", "f"), 11: ("complete_code", "i"), 12: ("complete_code_percent", "f"),
    13: ("complete_data", "i"), 14: ("complete_data_percent", "f"), 15: ("total_units", "i"), 16: ("complete_units", "i"),
}


def pb_fields(buf):
    import struct

    i = 0
    n = len(buf)
    while i < n:
        key = shift = 0
        while True:
            b = buf[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                break
        field, wire = key >> 3, key & 7
        if wire == 0:
            v = shift = 0
            while True:
                b = buf[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if b < 0x80:
                    break
            yield field, v
        elif wire == 1:
            yield field, struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wire == 2:
            ln = shift = 0
            while True:
                b = buf[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if b < 0x80:
                    break
            yield field, buf[i : i + ln]
            i += ln
        elif wire == 5:
            yield field, struct.unpack_from("<f", buf, i)[0]
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")


def pb_measures(buf):
    out = {}
    for f, v in pb_fields(buf):
        if f in MEASURE_FIELDS:
            out[MEASURE_FIELDS[f][0]] = v
    return out


def pb_report(buf):
    report = {"measures": {}, "units": [], "categories": []}
    for f, v in pb_fields(buf):
        if f == 1:
            report["measures"] = pb_measures(v)
        elif f == 2:
            unit = {"name": "", "measures": {}}
            for uf, uv in pb_fields(v):
                if uf == 1:
                    unit["name"] = uv.decode("utf-8", "replace")
                elif uf == 2:
                    unit["measures"] = pb_measures(uv)
            report["units"].append(unit)
        elif f == 4:
            cat = {}
            for cf, cv in pb_fields(v):
                if cf == 1:
                    cat["id"] = cv.decode()
                elif cf == 2:
                    cat["name"] = cv.decode()
            report["categories"].append(cat)
    return report


# ---------------------------------------------------------------- sources


def commit_info(repo, sha):
    c = gh(f"repos/{repo}/commits/{sha}")
    return {
        "sha": c["sha"],
        "message": c["commit"]["message"].split("\n")[0],
        "timestamp": c["commit"]["committer"]["date"],
    }


def unzip_member(blob, wanted):
    """The wanted file, else any report-looking .json, else an objdiff .binpb."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        pick = next((n for n in names if n.rsplit("/", 1)[-1] == wanted), None)
        if pick is not None:
            return json.loads(z.read(pick))
        # Projects name the file differently (progress.json, <version>.json...);
        # take any JSON that has the objdiff report shape.
        for n in sorted(names, key=lambda n: "report" not in n.lower()):
            if n.endswith(".json"):
                try:
                    data = json.loads(z.read(n))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(data, dict) and "measures" in data and "units" in data:
                    return data
        binpb = next((n for n in names if n.endswith(".binpb")), None)
        if binpb is not None:
            return pb_report(z.read(binpb))
        raise GhError(f"no report in artifact ({', '.join(names[:5])})")


def artifact_versions(repo, branch, name_filter=None, workflow=None, previous=None):
    """Newest artifact per name on `branch` -> list of (version_id, artifact)."""
    try:
        arts = gh(f"repos/{repo}/actions/artifacts?per_page=100")["artifacts"]
    except GhError:
        # Very busy repos can time out on the repo-wide listing; walk recent runs instead.
        runs = gh(f"repos/{repo}/actions/runs?branch={branch}&status=success&per_page=20")["workflow_runs"]
        arts = []
        for run in runs:
            for a in gh(f"repos/{repo}/actions/runs/{run['id']}/artifacts")["artifacts"]:
                a.setdefault("workflow_run", {"head_branch": run["head_branch"], "head_sha": run["head_sha"]})
                arts.append(a)
            if any("report" in a["name"].lower() for a in arts):
                break
    newest = {}
    for a in arts:
        run = a.get("workflow_run") or {}
        if a.get("expired") or run.get("head_branch") != branch:
            continue
        if name_filter and a["name"] != name_filter:
            continue
        if not name_filter and "report" not in a["name"].lower():
            continue
        if a["name"] not in newest or a["created_at"] > newest[a["name"]]["created_at"]:
            newest[a["name"]] = a
    out = []
    for name, a in sorted(newest.items()):
        version = re.sub(r"(?i)[_-]?(progress[_-]?)?report(s)?$", "", name) or "default"
        out.append((version, a))
    return out


def fetch_artifact_report(repo, art, file, convert, previous):
    """Reuse the stored version when the artifact hasn't changed."""
    if previous and previous.get("artifact_id") == art["id"]:
        return previous
    blob = gh(f"repos/{repo}/actions/artifacts/{art['id']}/zip", raw=True)
    data = convert(unzip_member(blob, file))
    data["commit"] = commit_info(repo, art["workflow_run"]["head_sha"])
    data["artifact_id"] = art["id"]
    return data


def fetch_project(p):
    pid = p["id"]
    out_path = REPORTS / f"{pid}.json"
    previous = json.loads(out_path.read_text()) if out_path.exists() else {}
    prev_versions = {v["id"]: v for v in previous.get("versions", [])}
    src = p.get("source") or {}
    repo = src.get("repo") or f"{p['owner']}/{p['repo']}"

    try:
        info = gh(f"repos/{repo}")
    except GhError as ex:
        if "HTTP 404" not in str(ex):
            raise
        # Deleted or made private: stop listing it.
        out_path.write_text(json.dumps({"meta": {"missing": True}, "versions": []}))
        return pid, "missing"
    meta = {
        "description": info.get("description"),
        "default_branch": info["default_branch"],
        "archived": info.get("archived", False),
        "stars": info.get("stargazers_count", 0),
        "html_url": info["html_url"],
    }
    versions = []
    kind = src.get("type", "auto")
    if kind == "objdiff_file":
        raw = gh_raw_file(repo, src["path"])
        data = from_objdiff(json.loads(raw))
        last = gh(f"repos/{repo}/commits?path={src['path']}&per_page=1")[0]["sha"]
        data["commit"] = commit_info(repo, last)
        data["id"] = p.get("version") or "default"
        versions.append(data)
    elif kind in ("objdiff_artifact", "naomi_proof", "auto"):
        convert = from_naomi_proof if kind == "naomi_proof" else from_objdiff
        branch = src.get("branch") or meta["default_branch"]
        found = artifact_versions(repo, branch, src.get("artifact"))
        for version, art in found:
            vid = p.get("version") if (src.get("artifact") and p.get("version")) else version
            try:
                data = fetch_artifact_report(repo, art, src.get("file", "report.json"), convert, prev_versions.get(vid))
            except (GhError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as ex:
                print(f"  {pid}: {art['name']}: {ex}", file=sys.stderr)
                continue
            if data["measures"]["total_code"] <= 0:
                continue  # some other kind of "report" artifact
            data["id"] = vid
            versions.append(data)
    else:
        raise SystemExit(f"{pid}: unknown source type {kind}")

    if not versions and previous.get("versions"):
        versions = previous["versions"]  # keep last good data
    record = {"meta": meta, "versions": versions, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out_path.write_text(json.dumps(record, separators=(",", ":")))
    if versions:
        append_history(pid, versions[0])
    return pid, len(versions)


def append_history(pid, v):
    path = HISTORY / f"{pid}.json"
    hist = json.loads(path.read_text()) if path.exists() else []
    if any(h.get("commit_sha") == v["commit"]["sha"] for h in hist):
        return
    m = v["measures"]
    hist.append(
        {
            "timestamp": v["commit"]["timestamp"],
            "commit_sha": v["commit"]["sha"],
            "commit_message": v["commit"]["message"],
            "source": "report",
            "measures": {
                "fuzzy_match_percent": m["fuzzy_match_percent"],
                "matched_code_percent": pct(m["matched_code"], m["total_code"]),
                "complete_code_percent": pct(m["linked_code"], m["total_code"]),
                "matched_data_percent": pct(m["matched_data"], m["total_data"]) if m["total_data"] else None,
                "complete_data_percent": pct(m["linked_data"], m["total_data"]) if m["total_data"] else None,
            },
        }
    )
    hist.sort(key=lambda h: h["timestamp"])
    path.write_text(json.dumps(hist, indent=1))


def all_projects():
    catalog = json.loads((DATA / "catalog.json").read_text()) if (DATA / "catalog.json").exists() else []
    ours = json.loads((DATA / "projects.json").read_text())
    by_repo = {f"{p['owner']}/{p['repo']}".lower(): p for p in catalog}
    for o in ours:
        key = f"{o['owner']}/{o['repo']}".lower()
        base = by_repo.get(key, {})
        merged = {**base, **o}
        merged["id"] = base.get("id") or o["id"]
        by_repo[key] = merged
    return list(by_repo.values())


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    projects = [p for p in all_projects() if not only or any(o.lower() in (p["id"] + p["repo"]).lower() for o in only)]
    print(f"fetching {len(projects)} project(s)")
    ok = missing = failed = missing_repo = 0
    no_report = []

    def run(p):
        try:
            return fetch_project(p)
        except GhError as ex:
            return p["id"], f"error: {ex}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for pid, res in pool.map(run, projects):
            if res == "missing":
                missing_repo += 1
                print(f"  {pid}: repository gone (404), will not be listed")
            elif isinstance(res, str):
                failed += 1
                print(f"  {pid}: {res}")
            elif res:
                ok += 1
            else:
                missing += 1
                no_report.append(pid)
    print(f"live reports: {ok}, no report available: {missing}, repos gone: {missing_repo}, failed: {failed}")
    if no_report:
        print("no report: " + " ".join(sorted(no_report)))


if __name__ == "__main__":
    main()
