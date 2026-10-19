# decomp.fun

A directory and leaderboard of matching decompilation projects for video games, at https://decomp.fun.
Progress is read automatically from each project's own CI (objdiff report artifacts). One project is listed per
game. People add projects themselves at `/submit/` (sign in with GitHub, all checks automatic). Nothing needs
manual upkeep; [RUNBOOK.md](RUNBOOK.md) covers the secrets and what to do when the watchdog opens an issue.

Agents and scripts: see https://decomp.fun/llms.txt and the static JSON API under https://decomp.fun/v1/.

```
python3 fetch.py --repo doldecomp/melee   # fetch one project's report (needs `gh auth login`); no args = all projects
python3 build.py                          # generate dist/ from data/
python3 -m http.server -d dist 8000       # look at it (the Worker routes /auth and /api won't work here)
python3 -m unittest discover -s tests     # offline tests (identification, report parsing, takeover streaks)
```

Deploys happen from GitHub Actions only (`npx wrangler@4.151.1 deploy` in `update.yml`).

## How it fits together

- **Static site** (`dist/`, built by `build.py`, Python 3 standard library only) served by Cloudflare Workers
  static assets. `dist/_headers` sets CSP and caching; `dist/_redirects` sends each default build's old
  `/<owner>/<repo>/<build>/` URL to `/<owner>/<repo>/`. Static asset URLs carry a content hash (`?v=`).
- **Worker** (`worker/index.js`, config `wrangler.jsonc`, name `decomp-fun`, custom domain `decomp.fun`). Runs only
  for `/auth/*` and `/api/*`; everything else is a static file.
  - `/auth/login`, `/auth/callback`, `/auth/logout`: GitHub OAuth app (no scopes, public profile only). Client ID in
    `wrangler.jsonc` `vars.GITHUB_CLIENT_ID`; callback `https://decomp.fun/auth/callback`. The session cookie
    (7 days, HMAC-signed with `SESSION_SECRET`) holds the login, numeric ID and account creation date.
  - `/api/me`: who is signed in.
  - `/api/submit`: quick checks and rate limits, then a `repository_dispatch` (`submission`) to this repo.
  - `/api/fresh?repo=owner/repo`: if the project's CI has a newer report than the page (build commit from
    `fresh.json`), dispatches `refresh`. Called by project pages on view, and by projects' own CI.
  - `/api/health`: whether the dispatch token works and when it expires (read by the watchdog).
  - KV namespace `LIMITS` (daily counters, refresh locks). Secrets: `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`,
    `GITHUB_DISPATCH_TOKEN`. Optional var `REFRESHES_PER_DAY` (default 100).
- **www redirect Worker** (`worker/www/`, name `decomp-fun-www`, custom domain `www.decomp.fun`): 301 to
  `https://decomp.fun` with the same path. Deployed by hand, rarely (see RUNBOOK).
- **GitHub Actions**
  - `update.yml`: every 6 hours, on pushes to `main` (except progress-data and docs commits), and on
    `repository_dispatch` (`submission`: run `submit.py`; `refresh`: run `fetch.py --repo`). Steps: restore the
    report cache → fetch / check / refresh → `tools/merge_remote.py` → build → deploy → commit `data/history`,
    `data/latest`, `data/submissions` (+ `data/slots.json`, `data/disputes.json`, which only non-dispatch runs
    record, so concurrent submission and refresh runs never clash on them).
  - `watchdog.yml`: daily. Checks `/v1/projects.json`, `health.json` and `/api/health`, and the project count
    against the previous day; opens or comments on a "decomp.fun needs attention" issue. Also keeps the scheduled
    workflows enabled and pushes a keepalive commit after 45 quiet days (GitHub disables schedules after 60).
  - `game-index.yml`: monthly. Rebuilds `data/games/` with `tools/build_game_index.py`; refuses to commit if any
    system's list shrinks by more than 20% (`tools/check_game_index.py`).

## Code

| File | What it does |
|---|---|
| `fetch.py` | Reads each project's newest report from its GitHub Actions artifacts (or a committed file) into `data/reports/` (full, cached) and `data/latest/` (slim, committed); appends to `data/history/`. |
| `build.py` | Generates every HTML page, `units.json`, `history.json`, card banners and social images, `fresh.json`, `health.json`, `submissions.json`, `sitemap.xml`, `_headers`. Wires `slots.py` and `api.py`. |
| `api.py` | Generates `/v1/*.json`, badges, todo lists, `/feed.xml`, `/llms.txt`, `_redirects`. Called from `build.py`. |
| `slots.py` | One slot per game: picks each game's listed repository, takeovers, disputes. State in `data/slots.json`, `data/disputes.json`. |
| `identify.py` | Works out which game a repository decompiles (disc ID, serial, title ID, ROM hash) against `data/games/`. |
| `submit.py` | Checks one submitted repository (run by `update.yml` on a `submission` dispatch); writes `data/submissions/`. |
| `tools/build_game_index.py` | Builds `data/games/<system>.json` from libretro-database (No-Intro, Redump; CC BY-SA 4.0), MAME, titledb, Wikidata. |
| `tools/check_game_index.py` | Fails if a rebuilt game list shrank by more than 20%. |
| `tools/merge_remote.py` | Before a build, takes newer `data/latest` and `data/submissions` files that another run committed meanwhile. |
| `tools/oneoff/` | Scripts used once to seed the data (decomp.dev archive import, history backfill, identity seeding). Kept for reference. |
| `tests/test_tier2.py` | Offline unit tests, standard library only. |
| `public/` | Static assets: Pico CSS and uPlot (MIT, `public/static/vendor/LICENSES.txt`) and the site's own CSS/JS. |

