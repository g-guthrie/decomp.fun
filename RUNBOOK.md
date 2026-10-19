# decomp.fun runbook

Everything runs on its own. This file is for the rare case something needs a person: a watchdog issue, a token
that expires, or a redeploy. Commands assume a checkout of this repository and `npx` (Node) installed.

## Secrets

| Secret | Lives in | Used for | How to rotate |
|---|---|---|---|
| `GITHUB_CLIENT_SECRET` | Worker `decomp-fun` (Cloudflare) | GitHub sign-in (OAuth app; client ID is `vars.GITHUB_CLIENT_ID` in `wrangler.jsonc`) | GitHub → Settings → Developer settings → OAuth Apps → decomp.fun → Generate a new client secret. Then `npx wrangler@4 secret put GITHUB_CLIENT_SECRET --name decomp-fun`, then delete the old secret on GitHub. |
| `SESSION_SECRET` | Worker `decomp-fun` | Signs sign-in session cookies (HMAC) | `openssl rand -base64 48 \| npx wrangler@4 secret put SESSION_SECRET --name decomp-fun`. Everyone is signed out; nothing else breaks. |
| `GITHUB_DISPATCH_TOKEN` | Worker `decomp-fun` | Sends `repository_dispatch` (submissions, refreshes) to this repo; also reads artifact lists for `/api/fresh` | GitHub → Settings → Developer settings → Fine-grained tokens → new token, resource owner `g-guthrie`, only repository `g-guthrie/decomp.fun`, permission **Contents: Read and write** (Metadata read is added automatically), longest expiry offered. Then `npx wrangler@4 secret put GITHUB_DISPATCH_TOKEN --name decomp-fun`. Check `https://decomp.fun/api/health` shows `"token_ok":true` and the new expiry. |
| `CLOUDFLARE_API_TOKEN` | GitHub repo → Settings → Secrets and variables → Actions | `wrangler deploy` in `update.yml` | Cloudflare dashboard → My Profile → API Tokens → create from the "Edit Cloudflare Workers" template, scoped to this account (and the `decomp.fun` zone). Paste it into the Actions secret, run "Update and deploy" by hand, then delete the old token. |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret | `wrangler deploy` | Not a credential; only changes if the account changes (Cloudflare dashboard → Workers & Pages → Account ID). |
| `GH_READ_TOKEN` (optional) | GitHub Actions secret | Higher GitHub API rate limit for `fetch.py` / `submit.py` (falls back to the built-in token) | Fine-grained token, public repositories read-only. Replace the secret value; nothing else. |

Not secrets but configured once: the KV namespace `LIMITS` (id in `wrangler.jsonc`), the custom domains
`decomp.fun` (main Worker) and `www.decomp.fun` (www Worker), and the OAuth app's callback URL
`https://decomp.fun/auth/callback`.

## Watchdog issues

`watchdog.yml` runs daily and opens (or comments on) an issue titled **decomp.fun needs attention**. Close the issue
once fixed; the next run reopens a new one if the problem is still there. Each line means:

- **The site isn't responding (health.json unreachable)** / **The project list (/v1/projects.json) isn't
  reachable**: the static site is down or the last deploy broke it. Check https://www.cloudflarestatus.com and the
  latest "Update and deploy" run. Re-run that workflow (Actions → Update and deploy → Run workflow). If the deploy
  step fails with an authentication error, rotate `CLOUDFLARE_API_TOKEN`.
- **The sign-in/submission server isn't responding (/api/health)**: the Worker throws or isn't deployed. Look at
  the Worker's logs in the Cloudflare dashboard (Workers & Pages → decomp-fun → Logs); re-running "Update and deploy"
  redeploys it.
- **The site hasn't been rebuilt for N hours**: "Update and deploy" is failing or disabled. Open its latest run and
  read the failing step. If the workflow was disabled, enable it (Actions → Update and deploy → Enable workflow).
- **N of M projects failed to update in the last run**: usually a GitHub outage or rate limit; it clears itself.
  If it persists for days, set or refresh `GH_READ_TOKEN`.
- **The number of listed projects dropped from A to B**: more than 10% of projects disappeared in a day. Usually
  GitHub errors making repositories look deleted. Check the last "Update and deploy" run's "Not listed" line. A
  repository is only delisted after 3 runs in a row and 48 hours of 404s, so one bad run never removes anything.
  If repositories really were deleted, nothing to do (the new count is the baseline from the next day).
- **The GitHub token used for submissions and refreshes has stopped working / expires in N days**: rotate
  `GITHUB_DISPATCH_TOKEN` (above). Until then submissions and instant refreshes fail; the hourly update still works.

The watchdog also re-enables `update.yml` and `game-index.yml` every day, and pushes an empty "Keepalive" commit if
the repository has had no commits for 45 days (GitHub disables scheduled workflows after 60 quiet days).

## Redeploying

- **Main site and Worker**: Actions → Update and deploy → Run workflow. Do not deploy from a laptop; the workflow
  builds with the cached full reports.
- **www redirect Worker** (only if it was deleted or `worker/www/` changed):
  `CLOUDFLARE_API_TOKEN=... npx wrangler@4 deploy -c worker/www/wrangler.jsonc`. Check
  `curl -sI https://www.decomp.fun/x` returns `301` to `https://decomp.fun/x`.

## Game lists

`game-index.yml` rebuilds `data/games/` on the 3rd of each month. To run it now: Actions → Refresh game lists →
Run workflow. Locally: `python3 tools/build_game_index.py && python3 tools/check_game_index.py`, then commit
`data/games`. If the check fails (a list shrank by more than 20%), an upstream source (libretro-database, titledb,
Wikidata) is broken or moved: the committed lists stay as they are; look at the failing system's source in
`tools/build_game_index.py`. Commits made by the workflow don't trigger a deploy; the next hourly run picks
them up.

## Limits to keep in mind

- Cloudflare static assets: 20,000 files per deploy (Workers Free; 100,000 on Paid) and 2,000 static redirect rules.
  `build.py` prints the counts; each project costs about 9 files plus 3 per extra build.
- Worker: `REFRESHES_PER_DAY` (default 100, site-wide) and 4 refreshes per repository per day, each project checked
  against GitHub at most every 10 minutes; 60 submissions per day site-wide, 3 per account, accounts at least 30
  days old. All counters live in the `LIMITS` KV namespace and expire by themselves.
