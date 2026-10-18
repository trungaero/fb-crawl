"""Central configuration for the Facebook people crawler.

All tunable values live here so you don't have to touch the crawler code.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Search parameters
# ---------------------------------------------------------------------------
SEARCH_QUERY = "Robert Downey Jr"  # the name to search for; can be any string
CITY_NAME = "New York, New York"  # for logging only; not used in the search

# Facebook's people search supports a `filters` URL parameter that encodes the
# location filter as base64url(JSON). The exact value is easiest to capture by
# hand:
#   1. Open https://www.facebook.com/search/people/?q=Quynh%20Anh in a browser
#      while logged in.
#   2. Click "Filters" -> "Location" -> choose "Ho Chi Minh City".
#   3. Copy the `filters=...` value from the resulting URL and paste it below.
#
# Leave empty to run the search WITHOUT a location filter (matches everywhere).
CITY_FILTERS = ""

# ---------------------------------------------------------------------------
# Crawling behaviour
# ---------------------------------------------------------------------------
MAX_RESULTS = 0            # 0 = no limit (keep scrolling until no new results)
SCROLL_PASSES = 40           # max number of "scroll to bottom" passes per search
SCROLL_IDLE_SECONDS = 2.0    # wait after each scroll before re-collecting

MIN_DELAY = 2.0              # seconds
MAX_DELAY = 6.0              # seconds
REQUEST_TIMEOUT_MS = 30_000

# Visit the optional About tabs too (Links, Contact info, Details/quotes).
# Costs one extra page load per profile and slows the crawl noticeably.
SCRAPE_ABOUT_EXTRAS = False

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "fb.db"
STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"
PROFILE_PICS_DIR = DATA_DIR / "profile_pictures"

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
# Which browser backend to drive:
#   "chromium"  Playwright's bundled Chromium (default, `playwright install chromium`)
#   "firefox"   Playwright's bundled Firefox   (`playwright install firefox`)
#   "webkit"    Playwright's bundled WebKit    (`playwright install webkit`)
#   "msedge"    Microsoft Edge already installed on this machine
#   "chrome"    Google Chrome already installed on this machine
# Can also be overridden per run with `python main.py --browser firefox`.
BROWSER = "msedge"

HEADLESS = False             # keep a visible window so you can solve challenges
LOCALE = "en-US"
