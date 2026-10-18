# Facebook People Crawler

Crawls Facebook **people** search results for a phrase,
optionally filtered to a city (default Ho Chi Minh City), then visits each
unique profile to store:

- the profile URL and numeric/user id
- all publicly visible info (name, bio, lives-in/from, work, education,
  birthday, gender, languages, relationship, follower count)
- optionally quotes, links and contact info (see `SCRAPE_ABOUT_EXTRAS`)
- the **profile picture** (downloaded, with an on-screen screenshot fallback)

Everything is saved to a SQLite database under `data/`.

> **⚠️ Disclaimer** — Automated crawling violates Facebook's Terms of Service
> and may get an account flagged or banned. Use conservatively, only for data
> you are authorized to collect, and at your own risk. The script uses polite
> random delays and resume support to reduce load, but that does not make it
> compliant with Facebook's ToS.

## Requirements

- Python 3.9+
- A Facebook account you can log in with

## Setup

```powershell
cd e:\trung\facebook-crawler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Using Edge or Firefox instead of the bundled Chromium? See
[Choosing the browser](#choosing-the-browser) below.

## Choosing the browser

Set `BROWSER` in `config.py`, or pass `--browser` per run:

| Value      | What runs                                    | Extra setup                          |
|------------|----------------------------------------------|--------------------------------------|
| `chromium` | Playwright's bundled Chromium (default)      | `playwright install chromium`        |
| `firefox`  | Playwright's bundled Firefox                 | `playwright install firefox`         |
| `webkit`   | Playwright's bundled WebKit (Safari engine)  | `playwright install webkit`          |
| `msedge`   | Microsoft Edge installed on this machine     | Edge must be installed               |
| `chrome`   | Google Chrome installed on this machine      | Chrome must be installed             |

```powershell
python main.py --browser msedge
python main.py --browser firefox --headless
playwright install firefox    # only needed for the bundled engines
```

Each backend keeps its own login session, e.g. `data/storage_state_msedge.json`
(`chromium` keeps the original `data/storage_state.json`), so switching browsers
does not overwrite another browser's saved login. The first run on a new browser
asks you to log in again.

If the browser-specific file does not exist, the crawler falls back to
`data/storage_state.json`, so a session saved once (e.g. with
`playwright codegen --save-storage data\storage_state.json ...`) works for any
`--browser` value. Pass `--storage-state <path>` to force a specific file:

```powershell
python main.py --browser msedge --storage-state data\storage_state.json
```

## Configure the city filter

1. Open `https://www.facebook.com/search/people/?q=Quynh%20Anh` while logged in.
2. Click **Filters → Location → Ho Chi Minh City**.
3. Copy the `filters=...` value from the URL and paste it into `config.py`
   (`CITY_FILTERS`).
4. Leave `CITY_FILTERS = ""` to search everywhere instead.

## Run

```powershell
# First run: logs in interactively, saves the session, then crawls
python main.py

# Just log in and save the session (no crawling)
python main.py --login-only

# Headless (only after a session has been saved)
python main.py --headless

# Different search phrase
python main.py --query "Nguyen Van A"

# Only the first 3 profiles, re-scraping ones already marked done
python main.py --limit 3 --redo
```

`--limit N` caps how many profiles are visited per run and `--redo` ignores the
`status = 'done'` resume marker (handy after changing the extractor).

## Output

- `data/fb.db` — SQLite DB:
  - `search_results` — every people result found for the query
  - `profiles` — per-profile public info + `profile_photo_path`, keyed by
    `user_id`, with a `status` column (`done` / `error`) used for resume.
    Columns: `name`, `bio`, `lives_in`, `from_city`, `birthday`, `gender`,
    `languages`, `work`, `education`, `family`, `relationship`, `quotes`,
    `links`, `contact_info`, `follower_count`. Multi-valued fields are
    newline-separated. Older databases are migrated automatically on open.
- `data/profile_pictures/` — `<user_id>.jpg|png` image files.
- `data/storage_state.json` — saved login session for Chromium
  (`data/storage_state_<browser>.json` for other backends).

## Resume behaviour

- Profiles with `status = 'done'` are skipped on subsequent runs.
- Profile pictures that already exist (non-empty) are not re-downloaded.

## Notes

Facebook's DOM changes frequently. If result parsing or profile extraction
stops working, the selectors live in `crawler/search.py`, `crawler/profile.py`,
and `crawler/images.py` and are kept deliberately simple/tolerant so they're
easy to adjust.

Rendered Facebook pages ship **no** `og:title` / `og:image` meta tags, and
result avatars are inline `<svg><image xlink:href=...>` rather than `<img>`.
Both extractors therefore read the rendered DOM: the name is the first line of
the main column (cross-checked against the avatar's `svg[aria-label]`), and the
picture URL comes from the header avatar's `<image>`.

### About tabs

Work, education, bio and personal details are **not** on the profile landing
page — it only shows a truncated preview. Each profile therefore costs a few
extra navigations:

| Section | Username profile | `profile.php?id=` profile |
| --- | --- | --- |
| About | `/<user>/about` | `?id=<id>&sk=about` |
| Work | `/<user>/directory_work` | `&sk=directory_work` |
| Education | `/<user>/directory_education` | `&sk=directory_education` |
| Personal details | `/<user>/directory_personal_details` | `&sk=directory_personal_details` |
| Details (quotes) | `/<user>/about_details` | `&sk=about_details` |
| Links | `/<user>/directory_links` | `&sk=directory_links` |
| Contact info | `/<user>/directory_contact_info` | `&sk=directory_contact_info` |

Each About card is a `<section>` whose `<h2>` is the title (`Work`,
`Education`, `Birthday`, …) and whose direct `div` children are the visual rows,
so a single generic parser handles every card. Only the first four tabs are
visited by default; set `SCRAPE_ABOUT_EXTRAS = True` in [config.py](./config.py)
to also pull quotes, links and contact info (one extra page load each).

`/people/<slug>/<id>/` URLs have no About tabs, so they are rewritten to
`profile.php?id=<id>` before the tabs are visited.
