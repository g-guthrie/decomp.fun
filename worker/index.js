// decomp.fun Worker: GitHub sign-in and project submissions. Everything else is static files.
//
// /auth/login, /auth/callback, /auth/logout  GitHub OAuth (no scopes: only the public profile)
// /api/me                                    who is signed in
// /api/submit                                quick checks and rate limits, then hands the repository
//                                            to the check workflow (repository_dispatch "submission");
//                                            the full checks and the result happen there
// /api/fresh?repo=owner/repo                 is there newer progress on GitHub than the project page
//                                            shows? If so, dispatch a "refresh" of just that project
// /api/health                                does the dispatch token work, and when does it expire
//                                            (read by the watchdog workflow)
//
// Config:  vars     GITHUB_CLIENT_ID, SITE_REPO, REFRESHES_PER_DAY (optional)
//          secrets  GITHUB_CLIENT_SECRET, SESSION_SECRET, GITHUB_DISPATCH_TOKEN
//          kv       LIMITS (submission and refresh counters, refresh locks)

const ORIGIN = "https://decomp.fun";
const MIN_ACCOUNT_AGE_DAYS = 30;
const PER_ACCOUNT_PER_DAY = 3;
const SITE_PER_DAY = 60;
const SESSION_DAYS = 7;
const NAME_ONLY = ["win32", "switch", "dos"]; // same as identify.NAME_ONLY (Python)
const FRESH_CHECK_SECONDS = 600; // ask GitHub about a project at most every 10 minutes
const REFRESH_LOCK_SECONDS = 900; // one refresh in flight per project
const DEFAULT_REFRESHES_PER_DAY = 100; // site-wide
const REFRESHES_PER_REPO_PER_DAY = 4; // one busy repository can't use up everyone's refreshes

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      switch (url.pathname) {
        case "/auth/login":
          return login(url, env);
        case "/auth/callback":
          return await callback(request, url, env);
        case "/auth/logout":
          return redirect("/submit/", clearCookie("session"));
        case "/api/me":
          return await me(request, env);
        case "/api/submit":
          return await submit(request, env);
        case "/api/fresh":
          return await fresh(url, env);
        case "/api/health":
          return await health(env);
        default:
          return env.ASSETS.fetch(request);
      }
    } catch (err) {
      console.error(err);
      return json({ ok: false, message: "Something went wrong. Try again in a minute." }, 500);
    }
  },
};

// ------------------------------------------------------------------ helpers

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", "x-content-type-options": "nosniff", "cache-control": "no-store", ...headers },
  });

const redirect = (location, ...cookies) => {
  const h = new Headers({ location });
  for (const c of cookies) h.append("set-cookie", c);
  return new Response(null, { status: 302, headers: h });
};

const cookie = (name, value, maxAge) =>
  `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
const clearCookie = (name) => cookie(name, "", 0);

const readCookie = (request, name) => {
  const raw = request.headers.get("cookie") || "";
  const m = raw.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : null;
};

const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function hmac(secret, data) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data)));
}

async function makeSession(env, user) {
  const body = btoa(JSON.stringify({ ...user, exp: Date.now() + SESSION_DAYS * 86400e3 }));
  return `${body}.${await hmac(env.SESSION_SECRET, body)}`;
}

async function readSession(request, env) {
  const raw = readCookie(request, "session");
  if (!raw || !raw.includes(".")) return null;
  const [body, sig] = raw.split(".");
  try {
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SESSION_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    const sigBytes = Uint8Array.from(atob(sig.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
    if (!(await crypto.subtle.verify("HMAC", key, sigBytes, new TextEncoder().encode(body)))) return null;
    const s = JSON.parse(atob(body));
    return s.exp > Date.now() ? s : null;
  } catch {
    return null;
  }
}

const github = (path, token, init = {}) =>
  fetch(`https://api.github.com/${path}`, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      "user-agent": "decomp.fun",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });

// ------------------------------------------------------------------ auth

function login(url, env) {
  const state = b64url(crypto.getRandomValues(new Uint8Array(18)));
  const auth = new URL("https://github.com/login/oauth/authorize");
  auth.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  auth.searchParams.set("redirect_uri", `${ORIGIN}/auth/callback`);
  auth.searchParams.set("state", state);
  auth.searchParams.set("allow_signup", "false");
  return redirect(auth.toString(), cookie("oauth_state", state, 600));
}

