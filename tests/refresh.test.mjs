// node --test tests/refresh.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { claim, mayCheck, REPO_PER_DAY, SITE_PER_HOUR, SITE_PER_DAY, LOCK_MS, CHECKS_PER_HOUR } from "../worker/refresh.js";

const T = Date.parse("2026-10-19T10:00:00Z");
const apply = (s, repo, w) => ({ ...s, repo: w.repo, day: w.day, hour: w.hour });

test("first claim goes, then waits while in flight", () => {
  let r = claim({}, "a/b", "s2", "s1", T);
  assert.equal(r.status, "go");
  assert.equal(claim({ repo: r.write.repo }, "a/b", "s2", "s1", T + 60e3).status, "updating");
  // a newer push while the first is still running also waits
  assert.equal(claim({ repo: r.write.repo }, "a/b", "s3", "s1", T + 60e3).status, "updating");
});

test("lock clears as soon as the site shows the fetched commit", () => {
  const r = claim({}, "a/b", "s2", "s1", T);
  assert.equal(claim({ repo: r.write.repo, day: r.write.day, hour: r.write.hour }, "a/b", "s3", "s2", T + 120e3).status, "go");
});

test("a lost refresh unlocks after LOCK_MS", () => {
  const r = claim({}, "a/b", "s2", "s1", T);
  assert.equal(claim({ repo: r.write.repo }, "a/b", "s2", "s1", T + LOCK_MS - 1).status, "updating");
  assert.equal(claim({ repo: r.write.repo }, "a/b", "s2", "s1", T + LOCK_MS).status, "go");
});

test("one project: unlimited in practice, capped at REPO_PER_DAY", () => {
  let s = {};
  let shown = "s0";
  let ok = 0;
  for (let i = 1; i <= REPO_PER_DAY + 5; i++) {
    const now = T + i * 90e3; // a push and a click every 90 seconds, across hours
    const r = claim({ ...s, hour: undefined }, "a/b", `s${i}`, shown, now);
    if (r.status === "go") {
      s = apply(s, "a/b", r.write);
      shown = `s${i}`; // that refresh landed
      ok++;
    }
  }
  assert.equal(ok, REPO_PER_DAY);
});

test("site-wide hourly and daily caps", () => {
  let s = {};
  let n = 0;
  for (let i = 0; i < SITE_PER_HOUR + 10; i++) {
    const r = claim({ day: s.day, hour: s.hour }, `r/${i}`, "new", "old", T + i);
    if (r.status === "go") {
      s = { day: r.write.day, hour: r.write.hour };
      n++;
    }
  }
  assert.equal(n, SITE_PER_HOUR);
  assert.equal(claim({ day: s.day, hour: s.hour }, "x/y", "new", "old", T + 3600e3).status, "go"); // next hour
  assert.equal(claim({ day: { period: "2026-10-19", n: SITE_PER_DAY } }, "x/y", "new", "old", T).status, "busy");
  assert.equal(claim({ day: { period: "2026-10-18", n: SITE_PER_DAY } }, "x/y", "new", "old", T).status, "go"); // new day
});

test("GitHub checks: once a minute per project, capped per hour", () => {
  const mem = { hour: null, n: 0, last: new Map() };
  assert.equal(mayCheck(mem, "a/b", T), true);
  assert.equal(mayCheck(mem, "a/b", T + 59e3), false);
  assert.equal(mayCheck(mem, "a/b", T + 60e3), true);
  for (let i = 0; i < CHECKS_PER_HOUR; i++) mayCheck(mem, `r/${i}`, T + 61e3);
  assert.equal(mayCheck(mem, "z/z", T + 62e3), false);
  assert.equal(mayCheck(mem, "z/z", T + 3600e3), true);
});

test("worst case leaves the hourly update its GitHub budget", () => {
  const CALLS_PER_REFRESH = 10; // measured 2-6
  const HOURLY_RUN = 250; // measured 249 with 203 projects
  assert.ok(SITE_PER_HOUR * CALLS_PER_REFRESH + HOURLY_RUN * 2 <= 1000); // room for the site to double
});
