// decomp.fun refresh-on-view limits, kept in one Durable Object so every count is exact.
//
// The caps are there so refreshes can never crowd out the hourly update of every project: that run
// needs ~250 of the site repository's 1,000 GitHub calls an hour, and a refresh makes up to ~10.
//
//   per project   one refresh in flight; at most REPO_PER_DAY a day
//   site-wide     at most SITE_PER_HOUR an hour and SITE_PER_DAY a day
//   GitHub checks each project at most once a minute; at most CHECKS_PER_HOUR an hour site-wide
//                 (they use the dispatch token, which has 5,000 calls an hour)
//
// Past any cap a page says the change will appear with the next hourly update, which never
// depends on any of this.

export const REPO_PER_DAY = 100;
export const SITE_PER_HOUR = 40;
export const SITE_PER_DAY = 500;
export const CHECK_EVERY_MS = 60e3;
export const CHECKS_PER_HOUR = 1500;
export const LOCK_MS = 15 * 60e3; // a refresh that hasn't landed by then is treated as lost

const dayOf = (now) => new Date(now).toISOString().slice(0, 10);
const hourOf = (now) => new Date(now).toISOString().slice(0, 13);
const counter = (c, period) => (c && c.period === period ? c.n : 0);

// May the Worker ask GitHub about this project now? (Pure; `mem` is the in-memory check log.)
export function mayCheck(mem, repo, now) {
  const hour = hourOf(now);
  if (mem.hour !== hour) Object.assign(mem, { hour, n: 0 });
  if (mem.n >= CHECKS_PER_HOUR || now - (mem.last.get(repo) || 0) < CHECK_EVERY_MS) return false;
  mem.n += 1;
  mem.last.set(repo, now);
  return true;
}

// GitHub has `latest`; the site shows `shown`. Returns { status, write } where status is
// "go" (dispatch a refresh), "updating" (one is already on its way) or "busy" (a cap is reached),
// and write is the state to store when status is "go". (Pure.)
export function claim(s, repo, latest, shown, now) {
  const rec = s.repo || {};
  const day = dayOf(now);
  const hour = hourOf(now);
  // In flight until the site shows what it was fetching (or it has had LOCK_MS to land). A newer
  // push meanwhile waits for that one to land; the page reloads then and asks again.
  if (rec.sha && rec.sha !== shown && now - rec.at < LOCK_MS) return { status: "updating" };
  const repoN = counter(rec.count, day);
  const dayN = counter(s.day, day);
  const hourN = counter(s.hour, hour);
  if (repoN >= REPO_PER_DAY || dayN >= SITE_PER_DAY || hourN >= SITE_PER_HOUR) return { status: "busy" };
  return {
    status: "go",
    write: {
      repo: { sha: latest, at: now, count: { period: day, n: repoN + 1 } },
      day: { period: day, n: dayN + 1 },
      hour: { period: hour, n: hourN + 1 },
    },
  };
}

// The Durable Object. One instance ("global") holds every count.
export class RefreshLimits {
  constructor(ctx) {
    this.storage = ctx.storage;
    this.mem = { hour: null, n: 0, last: new Map() };
  }

  async fetch(request) {
    const { op, repo, latest, shown } = await request.json();
    const now = Date.now();
    if (op === "check") return Response.json({ ok: mayCheck(this.mem, repo, now) });
    const key = `repo:${repo}`;
    if (op === "claim") {
      const got = await this.storage.get([key, "day", "hour"]);
      const r = claim({ repo: got.get(key), day: got.get("day"), hour: got.get("hour") }, repo, latest, shown, now);
      if (r.write) await this.storage.put({ [key]: r.write.repo, day: r.write.day, hour: r.write.hour });
      return Response.json({ status: r.status });
    }
    if (op === "release") {
      // The dispatch failed: drop the lock so the next view can try again (the count stays).
      const rec = await this.storage.get(key);
      if (rec) await this.storage.put(key, { ...rec, sha: null });
      return Response.json({ ok: true });
    }
    return new Response("bad op", { status: 400 });
  }
}