async function callback(request, url, env) {
  const state = url.searchParams.get("state");
  if (!state || state !== readCookie(request, "oauth_state")) return redirect("/submit/?error=state");
  const tokenRes = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      code: url.searchParams.get("code"),
      redirect_uri: `${ORIGIN}/auth/callback`,
    }),
  });
  const { access_token } = await tokenRes.json();
  if (!access_token) return redirect("/submit/?error=login");
  const userRes = await github("user", access_token);
  if (!userRes.ok) return redirect("/submit/?error=login");
  const u = await userRes.json();
  // The GitHub token is not kept: the session only records who signed in.
  const session = await makeSession(env, { login: u.login, id: u.id, created: u.created_at });
  return redirect("/submit/", cookie("session", session, SESSION_DAYS * 86400), clearCookie("oauth_state"));
}

async function me(request, env) {
  const s = await readSession(request, env);
  return json(s ? { login: s.login, created: s.created } : { login: null });
}

// ------------------------------------------------------------------ submit

const count = async (env, key) => Number((await env.LIMITS.get(key)) || 0);
const increment = async (env, key) =>
  env.LIMITS.put(key, String((await count(env, key)) + 1), { expirationTtl: 2 * 86400 });

async function submit(request, env) {
  if (request.method !== "POST") return json({ ok: false, message: "POST only." }, 405);
  if (request.headers.get("origin") !== ORIGIN) return json({ ok: false, message: "Bad origin." }, 403);
  const s = await readSession(request, env);
  if (!s) return json({ ok: false, message: "Sign in with GitHub first." }, 401);

  const ageDays = (Date.now() - Date.parse(s.created)) / 86400e3;
  if (!(ageDays >= MIN_ACCOUNT_AGE_DAYS))
    return json({ ok: false, message: `Your GitHub account must be at least ${MIN_ACCOUNT_AGE_DAYS} days old to submit.` }, 403);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, message: "Bad request." }, 400);
  }
  const repo = String(body.repo || "")
    .trim()
    .replace(/^(https?:\/\/)?(www\.)?github\.com\//i, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo))
    return json({ ok: false, message: "Enter a GitHub repository, like owner/name or its URL." }, 400);
  const system = NAME_ONLY.includes(body.system) ? body.system : "";
  const game = system ? String(body.game || "").trim().slice(0, 120) : "";
  if (system && !game) return json({ ok: false, message: "Enter the game's name for PC, DOS and Switch projects." }, 400);

  const repoRes = await github(`repos/${repo}`, env.GITHUB_DISPATCH_TOKEN);
  if (repoRes.status === 404) return json({ ok: false, message: "That repository doesn't exist or isn't public." }, 400);
  const info = await repoRes.json();
  if (info.private) return json({ ok: false, message: "That repository isn't public." }, 400);

  // Parallel requests from one account: only the first in a 30-second window gets through.
  if (await env.LIMITS.get(`busy:${s.id}`))
    return json({ ok: false, message: "Your previous submission is still being sent. Try again in a moment." }, 429);
  await env.LIMITS.put(`busy:${s.id}`, "1", { expirationTtl: 60 });

  const day = new Date().toISOString().slice(0, 10);
  const userKey = `user:${s.id}:${day}`;
  const siteKey = `site:${day}`;
  if ((await count(env, userKey)) >= PER_ACCOUNT_PER_DAY)
    return json({ ok: false, message: `You can submit ${PER_ACCOUNT_PER_DAY} repositories per day. Try again tomorrow.` }, 429);
  if ((await count(env, siteKey)) >= SITE_PER_DAY)
    return json({ ok: false, message: "The site has had a lot of submissions today. Try again tomorrow." }, 429);

  const dispatch = await github(`repos/${env.SITE_REPO}/dispatches`, env.GITHUB_DISPATCH_TOKEN, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ event_type: "submission", client_payload: { repo: info.full_name, by: s.login, system, game } }),
  });
  if (!dispatch.ok) {
    console.error("dispatch failed", dispatch.status, await dispatch.text());
    return json({ ok: false, message: "Couldn't start the checks. Try again in a few minutes." }, 502);
  }
  // Only submissions that actually started count toward the limits.
  await increment(env, userKey);
  await increment(env, siteKey);
  return json({ ok: true, repo: info.full_name, message: `Submitted ${info.full_name}.` });
}

