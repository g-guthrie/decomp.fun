# decomp.fun

A static directory of matching decompilation projects and their progress, deployed to Cloudflare at https://decomp.fun.

```
python3 fetch.py        # pull the latest progress reports from GitHub (needs `gh auth login`)
python3 build.py        # generate dist/
python3 -m http.server -d dist 8000
npx wrangler@4 deploy   # publish dist/ to decomp.fun
```

## Automation

`.github/workflows/update.yml` runs every 6 hours (and on demand from the Actions tab):
fetch every project's latest report → build → `wrangler deploy` → commit any new history points.

- `data/reports/` is kept in the Actions cache (large, re-fetchable); `data/history/` is committed (small, irreplaceable).
- Secrets: `CLOUDFLARE_API_TOKEN` (Workers edit), `CLOUDFLARE_ACCOUNT_ID`, optional `GH_READ_TOKEN` (fine-grained, public repos read-only) for a higher GitHub rate limit.
- A failed run leaves the last good site up; GitHub emails you about the failure.

## Layout

| Path | What it is |
|---|---|
| `data/projects.json` | Projects added or customised by hand (custom report source, version, description). Entries override the catalog by `owner/repo`. |
| `data/catalog.json` | The public project index, imported once from the archived decomp.dev listing (`tools/import_archive.py`). |
| `data/reports/<id>.json` | Latest normalised report per project, written by `fetch.py`. |
| `data/history/<id>.json` | Progress points over time (monthly archive snapshots, then one per new commit seen by `fetch.py`). |
| `data/images/` | Card banners. |
| `data/site.json` | Name, intro text, nav links, contact link. |
| `public/` | Static assets: Pico CSS and uPlot (MIT, see `public/static/vendor/LICENSES.txt`) plus this site's own CSS/JS. |
| `wrangler.jsonc` | Cloudflare Workers static-assets config, with `decomp.fun` and `www.decomp.fun` as custom domains. |

## Where progress comes from

`fetch.py` reads each project's report from its own GitHub Actions:

- **auto** (default): the newest non-expired artifact with `report` in its name on the default branch. Each artifact name is a version (`GALE01_report` → `GALE01`). This covers decomp-toolkit/objdiff projects.
- **objdiff_artifact**: a named artifact from a named workflow (`data/projects.json` → `source`).
- **objdiff_file**: a `report.json` committed to the repo (Top Spin 4).
- **naomi_proof**: the MVC2 NAOMI build's `progress.json`, converted with the same rules as that repo's `tools/report.py`.

GitHub deletes artifacts after 90 days, so projects with no CI activity since then keep their last known numbers, labelled as such on their page.

## Adding or removing a project

- Add: put an entry in `data/projects.json` (`owner`, `repo`, `name`, `platform`, optional `version`, `image`, `role`, `source`), then fetch and build.
- Remove: delete its entry from `data/catalog.json` (and `data/projects.json`).

Platforms: `n64 gc wii switch gba nds 3ds ps ps2 psp xbox xbox360 win32 dc naomi`.

None of decomp.dev's code is used here. Its author has said the code is not licensed for reuse.
