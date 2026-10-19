#!/usr/bin/env python3
"""One-off import of the public decomp.dev project index from the Internet Archive.

Reads the archived listing pages (one per month) and writes:
  data/catalog.json            every project in the last archived listing
  data/history/<id>.json       monthly progress points seen in the archive
  data/images/<id>.jpg         banner images, where the archive has them

Only facts are taken (names, repo links, platforms, percentages, commits).
No decomp.dev code is used. Downloads are cached in work/archive/.
"""

import html
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = ROOT / "work" / "archive"
LATEST = "20261001024908"  # last full listing before the sunset notice

UA = {"User-Agent": "decomp.fun archive import (https://decomp.fun)"}


def http_get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read(), r.headers.get("Content-Encoding", "")
        except Exception as ex:  # archive.org is flaky; back off and retry
            if i == tries - 1:
                raise
            print(f"  retry {url}: {ex}", file=sys.stderr)
            time.sleep(5 * (i + 1))


def decode(body, enc):
    if body[:2] == b"\x1f\x8b" or enc == "gzip":
        import gzip

        return gzip.decompress(body)
    if enc == "zstd" or body[:4] == b"\x28\xb5\x2f\xfd":
        return subprocess.run(["zstd", "-dq", "-c"], input=body, capture_output=True, check=True).stdout
    if enc == "br":
        return subprocess.run(["brotli", "-d", "-c"], input=body, capture_output=True, check=True).stdout
    return body


def wayback(ts, url, binary=False):
    key = CACHE / (ts + "_" + re.sub(r"[^A-Za-z0-9._-]", "_", url)[-120:])
    if key.exists():
        data = key.read_bytes()
    else:
        body, enc = http_get(f"https://web.archive.org/web/{ts}id_/{url}")
        data = decode(body, enc)
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_bytes(data)
        time.sleep(1)
    return data if binary else data.decode("utf-8", "replace")


def monthly_snapshots():
    body, _ = http_get(
        "https://web.archive.org/cdx/search/cdx?url=decomp.dev/&fl=timestamp&filter=statuscode:200&filter=mimetype:text/html"
    )
    by_month = {}
    for ts in body.decode().split():
        if ts < "20261002":  # later captures are the sunset notice
            by_month[ts[:6]] = ts
    snaps = sorted(by_month.values())
    if LATEST not in snaps:
        snaps.append(LATEST)
    return sorted(snaps)


def text(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


SIZE = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}


def parse_size(s):
    m = re.match(r"([\d.]+) (B|kB|MB|GB)", s)
    return float(m.group(1)) * SIZE[m.group(2)] if m else None


def parse_listing(page):
    projects = []
    for art in re.findall(r'<article class="project".*?</article>', page, re.S):
        href = re.search(r'href="(?:https://decomp\.dev)?/([^/"?]+)/([^/"?]+)"', art)
        if not href:
            continue
        owner, repo = href.group(1), href.group(2)
        title = re.search(r'class="project-title">(.*?)</h3>', art, re.S) or re.search(r"<h3>\s*<a[^>]*>(.*?)</a>", art, re.S)
        platform = re.search(r'platform-icon icon-([a-z0-9]+)', art)
        image = re.search(r'/images/([0-9a-f]{64})\.jpg', art)
        commit = re.search(r'github\.com/[^"]+/commit/([0-9a-f]{40})', art)
        ts = re.search(r'<span title="([\d-]+ [\d:]+) \+00:00"', art)
        p = {
            "owner": owner,
            "repo": repo,
            "name": text(title.group(1)) if title else repo,
            "platform": platform.group(1) if platform else None,
            "image": image.group(1) if image else None,
            "commit": commit.group(1) if commit else None,
            "timestamp": ts.group(1).replace(" ", "T") + "Z" if ts else None,
            "matched_code_percent": 0.0,
            "complete_code_percent": 0.0,
            "fuzzy_match_percent": None,
            "total_code": None,
        }
        for pct, kind, size in re.findall(r'data-tooltip="([\d.]+)% (fully linked|perfect match|fuzzy match)(?: \(([^)]+)\))?"', art):
            pct = float(pct)
            if kind == "fully linked":
                p["complete_code_percent"] = pct
            elif kind == "perfect match":
                p["matched_code_percent"] = pct
            else:
                p["fuzzy_match_percent"] = pct
            b = parse_size(size) if size else None
            if b and pct > 0 and not p["total_code"]:
                p["total_code"] = round(b / (pct / 100))
        if p["matched_code_percent"] < p["complete_code_percent"]:
            p["matched_code_percent"] = p["complete_code_percent"]
        if p["fuzzy_match_percent"] is None:
            p["fuzzy_match_percent"] = p["matched_code_percent"]
        projects.append(p)
    return projects


def pid(owner, repo):
    return f"{owner}__{repo}".lower()


def main():
    snaps = monthly_snapshots()
    print(f"{len(snaps)} monthly snapshots")
    history = {}
    latest = []
    for ts in snaps:
        page = wayback(ts, "https://decomp.dev/")
        projects = parse_listing(page)
        print(f"  {ts}: {len(projects)} projects")
        for p in projects:
            if not p["timestamp"]:
                continue
            h = history.setdefault(pid(p["owner"], p["repo"]), {})
            h[p["commit"] or p["timestamp"]] = {
                "timestamp": p["timestamp"],
                "commit_sha": p["commit"] or "",
                "commit_message": "",
                "source": "archive",
                "measures": {
                    "matched_code_percent": p["matched_code_percent"],
                    "complete_code_percent": p["complete_code_percent"],
                    "fuzzy_match_percent": p["fuzzy_match_percent"],
                },
            }
        if ts == LATEST:
            latest = projects

    (DATA / "history").mkdir(parents=True, exist_ok=True)
    (DATA / "images").mkdir(parents=True, exist_ok=True)
    catalog = []
    for p in latest:
        i = pid(p["owner"], p["repo"])
        p["id"] = i
        p["image_hash"] = p.pop("image")
        p["image"] = f"images/{i}.jpg" if p["image_hash"] else None
        catalog.append(p)
        points = sorted(history.get(i, {}).values(), key=lambda h: h["timestamp"])
        (DATA / "history" / f"{i}.json").write_text(json.dumps(points, indent=1))
    (DATA / "catalog.json").write_text(json.dumps(catalog, indent=1))
    print(f"catalog: {len(catalog)} projects, {sum(1 for p in catalog if p['image'])} with banner images")
    # Banner images are not imported: the site generates its own from each project's progress map.


def fetch_images(catalog):
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        out = DATA / p["image"]
        if out.exists():
            return None
        try:
            img = wayback(LATEST, f"https://decomp.dev/images/{p['image_hash']}.jpg?w=1024&h=256", binary=True)
        except Exception as ex:
            return f"{p['id']}: {ex}"
        if img[:3] != b"\xff\xd8\xff":
            return f"{p['id']}: not archived"
        out.write_bytes(img)
        return None

    todo = [p for p in catalog if p["image"]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for err in pool.map(one, todo):
            if err:
                print("  image", err)
    print(f"images: {sum(1 for p in todo if (DATA / p['image']).exists())}/{len(todo)}")


if __name__ == "__main__":
    main()