// ------------------------------------------------------------------ refresh on view

let manifest = null; // fresh.json, loaded once per Worker instance
let manifestAt = 0;

async function loadManifest(env, origin) {
  if (!manifest || Date.now() - manifestAt > 60e3) {
    const res = await env.ASSETS.fetch(new Request(`${origin}/fresh.json`));
    manifest = res.ok ? await res.json() : {};
    manifestAt = Date.now();
  }
  return manifest;
}

async function latestSha(env, repo, src) {
  if (src.kind === "objdiff_file" && src.path) {
    const r = await github(`repos/${repo}/commits?path=${encodeURIComponent(src.path)}&per_page=1`, env.GITHUB_DISPATCH_TOKEN);
    if (!r.ok) throw new Error(`GitHub ${r.status}`);
    const [c] = await r.json();
    return c ? c.sha : null;
  }
  const r = await github(`repos/${repo}/actions/artifacts?per_page=50`, env.GITHUB_DISPATCH_TOKEN);
  if (!r.ok) throw new Error(`GitHub ${r.status}`);
  const { artifacts = [] } = await r.json();
  const newest = artifacts
    .filter((a) => !a.expired && a.workflow_run && a.workflow_run.head_branch === src.branch)
    // A pull request from a fork whose branch is also called "main" also runs here; never trust it.
    .filter((a) => !a.workflow_run.head_repository_id || a.workflow_run.head_repository_id === a.workflow_run.repository_id)
    .filter((a) => (src.artifact ? a.name === src.artifact : /report/i.test(a.name)))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return newest ? newest.workflow_run.head_sha : null;
}

async function fresh(url, env) {
  const repo = String(url.searchParams.get("repo") || "").toLowerCase();
  const list = await loadManifest(env, url.origin);
  const src = list[repo];
  if (!src) return json({ status: "unknown" });

  // Answer from the edge cache when this project was checked recently (no KV writes per view).
  const cache = caches.default;
  const cacheKey = new Request(`${url.origin}/__fresh/${encodeURIComponent(repo)}/${src.sha}`);
  const cached = await cache.match(cacheKey);
  if (cached) return cached;

  let status = "current";
  let latest;
  try {
    latest = await latestSha(env, repo, src);
  } catch (err) {
    console.error("fresh check failed", repo, String(err));
    return json({ status: "error" });
  }
  if (latest && latest !== src.sha) {
    const day = new Date().toISOString().slice(0, 10);
    const limit = Number(env.REFRESHES_PER_DAY || DEFAULT_REFRESHES_PER_DAY);
    if (await env.LIMITS.get(`refreshing:${repo}`)) {
      status = "updating";
    } else if ((await count(env, `refresh:${day}`)) >= limit || (await count(env, `refresh:${repo}:${day}`)) >= REFRESHES_PER_REPO_PER_DAY) {
      status = "busy";
    } else {
      const d = await github(`repos/${env.SITE_REPO}/dispatches`, env.GITHUB_DISPATCH_TOKEN, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ event_type: "refresh", client_payload: { repo } }),
      });
      if (d.ok) {
        await env.LIMITS.put(`refreshing:${repo}`, latest, { expirationTtl: REFRESH_LOCK_SECONDS });
        await increment(env, `refresh:${day}`);
        await increment(env, `refresh:${repo}:${day}`);
        status = "updating";
      } else {
        console.error("refresh dispatch failed", d.status);
        status = "current";
      }
    }
  }
  const res = json({ status }, 200, { "cache-control": `public, max-age=${FRESH_CHECK_SECONDS}` });
  await cache.put(cacheKey, res.clone());
  return res;
}

// ------------------------------------------------------------------ health

// Read by the watchdog workflow: is the GitHub token still working, and when does it expire?
async function health(env) {
  const r = await github("rate_limit", env.GITHUB_DISPATCH_TOKEN);
  const expires = r.headers.get("github-authentication-token-expiration");
  return json({
    token_ok: r.ok,
    token_expires: expires ? new Date(expires).toISOString() : null,
    checked: new Date().toISOString(),
  });
}
