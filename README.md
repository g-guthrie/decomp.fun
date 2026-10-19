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
```

Deploys happen from GitHub Actions only (`npx wrangler@4.151.1 deploy` in `update.yml`).

## How it fits together

- **Static site** (`dist/`, built by `build.py`, Python 3 standard library only) served by Cloudflare Workers
  static assets. `dist/_headers` sets CSP and caching; `dist/_redirects` sends each default build's old
  `/<owner>/<repo>/<build>/` URL to `/<owner>/<repo>/`.
- **Worker** (`worker/index.js`, config `wrangler.jsonc`, name `decomp-fun`, custom domain `decomp.fun`). Runs only
  for `/auth/*` and `/api/*`; everything else is a static file.
  - `/auth/login`, `/auth/callback`, `/auth/logout`: GitHub OAuth app (no scopes, public profile only). Client ID in
    `wrangler.jsonc` `vars.GITHUB_CLIENT_ID`; callback `https://decomp.fun/auth/callback`.
  - `/api/submit`: quick checks and rate limits, then a `repository_dispatch` (`submission`) to this repo.
  - `/api/fresh?repo=owner/repo`: if the project's CI has a newer report than the page, dispatches `refresh`.
    Called by project pages on view, and by projects' own CI.
  - `/api/health`: whether the dispatch token works and when it expires (read by the watchdog).
  - KV namespace `LIMITS` (rate limits, refresh locks). Secrets: `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`,
    `GITHUB_DISPATCH_TOKEN`.
- **www redirect Worker** (`worker/www/`, name `decomp-fun-www`, custom domain `www.decomp.fun`): 301 to
  `https://decomp.fun` with the same path. Deployed by hand, rarely (see RUNBOOK).
- **GitHub Actions**
  - `update.yml`: every 6 hours, on pushes to `main`, and on `repository_dispatch` (`submission`, `refresh`):
    fetch → check submission → `tools/merge_remote.py` → build → deploy → commit `data/history`, `data/latest`,
    `data/submissions` (+ `data/slots.json`, `data/disputes.json` on scheduled runs).
  - `watchdog.yml`: daily. Checks `health.json`, `/api/health`, `/v1/projects.json` and the project count; opens or
    comments on a "decomp.fun needs attention" issue. Also keeps scheduled workflows enabled (GitHub turns them off
    after 60 quiet days).
  - `game-index.yml`: monthly. Rebuilds `data/games/` with `tools/build_game_index.py`; refuses to commit if any
    system's list shrinks by more than 20% (`tools/check_game_index.py`).

## Code

| File | What it does |
|---|---|
| `fetch.py` | Reads each project's newest report from its GitHub Actions artifacts (or a committed file) into `data/reports/` (full, cached) and `data/latest/` (slim, committed); appends to `data/history/`. |
| `build.py` | Generates every HTML page, `units.json`, `history.json`, banners, `fresh.json`, `health.json`, `_headers`. |
| `api.py` | Generates `/v1/*.json`, badges, todo lists, `/feed.xml`, `/llms.txt`, `_redirects`. Called from `build.py`. |
| `slots.py` | One slot per game: picks each game's listed repository, takeovers, disputes. State in `data/slots.json`, `data/disputes.json`. |
| `identify.py` | Works out which game a repository decompiles (disc ID, serial, title ID, ROM hash) against `data/games/`. |
| `submit.py` | Checks one submitted repository (run by `update.yml` on a `submission` dispatch); writes `data/submissions/`. |
| `tools/build_game_index.py` | Builds `data/games/<system>.json` from libretro-database (No-Intro, Redump; CC BY-SA 4.0), MAME, titledb, Wikidata. |
| `tools/check_game_index.py` | Fails if a rebuilt game list shrank by more than 20%. |
| `tools/merge_remote.py` | Before a deploy, takes newer data files that another run committed meanwhile. |
| `tools/oneoff/` | Scripts used once to seed the data (decomp.dev archive import, history backfill, identity seeding). Kept for reference. |

## Data

| Path | What it is | In git |
|---|---|---|
| `data/catalog.json` | The starting project index, recovered from the archived decomp.dev listing. | yes |
| `data/projects.json` | Hand-configured projects (custom report source, version, description); overrides the catalog by `owner/repo`. | yes |
| `data/submissions/` | One file per submitted repository: status, reasons, project entry. | yes |
| `data/identity.json` | Which game each repository is (platform, game key, title, builds). | yes |
| `data/games/` | Known games per system with their identifiers (CC BY-SA 4.0 sources). | yes |
| `data/slots.json`, `data/disputes.json` | Game slot leaders and challengers; lost disputes. | yes |
| `data/history/` | Progress points over time per project (small, irreplaceable). | yes |
| `data/latest/` | Slim copy of each project's latest report (no units), so a build never goes backwards. | yes |
| `data/reports/` | Full latest reports incl. per-unit data. Large and re-fetchable: Actions cache only. | no |
| `data/site.json` | Name, intro text, nav links, contact link. | yes |
| `public/` | Static assets: Pico CSS and uPlot (MIT, `public/static/vendor/LICENSES.txt`) and the site's own CSS/JS. | yes |

## Where progress comes from

`fetch.py` reads each project's report from its own GitHub Actions:

- **auto** (default): the newest non-expired artifact with `report` in its name on the default branch. Each artifact
  name is a build (`GALE01_report` → `GALE01`). This covers decomp-toolkit/objdiff projects.
- **objdiff_artifact**: a named artifact from a named workflow (`data/projects.json` → `source`).
- **objdiff_file**: a `report.json` committed to the repo.
- **naomi_proof**: the MVC2 NAOMI build's `progress.json`, converted with the same rules as that repo's `tools/report.py`.

GitHub deletes artifacts after 90 days, so projects with no CI activity since then keep their last known numbers,
labelled as such on their page.

## Adding or removing a project

- Normally: submit it at https://decomp.fun/submit/.
- By hand: add an entry to `data/projects.json` (`owner`, `repo`, `name`, `platform`, optional `version`, `role`,
  `source`) and push; the next run fetches it.
- Remove: delete its entry from `data/catalog.json` / `data/projects.json`. Deleted or private repositories drop off
  automatically.

None of decomp.dev's code is used here. Its author has said the code is not licensed for reuse.