## Data

| Path | What it is | In git |
|---|---|---|
| `data/catalog.json` | The starting project index, recovered from the archived decomp.dev listing. | yes |
| `data/projects.json` | Hand-configured projects (custom report source, version, description); overrides the catalog by `owner/repo`. | yes |
| `data/submissions/` | One file per submitted repository: status, reasons, project entry. | yes |
| `data/identity.json` | Which game each inherited repository is (platform, game key, title, builds). Submissions carry theirs in their own file. | yes |
| `data/games/` | Known games per system with their identifiers (CC BY-SA 4.0 sources). | yes |
| `data/slots.json`, `data/disputes.json` | Game slot leaders, challenger streaks and takeover history; projects that lost a dispute. | yes |
| `data/history/` | Progress points over time per project (small, irreplaceable). | yes |
| `data/latest/` | Slim copy of each project's latest report (no units), so a build never goes backwards after a cache loss. | yes |
| `data/reports/` | Full latest reports incl. per-unit data. Large and re-fetchable: Actions cache only. | no |
| `data/site.json` | Name, tagline, intro text, extra nav links. | yes |

## Where progress comes from

`fetch.py` reads each project's report from its own GitHub Actions:

- **auto** (default): the newest non-expired artifact with `report` in its name on the default branch. Each artifact
  name is a build (`GALE01_report` → `GALE01`). This covers decomp-toolkit/objdiff projects. Runs from forks' pull
  requests are ignored.
- **objdiff_artifact**: a named artifact (`data/projects.json` → `source.artifact`, optional `branch`, `file`).
- **objdiff_file**: a `report.json` committed to the repo (`source.path`).
- **naomi_proof**: the MVC2 NAOMI build's `progress.json`, converted with the same rules as that repo's `tools/report.py`.

GitHub deletes artifacts after 90 days, so projects with no CI activity since then keep their last known numbers,
labelled as such on their page.

## One listing per game

- Every project gets a slot: `platform:game` when the game is identified, else `repo:owner/name`. The repository
  with the most code decompiled leads; the others are shown on its page (`slots.py`).
- **Takeover**: a challenger takes the slot after leading by 1 percentage point for 7 days in a row, ahead on at
  least 90% of the runs in that time (one run behind is tolerated, two in a row end the streak). A submitted
  repository must have been listed for 30 days first; a fork can't take its parent's place (its page offers a
  pull-request link instead). Against a leader with only archived numbers, a live report at equal progress counts
  as ahead.
- **Build-ID comparison**: repositories are compared build to build. Same build ID and code size within 2%:
  the same executable. Different builds (USA, Japan): both fine. Same build ID, different size: a conflict.
- **Disputes**: a submission conflicting with the listed repository is recorded as *contesting*. Two independent
  contesters (different owners, no shared commits, not forks of each other, repositories at least 30 days old)
  that agree with each other replace a submitted listing. Original listings (inherited or hand-configured) are
  never removed this way; they are marked *contested*.

## Adding, refreshing and removing

- Normally: submit at https://decomp.fun/submit/. The Worker requires a GitHub account at least 30 days old and
  allows 3 submissions per account and 60 per site per day; `submit.py` then checks the repository (public, real
  objdiff report on the default branch, at least 5% decompiled, at least half the decompiled files present in the
  repo, game identified from the repo or, for Windows/DOS/Switch, named from the system's list).
- By hand: add an entry to `data/projects.json` (`owner`, `repo`, `name`, `platform`, optional `version`,
  `description`, `source`) and push; the next run fetches it.
- Refresh: every project every 6 hours. Project pages also call `/api/fresh` on view (one GitHub check per project
  per 10 minutes; at most 4 refreshes per repository and 100 per site per day), as can a project's own CI.
- Remove: delete its entry from `data/catalog.json` / `data/projects.json`. Deleted or private repositories drop off
  automatically once they've been missing for 3 runs in a row and at least 48 hours.

None of decomp.dev's code is used here. Its author has said the code is not licensed for reuse.
